# vitro/ui/summary.py
"""
📊 Сводка по проекту — главная витрина для Заказчика.

Структура (5 секций, каждая — 3 блока [2, 3, 2]):
  1. Объём замечаний — Всего + бублик / Активных + % / Закрыто и др.
  2. Листы РД       — Всего + бублик / A+B+C+% / И и др.
  3. Замечания на стороне АТП ТЛП — Всего + бублик / состав / просрочка.
  4. Замечания на стороне Заказчика — Всего + бублик / крупные / детали.
  5. Категории замечаний, оценка АТП ТЛП — Всего + бублик / плохие / хорошие.
  + Разрез по дисциплинам / разделам / комплектам.

Источник: `_load_all_categorized` из deadlines.py (единый).
Фильтры: дисциплина → раздел → комплект + расположение (4-й блок).
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

OURS_ACTIVE_FLAGS = [
    "new_overdue", "new_in_progress",
    "in_work_overdue", "in_work_in_progress",
    "rejected_overdue", "rejected_in_progress",
    "discussion_overdue", "discussion_in_progress",
]
OURS_OVERDUE_FLAGS = [
    "new_overdue", "in_work_overdue",
    "rejected_overdue", "discussion_overdue",
]
WAITING_FLAGS = [
    "waiting_customer_ontime",
    "waiting_customer",
    "waiting_customer_overdue",
]
CHRONIC_FLAG = "waiting_customer_chronic"
ABANDONED_FLAG = "abandoned"

DS_A = "A"
DS_ANNULLED = "АННУЛИРОВАНО"

STATUS_NEW = "Новое"
STATUS_IN_WORK = "Принято в работу"
STATUS_REJECTED = "Не принято"
STATUS_DISCUSSION = "К обсуждению"
STATUS_DONE = "Выполнено"

BLOCK_OPTIONS = {
    "Корпус 1":                    "1",
    "Корпус 2":                    "2",
    "Общие":                       "0",
    "Стилобат":                    "С",
    "Газовая котельная":           "ГК",
    "Генплан":                     "ГП",
    "Автомобильные дороги (УДС)":  "А",
}

# ---------------------------------------------------------------------------
#  Палитры
# ---------------------------------------------------------------------------
CAT_COLORS = {
    CAT_1:           "#C6EFCE",
    CAT_2:           "#FFEB9C",
    CAT_3:           "#BDD7EE",
    CAT_4:           "#FFC7CE",
    "Без категории": "#D9D9D9",
}

STATUS_COLORS = {
    "Новое":            "#1f77b4",
    "Принято в работу": "#17becf",
    "Не принято":       "#d62728",
    "К обсуждению":     "#ffdd57",
    "Выполнено":        "#2ca02c",
    "Закрыто":          "#e8e8e8",
    "Аннулировано":     "#000000",
    "Без статуса":      "#8c564b",
}

SHEET_COLORS = {
    "A — утверждён":              "#2ca02c",
    "B — к сдаче":                "#ffdd57",
    "C — в работе":               "#ff7f0e",
    "И — информационный":         "#e8e8e8",
    "На согласовании Заказчика":  "#17becf",
    "Готовится к загрузке":       "#9467bd",
    "Аннулировано":               "#7f7f7f",
}

EMOJI = {
    "blue":   "🔵",
    "globe":  "🌐",
    "red":    "🔴",
    "yellow": "🟡",
    "green":  "🟢",
    "white":  "⚪",
    "black":  "⚫",
    "brown":  "🟤",
    "orange": "🟠",
    "violet": "🟣",
}


# ---------------------------------------------------------------------------
#  Источник активных замечаний
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_categorized() -> pd.DataFrame:
    from vitro.ui.deadlines import _load_all_categorized as _src
    return _src()


# ---------------------------------------------------------------------------
#  Справочники
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _load_discipline_options() -> dict[str, str]:
    with get_conn() as conn:
        codes = [r["discipline"] for r in conn.execute(
            "SELECT DISTINCT discipline FROM documents "
            "WHERE discipline IS NOT NULL ORDER BY discipline")]
    return {c: f"{c} — {discipline_name(c)}" for c in codes}


@st.cache_data(ttl=600, show_spinner=False)
def _load_section_options(disciplines: tuple = ()) -> dict[str, str]:
    with get_conn() as conn:
        if disciplines:
            ph = ",".join("?" * len(disciplines))
            rows = conn.execute(f"""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                  AND discipline IN ({ph})
                ORDER BY section
            """, tuple(disciplines)).fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                ORDER BY section
            """).fetchall()
    return {r["section"]: r["section"] for r in rows}


@st.cache_data(ttl=600, show_spinner=False)
def _load_kit_options(disciplines: tuple = (),
                      sections: tuple = ()) -> dict[str, str]:
    with get_conn() as conn:
        where = ["c.code IS NOT NULL"]
        params: list = []

        if disciplines:
            where.append(
                f"c.discipline IN ({','.join('?' * len(disciplines))})")
            params += list(disciplines)

        if sections:
            ph = ",".join("?" * len(sections))
            where.append(f"""c.code IN (
                SELECT DISTINCT complex FROM documents
                WHERE section IN ({ph}) AND complex IS NOT NULL
            )""")
            params += list(sections)

        rows = conn.execute(f"""
            SELECT c.code, c.name FROM complexes c
            WHERE {' AND '.join(where)}
            ORDER BY c.code
        """, tuple(params)).fetchall()

    return {r["code"]: f"{r['code']} — {r['name']}"
                       if r["name"] else r["code"]
            for r in rows}


# ---------------------------------------------------------------------------
#  4-й блок шифра
# ---------------------------------------------------------------------------
def _extract_4th_block(complex_code) -> str:
    if not complex_code:
        return ""
    parts = str(complex_code).split("-")
    if len(parts) >= 4:
        return parts[3].strip().upper()
    return ""


def _filter_by_blocks(df: pd.DataFrame,
                       block_codes: tuple = ()) -> pd.DataFrame:
    if df.empty or not block_codes:
        return df
    blocks = df["complex"].apply(_extract_4th_block)
    return df[blocks.isin(block_codes)]


# ---------------------------------------------------------------------------
#  Фильтрация
# ---------------------------------------------------------------------------
def _filter_active(df: pd.DataFrame,
                    disciplines: tuple = (),
                    sections: tuple = (),
                    kits: tuple = (),
                    block_codes: tuple = ()) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        out = out[out["complex"].isin(kits)]
    if block_codes:
        out = _filter_by_blocks(out, block_codes)
    return out


def _filter_documents(df: pd.DataFrame,
                       disciplines: tuple = (),
                       sections: tuple = (),
                       kits: tuple = (),
                       block_codes: tuple = ()) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        out = out[out["complex"].isin(kits)]
    if block_codes:
        out = _filter_by_blocks(out, block_codes)
    return out


# ---------------------------------------------------------------------------
#  Документы
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_documents() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT id, leaf, discipline, section, complex,
                   status, revision, name, status_date, sheet_number
            FROM documents
        """, conn)


# ---------------------------------------------------------------------------
#  Общий объём
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_scale(disciplines: tuple = (),
                 sections: tuple = (),
                 kits: tuple = (),
                 block_codes: tuple = ()) -> dict:
    q = """
        SELECT
            COUNT(*) AS total_all,
            SUM(CASE WHEN c.status = 'Закрыто' THEN 1 ELSE 0 END)
                AS closed,
            SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END)
                AS annulled,
            SUM(CASE WHEN c.status IS NULL OR c.status = ''
                     THEN 1 ELSE 0 END) AS no_status
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE 1=1
    """
    params: list = []
    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if sections:
        q += f" AND d.section IN ({','.join('?' * len(sections))})"
        params += list(sections)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if block_codes:
        ors = [f"d.complex LIKE '%-{bc}-%'" for bc in block_codes]
        q += " AND (" + " OR ".join(ors) + ")"

    with get_conn() as conn:
        row = conn.execute(q, tuple(params)).fetchone()

    return {
        "total_all": _safe_int(row["total_all"]),
        "closed": _safe_int(row["closed"]),
        "annulled": _safe_int(row["annulled"]),
        "no_status": _safe_int(row["no_status"]),
    }


# ---------------------------------------------------------------------------
#  Утилиты
# ---------------------------------------------------------------------------
def _safe_int(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _fmt_num(n) -> str:
    return f"{_safe_int(n):,}".replace(",", " ")


def _metric(col, label, value, help_text=None):
    col.metric(
        label,
        value if isinstance(value, str) else _fmt_num(value),
        help=help_text,
    )


def _pie(fig_data: pd.DataFrame,
         names_col: str,
         values_col: str,
         color_map: dict,
         key: str) -> None:
    if fig_data.empty:
        st.caption("Нет данных для диаграммы.")
        return
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
        height=180,
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


# ===========================================================================
#  СЕКЦИЯ 1. Объём замечаний
# ===========================================================================
def _render_scale_section(scale: dict,
                            active_count: int,
                            active_df: pd.DataFrame) -> None:
    st.markdown("##### Объём замечаний")

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Всего замечаний", scale["total_all"],
            "Выгрузка из Витро: COUNT(comments).\n\n"
            "Включает: закрытые, аннулированные, без статуса, "
            "активные.",
        )
        st_dist = _load_status_distribution_by_filter(
            _filter_key(
                _LAST_FILTERS.get("disc_t", ()),
                _LAST_FILTERS.get("sect_t", ()),
                _LAST_FILTERS.get("kit_t", ()),
                _LAST_FILTERS.get("block_codes", ()),
            )
        )
        _pie(st_dist, "Статус", "Количество", STATUS_COLORS,
             "sum_pie_status_all")

    with col_mid:
        pct_done = (
            round((scale["closed"] + scale["annulled"])
                  / scale["total_all"] * 100, 1)
            if scale["total_all"] else 0.0
        )
        _metric(
            st, f"{EMOJI['blue']} Активных", active_count,
            "Выгрузка из Витро: замечания в 5 статусах — Новое, "
            "Принято в работу, Не принято, К обсуждению, Выполнено.\n\n"
            "Формула: Всего − Закрыто − Аннулировано − Без статуса.",
        )
        _metric(
            st, " % снято от всего", f"{pct_done}%",
            "Расчёт: (Закрыто + Аннулировано) / Всего × 100%.",
        )

    with col_right:
        _metric(
            st, f"{EMOJI['white']} Закрыто", scale["closed"],
            "Выгрузка из Витро: COUNT(comments WHERE status='Закрыто').",
        )
        _metric(
            st, f"{EMOJI['black']} Аннулировано", scale["annulled"],
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Аннулировано').",
        )
        _metric(
            st, f"{EMOJI['brown']} Без статуса", scale["no_status"],
            "Выгрузка из Витро: COUNT(comments WHERE status IS NULL "
            "OR status='').",
        )


# ===========================================================================
#  СЕКЦИЯ 2. Листы РД
# ===========================================================================
def _render_sheets_section(docs_df: pd.DataFrame) -> None:
    st.markdown("##### Листы РД")

    if docs_df.empty:
        st.info("Нет данных по листам.")
        return

    statuses = docs_df["status"].astype(str).str.strip().str.upper()

    total = len(docs_df)
    n_a = int((statuses == "A").sum())
    n_b = int((statuses == "B").sum())
    n_c = int((statuses == "C").sum())
    n_i = int((statuses == "И").sum())
    n_annulled = int((statuses == "АННУЛИРОВАНО").sum())
    n_review = int(statuses.isin(
        ["ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"]).sum())
    n_loading = int(statuses.isin(
        ["НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"]).sum())

    denominator = total - n_i - n_annulled
    pct_ab = (round((n_a + n_b) / denominator * 100, 1)
              if denominator > 0 else 0.0)

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Листов всего", total,
            "Выгрузка из Витро: COUNT(documents).",
        )
        sheet_dist = _sheet_distribution_from_df(docs_df)
        _pie(sheet_dist, "Статус листа", "Количество", SHEET_COLORS,
             "sum_pie_sheets")

    with col_mid:
        _metric(
            st, f"{EMOJI['green']} Статус A — утверждён", n_a,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='A').",
        )
        _metric(
            st, f"{EMOJI['yellow']} Статус B — к сдаче", n_b,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='B').",
        )
        _metric(
            st, f"{EMOJI['orange']} Статус C — в работе", n_c,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='C').",
        )
        _metric(
            st, " % готовых (A+B)", f"{pct_ab}%",
            "Расчёт: (A + B) / (Всего − И − Аннулировано) × 100%.",
        )

    with col_right:
        _metric(
            st, f"{EMOJI['white']} Статус И — информационный", n_i,
            "Выгрузка из Витро: COUNT(documents WHERE status='И').",
        )
        _metric(
            st, f"{EMOJI['globe']} На согласовании Заказчика",
            n_review,
            "Расчёт: «Для согласования» + «На рассмотрении».",
        )
        _metric(
            st, f"{EMOJI['violet']} Готовится к загрузке", n_loading,
            "Расчёт: «На корректировке» + «Размещено».",
        )
        _metric(
            st, f"{EMOJI['black']} Аннулировано", n_annulled,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='Аннулировано').",
        )


# ===========================================================================
#  СЕКЦИЯ 3. Замечания на стороне АТП ТЛП
# ===========================================================================
def _render_ours_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Замечания на стороне АТП ТЛП")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()
    mask_special = ds.isin([DS_A, DS_ANNULLED])
    clean = active_df[~mask_special]
    statuses = clean["status"]

    n_new = int((statuses == STATUS_NEW).sum())
    n_in_work = int((statuses == STATUS_IN_WORK).sum())
    n_rejected = int((statuses == STATUS_REJECTED).sum())
    n_disc = int((statuses == STATUS_DISCUSSION).sum())

    n_ours_total = n_new + n_in_work + n_rejected + n_disc

    flags = clean["category_flag"]
    mask_overdue = flags.isin(OURS_OVERDUE_FLAGS)
    mask_abandoned = flags == ABANDONED_FLAG
    n_overdue = int((mask_overdue | mask_abandoned).sum())

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Всего у АТП ТЛП", n_ours_total,
            "Расчёт: Новое + Принято в работу + Не принято + "
            "К обсуждению.",
        )
        pie_data = pd.DataFrame([
            {"Статус": STATUS_NEW,        "Количество": n_new},
            {"Статус": STATUS_IN_WORK,    "Количество": n_in_work},
            {"Статус": STATUS_REJECTED,   "Количество": n_rejected},
            {"Статус": STATUS_DISCUSSION, "Количество": n_disc},
        ])
        pie_data = pie_data[pie_data["Количество"] > 0]
        _pie(pie_data, "Статус", "Количество", STATUS_COLORS,
             "sum_pie_ours")

    with col_mid:
        _metric(
            st, f"{EMOJI['blue']} Новое", n_new,
            "Выгрузка из Витро: COUNT(comments WHERE status='Новое').",
        )
        _metric(
            st, f"{EMOJI['globe']} Принято в работу", n_in_work,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Принято в работу').",
        )
        _metric(
            st, f"{EMOJI['red']} Не принято", n_rejected,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Не принято').",
        )
        _metric(
            st, f"{EMOJI['yellow']} К обсуждению", n_disc,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='К обсуждению').",
        )

    with col_right:
        _metric(
            st, " из них Просрочено (>10 р.д.)", n_overdue,
            "Расчёт: замечания из состава «Всего у АТП ТЛП», "
            "у которых срок ответа (10 рабочих дней) истёк.",
        )


# ===========================================================================
#  СЕКЦИЯ 4. Замечания на стороне Заказчика
# ===========================================================================
def _render_customer_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Замечания на стороне Заказчика")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()

    mask_a = ds == DS_A
    mask_annul = ds == DS_ANNULLED
    mask_special = mask_a | mask_annul

    n_a_total = int(mask_a.sum())
    n_annul_total = int(mask_annul.sum())

    mask_done = active_df["status"] == STATUS_DONE
    mask_done_clean = mask_done & ~mask_special
    n_done_clean = int(mask_done_clean.sum())

    done = active_df[mask_done_clean]
    done_flags = done["category_flag"]

    n_ontime = int((done_flags == "waiting_customer_ontime").sum())
    n_waiting = int((done_flags == "waiting_customer").sum())
    n_overdue = int((done_flags == "waiting_customer_overdue").sum())
    n_chronic = int((done_flags == CHRONIC_FLAG).sum())

    n_overdue_review = n_waiting + n_overdue + n_chronic

    n_total = n_done_clean + n_a_total + n_annul_total

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Всего у заказчика", n_total,
            "Расчёт: со статусом Выполнено (без особых) "
            "+ К листам A + К аннулированным листам.",
        )
        pie_data = pd.DataFrame([
            {"Категория": "со статусом Выполнено",
             "Количество": n_done_clean},
            {"Категория": "К листам A",
             "Количество": n_a_total},
            {"Категория": "К аннулированным листам",
             "Количество": n_annul_total},
        ])
        pie_data = pie_data[pie_data["Количество"] > 0]
        _pie(
            pie_data, "Категория", "Количество",
            {
                "со статусом Выполнено":   "#2ca02c",
                "К листам A":              "#1f77b4",
                "К аннулированным листам": "#7f7f7f",
            },
            "sum_pie_customer",
        )

    with col_mid:
        _metric(
            st, f"{EMOJI['green']} со статусом Выполнено",
            n_done_clean,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Выполнено').\n\n"
            "Из них исключены замечания к листам A и аннулированным.",
        )
        _metric(
            st, f"{EMOJI['blue']} К листам A (утверждён)",
            n_a_total,
            "Расчёт: все замечания к листам со статусом A.",
        )
        _metric(
            st, f"{EMOJI['black']} К аннулированным листам",
            n_annul_total,
            "Расчёт: все замечания к аннулированным листам.",
        )

    with col_right:
        _metric(
            st, " из них срок рассмотрения не превышен",
            n_ontime,
            "Расчёт по датам: заказчик ещё в пределах "
            "10 рабочих дней с момента нашего ответа.",
        )
        _metric(
            st, " из них Просрочено рассмотрение (>10 р.д.)",
            n_overdue_review,
            "Расчёт: заказчик не рассмотрел наш ответ более "
            "10 рабочих дней.",
        )


# ===========================================================================
#  СЕКЦИЯ 5. Категории замечаний, оценка АТП ТЛП
# ===========================================================================
def _render_categories_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Категории замечаний, оценка АТП ТЛП")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()
    mask_special = ds.isin([DS_A, DS_ANNULLED])

    ours_statuses = [STATUS_NEW, STATUS_IN_WORK,
                     STATUS_REJECTED, STATUS_DISCUSSION]
    mask_ours = (
        active_df["status"].isin(ours_statuses) & ~mask_special
    )
    ours = active_df[mask_ours]

    counts = (
        ours["category"].fillna("Без категории")
        .replace("", "Без категории")
        .value_counts()
    )
    total = int(counts.sum())

    n_cat1 = int(counts.get(CAT_1, 0))
    n_cat2 = int(counts.get(CAT_2, 0))
    n_cat3 = int(counts.get(CAT_3, 0))
    n_cat4 = int(counts.get(CAT_4, 0))
    n_none = int(counts.get("Без категории", 0))

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Всего", total,
            "Расчёт: Новое + Принято в работу + Не принято + "
            "К обсуждению.\n\n"
            "Совпадает с «Всего у АТП ТЛП».",
        )
        pie_data = pd.DataFrame([
            {"Категория": CAT_1,           "Количество": n_cat1},
            {"Категория": CAT_2,           "Количество": n_cat2},
            {"Категория": CAT_3,           "Количество": n_cat3},
            {"Категория": CAT_4,           "Количество": n_cat4},
            {"Категория": "Без категории", "Количество": n_none},
        ])
        pie_data = pie_data[pie_data["Количество"] > 0]
        _pie(pie_data, "Категория", "Количество", CAT_COLORS,
             "sum_pie_categories")

    with col_mid:
        _metric(
            st, f"{EMOJI['red']} Не принято/нарушение", n_cat4,
            "Не принимаем: нарушение ТНПА.",
        )
        _metric(
            st, f"{EMOJI['blue']} Доп.требование", n_cat3,
            "Дополнительное требование, отсутствует в ТЗ.",
        )

    with col_right:
        _metric(
            st, f"{EMOJI['green']} Принято/корректное", n_cat1,
            "Замечание корректное, влияет на СМР. Принимаем.",
        )
        _metric(
            st, f"{EMOJI['yellow']} Формальное", n_cat2,
            "Формальное, не влияет на СМР.",
        )
        _metric(
            st, f"{EMOJI['white']} Без категории", n_none,
            "Ещё не разобрано специалистами.",
        )


# ===========================================================================
#  Бублик: статусы замечаний
# ===========================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _load_status_distribution_by_filter(filter_key: tuple) -> pd.DataFrame:
    disciplines, sections, kits, block_codes = filter_key
    q = """
        SELECT
            CASE WHEN c.status IS NULL OR c.status = ''
                 THEN 'Без статуса' ELSE c.status END AS "Статус",
            COUNT(*) AS "Количество"
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE 1=1
    """
    params: list = []
    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if sections:
        q += f" AND d.section IN ({','.join('?' * len(sections))})"
        params += list(sections)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if block_codes:
        ors = [f"d.complex LIKE '%-{bc}-%'" for bc in block_codes]
        q += " AND (" + " OR ".join(ors) + ")"
    q += ' GROUP BY "Статус" ORDER BY "Количество" DESC'
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


def _filter_key(disc_t: tuple, sect_t: tuple,
                 kit_t: tuple, block_codes: tuple) -> tuple:
    return (
        tuple(sorted(disc_t)),
        tuple(sorted(sect_t)),
        tuple(sorted(kit_t)),
        tuple(sorted(block_codes)),
    )


_LAST_FILTERS: dict = {
    "disc_t": (), "sect_t": (), "kit_t": (), "block_codes": (),
}


def _sheet_distribution_from_df(docs_df: pd.DataFrame) -> pd.DataFrame:
    if docs_df.empty:
        return pd.DataFrame()

    def _group(raw) -> str:
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

    grp = docs_df["status"].apply(_group).value_counts().reset_index()
    grp.columns = ["Статус листа", "Количество"]
    return grp


# ===========================================================================
#  Разрез: сводная таблица
# ===========================================================================
def _build_summary_rows(group_col: str,
                         active_df: pd.DataFrame) -> pd.DataFrame:
    """
    Сводная таблица для разреза:
      [группа] | Открытых | Новое | Не принято
      | К обсуждению | Принято в работу | Выполнено

    Замечания к листам A и к аннулированным — исключены
    (как в верхних секциях).
    """
    if active_df.empty:
        return pd.DataFrame()

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()
    mask_special = ds.isin([DS_A, DS_ANNULLED])
    clean = active_df[~mask_special]

    if clean.empty:
        return pd.DataFrame()

    statuses_order = [
        STATUS_NEW, STATUS_REJECTED, STATUS_DISCUSSION,
        STATUS_IN_WORK, STATUS_DONE,
    ]

    rows = []
    for grp, sub in clean.groupby(group_col, dropna=True):
        if not grp:
            continue

        row = {group_col: grp}
        sstatus = sub["status"]

        counts = {}
        for st in statuses_order:
            counts[st] = int((sstatus == st).sum())

        row["open_total"] = sum(counts.values())
        for st in statuses_order:
            row[st] = counts[st]

        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    result = result.fillna(0)

    group_col_names = {
        "discipline": "Код",
        "section":    "Раздел",
        "complex":    "Комплект",
    }
    result = result.rename(columns={group_col: group_col_names[group_col]})
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_discipline(disciplines: tuple = (),
                                  sections: tuple = (),
                                  kits: tuple = (),
                                  block_codes: tuple = ()) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             block_codes)
    return _build_summary_rows("discipline", active)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_section(disciplines: tuple = (),
                               sections: tuple = (),
                               kits: tuple = (),
                               block_codes: tuple = ()) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             block_codes)
    return _build_summary_rows("section", active)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_complex(disciplines: tuple = (),
                               sections: tuple = (),
                               kits: tuple = (),
                               block_codes: tuple = ()) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             block_codes)
    return _build_summary_rows("complex", active)


def _render_summary_table(df: pd.DataFrame,
                            group_label: str,
                            level: str) -> None:
    """
    Сводная таблица разреза:
      Код/Раздел/Комплект | [Наименование] | Открытых | 5 статусов
      + строка ИТОГО в конце.
    """
    if df.empty:
        st.info("Нет данных по заданным фильтрам.")
        return

    view = df.copy()

    # Наименование (для дисциплин)
    if level == "discipline" and "Код" in view.columns:
        view.insert(
            1, "Наименование",
            view["Код"].map(lambda c: discipline_name(c) if c else ""),
        )

    # Красивые названия колонок с эмодзи
    rename_map = {
        "open_total":       "Открытых",
        STATUS_NEW:         f"{EMOJI['blue']} Новое",
        STATUS_REJECTED:    f"{EMOJI['red']} Не принято",
        STATUS_DISCUSSION:  f"{EMOJI['yellow']} К обсуждению",
        STATUS_IN_WORK:     f"{EMOJI['globe']} Принято в работу",
        STATUS_DONE:        f"{EMOJI['green']} Выполнено",
    }
    view = view.rename(columns=rename_map)

    # Порядок колонок
    cols_order = [group_label]
    if level == "discipline":
        cols_order.append("Наименование")
    cols_order += [
        "Открытых",
        f"{EMOJI['blue']} Новое",
        f"{EMOJI['red']} Не принято",
        f"{EMOJI['yellow']} К обсуждению",
        f"{EMOJI['globe']} Принято в работу",
        f"{EMOJI['green']} Выполнено",
    ]
    cols_order = [c for c in cols_order if c in view.columns]
    view = view[cols_order]

    # Сортировка по «Открытых» (убывание)
    if "Открытых" in view.columns:
        view = view.sort_values("Открытых", ascending=False)

    # Строка ИТОГО — в конец
    totals_row = {group_label: "ИТОГО"}
    if level == "discipline":
        totals_row["Наименование"] = ""
    for c in view.columns:
        if c in (group_label, "Наименование"):
            continue
        totals_row[c] = int(view[c].sum())

    view_with_total = pd.concat(
        [view, pd.DataFrame([totals_row])],
        ignore_index=True,
    )

    # Стилизация: жирный + серый фон для строки ИТОГО
    def _style_row(row):
        if row.name == len(view_with_total) - 1:
            return ["font-weight: bold; "
                    "background-color: #f0f0f0"] * len(row)
        return [""] * len(row)

    styled = view_with_total.style.apply(_style_row, axis=1)

    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        height=600,
    )

    st.caption(
        f"Показано **{len(view)}** групп. "
        f"Строка **ИТОГО** — внизу таблицы. "
        f"Замечания к листам A и к аннулированным в разрез "
        f"не входят."
    )


# ===========================================================================
#  Точка входа
# ===========================================================================
def render():
    st.header("Сводка по проекту")
    st.caption(
        "Ключевые показатели проекта, распределение замечаний и листов, "
        "разрез по дисциплинам, разделам и комплектам."
    )

    disc_options = _load_discipline_options()

    with st.expander("Фильтры", expanded=True):
        c1, c2, c3 = st.columns(3)

        with c1:
            sel_disc_labels = st.multiselect(
                "Дисциплина",
                options=list(disc_options.values()),
                placeholder="Все дисциплины",
                key="sum_disc",
            )
            sel_disc = [code for code, label in disc_options.items()
                        if label in sel_disc_labels]

        section_options = _load_section_options(
            tuple(sel_disc) if sel_disc else ())

        with c2:
            if section_options:
                sel_section_labels = st.multiselect(
                    "Раздел",
                    options=list(section_options.values()),
                    placeholder="Все разделы",
                    key="sum_section",
                )
                sel_section = [
                    code for code, label in section_options.items()
                    if label in sel_section_labels
                ]
            else:
                sel_section = []
                st.multiselect(
                    "Раздел", options=[],
                    placeholder="Разделы не применимы",
                    disabled=True, key="sum_section_empty",
                )

        kit_options = _load_kit_options(
            tuple(sel_disc) if sel_disc else (),
            tuple(sel_section) if sel_section else (),
        )

        with c3:
            sel_kit_labels = st.multiselect(
                "Комплект",
                options=list(kit_options.values()),
                placeholder="Все комплекты",
                key="sum_kit",
            )
            sel_kit = [code for code, label in kit_options.items()
                        if label in sel_kit_labels]

        sel_blocks = st.multiselect(
            "Расположение (4-й блок шифра)",
            options=list(BLOCK_OPTIONS.keys()),
            placeholder="Все комплекты",
            key="sum_blocks",
            help="Фильтр по 4-му блоку шифра комплекта. "
                 "Можно выбрать несколько.",
        )
        block_codes = tuple(BLOCK_OPTIONS[b] for b in sel_blocks)

    disc_t = tuple(sel_disc)
    sect_t = tuple(sel_section)
    kit_t = tuple(sel_kit)

    _LAST_FILTERS["disc_t"] = disc_t
    _LAST_FILTERS["sect_t"] = sect_t
    _LAST_FILTERS["kit_t"] = kit_t
    _LAST_FILTERS["block_codes"] = block_codes

    with st.spinner("Загрузка данных..."):
        active_all = _load_all_categorized()
        active_filtered = _filter_active(
            active_all, disc_t, sect_t, kit_t, block_codes)
        docs_all = _load_documents()
        docs_filtered = _filter_documents(
            docs_all, disc_t, sect_t, kit_t, block_codes)
        scale = _load_scale(disc_t, sect_t, kit_t, block_codes)

    active_count = len(active_filtered)

    _render_scale_section(scale, active_count, active_filtered)
    st.divider()
    _render_sheets_section(docs_filtered)
    st.divider()
    _render_ours_section(active_filtered)
    st.divider()
    _render_customer_section(active_filtered)
    st.divider()
    _render_categories_section(active_filtered)
    st.divider()

    st.markdown("##### Разрез по дисциплинам, разделам, комплектам")

    tab_disc, tab_sect, tab_cx = st.tabs([
        "По дисциплинам", "По разделам", "По комплектам",
    ])
    with tab_disc:
        df = _load_summary_by_discipline(disc_t, sect_t, kit_t,
                                          block_codes)
        _render_summary_table(df, "Код", "discipline")
    with tab_sect:
        df = _load_summary_by_section(disc_t, sect_t, kit_t,
                                       block_codes)
        _render_summary_table(df, "Раздел", "section")
    with tab_cx:
        df = _load_summary_by_complex(disc_t, sect_t, kit_t,
                                       block_codes)
        _render_summary_table(df, "Комплект", "complex")