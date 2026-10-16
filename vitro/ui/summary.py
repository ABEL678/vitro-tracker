# vitro/ui/summary.py
"""
📊 Сводка по проекту — главная витрина для Заказчика.

Структура:
  1. Объём замечаний — 6 плиток.
  2. Листы РД — 8 плиток.
  3. Замечания на стороне АТП ТЛП — 8 плиток.
  4. Замечания на стороне Заказчика — 6 плиток.
  5. Категории замечаний, оценка АТП ТЛП — 6 плиток.
  + Разрез по дисциплинам / разделам / комплектам.

Источник: `_load_all_categorized` (deadlines.py).
Фильтры: дисциплина → раздел → расположение → комплект.
"""

import io
from datetime import datetime

import openpyxl
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

BLOCK_OPTIONS = {
    "Корпус 1":                    "1",
    "Корпус 2":                    "2",
    "Общие":                       "0",
    "Стилобат":                    "С",
    "Газовая котельная":           "ГК",
    "Генплан":                     "ГП",
    "Автомобильные дороги (УДС)":  "А",
}

# Статусы замечаний (сырые из Витрокад) — для фильтра «Статус замечания"
REMARK_STATUS_OPTIONS = [
    "Новое",
    "Принято в работу",
    "Не принято",
    "К обсуждению",
    "Выполнено",
]

# Группы статусов листов — для фильтра «Статус листа"
# Совпадают с бубликом Секции 2 и функцией _group()
SHEET_GROUP_OPTIONS = [
    "A — утверждён",
    "B — к сдаче",
    "C — в работе",
    "И — информационный",
    "На согласовании Заказчика",
    "Готовится к загрузке",
    "Аннулировано",
    "Прочее",
]

# Обратный маппинг: группа → список сырых статусов (для SQL-фильтра)
_SHEET_GROUP_TO_RAW = {
    "A — утверждён":              ["A"],
    "B — к сдаче":                ["B"],
    "C — в работе":               ["C"],
    "И — информационный":         ["И"],
    "На согласовании Заказчика":  ["ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"],
    "Готовится к загрузке":       ["НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"],
    "Аннулировано":               ["АННУЛИРОВАНО"],
    # «Прочее» обрабатывается отдельно — через NOT IN
}

# Все известные сырые статусы — для группы «Прочее»
_ALL_KNOWN_RAW_STATUSES = [
    "A", "B", "C", "И",
    "ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ",
    "НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО",
    "АННУЛИРОВАНО",
]

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
#  Источник
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
                      sections: tuple = (),
                      block_codes: tuple = ()) -> dict[str, str]:
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

    result = {r["code"]: f"{r['code']} — {r['name']}"
                       if r["name"] else r["code"]
            for r in rows}

    # Фильтр по расположению (4-й блок) — в Python
    if block_codes:
        filtered = {}
        for code, label in result.items():
            block = _extract_4th_block(code)
            if block in block_codes:
                filtered[code] = label
        return filtered
    return result


# ---------------------------------------------------------------------------
#  4-й блок
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
                    block_codes: tuple = (),
                    remark_statuses: tuple = (),
                    sheet_groups: tuple = ()) -> pd.DataFrame:
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
    if remark_statuses:
        out = out[out["status"].isin(remark_statuses)]
    if sheet_groups:
        out = out[out["doc_status"].apply(_doc_status_group).isin(sheet_groups)]
    return out


def _filter_documents(df: pd.DataFrame,
                       disciplines: tuple = (),
                       sections: tuple = (),
                       kits: tuple = (),
                       block_codes: tuple = (),
                       sheet_groups: tuple = ()) -> pd.DataFrame:
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
    if sheet_groups:
        out = out[out["status"].apply(_doc_status_group).isin(sheet_groups)]
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
#  SQL-хелпер для групп статусов листов
# ---------------------------------------------------------------------------
def _sheet_groups_to_sql(sheet_groups: tuple, doc_alias: str = "d"):
    """
    Возвращает (sql_condition, params) для фильтра по группам статусов листов.
    sql_condition — строка вида " AND (...)" или "".
    Использует NOT IN для группы «Прочее».
    """
    if not sheet_groups:
        return "", []

    raw = []
    for g in sheet_groups:
        raw += _SHEET_GROUP_TO_RAW.get(g, [])

    conditions = []
    params: list = []

    if raw:
        conditions.append(
            f"{doc_alias}.status IN ({','.join('?' * len(raw))})"
        )
        params += raw

    if "Прочее" in sheet_groups:
        placeholders = ",".join("?" * len(_ALL_KNOWN_RAW_STATUSES))
        conditions.append(
            f"({doc_alias}.status NOT IN ({placeholders}) "
            f"OR {doc_alias}.status IS NULL "
            f"OR {doc_alias}.status = '')"
        )
        params += _ALL_KNOWN_RAW_STATUSES

    if not conditions:
        return "", []

    return " AND (" + " OR ".join(conditions) + ")", params


# ---------------------------------------------------------------------------
#  Общий объём
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_scale(disciplines: tuple = (),
                 sections: tuple = (),
                 kits: tuple = (),
                 block_codes: tuple = (),
                 remark_statuses: tuple = (),
                 sheet_groups: tuple = ()) -> dict:
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
    if remark_statuses:
        q += f" AND c.status IN ({','.join('?' * len(remark_statuses))})"
        params += list(remark_statuses)
    if sheet_groups:
        cond, extra_params = _sheet_groups_to_sql(sheet_groups, "d")
        if cond:
            q += cond
            params += extra_params

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
            "Включает: закрытые, аннулированные, без статуса, активные.",
        )
        st_dist = _load_status_distribution_by_filter(
            _filter_key(
                _LAST_FILTERS.get("disc_t", ()),
                _LAST_FILTERS.get("sect_t", ()),
                _LAST_FILTERS.get("kit_t", ()),
                _LAST_FILTERS.get("block_codes", ()),
                _LAST_FILTERS.get("remark_statuses", ()),
                _LAST_FILTERS.get("sheet_groups", ()),
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
            "Выгрузка из Витро: COUNT(documents WHERE status='A').",
        )
        _metric(
            st, f"{EMOJI['yellow']} Статус B — к сдаче", n_b,
            "Выгрузка из Витро: COUNT(documents WHERE status='B').",
        )
        _metric(
            st, f"{EMOJI['orange']} Статус C — в работе", n_c,
            "Выгрузка из Витро: COUNT(documents WHERE status='C').",
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

    ours = active_df[active_df["holder"] == "ours"]

    if ours.empty:
        st.info("Нет замечаний на нашей стороне.")
        return

    n_total = len(ours)
    n_new = int((ours["our_status"] == "new").sum())
    n_in_work = int((ours["our_status"] == "in_work").sum())
    n_rejected = int((ours["our_status"] == "rejected").sum())
    n_disc = int((ours["our_status"] == "discussion").sum())

    n_in_time = int((ours["our_bucket"] == "≤10").sum())
    n_overdue = int(ours["our_bucket"].isin(
        ["10–30", "30–90", ">90"]).sum())

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Всего у АТП ТЛП", n_total,
            "Расчёт: Новое + Принято в работу + Не принято + "
            "К обсуждению.\n\n"
            f"Арифметика: {_fmt_num(n_new)} + {_fmt_num(n_in_work)} + "
            f"{_fmt_num(n_rejected)} + {_fmt_num(n_disc)} = "
            f"{_fmt_num(n_total)}.\n\n"
            "Не включает замечания к листам A и к аннулированным — "
            "они в блоке «Замечания на стороне Заказчика».",
        )

        pie_data = pd.DataFrame([
            {"Статус": "Новое",           "Количество": n_new},
            {"Статус": "Принято в работу", "Количество": n_in_work},
            {"Статус": "Не принято",      "Количество": n_rejected},
            {"Статус": "К обсуждению",    "Количество": n_disc},
        ])
        pie_data = pie_data[pie_data["Количество"] > 0]
        _pie(pie_data, "Статус", "Количество", STATUS_COLORS,
             "sum_pie_ours")

    with col_mid:
        _metric(
            st, f"{EMOJI['blue']} Новое", n_new,
            "Выгрузка из Витро: COUNT(comments WHERE status='Новое').\n\n"
            "Из них исключены замечания к листам A и к аннулированным.\n\n"
            "Входит в «Всего у АТП ТЛП».",
        )
        _metric(
            st, f"{EMOJI['red']} Не принято", n_rejected,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Не принято').\n\n"
            "Входит в «Всего у АТП ТЛП».",
        )
        _metric(
            st, f"{EMOJI['yellow']} К обсуждению", n_disc,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='К обсуждению').\n\n"
            "Входит в «Всего у АТП ТЛП».",
        )
        _metric(
            st, f"{EMOJI['globe']} Принято в работу", n_in_work,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Принято в работу').\n\n"
            "Входит в «Всего у АТП ТЛП».",
        )

    with col_right:
        _metric(
            st, " из них срок ответа не превышен", n_in_time,
            "Расчёт: замечания из состава «Всего у АТП ТЛП», "
            "у которых срок ответа (10 рабочих дней) не истёк.\n\n"
            "Арифметика: количество замечаний с бакетом «≤10».\n\n"
            "Входит в «Всего у АТП ТЛП».",
        )
        _metric(
            st, " из них Просрочено (>10 р.д.)", n_overdue,
            "Расчёт: замечания из состава «Всего у АТП ТЛП», "
            "у которых срок ответа (10 рабочих дней) истёк.\n\n"
            "Арифметика: количество замечаний с бакетами "
            "«10–30» + «30–90» + «>90».\n\n"
            "Входит в «Всего у АТП ТЛП».",
        )


# ===========================================================================
#  СЕКЦИЯ 4. Замечания на стороне Заказчика
# ===========================================================================
def _render_customer_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Замечания на стороне Заказчика")

    if active_df.empty:
        st.info("Нет данных.")
        return

    customer = active_df[active_df["holder"] == "customer"]
    customer_a = active_df[active_df["holder"] == "customer_a"]
    annulled = active_df[active_df["holder"] == "annulled"]

    n_done = len(customer)
    n_a_total = len(customer_a)
    n_annul_total = len(annulled)
    n_total = n_done + n_a_total + n_annul_total

    if customer.empty and customer_a.empty and annulled.empty:
        st.info("Нет замечаний на стороне заказчика.")
        return

    n_in_time = int((customer["customer_bucket"] == "≤10").sum())
    n_overdue = int(customer["customer_bucket"].isin(
        ["10–30", "30–90", ">90"]).sum())

    col_left, col_mid, col_right = st.columns([2, 3, 2])

    with col_left:
        _metric(
            st, "Всего у заказчика", n_total,
            "Расчёт: со статусом Выполнено + К листам A "
            "+ К аннулированным листам.\n\n"
            f"Арифметика: {_fmt_num(n_done)} + "
            f"{_fmt_num(n_a_total)} + {_fmt_num(n_annul_total)} = "
            f"{_fmt_num(n_total)}.",
        )

        pie_data = pd.DataFrame([
            {"Категория": "со статусом Выполнено", "Количество": n_done},
            {"Категория": "К листам A",            "Количество": n_a_total},
            {"Категория": "К аннулированным",      "Количество": n_annul_total},
        ])
        pie_data = pie_data[pie_data["Количество"] > 0]
        _pie(
            pie_data, "Категория", "Количество",
            {
                "со статусом Выполнено": "#2ca02c",
                "К листам A":            "#1f77b4",
                "К аннулированным":      "#7f7f7f",
            },
            "sum_pie_customer",
        )

    with col_mid:
        _metric(
            st, f"{EMOJI['green']} со статусом Выполнено", n_done,
            "Выгрузка из Витро: "
            "COUNT(comments WHERE status='Выполнено').\n\n"
            "Из них исключены замечания к листам A и к аннулированным.",
        )
        _metric(
            st, f"{EMOJI['blue']} К листам A (утверждён)", n_a_total,
            "Все замечания к листам со статусом A (утверждён), "
            "независимо от статуса замечания.\n\n"
            "Ожидаем снятия от заказчика.",
        )
        _metric(
            st, f"{EMOJI['black']} К аннулированным листам",
            n_annul_total,
            "Все замечания к аннулированным листам.\n\n"
            "Ожидаем снятия от заказчика.",
        )

    with col_right:
        _metric(
            st, " из них срок рассмотрения не превышен", n_in_time,
            "Расчёт: заказчик ещё в пределах 10 рабочих дней с момента "
            "нашего ответа.\n\n"
            "Арифметика: количество замечаний с бакетом «≤10».\n\n"
            "Входит в «со статусом Выполнено».",
        )
        _metric(
            st, " из них Просрочено рассмотрение (>10 р.д.)",
            n_overdue,
            "Расчёт: заказчик не рассмотрел наш ответ более "
            "10 рабочих дней.\n\n"
            "Арифметика: количество замечаний с бакетами "
            "«10–30» + «30–90» + «>90».\n\n"
            "Входит в «со статусом Выполнено».",
        )


# ===========================================================================
#  СЕКЦИЯ 5. Категории замечаний
# ===========================================================================
def _render_categories_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Категории замечаний, оценка АТП ТЛП")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ours = active_df[active_df["holder"] == "ours"]

    if ours.empty:
        st.info("Нет замечаний на нашей стороне.")
        return

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
            "Совпадает с «Всего у АТП ТЛП» в блоке выше.",
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
#  Бублик статусов замечаний
# ===========================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _load_status_distribution_by_filter(filter_key: tuple) -> pd.DataFrame:
    (disciplines, sections, kits, block_codes,
     remark_statuses, sheet_groups) = filter_key
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
    if remark_statuses:
        q += f" AND c.status IN ({','.join('?' * len(remark_statuses))})"
        params += list(remark_statuses)
    if sheet_groups:
        cond, extra_params = _sheet_groups_to_sql(sheet_groups, "d")
        if cond:
            q += cond
            params += extra_params
    q += ' GROUP BY "Статус" ORDER BY "Количество" DESC'
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


def _doc_status_group(raw) -> str:
    """Сырой doc_status → группа (как в бублике Секции 2)."""
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


def _filter_key(disc_t: tuple, sect_t: tuple,
                 kit_t: tuple, block_codes: tuple,
                 remark_statuses: tuple = (),
                 sheet_groups: tuple = ()) -> tuple:
    return (
        tuple(sorted(disc_t)),
        tuple(sorted(sect_t)),
        tuple(sorted(kit_t)),
        tuple(sorted(block_codes)),
        tuple(sorted(remark_statuses)),
        tuple(sorted(sheet_groups)),
    )


_LAST_FILTERS: dict = {
    "disc_t": (), "sect_t": (), "kit_t": (), "block_codes": (),
    "remark_statuses": (), "sheet_groups": (),
}


def _sheet_distribution_from_df(docs_df: pd.DataFrame) -> pd.DataFrame:
    if docs_df.empty:
        return pd.DataFrame()

    grp = docs_df["status"].apply(_doc_status_group).value_counts().reset_index()
    grp.columns = ["Статус листа", "Количество"]
    return grp


# ===========================================================================
#  Разрез
# ===========================================================================
def _build_summary_rows(group_col: str,
                         active_df: pd.DataFrame,
                         docs_df: pd.DataFrame) -> pd.DataFrame:
    if docs_df.empty and active_df.empty:
        return pd.DataFrame()

    # --- Листы ---
    if not docs_df.empty:
        doc_rows = []
        for grp, sub in docs_df.groupby(group_col, dropna=True):
            if not grp:
                continue
            total = len(sub)
            statuses = sub["status"].astype(str).str.strip().str.upper()
            a = int((statuses == "A").sum())
            b = int((statuses == "B").sum())
            c = int((statuses == "C").sum())
            i = int((statuses == "И").sum())
            annulled = int((statuses == "АННУЛИРОВАНО").sum())
            pct_ab = (round((a + b) / total * 100, 1)
                      if total > 0 else 0.0)
            doc_rows.append({
                group_col: grp,
                "docs_total": total,
                "doc_a": a,
                "doc_b": b,
                "doc_c": c,
                "doc_i": i,
                "doc_annulled": annulled,
                "doc_other": total - a - b - c - i - annulled,
                "pct_ab": pct_ab,
            })
        docs_grp = pd.DataFrame(doc_rows)
    else:
        docs_grp = pd.DataFrame()

    # --- Замечания (ours + customer) ---
    if not active_df.empty:
        rows = []
        for grp, sub in active_df.groupby(group_col, dropna=True):
            if not grp:
                continue

            ours = sub[sub["holder"] == "ours"]
            customer = sub[sub["holder"] == "customer"]

            # Метрики по ours — используем our_status и our_bucket
            if not ours.empty:
                our_status = ours["our_status"]
                our_bucket = ours["our_bucket"]
                cat_counts = (
                    ours["category"].fillna("Без категории")
                    .replace("", "Без категории")
                    .value_counts()
                )

                n_new = int((our_status == "new").sum())
                n_in_work = int((our_status == "in_work").sum())
                n_rejected = int((our_status == "rejected").sum())
                n_discussion = int((our_status == "discussion").sum())
                n_in_time = int((our_bucket == "≤10").sum())
                n_overdue = int(our_bucket.isin(
                    ["10–30", "30–90", ">90"]).sum())
            else:
                # ours пусто для этой группы — ставим нули
                n_new = n_in_work = n_rejected = n_discussion = 0
                n_in_time = n_overdue = 0
                cat_counts = pd.Series(dtype=int)

            # Метрики по customer
            n_customer = len(customer) if not customer.empty else 0

            # active_total = сумма всех открытых
            active_total = (n_new + n_in_work + n_rejected +
                            n_discussion + n_customer)

            rows.append({
                group_col: grp,
                "active_total": active_total,
                "n_new": n_new,
                "n_in_work": n_in_work,
                "n_rejected": n_rejected,
                "n_discussion": n_discussion,
                "n_customer": n_customer,
                "n_in_time": n_in_time,
                "n_overdue": n_overdue,
                "cat_1": int(cat_counts.get(CAT_1, 0)),
                "cat_2": int(cat_counts.get(CAT_2, 0)),
                "cat_3": int(cat_counts.get(CAT_3, 0)),
                "cat_4": int(cat_counts.get(CAT_4, 0)),
                "cat_none": int(cat_counts.get("Без категории", 0)),
            })

        active_grp = pd.DataFrame(rows) if rows else pd.DataFrame()
    else:
        active_grp = pd.DataFrame()

    if docs_grp.empty and active_grp.empty:
        return pd.DataFrame()
    if docs_grp.empty:
        result = active_grp
    elif active_grp.empty:
        result = docs_grp
    else:
        result = docs_grp.merge(active_grp, on=group_col, how="outer")
    result = result.fillna(0)

    group_col_names = {
        "discipline": "Код",
        "section":    "Раздел",
        "complex":    "Комплект",
    }
    result = result.rename(columns={group_col: group_col_names[group_col]})
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_discipline(
    disciplines: tuple = (),
    sections: tuple = (),
    kits: tuple = (),
    block_codes: tuple = (),
    remark_statuses: tuple = (),
    sheet_groups: tuple = (),
) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             block_codes, remark_statuses, sheet_groups)
    docs = _load_documents()
    docs = _filter_documents(docs, disciplines, sections, kits,
                              block_codes, sheet_groups)
    return _build_summary_rows("discipline", active, docs)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_section(
    disciplines: tuple = (),
    sections: tuple = (),
    kits: tuple = (),
    block_codes: tuple = (),
    remark_statuses: tuple = (),
    sheet_groups: tuple = (),
) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             block_codes, remark_statuses, sheet_groups)
    docs = _load_documents()
    docs = _filter_documents(docs, disciplines, sections, kits,
                              block_codes, sheet_groups)
    return _build_summary_rows("section", active, docs)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_complex(
    disciplines: tuple = (),
    sections: tuple = (),
    kits: tuple = (),
    block_codes: tuple = (),
    remark_statuses: tuple = (),
    sheet_groups: tuple = (),
) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             block_codes, remark_statuses, sheet_groups)
    docs = _load_documents()
    docs = _filter_documents(docs, disciplines, sections, kits,
                              block_codes, sheet_groups)
    result = _build_summary_rows("complex", active, docs)

    # Добавляем «Наименование» из complexes
    if not result.empty and "Комплект" in result.columns:
        with get_conn() as conn:
            names_df = pd.read_sql(
                'SELECT code AS "Комплект", name AS "Наименование" '
                "FROM complexes",
                conn,
            )
        result = result.merge(names_df, on="Комплект", how="left")
        result["Наименование"] = result["Наименование"].fillna("")

    return result


def _export_summary_to_excel(df: pd.DataFrame, group_label: str, level: str) -> bytes:
    """Формирует красиво оформленный XLSX с бледными цветами и переносом строк."""
    import re
    buf = io.BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=group_label)
        ws = writer.sheets[group_label]

        # --- Бледные цвета заголовков ---
        COLOR_HEADER_NEW = "D6E4F0"        # бледно-синий
        COLOR_HEADER_REJECTED = "FCE4E4"   # бледно-красный
        COLOR_HEADER_DISCUSSION = "FFF2CC" # бледно-жёлтый
        COLOR_HEADER_IN_WORK = "D0ECE7"    # бледно-бирюзовый
        COLOR_HEADER_DONE = "D9EAD3"       # бледно-зелёный
        COLOR_HEADER_OTHER = "F0F0F0"      # бледно-серый
        COLOR_TOTAL_ROW = "F0F0F0"         # серый для ИТОГО
        COLOR_BLACK = "000000"             # чёрный текст

        # --- Фиксированная ширина колонок ---
        ws.column_dimensions["A"].width = 20
        ws.column_dimensions["B"].width = 50
        ws.column_dimensions["C"].width = 14
        ws.column_dimensions["D"].width = 14
        ws.column_dimensions["E"].width = 14
        ws.column_dimensions["F"].width = 14
        ws.column_dimensions["G"].width = 14
        ws.column_dimensions["H"].width = 14
        ws.column_dimensions["I"].width = 14
        ws.column_dimensions["J"].width = 14

        # --- Закрепить заголовок ---
        ws.freeze_panes = "A2"

        # --- Границы на всю таблицу ---
        from openpyxl.styles import Border, Side
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin'),
        )
        last_row = ws.max_row
        last_col = ws.max_column
        for r in range(1, last_row + 1):
            for c in range(1, last_col + 1):
                ws.cell(row=r, column=c).border = thin_border

        # --- Заголовки: убрать эмодзи, бледный цвет, чёрный текст, по центру, wrap ---
        from openpyxl.styles import PatternFill, Font, Alignment
        header_fill_map = {
            "Новое": COLOR_HEADER_NEW,
            "Не принято": COLOR_HEADER_REJECTED,
            "К обсуждению": COLOR_HEADER_DISCUSSION,
            "Принято в работу": COLOR_HEADER_IN_WORK,
            "Выполнено": COLOR_HEADER_DONE,
        }

        for c in range(1, last_col + 1):
            cell = ws.cell(row=1, column=c)

            # Убрать эмодзи из заголовка
            if isinstance(cell.value, str):
                clean_name = re.sub(r'[\U0001F300-\U0001F9FF]', '', cell.value).strip()
                cell.value = clean_name

            # Определяем цвет заголовка
            col_name = str(cell.value)
            fill_color = None
            for key, color in header_fill_map.items():
                if key in col_name:
                    fill_color = color
                    break

            if fill_color:
                cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
                cell.font = Font(bold=True, color=COLOR_BLACK)
                cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)
            else:
                cell.fill = PatternFill(start_color=COLOR_HEADER_OTHER, end_color=COLOR_HEADER_OTHER, fill_type="solid")
                cell.font = Font(bold=True, color=COLOR_BLACK)
                cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)

        # --- Перенос строк в колонке B (наименование) ---
        for r in range(2, last_row + 1):
            cell = ws.cell(row=r, column=2)
            cell.alignment = Alignment(wrapText=True)

        # --- Выделение строки ИТОГО (последняя строка) ---
        total_fill = PatternFill(start_color=COLOR_TOTAL_ROW, end_color=COLOR_TOTAL_ROW, fill_type="solid")
        for c in range(1, last_col + 1):
            cell = ws.cell(row=last_row, column=c)
            cell.fill = total_fill
            cell.font = Font(bold=True)

        # --- Автофильтр ---
        ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(last_col)}{last_row}"

    buf.seek(0)
    return buf.getvalue()


def _render_summary_table(df: pd.DataFrame,
                          group_label: str,
                          level: str) -> None:
    if df.empty:
        st.info("Нет данных по заданным фильтрам.")
        return

    view = df.copy()

    # --- Колонка 2: «Полное наименование» ---
    if level == "discipline" and "Код" in view.columns:
        view.insert(1, "Полное наименование",
                    view["Код"].map(lambda c: discipline_name(c) if c else ""))
    elif level == "section":
        view.insert(1, "Полное наименование", "")
    elif level == "complex" and "Наименование" in view.columns:
        view = view.rename(columns={"Наименование": "Полное наименование"})
    else:
        view.insert(1, "Полное наименование", "")

    # --- Приводим числовые колонки к int ---
    for c in ["active_total", "n_new", "n_in_work", "n_rejected",
              "n_discussion", "n_customer"]:
        if c in view.columns:
            view[c] = view[c].fillna(0).astype(int)

    # --- Формируем финальные колонки ---
    view = view.rename(columns={
        "active_total": "Открытых замечаний",
        "n_new": "🔵 Новое",
        "n_rejected": "🔴 Не принято",
        "n_discussion": "🟡 К обсуждению",
        "n_in_work": "🌐 Принято в работу",
        "n_customer": "🟢 Выполнено",
    })

    # Добавляем пустую колонку «Пояснения»
    view["Пояснения"] = ""

    # --- Вычисляем "Всего у АТП ТЛП" = Новое + Принято в работу + Не принято + К обсуждению ---
    if "🔵 Новое" in view.columns and "🌐 Принято в работу" in view.columns:
        view["Всего у АТП ТЛП"] = (
            view["🔵 Новое"].fillna(0) +
            view["🌐 Принято в работу"].fillna(0) +
            view["🔴 Не принято"].fillna(0) +
            view["🟡 К обсуждению"].fillna(0)
        ).astype(int)

    # Оставляем только колонок нового формата
    cols_order = [
        group_label,
        "Полное наименование",
        "Открытых замечаний",
        "Всего у АТП ТЛП",
        "🔵 Новое",
        "🔴 Не принято",
        "🟡 К обсуждению",
        "🌐 Принято в работу",
        "🟢 Выполнено",
        "Пояснения",
    ]
    cols_order = [c for c in cols_order if c in view.columns]
    view = view[cols_order]

    # Сортировка по «Открытых замечаний»
    if "Открытых замечаний" in view.columns:
        view = view.sort_values("Открытых замечаний", ascending=False)

    # ===== Строка ИТОГО =====
    totals_row = {group_label: "ИТОГО"}
    totals_row["Полное наименование"] = ""
    totals_row["Пояснения"] = ""

    for c in view.columns:
        if c in (group_label, "Полное наименование", "Пояснения"):
            continue
        totals_row[c] = int(view[c].fillna(0).sum())

    view_with_total = pd.concat(
        [view, pd.DataFrame([totals_row])],
        ignore_index=True,
    )

    # --- Стилизация: жирный + серый фон для ИТОГО ---
    def _style_row(row):
        if row.name == len(view_with_total) - 1:
            return ["font-weight: bold; background-color: #f0f0f0"] * len(row)
        return [""] * len(row)

    # --- Отображение: все строки (строка ИТОГО должна быть видна) ---
    display_df = view_with_total.copy()
    display_count = len(display_df)  # ← считаем до применения стиля
    display_df = display_df.style.apply(_style_row, axis=1)

    # --- Кнопка выгрузки в Excel (полные данные) ---
    st.download_button(
        "📥 Выгрузить в Excel",
        data=_export_summary_to_excel(view_with_total, group_label, level),
        file_name=f"Разрез_{group_label}_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key=f"export_{level}",
    )

    # --- Таблица с пагинацией ---
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=max(500, min(display_count * 45 + 100, 1200)),
    )

    st.caption(
        f"Всего строк: **{len(view)}**. "
        f"Строка **ИТОГО** — внизу таблицы. "
        f"Для выгрузки нажмите «📥 Выгрузить в Excel»."
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
        c1, c2, c3, c4 = st.columns(4)

        # 1. Дисциплина
        with c1:
            sel_disc_labels = st.multiselect(
                "Дисциплина",
                options=list(disc_options.values()),
                placeholder="Все",
                key="sum_disc",
            )
            sel_disc = [code for code, label in disc_options.items()
                        if label in sel_disc_labels]

        # 2. Раздел
        section_options = _load_section_options(
            tuple(sel_disc) if sel_disc else ())
        with c2:
            if section_options:
                sel_section_labels = st.multiselect(
                    "Раздел",
                    options=list(section_options.values()),
                    placeholder="Все",
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
                    placeholder="—",
                    disabled=True, key="sum_section_empty",
                )

        # 3. Расположение
        with c3:
            sel_blocks = st.multiselect(
                "Расположение",
                options=list(BLOCK_OPTIONS.keys()),
                placeholder="Все",
                key="sum_blocks",
            )
            block_codes = tuple(BLOCK_OPTIONS[b] for b in sel_blocks)

        # 4. Комплект (зависит от всех трёх)
        kit_options = _load_kit_options(
            tuple(sel_disc) if sel_disc else (),
            tuple(sel_section) if sel_section else (),
            block_codes,
        )
        with c4:
            sel_kit_labels = st.multiselect(
                "Комплект",
                options=list(kit_options.values()),
                placeholder="Все",
                key="sum_kit",
            )
            sel_kit = [code for code, label in kit_options.items()
                        if label in sel_kit_labels]

        st.write("")
        c5, c6 = st.columns(2)
        with c5:
            sel_remark_statuses = st.multiselect(
                "Статус замечания",
                options=REMARK_STATUS_OPTIONS,
                placeholder="Все",
                key="sum_remark_status",
            )
        with c6:
            sel_sheet_groups = st.multiselect(
                "Статус листа",
                options=SHEET_GROUP_OPTIONS,
                placeholder="Все",
                key="sum_sheet_group",
            )

    disc_t = tuple(sel_disc)
    sect_t = tuple(sel_section)
    kit_t = tuple(sel_kit)
    remark_statuses_t = tuple(sel_remark_statuses) if sel_remark_statuses else ()
    sheet_groups_t = tuple(sel_sheet_groups) if sel_sheet_groups else ()

    _LAST_FILTERS["disc_t"] = disc_t
    _LAST_FILTERS["sect_t"] = sect_t
    _LAST_FILTERS["kit_t"] = kit_t
    _LAST_FILTERS["block_codes"] = block_codes
    _LAST_FILTERS["remark_statuses"] = remark_statuses_t
    _LAST_FILTERS["sheet_groups"] = sheet_groups_t

    with st.spinner("Загрузка данных..."):
        active_all = _load_all_categorized()
        active_filtered = _filter_active(
            active_all, disc_t, sect_t, kit_t, block_codes,
            remark_statuses_t, sheet_groups_t)
        docs_all = _load_documents()
        docs_filtered = _filter_documents(
            docs_all, disc_t, sect_t, kit_t, block_codes,
            sheet_groups_t)
        scale = _load_scale(
            disc_t, sect_t, kit_t, block_codes,
            remark_statuses_t, sheet_groups_t)

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
        df = _load_summary_by_discipline(
            disc_t, sect_t, kit_t, block_codes,
            remark_statuses_t, sheet_groups_t)
        _render_summary_table(df, "Код", "discipline")
    with tab_sect:
        df = _load_summary_by_section(
            disc_t, sect_t, kit_t, block_codes,
            remark_statuses_t, sheet_groups_t)
        _render_summary_table(df, "Раздел", "section")
    with tab_cx:
        df = _load_summary_by_complex(
            disc_t, sect_t, kit_t, block_codes,
            remark_statuses_t, sheet_groups_t)
        _render_summary_table(df, "Комплект", "complex")