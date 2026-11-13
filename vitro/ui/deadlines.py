# vitro/ui/deadlines.py
"""
⏰ Сроки — инструмент ведущего специалиста.

Одна вкладка:
  0. Дата последней выгрузки из Витро.
  1. Фильтры (каскадные): дисциплина → раздел → расположение →
     комплект → лист.
  2. KPI: Всего у АТП ТЛП / срок ответа не превышен / Просрочено.
  3. 3 бублика (с легендой): просрочка ответа / статусы замечаний /
     статусы листов.
  4. Список замечаний с фильтрами (AND), сортировкой и свёрткой.
"""

import time
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.workdays import add_workdays, parse_date, workdays_between
from vitro.text_utils import clean_leaf_key
from vitro.ui._urls import build_vitro_comment_url
from vitro.ui._filters import (
    _filter_by_marka,
    _extract_4th_block,
    render_standard_filters,
)


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
SLA_DAYS = 10
ABANDONED_DAYS = 90
CHRONIC_WORKDAYS = 90

BUCKET_ORDER = ["≤10", "10–30", "30–90", ">90"]

BUCKET_LABELS = {
    "≤10":   "≤10 — в сроке",
    "10–30": "10–30 — свежие",
    "30–90": "30–90 — значительное ожидание",
    ">90":   ">90 — критическое",
}

BUCKET_COLORS = {
    "≤10":   "#a5d6a7",
    "10–30": "#ffdd57",
    "30–90": "#ff7f0e",
    ">90":   "#d62728",
}

STATUS_COLORS = {
    "Новое":            "#1f77b4",
    "Принято в работу": "#17becf",
    "Не принято":       "#d62728",
    "К обсуждению":     "#ffdd57",
}

SHEET_COLORS = {
    "A — утверждён":              "#2ca02c",
    "B — к сдаче":                "#ffdd57",
    "C — в работе":               "#ff7f0e",
    "И — информационный":         "#e8e8e8",
    "На согласовании Заказчика":  "#17becf",
    "Готовится к загрузке":       "#9467bd",
    "Аннулировано":               "#7f7f7f",
    "Прочее":                     "#c7c7c7",
}

SHEET_GROUP_ORDER = [
    "A — утверждён",
    "B — к сдаче",
    "C — в работе",
    "И — информационный",
    "На согласовании Заказчика",
    "Готовится к загрузке",
    "Аннулировано",
    "Прочее",
]

SHEET_GROUPS_OURS = [
    "B — к сдаче",
    "C — в работе",
    "И — информационный",
    "На согласовании Заказчика",
    "Готовится к загрузке",
    "Прочее",
]


# ---------------------------------------------------------------------------
#  Справочники — общий модуль _filters.py (дисциплины / разделы / комплекты)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _load_sheet_options(kits: tuple = ()) -> list[str]:
    if not kits:
        return []
    with get_conn() as conn:
        ph = ",".join("?" * len(kits))
        rows = conn.execute(f"""
            SELECT DISTINCT leaf FROM documents
            WHERE complex IN ({ph})
              AND leaf IS NOT NULL AND leaf <> ''
            ORDER BY leaf
        """, tuple(kits)).fetchall()
    return [clean_leaf_key(r["leaf"]) for r in rows]


@st.cache_data(ttl=600, show_spinner=False)
def _load_last_sync_date() -> str | None:
    try:
        with get_conn() as conn:
            row = conn.execute("""
                SELECT MAX(timestamp) AS ts FROM log
                WHERE event IN ('sync_from_ui', 'refresh')
                  AND status = 'OK'
            """).fetchone()
        if row and row["ts"]:
            return row["ts"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
#  4-й блок — общий модуль _filters.py
# ---------------------------------------------------------------------------
def _sheet_status_group(raw) -> str:
    if raw is None:
        return "Прочее"
    s = str(raw).strip().upper()
    if s == "A":
        return "A — утверждён"
    if s == "B":
        return "B — к сдаче"
    if s == "C":
        return "C — в работе"
    if s == "И":
        return "И — информационный"
    if s in ("ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"):
        return "На согласовании Заказчика"
    if s in ("НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"):
        return "Готовится к загрузке"
    if s == "АННУЛИРОВАНО":
        return "Аннулировано"
    return "Прочее"


def _collapse_repeats(df: pd.DataFrame,
                       cols: list) -> pd.DataFrame:
    """
    Скрывает повторяющиеся значения в колонках.
    Если значение совпадает с предыдущей строкой — заменяется на "".
    """
    out = df.copy()
    key_prev = None
    for idx, row in out.iterrows():
        key = tuple(row[c] for c in cols)
        if key == key_prev:
            for c in cols:
                out.at[idx, c] = ""
        key_prev = key
    return out


# ---------------------------------------------------------------------------
#  Главный расчёт
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_categorized() -> pd.DataFrame:
    t0 = time.time()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.id, c.doc_id, c.comment, c.status, c.author, c.created,
                c.fix_date, c.category, c.category_date,
                c.category_user, c.category_version,
                d.discipline, d.section, d.complex,
                d.leaf AS sheet_raw,
                d.name AS sheet_name,
                d.status AS doc_status,
                d.vitro_guid AS vitro_guid,
                d.vitro_version AS vitro_version
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN (
                'Новое','Принято в работу','Не принято',
                'К обсуждению','Выполнено'
            )
              AND c.created IS NOT NULL AND c.created <> ''
        """).fetchall()

        t1 = time.time()
        print(f"[TIMING] _load_all_categorized: SQL {t1 - t0:.1f} сек "
              f"({len(rows)} строк)")

    today = date.today()
    result = []

    for r in rows:
        created = parse_date(r["created"])
        if not created:
            continue

        fix_d = parse_date(r["fix_date"]) if r["fix_date"] else None
        cat_d = parse_date(r["category_date"]) if r["category_date"] else None
        status = r["status"]
        doc_status = (r["doc_status"] or "").strip().upper()

        due = add_workdays(created, SLA_DAYS)
        cust_due = add_workdays(fix_d, SLA_DAYS) if fix_d else None

        movements = [d for d in [created, fix_d, cat_d] if d]
        last_movement = max(movements) if movements else created
        days_since_movement = (today - last_movement).days

        wd_overdue_work = (
            workdays_between(due, today) if today > due else 0
        )
        wd_waiting_customer = 0
        if cust_due and today > cust_due:
            wd_waiting_customer = workdays_between(cust_due, today)

        # holder
        holder = "other"
        if doc_status == "АННУЛИРОВАНО":
            holder = "annulled"
        elif doc_status == "A":
            holder = "customer_a"
        elif status == "Выполнено":
            holder = "customer"
        elif status in ("Новое", "Принято в работу",
                         "Не принято", "К обсуждению"):
            holder = "ours"

        our_status = None
        if holder == "ours":
            our_status = {
                "Новое": "new",
                "Принято в работу": "in_work",
                "Не принято": "rejected",
                "К обсуждению": "discussion",
            }.get(status)

        our_bucket = None
        if holder == "ours":
            if wd_overdue_work == 0:
                our_bucket = "≤10"
            elif wd_overdue_work <= 20:
                our_bucket = "10–30"
            elif wd_overdue_work <= 80:
                our_bucket = "30–90"
            else:
                our_bucket = ">90"

        customer_bucket = None
        if holder == "customer":
            if cust_due is None or wd_waiting_customer == 0:
                customer_bucket = "≤10"
            elif wd_waiting_customer <= 30:
                customer_bucket = "10–30"
            elif wd_waiting_customer <= 90:
                customer_bucket = "30–90"
            else:
                customer_bucket = ">90"

        # category_flag (для совместимости)
        category = "other"
        if status == "Выполнено":
            if doc_status in ("A", "B"):
                category = "closed_by_doc_status"
            elif cust_due and wd_waiting_customer > CHRONIC_WORKDAYS:
                category = "waiting_customer_chronic"
            elif cust_due and wd_waiting_customer > 30:
                category = "waiting_customer_overdue"
            elif cust_due and today > cust_due:
                category = "waiting_customer"
            else:
                category = "waiting_customer_ontime"
        elif status == "Не принято":
            if fix_d:
                reject_due = add_workdays(fix_d, SLA_DAYS)
                if today > reject_due:
                    if days_since_movement > ABANDONED_DAYS:
                        category = "abandoned"
                    else:
                        category = "rejected_overdue"
                else:
                    category = "rejected_in_progress"
            else:
                if today > due:
                    category = "rejected_overdue"
                else:
                    category = "rejected_in_progress"
        elif status == "Новое":
            if today > due:
                if days_since_movement > ABANDONED_DAYS:
                    category = "abandoned"
                else:
                    category = "new_overdue"
            else:
                category = "new_in_progress"
        elif status == "Принято в работу":
            if today > due:
                if days_since_movement > ABANDONED_DAYS:
                    category = "abandoned"
                else:
                    category = "in_work_overdue"
            else:
                category = "in_work_in_progress"
        elif status == "К обсуждению":
            if today > due:
                if days_since_movement > ABANDONED_DAYS:
                    category = "abandoned"
                else:
                    category = "discussion_overdue"
            else:
                category = "discussion_in_progress"

        result.append({
            "id": r["id"],
            "comment": r["comment"],
            "status": status,
            "doc_status": doc_status,
            "author": r["author"],
            "discipline": r["discipline"],
            "section": r["section"],
            "complex": r["complex"],
            "sheet": clean_leaf_key(r["sheet_raw"]),
            "sheet_raw": r["sheet_raw"],
            "sheet_name": r["sheet_name"],
            "created": r["created"],
            "created_d": created,
            "fix_date": r["fix_date"],
            "fix_date_d": fix_d,
            "category": r["category"],
            "category_date": r["category_date"],
            "category_date_d": cat_d,
            "category_user": r["category_user"],
            "category_version": r["category_version"],
            "due_date_d": due,
            "customer_due_d": cust_due,
            "days_overdue_work": wd_overdue_work,
            "days_waiting_customer": wd_waiting_customer,
            "wd_since_created": workdays_between(created, today),
            "days_since_movement": days_since_movement,
            "holder": holder,
            "our_status": our_status,
            "our_bucket": our_bucket,
            "customer_bucket": customer_bucket,
            "category_flag": category,
            "vitro_guid": r["vitro_guid"],
            "vitro_version": r["vitro_version"],
        })

    t2 = time.time()
    print(f"[TIMING] _load_all_categorized: обработка {t2 - t1:.1f} сек")
    print(f"[TIMING] _load_all_categorized: ИТОГО {t2 - t0:.1f} сек")
    return pd.DataFrame(result)


# ---------------------------------------------------------------------------
#  Утилита — бублик с легендой
# ---------------------------------------------------------------------------
def _pie_with_legend(data: pd.DataFrame,
                      names: str, values: str,
                      colors: dict,
                      key: str,
                      order: list = None,
                      labels_map: dict = None) -> None:
    if data.empty or data[values].sum() == 0:
        st.caption("Нет данных.")
        return
    data = data.copy()
    if labels_map:
        data["_label"] = data[names].map(labels_map).fillna(data[names])
        names_col = "_label"
    else:
        names_col = names

    fig = px.pie(
        data, names=names_col, values=values, hole=0.45,
        color=names,
        color_discrete_map=colors,
        category_orders={names: order} if order else None,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent",
        textfont_size=11,
    )
    fig.update_layout(
        showlegend=True,
        legend=dict(
            orientation="v",
            yanchor="middle", y=0.5,
            xanchor="left", x=1.05,
            font=dict(size=11),
        ),
        margin=dict(l=5, r=5, t=5, b=5),
        height=260,
    )
    st.plotly_chart(fig, width='stretch', key=key)


# ---------------------------------------------------------------------------
#  Верхние фильтры — единый стандарт (как в Сводке) + 6-я колонка «Лист»
# ---------------------------------------------------------------------------
def _render_filters():
    """
    Единый стандарт фильтров (_filters.render_standard_filters).
    Каскад ряда 1: Дисциплина → Раздел → Расположение → Комплект → Марка,
    плюс вкладко-специфичная колонка «Лист» (только из выбранного комплекта).
    Возвращает (disc, sect, kits, sheets, block_codes, marka) — прежний
    контракт, чтобы не менять код ниже.
    """

    def _sheet_filter(col, kits_t):
        if kits_t:
            sheet_options = _load_sheet_options(kits_t)
            val = col.multiselect(
                "Лист", options=sheet_options,
                placeholder="Все", key="dl_sheet",
            )
        else:
            val = []
            col.multiselect("Лист", options=[],
                            placeholder="Выберите комплект",
                            disabled=True, key="dl_sheet_empty")
        return {"sheets": tuple(val)}

    flt = render_standard_filters(
        key_prefix="dl_",
        show_remark_status=False,
        show_sheet_status=False,
        row1_extra=_sheet_filter,
    )
    return (flt["disc"], flt["section"], flt["kits"],
            flt["extra"].get("sheets", ()), flt["block_codes"],
            flt["marka"])


def _apply_filters(df: pd.DataFrame,
                    disc_t: tuple, sect_t: tuple,
                    kit_t: tuple, sheet_t: tuple,
                    block_codes: tuple,
                    marka: str = "") -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if disc_t:
        out = out[out["discipline"].isin(disc_t)]
    if sect_t:
        out = out[out["section"].isin(sect_t)]
    if kit_t:
        out = out[out["complex"].isin(kit_t)]
    if sheet_t:
        out = out[out["sheet"].isin(sheet_t)]
    if block_codes:
        blocks = out["complex"].apply(_extract_4th_block)
        out = out[blocks.isin(block_codes)]
    if marka:
        out = _filter_by_marka(out, marka)
    return out


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("⏰ Сроки")

    st.caption(
        "Инструмент ведущего специалиста: замечания в работе АТП ТЛП "
        "с фильтрацией и инфографикой."
    )

    disc_t, sect_t, kit_t, sheet_t, block_codes, marka_t = _render_filters()

    st.divider()

    with st.spinner("Загрузка..."):
        df = _load_all_categorized()

    if df.empty:
        st.warning("Нет данных. Запустите синхронизацию.")
        return

    df = _apply_filters(df, disc_t, sect_t, kit_t, sheet_t, block_codes,
                         marka_t)

    ours = df[df["holder"] == "ours"].copy()
    if ours.empty:
        st.info("Нет замечаний на нашей стороне с учётом фильтров.")
        return

    ours["_sheet_group"] = ours["doc_status"].apply(_sheet_status_group)

    # ===== KPI =====
    n_total = len(ours)
    n_in_time = int((ours["our_bucket"] == "≤10").sum())
    n_overdue = int(ours["our_bucket"].isin(
        ["10–30", "30–90", ">90"]).sum())

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Всего у АТП ТЛП", f"{n_total:,}".replace(",", " "),
        help="Все замечания, ожидающие ответа АТП ТЛП.",
    )
    c2.metric(
        "из них срок ответа не превышен",
        f"{n_in_time:,}".replace(",", " "),
        help="Срок ответа (10 рабочих дней) не истёк.",
    )
    c3.metric(
        "из них Просрочено (>10 р.д.)",
        f"{n_overdue:,}".replace(",", " "),
        help="Срок ответа (10 рабочих дней) истёк.",
    )

    st.divider()

    # ===== 3 бублика =====
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("##### Просрочка ответа АТП ТЛП")
        bucket_data = (
            ours.groupby("our_bucket").size()
            .reset_index(name="Количество")
            .rename(columns={"our_bucket": "Бакет"})
        )
        _pie_with_legend(
            bucket_data, "Бакет", "Количество",
            BUCKET_COLORS, "dl_pie_buckets",
            order=BUCKET_ORDER,
            labels_map=BUCKET_LABELS,
        )

    with col2:
        st.markdown("##### Статусы замечаний")
        status_data = (
            ours.groupby("status").size()
            .reset_index(name="Количество")
        )
        _pie_with_legend(
            status_data, "status", "Количество",
            STATUS_COLORS, "dl_pie_statuses",
            order=["Новое", "Принято в работу",
                   "Не принято", "К обсуждению"],
        )

    with col3:
        st.markdown("##### Статусы листов")
        sheet_data = (
            ours.groupby("_sheet_group").size()
            .reset_index(name="Количество")
            .rename(columns={"_sheet_group": "Статус листа"})
        )
        _pie_with_legend(
            sheet_data, "Статус листа", "Количество",
            SHEET_COLORS, "dl_pie_sheets",
            order=SHEET_GROUP_ORDER,
        )

    st.divider()

    # ===== Список замечаний =====
    st.markdown(f"#### Список замечаний ({n_total:,})".replace(",", " "))

    c1, c2, c3 = st.columns([2, 2, 2])

    with c1:
        sel_bucket = st.multiselect(
            "Просрочка", options=BUCKET_ORDER,
            placeholder="Все", key="dl_f_bucket",
        )

    with c2:
        sel_status = st.multiselect(
            "Статус замечания",
            options=["Новое", "Принято в работу",
                     "Не принято", "К обсуждению"],
            placeholder="Все", key="dl_f_status",
        )

    with c3:
        sel_sheet_group = st.multiselect(
            "Статус листа",
            options=SHEET_GROUPS_OURS,
            placeholder="Все", key="dl_f_sheet",
        )

    filtered = ours.copy()
    if sel_bucket:
        filtered = filtered[filtered["our_bucket"].isin(sel_bucket)]
    if sel_status:
        filtered = filtered[filtered["status"].isin(sel_status)]
    if sel_sheet_group:
        filtered = filtered[
            filtered["_sheet_group"].isin(sel_sheet_group)]

    if filtered.empty:
        st.warning("По фильтрам нет замечаний.")
        return

    # Сортировка: Комплект → Лист → Просрочка ↓
    filtered = filtered.sort_values(
        ["complex", "sheet", "days_overdue_work"],
        ascending=[True, True, False],
    )

    # Свёртка повторяющихся значений (Комплект + Лист)
    filtered_display = _collapse_repeats(
        filtered, ["complex", "sheet"]
    )

    table = filtered_display[[
        "id", "complex", "sheet", "comment", "author",
        "status", "created", "days_overdue_work",
        "vitro_guid", "vitro_version",
    ]].copy()

    table["_vitro_url"] = table.apply(
        lambda r: build_vitro_comment_url(
            r["vitro_guid"], r["vitro_version"], r["id"]
        ),
        axis=1,
    )
    table = table.drop(columns=["vitro_guid", "vitro_version"])

    table = table.rename(columns={
        "id": "ID",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "author": "Автор",
        "status": "Статус замечания",
        "created": "Создано",
        "days_overdue_work": "Просрочено (р.д.)",
        "_vitro_url": "Витро",
    })

    st.caption(
        f"Показано: **{len(filtered):,}** из **{n_total:,}**."
        .replace(",", " ")
    )

    st.dataframe(
        table.head(500),
        width='stretch', hide_index=True, height=600,
        column_config={
            "ID": st.column_config.NumberColumn(width="small"),
            "Комплект": st.column_config.TextColumn(width="medium"),
            "Лист": st.column_config.TextColumn(width="medium"),
            "Замечание": st.column_config.TextColumn(width="large"),
            "Автор": st.column_config.TextColumn(width="medium"),
            "Статус замечания": st.column_config.TextColumn(width="small"),
            "Создано": st.column_config.TextColumn(width="small"),
            "Просрочено (р.д.)": st.column_config.NumberColumn(
                width="small", format="%d"),
            "Витро": st.column_config.LinkColumn(
                "Витро",
                display_text="🔗 Открыть",
                width="small",
                help="Открыть замечание в Витро (подсветится на листе)",
            ),
        },
    )
    if len(filtered) > 500:
        st.caption(f"Показаны первые 500 из {len(filtered):,}."
                   .replace(",", " "))