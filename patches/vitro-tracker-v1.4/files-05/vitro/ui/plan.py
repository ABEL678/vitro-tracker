# vitro/ui/plan.py
"""
📅 План выдачи комплектов — планирование выдачи РД Заказчику.

Формирует план выдачи ВСЕХ комплектов (загруженных в Витро и планируемых
к загрузке) на основании вводных данных:
  - «Ведомость основных комплектов» (Excel) — шифр, наименование, статус,
    срок готовности.
  - «Ведомости чертежей каждого комплекта» (Excel) — номер листа,
    наименование, количество, плановая дата. Поддерживаются как шаблон,
    так и реальные регистры «Комплекты чертежей» (ШИФР / Тип чертежа /
    Номер листа / Название листа / Шифр листа, шапки комплектов,
    приложения без шифра, латинские двойники в шифрах).

Структура вкладки (сверху вниз):
  0. 📥 Вводные данные — импорт ведомостей Excel + шаблоны (свёрнут).
  1. Общая таблица: шифры, названия всех комплектов, общие статусы,
     срок готовности (редактируется прямо в таблице).
  2. Фильтры — единый стандарт (как в «Сводке по проекту»).
  3. Бублики: «Комплектов всего/план» и «Листов всего/план».
  4. Детальная таблица (плашка выгрузки в Excel): дисциплина / раздел /
     комплект(ы) / наименование / количество чертежей (план) —
     с фильтром по датам (периоду).

Данные плана хранятся в SQLite: plan_kits, plan_drawings (sqlite_db.py).
"""

from datetime import date, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import (
    get_conn,
    load_plan_kits,
    load_plan_drawings,
    upsert_plan_kits,
    replace_plan_drawings_for_kits,
    update_plan_due_dates,
    clear_plan,
    log_event,
)
from vitro.ui._auth import ensure_user
from vitro.ui._filters import (
    _extract_2nd_block,
    _extract_4th_block,
    load_kit_options as _load_kit_options,
    render_standard_filters,
)
from vitro.plan_xlsx import (
    MIME_XLSX,
    build_drawings_template,
    build_kits_template,
    build_plan_export,
    parse_drawings_workbook,
    parse_kits_workbook,
)

# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
KIT_STATUS_OPTIONS = [
    "Загружен в Витро",
    "Загружается (частично)",
    "План (не загружен)",
    "Только в Витро",
]

KIT_STATUS_COLORS = {
    "Загружен в Витро":        "#2ca02c",
    "Загружается (частично)":  "#ffdd57",
    "План (не загружен)":      "#9467bd",
    "Только в Витро":          "#1f77b4",
}

OVERVIEW_COLUMNS = [
    "Шифр комплекта", "Наименование комплекта", "Дисциплина", "Раздел",
    "Общий статус", "Листов в Витро", "Листов (план)",
    "Срок готовности", "Статус (ведомость)", "Обновлён",
]

DETAIL_COLUMNS = [
    "Дисциплина", "Раздел", "Комплект(ы)", "Номер листа", "Наименование",
    "Кол-во чертежей (план)", "Плановая дата", "Статус комплекта",
]


# ---------------------------------------------------------------------------
#  Данные Витро
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _vitro_sheet_counts() -> dict[str, int]:
    """{шифр комплекта: количество листов в Витро}."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT complex, COUNT(*) AS n
            FROM documents
            WHERE complex IS NOT NULL
            GROUP BY complex
        """).fetchall()
    return {r["complex"]: int(r["n"]) for r in rows if r["complex"]}


@st.cache_data(ttl=600, show_spinner=False)
def _vitro_kit_meta() -> dict[str, dict]:
    """{шифр: {name, discipline, section}} — справочник комплектов Витро."""
    meta: dict[str, dict] = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT code, name, discipline FROM complexes"
        ).fetchall():
            meta[r["code"]] = {
                "name": r["name"] or "",
                "discipline": r["discipline"] or "",
                "section": "",
            }
        for r in conn.execute("""
            SELECT complex, discipline, section
            FROM documents WHERE complex IS NOT NULL
        """).fetchall():
            code = r["complex"]
            if code not in meta:
                meta[code] = {
                    "name": "",
                    "discipline": r["discipline"] or "",
                    "section": r["section"] or "",
                }
            else:
                if not meta[code]["discipline"]:
                    meta[code]["discipline"] = r["discipline"] or ""
                if not meta[code]["section"]:
                    meta[code]["section"] = r["section"] or ""
    return meta


# ---------------------------------------------------------------------------
#  Общая таблица
# ---------------------------------------------------------------------------
def _build_overview(include_vitro_only: bool) -> pd.DataFrame:
    """
    Одна строка = один комплект: план (ведомости) + Витро.
    Общий статус вычисляется: Загружен в Витро / Загружается (частично) /
    План (не загружен) / Только в Витро.
    """
    plan = load_plan_kits()
    drawings = load_plan_drawings()
    vitro_counts = _vitro_sheet_counts()
    plan_qty = {}
    if not drawings.empty:
        plan_qty = drawings.groupby("kit_code")["qty"].sum().to_dict()

    rows = []
    if not plan.empty:
        for _, r in plan.iterrows():
            code = r["code"]
            in_vitro = code in vitro_counts
            sheets_vitro = vitro_counts.get(code, 0)
            sheets_plan = int(plan_qty.get(code, 0))
            if in_vitro and sheets_plan > 0 and sheets_vitro < sheets_plan:
                status = "Загружается (частично)"
            elif in_vitro:
                status = "Загружен в Витро"
            else:
                status = "План (не загружен)"
            rows.append({
                "code": code,
                "name": r["name"] or "",
                "discipline": r["discipline"] or "",
                "section": r["section"] or "",
                "plan_status": r["plan_status"] or "",
                "due_date": r["due_date"] or None,
                "sheets_vitro": sheets_vitro,
                "sheets_plan": sheets_plan,
                "status": status,
                "in_vitro": in_vitro,
                "updated_at": r["updated_at"] or "",
                "updated_by": r["updated_by"] or "",
            })

    if include_vitro_only:
        meta = _vitro_kit_meta()
        for code in sorted(vitro_counts):
            if code in set(r["code"] for r in rows):
                continue
            info = meta.get(code, {})
            rows.append({
                "code": code,
                "name": info.get("name", ""),
                "discipline": info.get("discipline", ""),
                "section": info.get("section", ""),
                "plan_status": "",
                "due_date": None,
                "sheets_vitro": vitro_counts[code],
                "sheets_plan": 0,
                "status": "Только в Витро",
                "in_vitro": True,
                "updated_at": "",
                "updated_by": "",
            })

    if not rows:
        return pd.DataFrame(columns=OVERVIEW_COLUMNS)

    df = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    return df


def _overview_to_display(df: pd.DataFrame) -> pd.DataFrame:
    """Внутренний DataFrame → вид таблицы (русские колонки).
    Срок готовности — тип date (для редактирования DateColumn)."""
    due = pd.to_datetime(df["due_date"], format="%Y-%m-%d",
                         errors="coerce").dt.date
    due = due.where(pd.notna(due), None)
    out = pd.DataFrame({
        "Шифр комплекта": df["code"],
        "Наименование комплекта": df["name"],
        "Дисциплина": df["discipline"],
        "Раздел": df["section"],
        "Общий статус": df["status"],
        "Листов в Витро": df["sheets_vitro"].astype(int),
        "Листов (план)": df["sheets_plan"].astype(int),
        "Срок готовности": due,
        "Статус (ведомость)": df["plan_status"],
        "Обновлён": df["updated_at"],
    })
    return out


def _save_due_dates(display_before: pd.DataFrame,
                    display_after: pd.DataFrame) -> int:
    """Разница сроков готовности → UPDATE в БД. Возвращает число строк
    (или -1, если требуется имя пользователя)."""
    changes: dict[str, str] = {}
    for i in range(len(display_before)):
        old = display_before.iloc[i]["Срок готовности"]
        new = display_after.iloc[i]["Срок готовности"]
        if old != new:
            code = display_after.iloc[i]["Шифр комплекта"]
            if new is None or pd.isna(new):
                changes[code] = None
            else:
                changes[code] = new.isoformat()
    if not changes:
        return 0
    user = st.session_state.get("user", "")
    if not user or user == "инженер":
        return -1  # требуется имя пользователя
    return update_plan_due_dates(changes, user)


def _render_overview_table() -> None:
    """Секция 1. Общая таблица комплектов с редактируемым сроком."""
    st.markdown("##### 📋 Общая таблица комплектов")

    include_vitro_only = st.checkbox(
        "Включить комплекты, загруженные в Витро, но отсутствующие "
        "в ведомостях",
        value=True, key="plan_include_vitro_only",
        help="«Только в Витро» — комплекты есть в базе Витро, но их нет "
             "в загруженных ведомостях. Срок готовности для них можно "
             "назначить здесь.",
    )

    overview = _build_overview(include_vitro_only)

    if overview.empty:
        st.info(
            "План пока пуст. Разверните блок «📥 Вводные данные» и "
            "загрузите ведомости Excel (шаблоны — в том же блоке)."
        )
        return

    display_before = _overview_to_display(overview)

    edited = st.data_editor(
        display_before,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=min(max(len(display_before) * 42 + 40, 300), 700),
        column_config={
            "Шифр комплекта": st.column_config.TextColumn(width="medium"),
            "Наименование комплекта": st.column_config.TextColumn(
                width="large"),
            "Дисциплина": st.column_config.TextColumn(width="small"),
            "Раздел": st.column_config.TextColumn(width="small"),
            "Общий статус": st.column_config.TextColumn(width="small"),
            "Листов в Витро": st.column_config.NumberColumn(width="small"),
            "Листов (план)": st.column_config.NumberColumn(width="small"),
            "Срок готовности": st.column_config.DateColumn(
                "Срок готовности",
                format="DD.MM.YYYY",
                width="small",
                help="Редактируется прямо в таблице — изменения сохраняются "
                     "автоматически.",
            ),
            "Статус (ведомость)": st.column_config.TextColumn(width="small"),
            "Обновлён": st.column_config.TextColumn(width="small"),
        },
        disabled=[
            "Шифр комплекта", "Наименование комплекта", "Дисциплина",
            "Раздел", "Общий статус", "Листов в Витро", "Листов (план)",
            "Статус (ведомость)", "Обновлён",
        ],
        key="plan_overview_editor",
    )

    n = _save_due_dates(display_before, edited)
    if n > 0:
        st.toast(f"Срок готовности обновлён: {n} компл.")
        st.rerun()
    elif n == -1:
        st.warning(
            "✏️ Изменены сроки готовности. Введите имя, чтобы сохранить: "
            "нажмите кнопку ниже."
        )
        if st.button("💾 Сохранить сроки готовности", type="primary",
                     key="plan_save_due_btn"):
            user = ensure_user()
            if user:
                changed = _save_due_dates(display_before, edited)
                if changed > 0:
                    st.toast(f"Срок готовности сохранён: {changed} компл.")
                st.rerun()

    st.caption(
        f"Всего комплектов: **{len(overview)}**. "
        f"«Общий статус» вычисляется автоматически: наличие комплекта "
        f"и листов в Витро против плана из ведомостей. "
        f"«Срок готовности» — редактируемый."
    )


# ---------------------------------------------------------------------------
#  Фильтры (единый стандарт + статус комплекта)
# ---------------------------------------------------------------------------
def _plan_kit_options_fn(disc, sect, block_codes) -> dict[str, str]:
    """
    Комплекты для каскада: справочник Витро + комплекты из плана
    (их ещё нет в Витро, но они есть в ведомостях).
    """
    base = _load_kit_options(disc, sect, block_codes)
    plan = load_plan_kits()
    if plan.empty:
        return base
    for _, r in plan.iterrows():
        code = r["code"]
        if disc and (r["discipline"] or "") not in disc:
            continue
        if sect and (r["section"] or "") not in sect:
            continue
        if block_codes and _extract_4th_block(code) not in block_codes:
            continue
        if code not in base:
            base[code] = f"{code} — {r['name']}" if r["name"] else code
    return dict(sorted(base.items()))


def _render_filters() -> dict:
    """
    Единый стандарт фильтров (как в Сводке) + фильтр «Статус комплекта»
    и «Только с датами» в 3-м ряду.
    """
    def _plan_extra_row():
        c1, c2 = st.columns(2)
        with c1:
            kit_status = st.multiselect(
                "Статус комплекта",
                options=KIT_STATUS_OPTIONS,
                placeholder="Все",
                key="plan_kit_status",
                help="«План (не загружен)» — комплект ещё не загружен "
                     "в Витро. «Только в Витро» — нет в ведомостях.",
            )
        with c2:
            only_dated = st.checkbox(
                "Только позиции со сроками",
                value=False,
                key="plan_only_dated",
                help="Скрыть позиции без срока готовности и без плановой даты.",
            )
        return {
            "kit_status": tuple(kit_status),
            "only_dated": bool(only_dated),
        }

    return render_standard_filters(
        key_prefix="plan_",
        expander_label="Фильтры",
        expanded=True,
        show_remark_status=False,
        show_sheet_status=False,
        kit_options_fn=_plan_kit_options_fn,
        extra_row=_plan_extra_row,
    )


def _apply_overview_filters(overview: pd.DataFrame, flt: dict) -> pd.DataFrame:
    """Стандартные фильтры + статус комплекта → к общей таблице."""
    if overview.empty:
        return overview
    out = overview
    if flt["disc"]:
        out = out[out["discipline"].isin(flt["disc"])]
    if flt["section"]:
        out = out[out["section"].isin(flt["section"])]
    if flt["kits"]:
        out = out[out["code"].isin(flt["kits"])]
    if flt["block_codes"]:
        blocks = out["code"].apply(_extract_4th_block)
        out = out[blocks.isin(flt["block_codes"])]
    if flt["marka"]:
        m = flt["marka"].strip().upper()
        if m:
            out = out[out["code"].apply(_extract_2nd_block) == m]
    kit_status = flt["extra"].get("kit_status", ())
    if kit_status:
        out = out[out["status"].isin(kit_status)]
    return out


# ---------------------------------------------------------------------------
#  Бублики
# ---------------------------------------------------------------------------
def _donut(fig_data: pd.DataFrame, names_col: str, values_col: str,
           key: str, center_total: str) -> None:
    """Бублик в стиле Сводки: без легенды, проценты внутри."""
    if fig_data.empty or int(fig_data[values_col].sum()) == 0:
        st.caption("Нет данных для диаграммы.")
        return
    fig = px.pie(
        fig_data,
        names=names_col,
        values=values_col,
        hole=0.45,
        color=names_col,
        color_discrete_map=KIT_STATUS_COLORS,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent",
        textfont_size=11,
    )
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=11)),
        margin=dict(l=5, r=5, t=5, b=5),
        height=220,
        annotations=[dict(
            text=center_total,
            showarrow=False,
            font=dict(size=16, color="#1F4E78"),
            x=0.5, y=0.5,
        )],
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _render_donuts(overview_filtered: pd.DataFrame) -> tuple[int, int]:
    """Секция 3. Бублики «Комплектов всего/план» и «Листов всего/план»."""
    # В бублики включаем только комплекты плана («Только в Витро» —
    # вне плана, их объёмы не учитываем).
    plan_df = overview_filtered[
        overview_filtered["status"] != "Только в Витро"
    ] if not overview_filtered.empty else overview_filtered

    st.markdown("##### 🎯 Комплектов и листов: всего / план")

    if plan_df.empty:
        st.info("Нет комплектов плана по выбранным фильтрам.")
        return (0, 0)

    n_total = len(plan_df)
    n_loaded = int(plan_df["in_vitro"].sum())
    n_plan = n_total - n_loaded

    kits_data = pd.DataFrame([
        {"Группа": "Загружено в Витро", "Количество": n_loaded},
        {"Группа": "План к загрузке", "Количество": n_plan},
    ])
    kits_data = kits_data[kits_data["Количество"] > 0]

    # ---- ЛИСТЫ: «всего» = плановое количество из ведомостей ----
    # По каждому комплекту:
    #   загружено = min(листов в Витро, план) — сколько ПЛАНОВЫХ листов
    #               уже в Витро;
    #   остаток   = план − загружено (разница, а не сумма).
    # Листы Витро сверх плана (повторы, извещения, комплекты без
    # ведомости чертежей) в бублик не включаются — иначе «всего»
    # раздувается выше суммы строк ведомости.
    plan_sheets = plan_df["sheets_plan"].astype(int)
    vitro_sheets = plan_df["sheets_vitro"].astype(int)
    sheets_total = int(plan_sheets.sum())
    sheets_loaded = int(
        pd.concat([vitro_sheets, plan_sheets], axis=1).min(axis=1).sum())
    sheets_remaining = sheets_total - sheets_loaded
    sheets_excess = int((vitro_sheets - plan_sheets).clip(lower=0).sum())

    sheets_data = pd.DataFrame([
        {"Группа": "Загружено в Витро", "Количество": sheets_loaded},
        {"Группа": "План к загрузке", "Количество": sheets_remaining},
    ])
    sheets_data = sheets_data[sheets_data["Количество"] > 0]

    col1, col2 = st.columns(2)
    with col1:
        st.caption(
            f"Комплектов всего: **{n_total}** · "
            f"загружено **{n_loaded}** · план **{n_plan}**"
        )
        _donut(kits_data, "Группа", "Количество", "plan_pie_kits",
               center_total=f"{n_total}")
    with col2:
        st.caption(
            f"Листов всего (план): **{sheets_total}** · "
            f"загружено **{sheets_loaded}** · план **{sheets_remaining}**"
        )
        _donut(sheets_data, "Группа", "Количество", "plan_pie_sheets",
               center_total=f"{sheets_total}")

    if sheets_excess > 0:
        st.caption(
            f"ℹ️ В Витро есть ещё {sheets_excess} лист(ов) сверх "
            "планового количества (повторы, извещения, комплекты без "
            "ведомости чертежей) — в бублик не включены: «всего» "
            "никогда не превышает ведомость."
        )
    return (n_total, sheets_total)


# ---------------------------------------------------------------------------
#  Детальная таблица
# ---------------------------------------------------------------------------
def _build_detail(overview_filtered: pd.DataFrame,
                  flt: dict,
                  date_from, date_to) -> pd.DataFrame:
    """
    Детальные строки чертежей (план) по результату фильтрации
    и выбранному периоду. Дата позиции = плановая дата чертежа,
    а если её нет — срок готовности комплекта.
    """
    drawings = load_plan_drawings()
    if drawings.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    overview = overview_filtered
    if overview.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    # Допускаем чертежи и «Только в Витро» комплектов (план = фактические
    # листы Витро в этом случае не из ведомостей, а из documents —
    # но ведомости на них нет, поэтому строк чертежей у них нет).
    df = drawings.merge(
        overview[["code", "status", "due_date"]],
        left_on="kit_code", right_on="code", how="inner",
    )
    df = df.rename(columns={"kit_code": "Комплект(ы)"})
    df = df.drop(columns=["code"])

    # ---- Стандартные фильтры ----
    if flt["disc"]:
        df = df[df["discipline"].isin(flt["disc"])]
    if flt["section"]:
        df = df[df["section"].isin(flt["section"])]
    if flt["kits"]:
        df = df[df["Комплект(ы)"].isin(flt["kits"])]
    if flt["block_codes"]:
        blocks = df["Комплект(ы)"].apply(_extract_4th_block)
        df = df[blocks.isin(flt["block_codes"])]
    if flt["marka"]:
        m = flt["marka"].strip().upper()
        if m:
            df = df[df["Комплект(ы)"].apply(_extract_2nd_block) == m]
    kit_status = flt["extra"].get("kit_status", ())
    if kit_status:
        df = df[df["status"].isin(kit_status)]

    # ---- Дата позиции: плановая дата чертежа или срок комплекта ----
    df["_date"] = df["planned_date"].where(
        df["planned_date"].notna() & (df["planned_date"] != ""),
        df["due_date"],
    )

    only_dated = flt["extra"].get("only_dated", False)
    if only_dated:
        df = df[df["_date"].notna()]

    # ---- Период ----
    if date_from or date_to:
        df = df[df["_date"].notna()]
        iso = df["_date"].astype(str).str.slice(0, 10)
        if date_from:
            df = df[iso >= date_from.isoformat()]
        if date_to:
            df = df[iso <= date_to.isoformat()]

    if df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    out = pd.DataFrame({
        "Дисциплина": df["discipline"].fillna(""),
        "Раздел": df["section"].fillna(""),
        "Комплект(ы)": df["Комплект(ы)"],
        "Номер листа": df.get("sheet_no", ""),
        "Наименование": df["title"].fillna(""),
        "Кол-во чертежей (план)": df["qty"].astype(int),
        "Плановая дата": df["_date"],
        "Статус комплекта": df["status"],
    })
    out = out.sort_values(
        ["Дисциплина", "Раздел", "Комплект(ы)", "Номер листа"],
    ).reset_index(drop=True)
    return out


def _render_detail(overview_filtered: pd.DataFrame, flt: dict) -> None:
    """Секция 4. Детальная таблица + период + плашка выгрузки в Excel."""
    st.markdown("##### 🗓 Детальный план по чертежам")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        date_from = st.date_input(
            "Период: с",
            value=None,
            min_value=date(2020, 1, 1),
            max_value=date(2100, 12, 31),
            key="plan_date_from",
            help="Фильтр по плановой дате чертежа (или сроку готовности "
                 "комплекта, если у чертежа даты нет).",
        )
    with c2:
        date_to = st.date_input(
            "по",
            value=None,
            min_value=date(2020, 1, 1),
            max_value=date(2100, 12, 31),
            key="plan_date_to",
        )
    with c3:
        st.caption(
            "Пустые даты — показываются все позиции. Выгрузка в Excel "
            "делает тот же отбор, что и таблица."
        )

    detail = _build_detail(overview_filtered, flt, date_from, date_to)

    if date_from or date_to:
        period_label = (
            f"{date_from.strftime('%d.%m.%Y') if date_from else '…'} — "
            f"{date_to.strftime('%d.%m.%Y') if date_to else '…'}"
        )
    else:
        period_label = "все даты"

    # ---- Плашка выгрузки в Excel ----
    export_bytes = build_plan_export(
        overview_filtered, detail, period_label=period_label)
    st.download_button(
        "📥 Выгрузить в Excel (комплекты + чертежи выбранного периода)",
        data=export_bytes,
        file_name=f"План_выдачи_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime=MIME_XLSX,
        use_container_width=False,
        key="plan_export_excel",
    )

    if detail.empty:
        st.info(
            "Нет позиций чертежей по выбранным фильтрам/периоду. "
            "Загрузите ведомости чертежей в блоке «📥 Вводные данные»."
        )
        return

    st.caption(
        f"Строк: **{len(detail)}** · позиций: "
        f"**{int(detail['Кол-во чертежей (план)'].sum())}** · "
        f"период: **{period_label}**"
    )

    st.dataframe(
        detail,
        use_container_width=True,
        hide_index=True,
        height=min(max(len(detail) * 36 + 40, 300), 700),
        column_config={
            "Дисциплина": st.column_config.TextColumn(width="small"),
            "Раздел": st.column_config.TextColumn(width="small"),
            "Комплект(ы)": st.column_config.TextColumn(width="medium"),
            "Номер листа": st.column_config.TextColumn(width="small"),
            "Наименование": st.column_config.TextColumn(width="large"),
            "Кол-во чертежей (план)": st.column_config.NumberColumn(
                width="small"),
            "Плановая дата": st.column_config.DateColumn(
                format="DD.MM.YYYY", width="small"),
            "Статус комплекта": st.column_config.TextColumn(width="small"),
        },
    )


# ---------------------------------------------------------------------------
#  Секция 0. Импорт ведомостей
# ---------------------------------------------------------------------------
def _render_import_block() -> None:
    with st.expander("📥 Вводные данные: ведомости (Excel)", expanded=False):
        st.caption(
            "Загрузите **Ведомость основных комплектов** и **ведомости "
            "чертежей** в формате Excel. Колонки распознаются по названиям "
            "(«Шифр комплекта», «Номер листа», «Название листа», «Шифр "
            "листа» и т. п.) — порядок и лишние колонки не мешают; подходят "
            "и реальные регистры «Комплекты чертежей» с шапками комплектов "
            "и приложениями (СО/ВР/П). Повторная загрузка ведомости "
            "комплектов обновляет шифры; повторная загрузка ведомости "
            "чертежей **заменяет** чертежи указанных в ней комплектов."
        )

        c1, c2 = st.columns(2)

        with c1:
            up_kits = st.file_uploader(
                "Ведомость основных комплектов (Excel)",
                type=["xlsx", "xlsm", "xltx"],
                accept_multiple_files=True,
                key="plan_up_kits",
            )
            st.download_button(
                "📋 Шаблон: ведомость комплектов",
                data=build_kits_template(),
                file_name="Шаблон_ведомость_комплектов.xlsx",
                mime=MIME_XLSX,
                use_container_width=True,
                key="plan_tpl_kits",
            )

        with c2:
            up_drawings = st.file_uploader(
                "Ведомости чертежей комплектов (Excel)",
                type=["xlsx", "xlsm", "xltx"],
                accept_multiple_files=True,
                key="plan_up_drawings",
            )
            st.download_button(
                "📋 Шаблон: ведомость чертежей",
                data=build_drawings_template(),
                file_name="Шаблон_ведомость_чертежей.xlsx",
                mime=MIME_XLSX,
                use_container_width=True,
                key="plan_tpl_drawings",
            )
            default_kit = st.text_input(
                "Шифр комплекта (если в файлах нет колонки «Шифр»)",
                placeholder="например: АТ-РД-КЖ1-1-00",
                key="plan_default_kit",
                help="Если ведомость чертежей сделана для одного комплекта "
                     "без колонки шифра — укажите его здесь. Можно вводить "
                     "несколько через запятую (лист = шифр по порядку).",
            )

        # ---- Разбор ведомости комплектов ----
        kits_rows: list[dict] = []
        kits_warnings: list[str] = []
        if up_kits:
            for f in up_kits:
                r, w = parse_kits_workbook(f.getvalue(), f.name)
                kits_rows += r
                kits_warnings += w
            if kits_rows:
                st.markdown(
                    f"**Ведомость комплектов:** распознано строк — "
                    f"**{len(kits_rows)}**")
                df_kits = pd.DataFrame(kits_rows).head(300)
                st.dataframe(
                    df_kits.rename(columns={
                        "code": "Шифр комплекта",
                        "name": "Наименование комплекта",
                        "discipline": "Дисциплина",
                        "section": "Раздел",
                        "plan_status": "Статус",
                        "due_date": "Срок готовности",
                        "due_comment": "Пояснение",
                        "updated_at": "Обновлён",
                        "updated_by": "Кем",
                    }),
                    use_container_width=True, hide_index=True, height=200)
                if st.button("💾 Сохранить комплекты в план",
                             type="primary", key="plan_save_kits"):
                    user = ensure_user()
                    if user:
                        try:
                            n = upsert_plan_kits(kits_rows, user)
                            st.success(f"✅ Сохранено комплектов: {n}")
                            log_event("plan_import",
                                      f"Ведомость комплектов: {n} строк "
                                      f"(файлов: {len(up_kits)})", rows=n)
                            st.rerun()
                        except Exception as e:
                            st.error(f"ОШИБКА БД при сохранении комплектов: {e}")
                            st.stop()
            else:
                st.info("Строки комплектов не распознаны.")

        # ---- Разбор ведомостей чертежей ----
        drawings_rows: list[dict] = []
        drawings_warnings: list[str] = []
        drawings_kits: dict[str, str] = {}
        if up_drawings:
            # Поддержка «один файл = один комплект»: если файлов несколько
            # и колонки шифра нет, применяется шифр по порядку файлов.
            default_codes = [c.strip().upper() for c in
                             (default_kit or "").split(",") if c.strip()]
            for idx, f in enumerate(up_drawings):
                dk = default_codes[idx] if idx < len(default_codes) else \
                    (default_codes[0] if len(default_codes) == 1 else "")
                r, w, k = parse_drawings_workbook(f.getvalue(), f.name, dk)
                drawings_rows += r
                drawings_warnings += w
                for code, kname in k.items():
                    # название берём из шапки комплекта; пустое не затираем
                    if kname and not drawings_kits.get(code):
                        drawings_kits[code] = kname
                    drawings_kits.setdefault(code, drawings_kits.get(code, ""))
            if drawings_rows:
                n_drw = sum(int(r["qty"]) for r in drawings_rows)
                st.markdown(
                    f"**Ведомости чертежей:** распознано листов — "
                    f"**{len(drawings_rows)}** ({n_drw} чертежей), "
                    f"комплектов — **{len({r['kit_code'] for r in drawings_rows})}**")
                df_drw = pd.DataFrame(drawings_rows).head(300)
                if "qty" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"qty": "Кол-во"})
                if "sheet_no" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"sheet_no": "Номер листа"})
                if "title" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"title": "Наименование чертежа"})
                st.dataframe(
                    df_drw.rename(columns={
                        "kit_code": "Шифр комплекта",
                        "planned_date": "Плановая дата",
                        "discipline": "Дисциплина",
                        "section": "Раздел",
                        "source_file": "Файл",
                    }),
                    use_container_width=True, hide_index=True, height=200)
                if st.button("💾 Сохранить чертежи в план",
                             type="primary", key="plan_save_drawings"):
                    user = ensure_user()
                    if user:
                        try:
                            # Названия комплектов из шапок регистров —
                            # в план (пустые существующие поля заполняются)
                            if drawings_kits:
                                upsert_plan_kits(
                                    [{"code": c, "name": n} for c, n
                                     in drawings_kits.items()], user)
                            n = replace_plan_drawings_for_kits(drawings_rows, user)
                            n_kits = len({r["kit_code"] for r in drawings_rows})
                            st.success(f"✅ Сохранено листов: {n} "
                                       f"({n_kits} компл.)")
                            log_event("plan_import",
                                      f"Ведомости чертежей: {n} листов "
                                      f"(файлов: {len(up_drawings)})", rows=n)
                            st.rerun()
                        except Exception as e:
                            st.error(f"ОШИБКА БД при сохранении чертежей: {e}")
                            st.stop()
            else:
                st.info("Строки чертежей не распознаны.")

        # ---- Предупреждения разбора ----
        all_warnings = kits_warnings + drawings_warnings
        if all_warnings:
            with st.expander(
                    f"⚠️ Замечания по файлам ({len(all_warnings)})",
                    expanded=False):
                for w in all_warnings[:50]:
                    st.write(f"- {w}")

        # ---- Опасная зона ----
        st.divider()
        confirm = st.text_input(
            "Очистить план целиком: введите DELETE",
            key="plan_confirm_clear",
            help="Удалит все комплекты и чертежи плана. Данные Витро "
                 "не затрагиваются.",
        )
        if st.button("🗑 Очистить план", key="plan_clear_btn",
                     disabled=confirm != "DELETE"):
            user = ensure_user()
            if user:
                clear_plan()
                log_event("plan_clear",
                          f"План выдачи очищен. Пользователь: {user}")
                st.success("План очищен.")
                st.rerun()


# ===========================================================================
#  Точка входа
# ===========================================================================
def render():
    st.header("📅 План выдачи комплектов")
    st.caption(
        "План выдачи всех комплектов РД: загруженных в Витро и "
        "планируемых к загрузке — на основании ведомостей Excel. "
        "Данные плана общие для всех пользователей приложения."
    )

    # ---- 0. Вводные данные ----
    _render_import_block()

    st.divider()

    # ---- 1. Общая таблица ----
    _render_overview_table()

    st.divider()

    # ---- 2. Фильтры (единый стандарт) ----
    flt = _render_filters()

    overview_all = _build_overview(
        st.session_state.get("plan_include_vitro_only", True))
    overview_filtered = _apply_overview_filters(overview_all, flt)

    st.divider()

    # ---- 3. Бублики ----
    _render_donuts(overview_filtered)

    st.divider()

    # ---- 4. Детальная таблица + период + выгрузка ----
    _render_detail(overview_filtered, flt)
