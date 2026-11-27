"""
📑 СО и ВОР — листы СО и ВР (патчи 22.22–22.24).

Витрина статусов листов СО/ВР из таблицы documents_sovr.
Две подвкладки: «Основная» (фильтры, бублики, таблица, экспорт) и
«Сервисная» (обновление из Витро + загрузка перечня СВОК).

Патч 22.24:
  A1 — бублики статусов зависят от всех фильтров, КРОМЕ периода
       (send_date); таблица — от всех, включая период.
  A2 — переключатель подвкладок (st.tabs), по умолчанию «Основная».
  A3 — развёрнутые названия блоков без иконок:
       «Спецификация оборудования и материалов» и
       «Ведомость объемов работ»; краткие «СО»/«ВОР» — в шапках групп.
  A4 — легенда статусов A/B/C/другие — общий компонент проекта
       (_pie_with_legend из deadlines.py), цвета SHEET_STATUS_COLORS.
  A5 — бублики в st.columns рядом: «Статусы» и «Загружено / План».
  B  — send_date/workflow_id берутся из той же записи витрины, что и
       статус («последний по процессу»); None → пусто, без «None».
  C  — парсер перечня СВОК (лист «СВОК», шапка по «Шифр РД» в колонке C,
       колонка T «Текущий статус ВОР»), план к загрузке, строки
       «Не загружен», фильтр по ним.
"""

from datetime import date, datetime

import io
import pandas as pd
import plotly.express as px
import streamlit as st
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils import range_boundaries

from vitro._cache import versioned_cache
from vitro.sheets import looks_like_rd_code, normalize_kit_code
from vitro.sqlite_db import (
    get_conn, load_sovr_table, load_sovr_template,
    replace_sovr_template, sovr_template_meta,
)

# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
KIND_SO = "СО"
KIND_VR = "ВР"

KIND_TITLES = {
    KIND_SO: "Спецификация оборудования и материалов",
    KIND_VR: "Ведомость объемов работ",
}
KIND_SHORT = {KIND_SO: "СО", KIND_VR: "ВОР"}

SHEET_STATUS_COLORS = {
    "Статус A — утверждён": "#2ca02c",
    "Статус B — к сдаче":   "#ffdd57",
    "Статус C — в работе":  "#ff7f0e",
    "Другие статусы":       "#9467bd",
}

SHEET_STATUS_ORDER = [
    "Статус A — утверждён",
    "Статус B — к сдаче",
    "Статус C — в работе",
    "Другие статусы",
]

SHEET_STATUS_COLORS_LOADED = {
    "Загружено": "#2ca02c",
}

PLAN_COLORS = {
    "Загружено": "#2ca02c",
    "План к загрузке": "#9467bd",
}

STATUS_OPTIONS = ["Не загружен", "A", "B", "C", "Другие"]
STATUS_NOT_LOADED = "Не загружен"

# ---------------------------------------------------------------------------
#  Кэшированные данные
# ---------------------------------------------------------------------------
@versioned_cache(3600)
def _load_sovr_data() -> pd.DataFrame:
    """Все строки витрины СО/ВР (+ дисциплина/раздел из documents)."""
    return load_sovr_table()


@versioned_cache(600)
def _load_sovr_template() -> pd.DataFrame:
    """Перечень СВОК: одна строка = комплект (kit_code, признак ВОР)."""
    return load_sovr_template()


@versioned_cache(600)
def _load_sovr_template_meta() -> dict:
    """Количество комплектов перечня и время последней загрузки."""
    return sovr_template_meta()


@versioned_cache(600)
def _load_sheets_sovr_template_count() -> int:
    """Количество комплектов в перечне (совместимость с 22.22)."""
    return int(_load_sovr_template_meta().get("count", 0))


# ---------------------------------------------------------------------------
#  Утилиты форматирования
# ---------------------------------------------------------------------------
def _fmt_date(v) -> str:
    """Дата → ДД.ММ.ГГГГ. Пусто → «—» (как на других вкладках).

    Патч 22.24 (B3): ISO-строка, datetime, date и null поддерживаются;
    непарсируемое значение → пустая строка (без «None»).
    """
    if v is None:
        return "—"
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    s = str(v).strip()
    if not s or s.lower() in ("none", "nan", "nat", "—"):
        return "—"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return ""


def _fmt_workflow(v) -> str:
    """№ процесса → строка. null/None/NaN → «» (никогда «None»)."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() in ("none", "nan", "nat", "<na>"):
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


def _fmt_num(n) -> str:
    """Число с пробелами-разделителями тысяч."""
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _classify_status(status: str) -> str:
    """Классифицирует статус листа: A / B / C / Другие."""
    s = (status or "").strip().upper()
    if s == "A":
        return "Статус A — утверждён"
    if s == "B":
        return "Статус B — к сдаче"
    if s == "C":
        return "Статус C — в работе"
    return "Другие статусы"


def _is_not_loaded(status) -> bool:
    return str(status or "").strip() == STATUS_NOT_LOADED


# ---------------------------------------------------------------------------
#  Фильтры
# ---------------------------------------------------------------------------
def _render_filters() -> dict:
    """Фильтры вкладки: стандартные (дисциплина/раздел/комплект/марка) +
    статус листа + период по дате выдачи."""
    from vitro.ui._filters import render_standard_filters

    def _extra_row():
        """Ряд 3: статус листа + период."""
        status = st.multiselect(
            "Статус листа",
            options=STATUS_OPTIONS,
            placeholder="Все",
            key="sovr_status",
        )
        st.caption(
            "Период по дате выдачи (send_date). "
            "Пустые даты исключаются при заданном периоде."
        )
        c1, c2 = st.columns(2)
        with c1:
            date_from = st.date_input(
                "Период: с",
                value=None,
                min_value=date(2020, 1, 1),
                max_value=date(2100, 12, 31),
                key="sovr_date_from",
            )
        with c2:
            date_to = st.date_input(
                "по",
                value=None,
                min_value=date(2020, 1, 1),
                max_value=date(2100, 12, 31),
                key="sovr_date_to",
            )
        return {
            "status": tuple(status),
            "date_from": date_from,
            "date_to": date_to,
        }

    std = render_standard_filters(
        key_prefix="sovr_",
        show_remark_status=False,
        show_sheet_status=False,
        extra_row=_extra_row,
    )

    return {
        "disc": std["disc"],
        "section": std["section"],
        "kits": std["kits"],
        "block_codes": std["block_codes"],
        "marka": std["marka"],
        "status": std["extra"].get("status", ()),
        "date_from": std["extra"].get("date_from"),
        "date_to": std["extra"].get("date_to"),
    }


def _extract_4th_block(code) -> str:
    if not code:
        return ""
    parts = str(code).split("-")
    return parts[3].strip().upper() if len(parts) >= 4 else ""


def _filter_by_marka(df: pd.DataFrame, marka: str) -> pd.DataFrame:
    """Строгий фильтр по марке (3-й блок шифра) — общий хелпер проекта."""
    if not marka:
        return df
    from vitro.ui._filters import _filter_by_marka as _fbm
    return _fbm(df, marka)


def _apply_filters(df: pd.DataFrame, flt: dict,
                   apply_period: bool = True) -> pd.DataFrame:
    """Применяет фильтры к DataFrame витрины.

    A1: дисциплина/раздел/комплект/расположение/марка/статус режут выборку
    одинаково и для бубликов, и для таблицы; период (send_date) —
    единственный фильтр, от которого бублики статусов не зависят
    (вызываются с apply_period=False).

    «Не загружен» — не статус витрины: реальные строки по нему пустые,
    синтетические строки добавляет _build_sovr_table по перечню СВОК.
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df.copy()

    disc = tuple(flt.get("disc") or ())
    if disc and "discipline" in out.columns:
        out = out[out["discipline"].isin(disc)]

    section = tuple(flt.get("section") or ())
    if section and "section" in out.columns:
        out = out[out["section"].isin(section)]

    kits = tuple(flt.get("kits") or ())
    if kits:
        out = out[out["complex"].isin(kits)]

    block_codes = tuple(flt.get("block_codes") or ())
    if block_codes:
        out = out[out["complex"].apply(_extract_4th_block).isin(block_codes)]

    out = _filter_by_marka(out, flt.get("marka") or "")

    statuses = tuple(flt.get("status") or ())
    if statuses:
        real_statuses = [s for s in statuses if s != STATUS_NOT_LOADED]
        if not real_statuses:
            # только «Не загружен» — реальных строк нет
            return out.iloc[0:0]
        out = out[out["status"].isin(real_statuses)]

    if apply_period:
        date_from = flt.get("date_from")
        date_to = flt.get("date_to")
        if date_from or date_to:
            mask = (out["send_date"].notna()
                    & (out["send_date"].astype(str).str.strip() != ""))
            out = out[mask]
            if not out.empty:
                iso = out["send_date"].astype(str).str.slice(0, 10)
                if date_from:
                    out = out.loc[iso >= date_from.isoformat()]
                if date_to:
                    out = out.loc[iso <= date_to.isoformat()]

    return out


def _filter_template(template_df: pd.DataFrame, flt: dict) -> pd.DataFrame:
    """Перечень СВОК под те же фильтры, что и витрина (без статуса/периода).

    Синтетические строки «Не загружен» не имеют send_date/статуса,
    поэтому при заданном периоде они не показываются (правило пустых дат).
    """
    if template_df is None or template_df.empty:
        return template_df if template_df is not None else pd.DataFrame()
    if flt.get("date_from") or flt.get("date_to"):
        return template_df.iloc[0:0]

    out = template_df.copy()

    disc = tuple(flt.get("disc") or ())
    if disc and "discipline" in out.columns:
        out = out[out["discipline"].isin(disc)]
    section = tuple(flt.get("section") or ())
    if section and "section" in out.columns:
        out = out[out["section"].isin(section)]
    kits = tuple(flt.get("kits") or ())
    if kits:
        out = out[out["kit_code"].isin(kits)]
    block_codes = tuple(flt.get("block_codes") or ())
    if block_codes:
        out = out[out["kit_code"].apply(_extract_4th_block).isin(block_codes)]

    marka = flt.get("marka") or ""
    if marka and not out.empty:
        tmp = out.rename(columns={"kit_code": "complex"})
        keep = _filter_by_marka(tmp, marka).index
        out = out.loc[out.index.intersection(keep)]
    return out


# ---------------------------------------------------------------------------
#  Бублики
# ---------------------------------------------------------------------------
def _donut(fig_data: pd.DataFrame, names_col: str, values_col: str,
           key: str, center_total: str,
           color_map: dict | None = None) -> None:
    """Бублик в стиле Сводки: без легенды, проценты внутри."""
    if fig_data is None or fig_data.empty:
        st.caption("Нет данных для диаграммы.")
        return
    if names_col not in fig_data.columns or values_col not in fig_data.columns:
        st.caption("Нет данных для диаграммы.")
        return
    fig_data = fig_data.copy()
    fig_data[names_col] = fig_data[names_col].astype(str).replace("nan", "")
    fig_data = fig_data[fig_data[names_col] != ""]
    if fig_data.empty or int(fig_data[values_col].sum()) == 0:
        st.caption("Нет данных для диаграммы.")
        return
    if color_map:
        present = set(fig_data[names_col].unique())
        color_map = {k: v for k, v in color_map.items() if k in present}
    else:
        color_map = {}
    fig = px.pie(
        fig_data,
        names=names_col,
        values=values_col,
        hole=0.45,
        color=names_col,
        color_discrete_map=color_map,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent",
        textfont_size=11,
    )
    fig.update_layout(
        showlegend=False,
        margin=dict(l=5, r=5, t=5, b=5),
        height=200,
        annotations=[dict(
            text=center_total,
            showarrow=False,
            font=dict(size=16, color="#1F4E78"),
            x=0.5, y=0.5,
        )],
    )
    st.plotly_chart(fig, width='stretch', key=key)


def _status_donut_data(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Счётчики статусов A/B/C/Другие для листов одного вида (СО/ВР).

    Все четыре категории остаются в данных (даже с нулём) — легенда
    показывает полный набор статусов.
    """
    if df is None or df.empty or "sheet_number" not in df.columns:
        return pd.DataFrame(columns=["Группа", "Количество"])
    sub = df[df["sheet_number"] == kind]
    counts = {label: 0 for label in SHEET_STATUS_ORDER}
    for s in sub.get("status", pd.Series(dtype=object)):
        counts[_classify_status(s)] += 1
    return pd.DataFrame(
        [{"Группа": label, "Количество": counts[label]}
         for label in SHEET_STATUS_ORDER])


def _plan_donut_counts(template_df: pd.DataFrame,
                       loaded_df: pd.DataFrame, kind: str) -> dict:
    """Числа для бублика «Загружено / План» (C2).

    План СО — все комплекты перечня; план ВОР — комплекты перечня без
    «Не выдается» в колонке T (N «Наличие ВОР» игнорируется).
    «Загружено» — комплекты с записью листа этого вида в витрине.
    Бублик рисуется в границах плана: loaded_in_plan + remaining = plan;
    загруженные сверх перечня показываются в подписи (extra).

    Возвращает {"has_template", "plan", "loaded_total", "loaded_in_plan",
    "remaining", "extra"}.
    """
    result = {
        "has_template": False, "plan": 0, "loaded_total": 0,
        "loaded_in_plan": 0, "remaining": 0, "extra": 0,
    }

    loaded_set: set = set()
    if loaded_df is not None and not loaded_df.empty \
            and "sheet_number" in loaded_df.columns:
        loaded_set = set(
            loaded_df.loc[loaded_df["sheet_number"] == kind, "complex"]
            .dropna().astype(str))

    result["loaded_total"] = len(loaded_set)

    if template_df is None or template_df.empty:
        return result

    result["has_template"] = True
    plan_df = template_df
    if kind == KIND_VR:
        if "vor_not_issued" in template_df.columns:
            not_issued = pd.to_numeric(
                template_df["vor_not_issued"], errors="coerce").fillna(0)
        else:
            not_issued = pd.Series(0, index=template_df.index)
        plan_df = template_df[not_issued.astype(int) == 0]

    plan_set = set(plan_df["kit_code"].dropna().astype(str))
    all_template = set(template_df["kit_code"].dropna().astype(str))
    result["plan"] = len(plan_set)
    result["loaded_in_plan"] = len(loaded_set & plan_set)
    result["remaining"] = len(plan_set - loaded_set)
    # «сверх перечня» — загружено то, чего нет в перечне вовсе;
    # комплекты с «Не выдается» из плана ВОР исключены правилом, а не
    # «сверх перечня».
    result["extra"] = len(loaded_set - all_template)
    return result


def _render_sovr_donuts(df_status: pd.DataFrame,
                        template_df: pd.DataFrame | None = None,
                        loaded_df: pd.DataFrame | None = None) -> None:
    """Бублики СО и ВОРО: «Статусы» и «Загружено / План» рядом (A5).

    df_status — витрина под всеми фильтрами, КРОМЕ периода (A1).
    loaded_df — вся витрина (для плана бублика, C2: план от фильтров
    не зависит).
    """
    if loaded_df is None:
        loaded_df = df_status

    for kind in (KIND_SO, KIND_VR):
        st.markdown(f"##### {KIND_TITLES[kind]}")
        left, right = st.columns(2)

        # --- Статусы (A4: общий компонент легенды проекта) ---
        with left:
            st.markdown("###### Статусы")
            status_data = _status_donut_data(df_status, kind)
            total_status = int(status_data["Количество"].sum()) \
                if not status_data.empty else 0
            if status_data.empty or total_status == 0:
                st.caption("Нет данных для диаграммы.")
            else:
                from vitro.ui.deadlines import _pie_with_legend
                _pie_with_legend(
                    status_data, "Группа", "Количество",
                    colors=SHEET_STATUS_COLORS,
                    key=f"sovr_pie_{kind.lower()}_status",
                    order=SHEET_STATUS_ORDER,
                )

        # --- Загружено / План (C2) ---
        with right:
            st.markdown("###### Загружено / План")
            counts = _plan_donut_counts(template_df, loaded_df, kind)

            if not counts["has_template"]:
                st.caption(
                    "Перечень не загружен — план не рассчитывается. "
                    "Загрузите перечень на подвкладке «Сервисная»."
                )
                loaded_data = pd.DataFrame([{
                    "Группа": "Загружено",
                    "Количество": counts["loaded_total"],
                }])
                _donut(loaded_data, "Группа", "Количество",
                       f"sovr_donut_{kind.lower()}_plan",
                       center_total=_fmt_num(counts["loaded_total"]),
                       color_map=SHEET_STATUS_COLORS_LOADED)
                continue

            plan_data = pd.DataFrame([
                {"Группа": "Загружено",
                 "Количество": counts["loaded_in_plan"]},
                {"Группа": "План к загрузке",
                 "Количество": counts["remaining"]},
            ])
            plan_data = plan_data[plan_data["Количество"] > 0]
            caption = (f"План: **{_fmt_num(counts['plan'])}** · "
                       f"Загружено: **{_fmt_num(counts['loaded_total'])}**")
            if counts["extra"]:
                caption += f" · сверх перечня: **{counts['extra']}**"
            st.caption(caption)
            _donut(plan_data, "Группа", "Количество",
                   f"sovr_donut_{kind.lower()}_plan",
                   center_total=_fmt_num(counts["plan"]),
                   color_map=PLAN_COLORS)


# ---------------------------------------------------------------------------
#  Сводная таблица
# ---------------------------------------------------------------------------
_TABLE_COLUMNS = [
    "complex", "sheet_number", "name", "revision", "status",
    "send_date", "status_date", "workflow_id", "doc_id",
]


def _build_sovr_table(df: pd.DataFrame, flt: dict,
                      template_df: pd.DataFrame | None = None,
                      base_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Сборка сводной таблицы: строка = лист + строки «Не загружен» (C3).

    df        — реальные строки витрины (уже отфильтрованы статусом/периодом);
    base_df   — витрина без статуса/периода (для определения «загружен ли
                комплект»); по умолчанию = df;
    template  — перечень СВОК; комплекты из него без записи в витрине
                дают строку со статусом «Не загружен» (для ВОР — кроме
                признака «Не выдается», C2).

    Синтетические строки добавляются, если фильтр статуса не задан или
    среди выбранных есть «Не загружен» (C4); при заданном периоде они
    исключаются (пустая дата выдачи).
    """
    if base_df is None:
        base_df = df

    rows: list[dict] = []

    if df is not None and not df.empty:
        groups = df.groupby(["complex", "sheet_number"], dropna=False)
        for (complex_code, sheet_no), group in groups:
            row = group.iloc[0]
            rows.append({
                "complex": complex_code,
                "sheet_number": sheet_no,
                "status": row.get("status"),
                "send_date": row.get("send_date"),
                "status_date": row.get("status_date"),
                "workflow_id": row.get("workflow_id"),
                "revision": row.get("revision"),
                "name": row.get("name"),
                "doc_id": row.get("doc_id"),
                "discipline": row.get("discipline"),
                "section": row.get("section"),
            })

    statuses = tuple(flt.get("status") or ())
    want_not_loaded = (not statuses) or (STATUS_NOT_LOADED in statuses)
    template_filtered = _filter_template(template_df, flt) \
        if template_df is not None else pd.DataFrame()

    if want_not_loaded and not template_filtered.empty:
        loaded_by_kind = {}
        for kind in (KIND_SO, KIND_VR):
            if base_df is not None and not base_df.empty:
                loaded_by_kind[kind] = set(
                    base_df.loc[base_df["sheet_number"] == kind, "complex"]
                    .dropna().astype(str))
            else:
                loaded_by_kind[kind] = set()

        for kind in (KIND_SO, KIND_VR):
            if kind == KIND_VR:
                if "vor_not_issued" in template_filtered.columns:
                    not_issued = pd.to_numeric(
                        template_filtered["vor_not_issued"],
                        errors="coerce").fillna(0)
                else:
                    not_issued = pd.Series(0, index=template_filtered.index)
                tdf = template_filtered[not_issued.astype(int) == 0]
            else:
                tdf = template_filtered

            for _, t in tdf.iterrows():
                code = str(t.get("kit_code") or "")
                if not code or code in loaded_by_kind[kind]:
                    continue
                rows.append({
                    "complex": code,
                    "sheet_number": kind,
                    "status": STATUS_NOT_LOADED,
                    "send_date": None,
                    "status_date": None,
                    "workflow_id": None,
                    "revision": None,
                    "name": t.get("title") or "",
                    "doc_id": None,
                    "discipline": t.get("discipline"),
                    "section": t.get("section"),
                })

    if not rows:
        return pd.DataFrame(columns=_TABLE_COLUMNS + ["discipline", "section"])

    result = pd.DataFrame(rows)
    for col in _TABLE_COLUMNS + ["discipline", "section"]:
        if col not in result.columns:
            result[col] = None

    order = {KIND_SO: 0, KIND_VR: 1}
    result["_ord"] = result["sheet_number"].map(order).fillna(9)
    result = result.sort_values(
        ["complex", "_ord", "sheet_number"]).drop(columns="_ord")
    return result.reset_index(drop=True)


def _render_sovr_table(df: pd.DataFrame, flt: dict,
                       template_df: pd.DataFrame | None = None,
                       base_df: pd.DataFrame | None = None) -> None:
    """Отображение сводной таблицы: два блока с развёрнутыми названиями."""
    table = _build_sovr_table(df, flt, template_df, base_df)

    if table.empty:
        st.info("Нет данных по выбранным фильтрам.")
        return

    for kind in (KIND_SO, KIND_VR):
        sub = table[table["sheet_number"] == kind]
        if sub.empty:
            continue
        st.markdown(f"##### {KIND_TITLES[kind]}")
        display = sub[["complex", "sheet_number", "name", "revision",
                       "status", "send_date", "status_date",
                       "workflow_id"]].copy()
        display.columns = [
            "Комплект", "Номер листа", "Наименование", "Ревизия",
            "Статус", "Дата выдачи", "Дата получения статуса", "№ процесса",
        ]
        display["Дата выдачи"] = display["Дата выдачи"].apply(_fmt_date)
        display["Дата получения статуса"] = display[
            "Дата получения статуса"].apply(_fmt_date)
        display["№ процесса"] = display["№ процесса"].apply(_fmt_workflow)

        st.dataframe(
            display,
            width='stretch',
            hide_index=True,
            height=min(max(len(display) * 36 + 40, 200), 600),
            column_config={
                "Комплект": st.column_config.TextColumn(width="medium"),
                "Номер листа": st.column_config.TextColumn(width="small"),
                "Наименование": st.column_config.TextColumn(width="large"),
                "Ревизия": st.column_config.TextColumn(width="small"),
                "Статус": st.column_config.TextColumn(width="small"),
                "Дата выдачи": st.column_config.TextColumn(width="small"),
                "Дата получения статуса": st.column_config.TextColumn(width="small"),
                "№ процесса": st.column_config.TextColumn(width="small"),
            },
        )

    so_count = int((table["sheet_number"] == KIND_SO).sum())
    vr_count = int((table["sheet_number"] == KIND_VR).sum())
    not_loaded = int((table["status"] == STATUS_NOT_LOADED).sum())
    caption = (f"Листов всего: **{len(table)}** · СО: **{so_count}** · "
               f"ВОР: **{vr_count}**")
    if not_loaded:
        caption += f" · «Не загружен»: **{not_loaded}**"
    st.caption(caption)


# ---------------------------------------------------------------------------
#  Экспорт в Excel
# ---------------------------------------------------------------------------
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP = Alignment(wrap_text=True, vertical="top")

_EXPORT_HEADERS = ["Комплект", "Номер листа", "Наименование", "Ревизия",
                   "Статус", "Дата выдачи", "Дата получения статуса",
                   "№ процесса"]
_EXPORT_KEYS = ["complex", "sheet_number", "name", "revision", "status",
                "send_date", "status_date", "workflow_id"]


def _write_sovr_sheet(ws, kind: str, sub: pd.DataFrame) -> None:
    """Лист одного вида: развёрнутое название + шапка + данные."""
    ncols = len(_EXPORT_HEADERS)

    # Строка 1 — развёрнутое название блока (A3)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    title_cell = ws.cell(row=1, column=1, value=KIND_TITLES[kind])
    title_cell.font = Font(bold=True, size=12, color="1F4E78")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")

    # Строка 2 — шапка колонок
    for j, h in enumerate(_EXPORT_HEADERS, start=1):
        cell = ws.cell(row=2, column=j, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for i, (_, row) in enumerate(sub.iterrows(), start=3):
        for j, (key, header) in enumerate(zip(_EXPORT_KEYS, _EXPORT_HEADERS),
                                          start=1):
            val = row.get(key)
            if key in ("send_date", "status_date"):
                val = _fmt_date(val)
            elif key == "workflow_id":
                val = _fmt_workflow(val)
            cell = ws.cell(row=i, column=j, value=val)
            cell.border = _BORDER
            cell.alignment = _WRAP

    for j in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(j)].width = 20

    ws.freeze_panes = "A3"
    if ws.max_row >= 3:
        ws.auto_filter.ref = f"A2:{get_column_letter(ncols)}{ws.max_row}"


def _export_sovr_excel(df: pd.DataFrame, flt: dict | None = None) -> bytes:
    """Сводная таблица СО/ВОР → Excel (листы «СО» и «ВОР»)."""
    from openpyxl import Workbook

    wb = Workbook()
    for idx, kind in enumerate((KIND_SO, KIND_VR)):
        if df is None or df.empty:
            sub = pd.DataFrame(columns=_EXPORT_KEYS)
        else:
            sub = df[df["sheet_number"] == kind]
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = KIND_SHORT[kind]
        _write_sovr_sheet(ws, kind, sub)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Обогащение пользовательского Excel (СВОК) — патч 22.23, без изменений
# ---------------------------------------------------------------------------
_SOVSo_HEADERS = ["Ревизия СО", "Статус СО", "Дата выдачи СО", "Дата получения статуса СО", "№ процесса СО"]
_SOVr_HEADERS = ["Ревизия ВР", "Статус ВР", "Дата выдачи ВР", "Дата получения статуса ВР", "№ процесса ВР"]

_SOV_HEADER_FILL_SO = PatternFill("solid", fgColor="1F4E78")
_SOV_HEADER_FILL_VR = PatternFill("solid", fgColor="2E75B6")
_SOV_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)


def _enrich_svok_excel(
    source_bytes: bytes,
    sheet_name: str,
    sovr_data: pd.DataFrame,
    is_xlsm: bool,
) -> bytes:
    """
    Обогащает пользовательский Excel (СВОК) колонками СО/ВР.

    - Находит лист СВОК, шапку по "Шифр РД" в колонке C.
    - Добавляет справа блок СО (5 колонок) и блок ВР (5 колонок).
    - Двухэтажная шапка: строка с "СО" / "ВОР" (объединённые ячейки),
      под ней — подколонки.
    - Заполняет данные по complex из sovr_data.
    - Учитывает "Не выдается" в колонке T файла для ВР.
    - Сохраняет VBA для .xlsm.
    """
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(source_bytes), read_only=False, keep_vba=is_xlsm)

    if sheet_name not in wb:
        # Ищем лист с "СВОК" в имени
        found = None
        for name in wb.sheetnames:
            if "свок" in name.lower():
                found = name
                break
        if found:
            sheet_name = found
        else:
            st.error(f"Лист «{sheet_name}» не найден. Доступные листы: {', '.join(wb.sheetnames)}")
            return source_bytes

    ws = wb[sheet_name]

    # Находим колонку "Шифр РД" (ищем в первых 50 строках)
    kit_col_num = None
    kit_header_row = None
    for row in ws.iter_rows(min_row=1, max_row=50, max_col=ws.max_column):
        for cell in row:
            if cell.value and "шифр" in str(cell.value).lower():
                kit_col_num = cell.column
                kit_header_row = cell.row
                break
        if kit_col_num:
            break

    if kit_col_num is None:
        st.error("Не найдена колонка с шифрами (ожидался заголовок 'Шифр РД').")
        return source_bytes

    # Последняя колонка данных
    max_col = ws.max_column

    # Определяем колонку T (для проверки "Не выдается")
    col_t_num = 20  # T = 20

    # Собираем словарь "Не выдается" по шифрам (после insert_rows данные сдвинулись)
    not_issued: set = set()
    for row in ws.iter_rows(min_row=kit_header_row + 2, max_row=ws.max_row, min_col=col_t_num, max_col=col_t_num):
        cell = row[0]
        if cell.value and "не выд" in str(cell.value).lower():
            # Ищем шифр в этой строке (колонка kit_col_num)
            for r in ws.iter_rows(min_row=cell.row, max_row=cell.row, min_col=kit_col_num, max_col=kit_col_num):
                code_cell = r[0]
                if code_cell.value:
                    not_issued.add(str(code_cell.value).strip())
                break

    # Добавляем колонки СО и ВР справа
    col_start_so = max_col + 1
    col_end_so = col_start_so + 4  # 5 колонок СО
    col_start_vr = col_end_so + 1
    col_end_vr = col_start_vr + 4  # 5 колонок ВР

    last_letter = get_column_letter(col_end_vr)

    # Двухэтажная шапка: строка над шапкой — объединённые ячейки "СО" / "ВОР"
    header_merge_row = kit_header_row - 1
    ws.insert_rows(header_merge_row)

    # Сдвигаем существующие merges
    src_merges = [str(_r) for _r in ws.merged_cells.ranges]
    for _m in src_merges:
        ws.unmerge_cells(_m)

    # Вставляем объединённые ячейки для "СО" и "ВОР"
    ws.merge_cells(
        start_row=header_merge_row, start_column=col_start_so,
        end_row=header_merge_row, end_column=col_end_so
    )
    cell_merge_so = ws.cell(row=header_merge_row, column=col_start_so, value="СО")
    cell_merge_so.font = Font(bold=True)
    cell_merge_so.fill = _SOV_HEADER_FILL_SO
    cell_merge_so.border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
    cell_merge_so.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(
        start_row=header_merge_row, start_column=col_start_vr,
        end_row=header_merge_row, end_column=col_end_vr
    )
    cell_merge_vr = ws.cell(row=header_merge_row, column=col_start_vr, value="ВОР")
    cell_merge_vr.font = Font(bold=True)
    cell_merge_vr.fill = _SOV_HEADER_FILL_VR
    cell_merge_vr.border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
    cell_merge_vr.alignment = Alignment(horizontal="center", vertical="center")

    # Возвращаем merges (со сдвигом)
    for _m in src_merges:
        _c1, _r1, _c2, _r2 = range_boundaries(_m)
        if _r1 >= header_merge_row:
            _r1 += 1
            _r2 += 1
        elif _r2 >= header_merge_row:
            _r2 += 1
        ws.merge_cells(start_row=_r1, start_column=_c1,
                       end_row=_r2, end_column=_c2)

    # Шапки подколонок
    for i, name in enumerate(_SOVSo_HEADERS, start=col_start_so):
        c = ws.cell(row=kit_header_row, column=i, value=name)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="D9E1F2")
        c.border = _BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")

    for i, name in enumerate(_SOVr_HEADERS, start=col_start_vr):
        c = ws.cell(row=kit_header_row, column=i, value=name)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="D9E1F2")
        c.border = _BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")

    # Ширина колонок
    for col in range(col_start_so, col_end_so + 1):
        ws.column_dimensions[get_column_letter(col)].width = 18
    for col in range(col_start_vr, col_end_vr + 1):
        ws.column_dimensions[get_column_letter(col)].width = 18

    # Заполняем данные (после insert_rows данные сдвинулись на +1)
    # Создаём индекс sovr_data по complex + sheet_number
    sovr_index: dict[tuple, dict] = {}
    for _, row in sovr_data.iterrows():
        key = (row["complex"], row["sheet_number"])
        sovr_index[key] = {
            "revision": row.get("revision") or "",
            "status": row.get("status") or "",
            "send_date": _fmt_date(row.get("send_date")) if row.get("send_date") else "",
            "status_date": _fmt_date(row.get("status_date")) if row.get("status_date") else "",
            "workflow_id": row.get("workflow_id") or "",
        }

    # Заполняем строки данных (после insert_rows данные с kit_header_row+2)
    for row_idx in range(kit_header_row + 2, ws.max_row + 1):
        # Получаем шифр
        code_cell = ws.cell(row=row_idx, column=kit_col_num)
        code = code_cell.value
        if not code or (isinstance(code, float) and pd.isna(code)):
            continue

        code_str = str(code).strip()

        # Блок СО — по всем строкам
        so_key = (code_str, "СО")
        so_info = sovr_index.get(so_key)
        for i, key_field in enumerate(["revision", "status", "send_date", "status_date", "workflow_id"]):
            val = so_info[key_field] if so_info else ""
            ws.cell(row=row_idx, column=col_start_so + i, value=val)

        # Блок ВР — если "Не выдается" → пусто
        vr_key = (code_str, "ВР")
        if code_str in not_issued:
            # Пустые ячейки
            for i in range(5):
                ws.cell(row=row_idx, column=col_start_vr + i, value="")
        else:
            vr_info = sovr_index.get(vr_key)
            for i, key_field in enumerate(["revision", "status", "send_date", "status_date", "workflow_id"]):
                val = vr_info[key_field] if vr_info else ""
                ws.cell(row=row_idx, column=col_start_vr + i, value=val)

    # Форматирование данных (после insert_rows данные с kit_header_row+2)
    for row_idx in range(kit_header_row + 2, ws.max_row + 1):
        for col in range(col_start_so, col_end_so + 1):
            c = ws.cell(row=row_idx, column=col)
            c.border = _THIN
            c.alignment = Alignment(horizontal="center", vertical="center")
        for col in range(col_start_vr, col_end_vr + 1):
            c = ws.cell(row=row_idx, column=col)
            c.border = _THIN
            c.alignment = Alignment(horizontal="center", vertical="center")

    # Сохраняем
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Парсер перечня СВОК (блок C патча 22.24)
# ---------------------------------------------------------------------------
def _norm_header(value) -> str:
    """Нормализация заголовка: нижний регистр, ё→е, схлопнутые пробелы."""
    s = str(value or "").replace("\xa0", " ").strip().lower().replace("ё", "е")
    return " ".join(s.split())


def _find_svok_sheet(wb) -> str | None:
    """Лист, в имени которого есть «свок» (регистронезависимо)."""
    for name in wb.sheetnames:
        if "свок" in _norm_header(name):
            return name
    return None


def _find_svok_header(ws) -> int | None:
    """Строка шапки: в колонке C есть «Шифр РД» (эвристика 22.23).

    Приоритет — «шифр» + «рд» в колонке C; запасной вариант — любая
    строка с «шифр» в колонке C. Сводный блок сверху (строки 3–8)
    не считается: там нет «Шифр РД» в C.
    """
    limit = min(int(ws.max_row or 1), 80)
    fallback = None
    for r in range(1, limit + 1):
        h = _norm_header(ws.cell(row=r, column=3).value)
        if not h:
            continue
        if "шифр" in h and "рд" in h:
            return r
        if fallback is None and "шифр" in h:
            fallback = r
    return fallback


def _find_col(ws, header_row: int, keywords: tuple) -> int | None:
    """Первая колонка шапки, в заголовке которой есть любой из keywords."""
    max_col = int(ws.max_column or 0)
    for j in range(1, max_col + 1):
        h = _norm_header(ws.cell(row=header_row, column=j).value)
        if h and any(k in h for k in keywords):
            return j
    return None


def _find_vor_status_col(ws, header_row: int) -> int:
    """Колонка «Текущий статус ВОР»: по заголовку, иначе T (20)."""
    max_col = int(ws.max_column or 0)
    for j in range(1, max_col + 1):
        h = _norm_header(ws.cell(row=header_row, column=j).value)
        if not h:
            continue
        if "вор" in h or ("статус" in h and "вр" in h):
            return j
    return 20  # T


def _is_not_issued_text(value) -> bool:
    """«Не выдается» — trim + регистронезависимо (в т.ч. «Не выдаётся»)."""
    s = str(value or "").strip().lower().replace("ё", "е")
    return "не выд" in s


def _parse_svok_workbook(data: bytes, filename: str = "") -> dict:
    """Разбирает Excel-перечень СВОК.

    Образец: лист «СВОК», шапка в строке 13 (в колонке C — «Шифр РД»),
    данные со строки 16 (сводный блок строк 3–8 не трогается — читаем
    только нужные строки). Возвращает:
      {"rows": [ {kit_code, title, discipline, section, marka,
                  vor_status, vor_not_issued} ],
       "warnings": [...], "sheet": имя листа, "header_row": N}

    Ничего не пишет в БД и не меняет файл.
    """
    from openpyxl import load_workbook

    is_xlsm = str(filename).lower().endswith(".xlsm")
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True,
                           keep_vba=is_xlsm)
    except Exception as e:  # noqa: BLE001
        return {"rows": [], "warnings": [f"Не удалось открыть файл: {e}"],
                "sheet": "", "header_row": 0}

    sheet = _find_svok_sheet(wb)
    if not sheet:
        return {"rows": [],
                "warnings": ["Лист «СВОК» не найден в файле."],
                "sheet": "", "header_row": 0}

    ws = wb[sheet]
    header_row = _find_svok_header(ws)
    if not header_row:
        return {"rows": [],
                "warnings": ["Не найдена шапка: в колонке C нет «Шифр РД»."],
                "sheet": sheet, "header_row": 0}

    title_col = _find_col(ws, header_row, ("наименован",))
    disc_col = _find_col(ws, header_row, ("дисциплин", "группа"))
    section_col = _find_col(ws, header_row, ("раздел",))
    marka_col = _find_col(ws, header_row, ("марка",))
    vor_col = _find_vor_status_col(ws, header_row)

    warnings: list[str] = []
    missing = [name for name, col in (
        ("Наименование", title_col), ("Дисциплина", disc_col),
        ("Раздел", section_col), ("Марка", marka_col)) if col is None]
    if missing:
        warnings.append(
            "Не найдены колонки: " + ", ".join(missing) + " — поля пустые.")

    rows: list[dict] = []
    seen: set = set()
    duplicates = 0
    skipped = 0
    not_issued_count = 0

    for r in range(header_row + 1, int(ws.max_row or header_row) + 1):
        raw_code = ws.cell(row=r, column=3).value
        if raw_code is None or str(raw_code).strip() == "":
            continue
        code = normalize_kit_code(str(raw_code))
        if not code or not looks_like_rd_code(code):
            skipped += 1
            continue
        if code in seen:
            duplicates += 1
            continue
        seen.add(code)

        def _cell(col):
            if col is None:
                return ""
            return ws.cell(row=r, column=col).value

        vor_status = _cell(vor_col)
        not_issued = _is_not_issued_text(vor_status)
        if not_issued:
            not_issued_count += 1

        rows.append({
            "kit_code": code,
            "title": str(_cell(title_col) or "").strip(),
            "discipline": str(_cell(disc_col) or "").strip(),
            "section": str(_cell(section_col) or "").strip(),
            "marka": str(_cell(marka_col) or "").strip(),
            "vor_status": str(vor_status or "").strip(),
            "vor_not_issued": 1 if not_issued else 0,
        })

    if duplicates:
        warnings.append(f"Повторяющихся шифров: {duplicates} (взят первый).")
    if skipped:
        warnings.append(f"Строк без РД-шифра пропущено: {skipped}.")
    if not rows:
        warnings.append("В перечне не найдено ни одного шифра АТ-РД-*.")

    return {"rows": rows, "warnings": warnings,
            "sheet": sheet, "header_row": header_row,
            "not_issued": not_issued_count}


# ---------------------------------------------------------------------------
#  Рендер
# ---------------------------------------------------------------------------
def _render_main() -> None:
    """Основная подвкладка: фильтры, бублики, таблица, экспорт."""
    flt = _render_filters()

    df_all = _load_sovr_data()
    template_df = _load_sovr_template()

    # A1: бублики статусов — все фильтры, КРОМЕ периода
    df_no_period = _apply_filters(df_all, flt, apply_period=False)
    # Таблица — все фильтры, включая период
    df_table = _apply_filters(df_all, flt, apply_period=True)
    # Для «Не загружен»: загруженность комплекта без статуса/периода
    df_base = _apply_filters(
        df_all, {**flt, "status": (), "date_from": None, "date_to": None},
        apply_period=False)

    _render_sovr_donuts(df_no_period, template_df, df_all)
    _render_sovr_table(df_table, flt, template_df, df_base)

    table = _build_sovr_table(df_table, flt, template_df, df_base)
    if not table.empty:
        excel_bytes = _export_sovr_excel(table, flt)
        st.download_button(
            "📥 Выгрузить в Excel",
            data=excel_bytes,
            file_name=f"СО_и_ВОР_{datetime.now():%Y%m%d_%H%M}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='content',
            key="sovr_export_excel",
            help="Выгрузка витрины статусов СО/ВОР и строк «Не загружен».",
        )


def _render_template_upload() -> None:
    """Загрузка перечня СВОК (C1): полная синхронизация состава."""
    st.markdown("#### 📋 Перечень СО и ВОР (СВОК)")

    meta = _load_sovr_template_meta()
    if meta.get("count"):
        st.caption(
            f"В перечне комплектов: **{meta['count']}** · обновлён: "
            f"{meta.get('updated_at') or '—'}")
    else:
        st.caption("Перечень ещё не загружен.")

    uploaded = st.file_uploader(
        "Файл перечня (.xlsx / .xlsm)",
        type=["xlsx", "xlsm"],
        key="sovr_svok_file",
        help="Лист «СВОК», шапка с «Шифр РД» в колонке C, "
             "признак «Не выдается» — в колонке T.",
    )

    if uploaded is not None:
        st.caption(f"Выбран файл: **{uploaded.name}**")

    if st.button(
        "Загрузить перечень СО/ВОР",
        type="primary",
        key="sovr_svok_load",
        disabled=uploaded is None,
    ):
        parsed = _parse_svok_workbook(uploaded.getvalue(), uploaded.name)
        for w in parsed["warnings"]:
            st.warning(w)
        if not parsed["rows"]:
            st.error("Перечень не загружен: не найдено ни одного комплекта.")
            return

        result = replace_sovr_template(parsed["rows"], source=uploaded.name)
        # Сброс кэшей витрины/перечня (versioned_cache также ловит
        # изменение data_version, но точечный сброс даёт мгновенный эффект)
        _load_sovr_template.clear()
        _load_sovr_template_meta.clear()
        _load_sovr_data.clear()

        st.success(
            f"Перечень загружен: комплектов **{result['kits']}**, "
            f"из них «Не выдается» (ВОР) — **{result['not_issued']}**. "
            f"Добавлено: {result['added']}, удалено: {result['removed']}.")
        st.caption(
            f"Лист: «{parsed['sheet']}», шапка: строка {parsed['header_row']}.")


def render_service() -> None:
    """Сервисная подвкладка: обновление витрины и загрузка перечня."""
    st.markdown("### ⚙️ Сервисные операции")

    # --- Перечень СВОК (C1) ---
    _render_template_upload()

    st.divider()

    # --- Обновление статусов из Витро (22.23) ---
    st.markdown("#### 🔄 Обновление статусов СО и ВОР из Витро")
    st.caption(
        "Загрузка актуальных статусов листов СО/ВР из SharePoint Витро. "
        "Обновляет витрину documents_sovr; дата выдачи и № процесса "
        "берутся из той же записи, что и статус."
    )

    if st.session_state.get("sovr_refresh_running", False):
        st.warning("⏳ Обновление уже запущено. Подождите завершения.")
        return

    if st.button(
        "🔄 Обновить статусы СО и ВОР из Витро",
        type="primary",
        key="sovr_refresh_btn",
    ):
        st.session_state["sovr_refresh_running"] = True

        from vitro.sharepoint import fetch_atp_documents
        from vitro.sqlite_db import (
            load_sovr_statuses as _load_sovr_statuses,
            upsert_documents,
        )

        progress_bar = st.progress(0, text="Инициализация...")
        status_text = st.empty()

        try:
            progress_bar.progress(20, text="Шаг 1/2: Загрузка документов из Витро...")
            status_text.caption("Подключение к SharePoint Витро...")
            docs = fetch_atp_documents()
            status_text.caption(f"Загружено документов: **{len(docs)}**")

            progress_bar.progress(60, text="Шаг 2/2: Обновление витрины СО/ВР...")
            status_text.caption("Сохранение документов и статусов в локальную базу...")
            n_saved = upsert_documents(docs)
            inserted = _load_sovr_statuses(user="service_subtab")
            progress_bar.progress(100, text="Готово!")

            _load_sovr_data.clear()
            st.success(
                f"✅ Витрина обновлена: документов сохранено **{n_saved}**, "
                f"строк СО/ВР — **{inserted}**.")

            with get_conn() as conn:
                row = conn.execute(
                    "SELECT MAX(updated_at) as max_updated FROM documents_sovr"
                ).fetchone()
                updated_at = row["max_updated"] if row and row["max_updated"] else None
                so_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM documents_sovr "
                    "WHERE sheet_number = 'СО'").fetchone()["cnt"]
                vr_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM documents_sovr "
                    "WHERE sheet_number = 'ВР'").fetchone()["cnt"]

            if updated_at:
                st.caption(f"Последняя запись обновлена: **{updated_at}**")
            st.caption(f"Листов СО: **{so_count}** · ВОР: **{vr_count}**")

        except Exception as e:  # noqa: BLE001
            st.error(f"❌ Ошибка обновления: {e}")
        finally:
            st.session_state["sovr_refresh_running"] = False
            status_text.empty()


def render() -> None:
    """Вкладка «СО и ВОР»: подвкладки «Основная» и «Сервисная» (A2)."""
    tab_main, tab_service = st.tabs(["Основная", "Сервисная"])
    with tab_main:
        _render_main()
    with tab_service:
        render_service()
