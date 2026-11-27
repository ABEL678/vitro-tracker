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

Патч 22.24b:
  «Загружено / План» реагирует на дисциплину, раздел, расположение,
  комплект и марку; НЕ реагирует на период и «Статус листа».
  Расположение плана — из колонки СВОК «Здание, Сооружение, Корпус»
  (после повторной загрузки перечня; для старых строк — fallback по
  4-му блоку шифра). «Сверх перечня» — загруженные вне полного перечня.
"""

from datetime import date, datetime

import io
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import plotly.express as px
import streamlit as st
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange

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


# Соответствие кодов СВОК кодам фильтра «Расположение» (BLOCK_OPTIONS).
# В перечне СВОК колонка «Здание, Сооружение, Корпус» содержит код в
# начале значения: «С (Стилобат)», «1 (Корпус 1)», «ГП,АД (...)",
# «0 (Все здание)». АД (автодороги) в фильтре — код «А».
_LOCATION_CODE_ALIASES = {"АД": "А", "УДС": "А"}


def _location_codes(location) -> set:
    """Коды расположения из колонки СВОК «Здание, Сооружение, Корпус».

    «С (Стилобат)» → {«С»}; «ГП,АД (Генеральный план, УСД)» → {«ГП», «А»};
    пусто/None → пустое множество (вызывающий код использует fallback
    по 4-му блоку шифра).
    """
    s = str(location or "").strip()
    if not s:
        return set()
    lead = s.split("(", 1)[0]
    codes: set = set()
    for token in lead.replace(";", ",").replace("/", ",").split(","):
        code = token.strip().upper()
        if not code:
            continue
        codes.add(_LOCATION_CODE_ALIASES.get(code, code))
    return codes


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
    if block_codes and not out.empty:
        # 22.24b: расположение берём из колонки СВОК «Здание,
        # Сооружение, Корпус» (после повторной загрузки перечня).
        # Для старых строк без location — fallback по 4-му блоку шифра.
        selected = set(block_codes)
        if "location" in out.columns:
            codes = [
                (_location_codes(loc) or {_extract_4th_block(code)})
                for code, loc in zip(out["kit_code"], out["location"])
            ]
        else:
            codes = [{_extract_4th_block(code)} for code in out["kit_code"]]
        mask = [bool(c & selected) for c in codes]
        out = out[mask]

    marka = flt.get("marka") or ""
    if marka and not out.empty:
        tmp = out.rename(columns={"kit_code": "complex"})
        keep = _filter_by_marka(tmp, marka).index
        out = out.loc[out.index.intersection(keep)]
    return out


def _template_location_map(template_df: pd.DataFrame | None) -> dict:
    """{kit_code: set(кодов расположения)} из колонки location перечня."""
    if template_df is None or template_df.empty \
            or "location" not in template_df.columns:
        return {}
    return {
        str(code).strip(): _location_codes(loc)
        for code, loc in zip(template_df["kit_code"], template_df["location"])
    }


def _filter_loaded_by_location(df: pd.DataFrame, block_codes,
                               template_df: pd.DataFrame | None
                               ) -> pd.DataFrame:
    """Фильтр витрины по расположению с учётом колонки location перечня.

    План и «Загружено» должны решаться по одному правилу расположения
    (22.24b). Для комплектов перечня код берётся из СВОК; для записей
    вне перечня («сверх перечня») — fallback по 4-му блоку шифра.
    """
    blocks = tuple(block_codes or ())
    if df is None or df.empty or not blocks:
        return df

    selected = set(blocks)
    loc_map = _template_location_map(template_df)

    def _keep(code) -> bool:
        codes = loc_map.get(str(code).strip())
        if not codes:
            codes = {_extract_4th_block(code)}
        return bool(codes & selected)

    return df[df["complex"].apply(_keep)]


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
                       loaded_df: pd.DataFrame, kind: str,
                       all_template_codes: set | None = None) -> dict:
    """Числа для бублика «Загружено / План» (C2, правило 22.24b).

    template_df — перечень СВОК, УЖЕ отфильтрованный по дисциплине,
    разделу, расположению, комплекту и марке (без статуса листа/периода).
    loaded_df   — витрина под теми же фильтрами (без статуса/периода).

    План СО — комплекты отфильтрованного перечня; план ВОР — они же без
    «Не выдается» в колонке T (N «Наличие ВОР» игнорируется).
    «Загружено» (loaded_in_plan) — комплекты плана с записью листа этого
    вида в отфильтрованной витрине.
    «Сверх перечня» (extra) — загруженные комплекты, которых нет в ПОЛНОМ
    перечне (all_template_codes); в план они не входят.

    Загруженные комплекты с «Не выдается» (ВОР) исключены из плана
    правилом, а не отнесены к «сверх перечня».

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

    if all_template_codes is not None:
        all_template = {str(c) for c in all_template_codes if str(c).strip()}
        result["has_template"] = bool(all_template)
    else:
        all_template = set()
        if template_df is not None and not template_df.empty:
            all_template = set(
                template_df["kit_code"].dropna().astype(str))
        result["has_template"] = bool(all_template)

    if template_df is None or template_df.empty:
        result["extra"] = len(loaded_set - all_template)
        return result

    plan_df = template_df
    if kind == KIND_VR:
        if "vor_not_issued" in template_df.columns:
            not_issued = pd.to_numeric(
                template_df["vor_not_issued"], errors="coerce").fillna(0)
        else:
            not_issued = pd.Series(0, index=template_df.index)
        plan_df = template_df[not_issued.astype(int) == 0]

    plan_set = set(plan_df["kit_code"].dropna().astype(str))
    if all_template_codes is None:
        all_template = set(template_df["kit_code"].dropna().astype(str))
    result["plan"] = len(plan_set)
    result["loaded_in_plan"] = len(loaded_set & plan_set)
    result["remaining"] = len(plan_set - loaded_set)
    # «сверх перечня» — загруженные вне ПОЛНОГО перечня; membership
    # проверяем по полному набору, а не по отфильтрованному.
    result["extra"] = len(loaded_set - all_template)
    return result


def _render_sovr_donuts(df_status: pd.DataFrame,
                        template_df: pd.DataFrame | None = None,
                        loaded_df: pd.DataFrame | None = None,
                        all_template_codes: set | None = None) -> None:
    """Бублики СО и ВОР: «Статусы» и «Загружено / План» рядом (A5).

    df_status — витрина под фильтрами дисциплины/раздела/расположения/
    комплекта/марки и статуса листа, но БЕЗ периода (A1).
    template_df / loaded_df — перечень и витрина под теми же фильтрами,
    кроме статуса листа и периода (22.24b: план зависит от фильтров).
    all_template_codes — полный набор шифров перечня (для «сверх перечня»).
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
            counts = _plan_donut_counts(
                template_df, loaded_df, kind, all_template_codes)

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
                       f"Загружено: **{_fmt_num(counts['loaded_in_plan'])}**")
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
                       base_df: pd.DataFrame | None = None
                       ) -> pd.DataFrame | None:
    """Отображение сводной таблицы: два блока с развёрнутыми названиями.

    Возвращает построенную таблицу (включая строки «Не загружен») —
    её же использует выгрузка Excel под таблицами.
    """
    table = _build_sovr_table(df, flt, template_df, base_df)

    if table.empty:
        st.info("Нет данных по выбранным фильтрам.")
        return None

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
    return table


# ---------------------------------------------------------------------------
#  Экспорт в Excel (22.24b: одна таблица по комплектам)
# ---------------------------------------------------------------------------
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_GROUP_HEADERS = ["Ревизия", "Статус", "Дата выдачи",
                  "Дата получения статуса", "Номер процесса"]
_GROUP_KEYS = ["revision", "status", "send_date", "status_date", "workflow"]
_EXPORT_SHEET_TITLE = "СО и ВОР"


def _cell_text(value):
    """Текст ячейки экспорта: null/NaN/«None» → пустая ячейка (None)."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan", "nat", "<na>"):
        return None
    return s


def _cell_date(value):
    """Дата ячейки экспорта: ДД.ММ.ГГГГ; пусто/непарсируемое → None."""
    formatted = _fmt_date(value)
    return formatted if formatted and formatted != "—" else None


def _cell_workflow(value):
    """№ процесса ячейки экспорта: пусто → None (без «None»)."""
    return _cell_text(_fmt_workflow(value))


def _build_sovr_export_rows(df: pd.DataFrame) -> list[dict]:
    """Одна строка на комплект: блоки СО и ВОР рядом.

    Принимает и «сырую» витрину (по строке на лист), и таблицу
    ``_build_sovr_table`` (включая синтетические строки «Не загружен»).
    Если листа вида нет (или строка «Не загружен») — блок пустой.
    """
    if df is None or df.empty or "sheet_number" not in df.columns:
        return []

    by_kind: dict[str, dict[str, object]] = {KIND_SO: {}, KIND_VR: {}}
    for kind in (KIND_SO, KIND_VR):
        sub = df[df["sheet_number"] == kind]
        if sub.empty:
            continue
        # В таблице уже одна строка на (комплект, вид); на «сырой»
        # витрине берём первую запись, чтобы не дублировать комплект.
        sub = sub.drop_duplicates(subset=["complex"], keep="first")
        for _, row in sub.iterrows():
            code = _cell_text(row.get("complex"))
            if code:
                by_kind[kind][code] = row

    codes = sorted(set(by_kind[KIND_SO]) | set(by_kind[KIND_VR]))
    rows: list[dict] = []
    for code in codes:
        item: dict = {"complex": code}
        for kind, prefix in ((KIND_SO, "so"), (KIND_VR, "vr")):
            row = by_kind[kind].get(code)
            if row is None or _is_not_loaded(row.get("status")):
                values = (None, None, None, None, None)
            else:
                values = (
                    _cell_text(row.get("revision")),
                    _cell_text(row.get("status")),
                    _cell_date(row.get("send_date")),
                    _cell_date(row.get("status_date")),
                    _cell_workflow(row.get("workflow_id")),
                )
            for key, value in zip(_GROUP_KEYS, values):
                item[f"{prefix}_{key}"] = value
        rows.append(item)
    return rows


def _write_sovr_export_sheet(ws, rows: list[dict], sync_ts=None) -> None:
    """Лист по макету samples/СО_ВР_Таблица.xlsx (v1_предпочтительно).

    Строка 1 — полоса актуальности базы (как в выгрузках «Экспорт»),
    строка 2 — группы «СО»/«ВОР», строка 3 — подколонки, данные с 4-й.
    Исходная таблица сохраняет свою колонку «Комплект»; блоки статусов
    добавляются справа, порядок и названия — как на экране/в макете.
    """
    # Переиспользуем стили и текст полосы даты из «Экспорта»,
    # не создавая второй набор оформления.
    from vitro.ui.export import (
        _KIT_DATE_FILL, _KIT_HEADER_FILL, _KIT_TABLE_BORDER,
        _kit_sync_caption,
    )

    ncols = 1 + 2 * len(_GROUP_HEADERS)
    last_letter = get_column_letter(ncols)

    # --- Строка 1: дата и время последнего обновления базы ---
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = _KIT_DATE_FILL
        cell.border = _KIT_TABLE_BORDER
    strip = ws.cell(row=1, column=1, value=_kit_sync_caption(sync_ts))
    strip.font = Font(bold=True)
    strip.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws.row_dimensions[1].height = 22

    # --- Строка 2: группы «СО» и «ВОР»; «Комплект» — на две строки ---
    ws.merge_cells(start_row=2, start_column=1, end_row=3, end_column=1)
    kit_header = ws.cell(row=2, column=1, value="Комплект")
    kit_header.fill = _KIT_HEADER_FILL
    kit_header.font = Font(bold=True)
    kit_header.border = _KIT_TABLE_BORDER
    kit_header.alignment = Alignment(horizontal="center",
                                     vertical="center")
    ws.cell(row=3, column=1).border = _KIT_TABLE_BORDER

    for group_idx, title in enumerate(
            (KIND_SHORT[KIND_SO], KIND_SHORT[KIND_VR])):
        start_col = 2 + group_idx * len(_GROUP_HEADERS)
        end_col = start_col + len(_GROUP_HEADERS) - 1
        ws.merge_cells(start_row=2, start_column=start_col,
                       end_row=2, end_column=end_col)
        group_cell = ws.cell(row=2, column=start_col, value=title)
        group_cell.fill = _KIT_HEADER_FILL
        group_cell.font = Font(bold=True)
        group_cell.border = _KIT_TABLE_BORDER
        group_cell.alignment = Alignment(horizontal="center",
                                         vertical="center")
        for col in range(start_col, end_col + 1):
            ws.cell(row=2, column=col).border = _KIT_TABLE_BORDER
            sub_cell = ws.cell(row=3, column=col,
                               value=_GROUP_HEADERS[col - start_col])
            sub_cell.fill = _KIT_HEADER_FILL
            sub_cell.font = Font(bold=True)
            sub_cell.border = _KIT_TABLE_BORDER
            sub_cell.alignment = Alignment(wrap_text=True,
                                           vertical="center",
                                           horizontal="center")

    # --- Данные с 4-й строки ---
    for row_idx, item in enumerate(rows, start=4):
        kit_cell = ws.cell(row=row_idx, column=1, value=item.get("complex"))
        kit_cell.border = _KIT_TABLE_BORDER
        kit_cell.alignment = Alignment(vertical="center")
        for group_idx, prefix in enumerate(("so", "vr")):
            start_col = 2 + group_idx * len(_GROUP_HEADERS)
            for offset, key in enumerate(_GROUP_KEYS):
                cell = ws.cell(
                    row=row_idx, column=start_col + offset,
                    value=item.get(f"{prefix}_{key}"),
                )
                cell.border = _KIT_TABLE_BORDER
                cell.alignment = Alignment(horizontal="center",
                                           vertical="center")

    widths = [28, 10, 10, 13, 18, 14,
              10, 10, 13, 18, 14]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.freeze_panes = "B4"
    ws.auto_filter.ref = f"A3:{last_letter}{max(ws.max_row, 3)}"


def _export_sovr_excel(df: pd.DataFrame, flt: dict | None = None,
                       sync_ts=None) -> bytes:
    """Витрина СО/ВОР → Excel по макету v1 (одна строка на комплект).

    ``df`` — отфильтрованные данные экрана (таблица ``_build_sovr_table``)
    либо «сырая» витрина; ``flt`` сохранён для совместимости вызова.
    ``sync_ts`` — время последнего обновления базы
    (``deadlines._load_last_sync_date``), как в выгрузках «Экспорт».
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = _EXPORT_SHEET_TITLE
    _write_sovr_export_sheet(ws, _build_sovr_export_rows(df), sync_ts)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _safe_filename_part(value) -> str:
    """Убирает из части имени файла символы, запрещённые в Windows."""
    return "".join(
        ch if ch not in '\\/:*?"<>|' else "_" for ch in str(value)
    ).strip()


def _sovr_export_filename(flt: dict | None = None) -> str:
    """«СО_ВОР_выгрузка_ГГГГ-ММ-ДД.xlsx» + суффикс дисциплины."""
    name = f"СО_ВОР_выгрузка_{date.today():%Y-%m-%d}"
    for disc in tuple((flt or {}).get("disc") or ()):
        part = _safe_filename_part(disc)
        if part:
            name += f"_{part}"
    return f"{name}.xlsx"


# ---------------------------------------------------------------------------
#  Обогащение пользовательского Excel (СВОК) — патч 22.23, без изменений
# ---------------------------------------------------------------------------
_SOVSo_HEADERS = ["Ревизия СО", "Статус СО", "Дата выдачи СО", "Дата получения статуса СО", "№ процесса СО"]
_SOVr_HEADERS = ["Ревизия ВР", "Статус ВР", "Дата выдачи ВР", "Дата получения статуса ВР", "№ процесса ВР"]

_SOV_HEADER_FILL_SO = PatternFill("solid", fgColor="1F4E78")
_SOV_HEADER_FILL_VR = PatternFill("solid", fgColor="2E75B6")
_SOV_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)


def _cell_has_value(value) -> bool:
    """Непустое значение ячейки: None, пустая строка и NaN — пусто."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, float):
        return not pd.isna(value)
    return True


def _sheet_content_bounds(ws) -> tuple[int, int]:
    """Реальная последняя строка и колонка с данными.

    Смотрим только уже существующие ячейки с непустым значением:
    форматирование/объединения, растянутые до конца листа, больше не
    раздувают диапазон (``ws.max_row`` на таких файлах огромен).
    """
    last_row = 0
    last_col = 0
    for (row, col), cell in getattr(ws, "_cells", {}).items():
        if not _cell_has_value(getattr(cell, "value", None)):
            continue
        if row > last_row:
            last_row = row
        if col > last_col:
            last_col = col
    return max(last_row, 1), max(last_col, 1)


def _last_data_row(ws, col: int, min_row: int) -> int:
    """Последняя строка с непустым значением в колонке ``col``.

    Для СВОК это колонка с шифрами РД: ниже таблицы могут быть только
    пустые форматированные строки, они в диапазон данных не входят.
    """
    last = min_row - 1
    for (row, c), cell in getattr(ws, "_cells", {}).items():
        if c != col or row < min_row:
            continue
        if _cell_has_value(getattr(cell, "value", None)) and row > last:
            last = row
    return last


def _insert_row_bounded(ws, idx: int, last_row: int) -> None:
    """Вставить пустую строку перед ``idx``, не обходя весь лист.

    ``openpyxl.insert_rows`` внутри вызывает ``iter_rows`` до ``ws.max_row``
    и создаёт ячейку для каждой координаты: на файлах, где форматирование
    раздуло лист до сотен тысяч строк, это часы работы. Здесь сдвигаются
    только уже существующие ячейки в реальном диапазоне данных.
    """
    rows = sorted(
        ((r, c) for (r, c) in getattr(ws, "_cells", {})
         if idx <= r <= last_row),
        reverse=True,
    )
    for row, col in rows:
        ws._move_cell(row, col, 1, 0)
    try:
        ws._current_row = max(int(ws._current_row or 0), last_row + 1)
    except Exception:  # noqa: BLE001
        pass


def _shift_merges_after_insert(ws, insert_row: int) -> None:
    """Сдвинуть объединения вниз на вставленную строку без unmerge/merge.

    ``unmerge_cells``/``merge_cells`` пересоздают MergedCell по каждой
    ячейке диапазона; на объединениях, растянутых до конца листа, это
    миллионы объектов. Здесь правится только набор диапазонов.
    """
    src_merges = [CellRange(str(rng)) for rng in ws.merged_cells.ranges]
    ws.merged_cells.ranges.clear()
    for cr in src_merges:
        if cr.min_row >= insert_row:
            cr.shift(row_shift=1)
        elif cr.max_row >= insert_row:
            cr.max_row += 1
        ws.merged_cells.ranges.add(cr)


def _enrich_svok_excel(
    source_bytes: bytes,
    sheet_name: str,
    sovr_data: pd.DataFrame,
    is_xlsm: bool,
    wb=None,
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

    ``wb`` — уже открытый workbook (обычный режим, keep_vba по ``is_xlsm``),
    чтобы не читать файл второй раз за нажатие. Функция его не закрывает:
    это делает вызывающий. Если ``wb`` не передан, файл читается здесь
    (совместимость со старыми вызовами).
    """
    if wb is None:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(source_bytes), read_only=False,
                           keep_vba=is_xlsm)

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

    # Реальный диапазон данных. ws.max_row/max_column могут быть раздуты
    # форматированием или объединениями до конца листа — обходить их
    # нельзя: на реальном файле 6,6 МБ это 10+ минут.
    content_last_row, content_last_col = _sheet_content_bounds(ws)
    print(f"[enrich] диапазон листа «{sheet_name}»: по данным строки до "
          f"{content_last_row}, колонки до {content_last_col} "
          f"(ws.max_row={ws.max_row}, ws.max_column={ws.max_column})",
          flush=True)

    # Находим колонку "Шифр РД" (ищем в первых 50 строках)
    kit_col_num = None
    kit_header_row = None
    for row in ws.iter_rows(min_row=1, max_row=50,
                            max_col=content_last_col):
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

    # Последняя строка таблицы — по непустым шифрам РД (а не по max_row).
    last_data_row = _last_data_row(ws, kit_col_num, kit_header_row + 1)
    print(f"[enrich] строк с шифрами РД: до {last_data_row} "
          f"(шапка в строке {kit_header_row})", flush=True)

    # Последняя колонка данных
    max_col = content_last_col

    # Определяем колонку T (для проверки "Не выдается")
    col_t_num = 20  # T = 20

    # Собираем словарь "Не выдается" по шифрам (после insert_rows данные сдвинулись)
    not_issued: set = set()
    for row in ws.iter_rows(min_row=kit_header_row + 2, max_row=last_data_row,
                            min_col=col_t_num, max_col=col_t_num):
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
    # Вставка только по реальному диапазону (не по раздутому ws.max_row).
    _insert_row_bounded(ws, header_merge_row, content_last_row)

    # Сдвигаем существующие merges (набор диапазонов, без пересоздания
    # MergedCell по каждой ячейке).
    _shift_merges_after_insert(ws, header_merge_row)

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

    # Заполняем строки данных (после insert_rows данные с kit_header_row+2).
    # Конец — по реальной последней строке с шифром, а не по ws.max_row.
    for row_idx in range(kit_header_row + 2, last_data_row + 2):
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
    for row_idx in range(kit_header_row + 2, last_data_row + 2):
        for col in range(col_start_so, col_end_so + 1):
            c = ws.cell(row=row_idx, column=col)
            c.border = _THIN
            c.alignment = Alignment(horizontal="center", vertical="center")
        for col in range(col_start_vr, col_end_vr + 1):
            c = ws.cell(row=row_idx, column=col)
            c.border = _THIN
            c.alignment = Alignment(horizontal="center", vertical="center")

    # Сохраняем
    _t_save = time.perf_counter()
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    print(f"[enrich] сохранение результата: "
          f"{time.perf_counter() - _t_save:.2f} с", flush=True)
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
       {"rows": [ {kit_code, title, discipline, section, marka, location,
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
    # 22.24b: «Здание, Сооружение, Корпус» — источник фильтра
    # «Расположение» для плана.
    location_col = _find_col(
        ws, header_row, ("здание", "сооружение", "корпус"))
    vor_col = _find_vor_status_col(ws, header_row)

    warnings: list[str] = []
    missing = [name for name, col in (
        ("Наименование", title_col), ("Дисциплина", disc_col),
        ("Раздел", section_col), ("Марка", marka_col),
        ("Здание/Корпус", location_col)) if col is None]
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
            "location": str(_cell(location_col) or "").strip(),
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

    # 22.24b: «Загружено / План» зависит от дисциплины/раздела/
    # расположения/комплекта/марки, но НЕ от статуса листа и периода.
    # Один и тот же набор фильтров для плана и загруженного.
    plan_flt = {**flt, "status": (), "date_from": None, "date_to": None}
    # Расположение плана — из колонки СВОК, поэтому в _apply_filters
    # блоки не применяем (иначе отсечём по 4-му блоку шифра).
    df_plan = _apply_filters(
        df_all, {**plan_flt, "block_codes": ()}, apply_period=False)
    df_plan = _filter_loaded_by_location(
        df_plan, flt.get("block_codes") or (), template_df)
    template_plan = _filter_template(template_df, plan_flt)
    all_template_codes = set()
    if template_df is not None and not template_df.empty:
        all_template_codes = set(
            template_df["kit_code"].dropna().astype(str))

    # Для «Не загружен»: загруженность комплекта без статуса/периода
    df_base = _apply_filters(
        df_all, {**flt, "status": (), "date_from": None, "date_to": None},
        apply_period=False)

    _render_sovr_donuts(df_no_period, template_plan, df_plan,
                        all_template_codes)
    table = _render_sovr_table(df_table, flt, template_df, df_base)

    if table is not None and not table.empty:
        from vitro.ui.deadlines import _load_last_sync_date
        st.download_button(
            "📥 Скачать Excel",
            data=_export_sovr_excel(
                table, flt, sync_ts=_load_last_sync_date()),
            file_name=_sovr_export_filename(flt),
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            width='stretch',
            key="sovr_export_excel",
            help="Отфильтрованные данные экрана: одна строка на комплект, "
                 "блоки СО и ВОР справа; строки «Не загружен» — с пустыми "
                 "полями.",
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

    # Фикс 27.11.2026: рядом с загрузкой перечня — кнопка добавления
    # статусов СО и ВОР по тому же файлу (без повторной загрузки).
    col_load, col_enrich = st.columns([1, 1])
    with col_load:
        load_clicked = st.button(
            "Загрузить перечень СО/ВОР",
            type="primary",
            key="sovr_svok_load",
            disabled=uploaded is None,
            width='stretch',
        )
    with col_enrich:
        if uploaded is not None:
            _render_enrich_download(
                uploaded,
                key_prefix="sovr_svok_enrich",
            )
        else:
            st.caption(
                "После выбора файла нажмите кнопку — получите файл "
                "с добавленными статусами СО и ВОР.")

    if load_clicked:
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


def _file_stem(name: str) -> str:
    """Имя файла без расширения (для «<имя>_СО_ВОР.xlsx»)."""
    name = str(name or "СВОК")
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return _safe_filename_part(name) or "СВОК"


def _is_macro_workbook(uploaded) -> bool:
    """Нужно ли сохранять VBA: .xlsm по имени или по MIME.

    Это не фильтр приёма файла, а только выбор ``keep_vba`` для openpyxl.
    """
    name = str(getattr(uploaded, "name", "") or "").lower()
    mime = str(getattr(uploaded, "type", "") or "").lower()
    return name.endswith(".xlsm") or "macroenabled" in mime


def _find_kits_sheet(wb) -> str | None:
    """Лист с шифрами РД.

    Сначала лист с «СВОК» в имени, затем любой лист с шапкой «Шифр РД»
    в колонке C, затем любое упоминание «шифр» в первых 50 строках.
    """
    svok = _find_svok_sheet(wb)
    if svok is not None and _find_svok_header(wb[svok]):
        return svok
    for name in wb.sheetnames:
        if _find_svok_header(wb[name]):
            return name
    for name in wb.sheetnames:
        ws = wb[name]
        max_row = min(int(ws.max_row or 0), 50)
        max_col = min(int(ws.max_column or 0), 60)
        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                value = cell.value
                if value and "шифр" in str(value).lower():
                    return name
    return None


def _detect_sheet_in_workbook(wb) -> tuple[str | None, list[str]]:
    """Лист с шифрами РД и список листов уже открытого файла.

    Файл повторно не читается: вызывающий передаёт тот же workbook,
    который затем идёт в ``_enrich_svok_excel``.
    """
    try:
        sheets = list(wb.sheetnames)
    except Exception:  # noqa: BLE001
        return None, []
    try:
        return _find_kits_sheet(wb), sheets
    except Exception:  # noqa: BLE001
        return None, sheets


def _enrich_pipeline(source, is_xlsm, sheet, sovr_data, progress) -> dict:
    """Тяжёлая часть «Добавить статусы СО и ВОР» в отдельном потоке.

    Вызовов ``st`` здесь нет: этап пишется в ``progress["stage"]``, а
    результат возвращается словарём ``{"payload", "sheet", "sheets",
    "error"}``. Так главный поток может показывать прогресс и сработать
    по тайм-гварду, пока openpyxl читает/пишет файл.
    """
    from openpyxl import load_workbook

    t_all = time.perf_counter()
    progress["stage"] = "Открываем файл…"
    t0 = time.perf_counter()
    try:
        wb = load_workbook(io.BytesIO(source), read_only=False,
                           keep_vba=is_xlsm)
    except Exception as exc:  # noqa: BLE001
        print(f"[enrich] открытие workbook: сбой за "
              f"{time.perf_counter() - t0:.2f} с", flush=True)
        return {"error": f"Не удалось прочитать файл: {exc}"}
    print(f"[enrich] открытие workbook (обычный режим): "
          f"{time.perf_counter() - t0:.2f} с", flush=True)

    try:
        sheets: list[str] = []
        if sheet is None:
            progress["stage"] = "Определяем лист с шифрами РД…"
            t0 = time.perf_counter()
            sheet, sheets = _detect_sheet_in_workbook(wb)
            print(f"[enrich] детект листа: "
                  f"{time.perf_counter() - t0:.2f} с", flush=True)
            if sheet is None and sheets:
                return {"sheets": sheets}
            sheet = sheet or "СВОК"

        progress["stage"] = "Добавляем статусы СО и ВОР…"
        t0 = time.perf_counter()
        payload = _enrich_svok_excel(source, sheet, sovr_data, is_xlsm, wb=wb)
        print(f"[enrich] _enrich_svok_excel: "
              f"{time.perf_counter() - t0:.2f} с", flush=True)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось добавить статусы СО и ВОР: {exc}"}
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
        print(f"[enrich] итого обработки: "
              f"{time.perf_counter() - t_all:.2f} с", flush=True)

    if payload is source:
        return {"error": "Не найдена шапка с шифрами РД на выбранном листе."}
    return {"payload": payload, "sheet": sheet}


def _render_enrich_download(uploaded, key_prefix: str) -> None:
    """Кнопка «Добавить статусы СО и ВОР» для загруженного файла.

    Если файл выбран — кнопка видна всегда. До нажатия файл не читается:
    определение листа с шифрами РД и добавление статусов выполняются
    только в обработчике нажатия. Файл открывается ОДИН раз за нажатие:
    тот же workbook идёт и в детект листа, и в ``_enrich_svok_excel``.
    Любой сбой (чтение файла, детект листа, добавление статусов)
    показывает сообщение, но кнопку не прячет.

    Повторные перерисовки страницы обработку не перезапускают: результат
    лежит в ``st.session_state`` по отпечатку файла (``file_id`` + имя +
    размер), а тяжёлая часть запускается только из обработчика кнопки.
    """
    filename = str(getattr(uploaded, "name", "") or "СВОК")
    is_xlsm = _is_macro_workbook(uploaded)
    payload_key = f"{key_prefix}_payload"
    name_key = f"{key_prefix}_name"
    error_key = f"{key_prefix}_error"
    sheets_key = f"{key_prefix}_sheets"
    fingerprint_key = f"{key_prefix}_fingerprint"

    # Новый файл — сохранённый результат, ошибка и список листов относятся
    # к прежнему файлу и больше не подходят. getvalue() здесь не вызываем:
    # отпечаток берётся из метаданных UploadedFile.
    fingerprint = (
        getattr(uploaded, "file_id", None),
        filename,
        getattr(uploaded, "size", None),
    )
    if st.session_state.get(fingerprint_key) != fingerprint:
        for stale in (payload_key, name_key, error_key, sheets_key,
                      f"{key_prefix}_sheet"):
            st.session_state.pop(stale, None)
        st.session_state[fingerprint_key] = fingerprint

    def add_statuses(sheet: str | None) -> None:
        """Один проход: чтение → детект (если нужно) → добавление → сохранение.

        Тяжёлая часть (openpyxl) идёт в отдельном потоке: главный поток
        показывает этап и через 60 секунд выводит предупреждение, а не
        крутит бесконечный спиннер.
        """
        st.session_state.pop(error_key, None)
        st.session_state.pop(payload_key, None)
        t_all = time.perf_counter()

        t0 = time.perf_counter()
        try:
            source = uploaded.getvalue()
        except Exception as exc:  # noqa: BLE001
            print(f"[enrich] чтение файла из uploader: сбой за "
                  f"{time.perf_counter() - t0:.2f} с", flush=True)
            st.session_state[error_key] = (
                f"Не удалось прочитать файл: {exc}")
            return
        print(f"[enrich] чтение файла из uploader: "
              f"{time.perf_counter() - t0:.2f} с", flush=True)
        print(f"[enrich] файл: {filename} "
              f"({len(source) / 1048576:.1f} МБ), xlsm={is_xlsm}",
              flush=True)

        t0 = time.perf_counter()
        try:
            sovr_data = _load_sovr_data()
        except Exception as exc:  # noqa: BLE001
            st.session_state[error_key] = (
                f"Не удалось получить статусы СО и ВОР из базы: {exc}")
            return
        print(f"[enrich] данные витрины СО/ВР: "
              f"{time.perf_counter() - t0:.2f} с", flush=True)

        progress = {"stage": "Открываем файл…"}
        slow_slot = st.empty()
        initial_label = "Добавляем статусы СО и ВОР…"
        if len(source) >= 5 * 1024 * 1024:
            initial_label = ("Добавляем статусы СО и ВОР… Файл большой — "
                             "это может занять несколько минут.")
        try:
            with st.status(initial_label, expanded=False) as status:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        _enrich_pipeline, source, is_xlsm, sheet,
                        sovr_data, progress)
                    warned = False
                    while not future.done():
                        time.sleep(0.5)
                        label = progress.get("stage") or initial_label
                        if not warned and time.perf_counter() - t_all > 60:
                            warned = True
                            label = ("Файл большой: обработка идёт больше "
                                     "минуты…")
                            slow_slot.warning(
                                "Файл большой — обработка занимает больше "
                                "60 секунд. Это может занять несколько "
                                "минут; страницу не закрывайте.")
                        status.update(label=label)
                    result = future.result()
                status.update(label="Готово", state="complete")
        except Exception as exc:  # noqa: BLE001
            st.session_state[error_key] = (
                f"Не удалось добавить статусы СО и ВОР: {exc}")
            return
        finally:
            print(f"[enrich] итого обработчика: "
                  f"{time.perf_counter() - t_all:.2f} с", flush=True)

        if result.get("error"):
            st.session_state[error_key] = result["error"]
            return
        if result.get("sheets"):
            # Лист не определён — ниже покажем список листов и отдельную
            # кнопку, чтобы добавление запускалось осознанно.
            st.session_state[sheets_key] = result["sheets"]
            if not st.session_state.get(error_key):
                st.session_state[error_key] = (
                    "В файле не найден лист с шифрами РД, укажите лист")
            return
        payload = result.get("payload")
        if not payload:
            st.session_state[error_key] = (
                "Не удалось добавить статусы СО и ВОР: пустой результат.")
            return
        st.session_state[payload_key] = payload
        st.session_state[name_key] = f"{_file_stem(filename)}_СО_ВОР.xlsx"
        st.session_state.pop(sheets_key, None)

    if st.button(
        "📥 Добавить статусы СО и ВОР",
        width='stretch',
        key=f"{key_prefix}_add",
        help="Файл читается только при нажатии: лист с шифрами РД "
             "определяется в этот момент.",
    ):
        # Результат по этому же файлу уже собран: повторное нажатие не
        # запускает тяжёлый проход заново (пересборка — повторный выбор
        # файла, у него новый file_id).
        if not st.session_state.get(payload_key):
            st.session_state.pop(sheets_key, None)
            st.session_state.pop(f"{key_prefix}_sheet", None)
            add_statuses(None)

    sheets = st.session_state.get(sheets_key) or []
    if sheets:
        sheet = st.selectbox(
            "Лист файла",
            sheets,
            key=f"{key_prefix}_sheet",
        )
        if st.button(
            "Добавить статусы на выбранный лист",
            width='stretch',
            key=f"{key_prefix}_apply",
        ):
            add_statuses(sheet)

    error = st.session_state.get(error_key)
    if error:
        st.error(error)

    payload = st.session_state.get(payload_key)
    if payload:
        st.download_button(
            "📥 Добавить статусы СО и ВОР",
            data=payload,
            file_name=st.session_state.get(name_key)
            or f"{_file_stem(filename)}_СО_ВОР.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            width='stretch',
            key=f"{key_prefix}_download",
        )


def _render_enrich_excel() -> None:
    """Добавление статусов СО и ВОР в пользовательский Excel.

    Поведение как в «Экспорте»: поле загрузки → сразу кнопка
    «Добавить статусы СО и ВОР» → файл ``<имя>_СО_ВОР.xlsx``. До нажатия
    кнопки файл не читается; предпросмотра и предварительных проверок
    нет; используется тот же ``_enrich_svok_excel``, что и раньше.
    """
    st.markdown("#### ➕ Добавить статусы СО и ВОР в Excel")
    st.caption(
        "Загрузите тот же файл, что и в «Перечень СО и ВОР (СВОК)». "
        "Справа от таблицы появятся два блока по 5 колонок: «СО» и «ВОР» — "
        "ревизия, статус, дата выдачи, дата получения статуса, номер "
        "процесса. Файл принимается без фильтрации по типу и расширению."
    )

    col_up, col_dl = st.columns([1, 1])
    with col_up:
        uploaded = st.file_uploader(
            "Excel-файл с перечнем комплектов",
            key="sovr_enrich_file",
            help="Приём файла не фильтруется. Отказ — только если в файле "
                 "нет листа с шифрами РД.",
        )
        # Виджет и session_state могут разойтись — берём любой непустой.
        if uploaded is None:
            uploaded = st.session_state.get("sovr_enrich_file")

    if uploaded is None or not hasattr(uploaded, "getvalue"):
        return

    with col_dl:
        _render_enrich_download(
            uploaded,
            key_prefix="sovr_enrich_file",
        )


def _refresh_sovr_manual(user: str = "service_subtab") -> dict:
    """Ручное обновление витрины СО/ВР (кнопка «Сервисной» подвкладки).

    Единственный путь, который пишет отдельное событие журнала
    ``sovr_sync``: это самостоятельное действие пользователя, у него
    должна быть своя запись. Регулярный синк пишет одно событие
    ``refresh`` в конце и складывает туда же итог шага витрины.

    Возвращает словарь ``refresh_sovr_statuses``:
    ``{"ok", "documents", "sheets"}`` (+ ``"error"`` при сбое).
    """
    from vitro.sqlite_db import log_event
    from vitro.sync import refresh_sovr_statuses

    result = refresh_sovr_statuses(verbose=False, user=user)
    if result.get("ok"):
        log_event(
            "sovr_sync",
            f"Витрина СО/ВР: документов {result.get('documents', 0)}, "
            f"листов {result.get('sheets', 0)}",
            rows=result.get("sheets", 0),
        )
    else:
        try:
            log_event("sovr_sync", result.get("error") or "ошибка",
                      status="ERROR")
        except Exception:
            pass
    return result


def render_service() -> None:
    """Сервисная подвкладка: обновление витрины и загрузка перечня."""
    st.markdown("### ⚙️ Сервисные операции")

    # --- Перечень СВОК (C1) ---
    _render_template_upload()

    st.divider()

    # --- Добавление статусов СО/ВОР в пользовательский Excel (22.23) ---
    _render_enrich_excel()

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

        progress_bar = st.progress(0, text="Инициализация...")
        status_text = st.empty()

        try:
            progress_bar.progress(20, text="Шаг 1/2: Загрузка документов из Витро...")
            status_text.caption("Подключение к SharePoint Витро...")
            result = _refresh_sovr_manual()

            if not result.get("ok"):
                raise RuntimeError(
                    result.get("error") or "неизвестная ошибка")

            n_saved = result.get("documents", 0)
            inserted = result.get("sheets", 0)
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
