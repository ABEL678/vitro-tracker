# vitro/ui/overview.py
"""
📊 Обзор — главная витрина проекта.
KPI, сводка по дисциплинам, графики с кнопками скачивания.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from vitro.sqlite_db import get_conn, db_stats
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

CAT_COLORS = {
    CAT_1: "#C6EFCE",
    CAT_2: "#FFEB9C",
    CAT_3: "#BDD7EE",
    CAT_4: "#FFC7CE",
    "Без категории": "#D9D9D9",
}

STATUS_COLORS = {
    "Закрыто":          "#2E7D32",
    "Выполнено":        "#A5D6A7",
    "Новое":            "#64B5F6",
    "Принято в работу": "#FFD54F",
    "К обсуждению":     "#FFB74D",
    "Не принято":       "#E57373",
    "Аннулировано":     "#BDBDBD",
    "Не указан":        "#EEEEEE",
}

SHEET_STATUS_COLORS = {
    "A — Утверждён":      "#2E7D32",
    "B — Готов к сдаче":  "#A5D6A7",
    "И — Информационный": "#81C784",
    "C — В работе":       "#F48FB1",
    "На согласовании":    "#FFB74D",
    "Корректировка":      "#E57373",
    "Аннулировано":       "#BDBDBD",
    "Нет статуса":        "#EEEEEE",
    "Прочее":             "#90CAF9",
}


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_kpi() -> dict:
    with get_conn() as conn:
        docs  = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        comms = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        kits  = conn.execute("SELECT COUNT(*) FROM complexes").fetchone()[0]

        cat_rows = conn.execute("""
            SELECT
                SUM(CASE WHEN category = ? THEN 1 ELSE 0 END) AS c1,
                SUM(CASE WHEN category = ? THEN 1 ELSE 0 END) AS c2,
                SUM(CASE WHEN category = ? THEN 1 ELSE 0 END) AS c3,
                SUM(CASE WHEN category = ? THEN 1 ELSE 0 END) AS c4,
                SUM(CASE WHEN category IS NULL OR category = '' THEN 1 ELSE 0 END) AS c_none
            FROM comments
        """, (CAT_1, CAT_2, CAT_3, CAT_4)).fetchone()

    return {
        "complexes": kits,
        "documents": docs,
        "comments":  comms,
        "c1": cat_rows["c1"] or 0,
        "c2": cat_rows["c2"] or 0,
        "c3": cat_rows["c3"] or 0,
        "c4": cat_rows["c4"] or 0,
        "c_none": cat_rows["c_none"] or 0,
    }


def _render_kpi():
    k = _load_kpi()

    st.markdown("##### 📦 Масштаб проекта")
    c1, c2, c3 = st.columns(3)
    c1.metric("Комплектов", k["complexes"])
    c2.metric("Листов РД", f"{k['documents']:,}".replace(",", " "))
    c3.metric("Замечаний", f"{k['comments']:,}".replace(",", " "))

    st.markdown("##### 🏷 Категории замечаний")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Принято/корректное",        f"{k['c1']:,}".replace(",", " "))
    c2.metric("Формальное/нет влияния",    f"{k['c2']:,}".replace(",", " "))
    c3.metric("Доп.требование/нет в ТЗ",   f"{k['c3']:,}".replace(",", " "))
    c4.metric("Не принято/нарушение ТНПА", f"{k['c4']:,}".replace(",", " "))
    c5.metric("Без категории",             f"{k['c_none']:,}".replace(",", " "))


# ---------------------------------------------------------------------------
#  Сводка по дисциплинам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_discipline_summary() -> pd.DataFrame:
    with get_conn() as conn:
        comms = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                COUNT(DISTINCT d.complex) AS complexes,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS annulled,
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_1,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_2,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_3,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_4,
                SUM(CASE WHEN c.category IS NULL OR c.category = '' THEN 1 ELSE 0 END) AS cat_none
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
            ORDER BY d.discipline
        """, conn, params=(CAT_1, CAT_2, CAT_3, CAT_4))

        docs = pd.read_sql("""
            SELECT
                discipline,
                COUNT(DISTINCT id) AS docs_total,
                SUM(CASE WHEN UPPER(status) = 'A' THEN 1 ELSE 0 END) AS doc_a,
                SUM(CASE WHEN UPPER(status) = 'B' THEN 1 ELSE 0 END) AS doc_b,
                SUM(CASE WHEN UPPER(status) = 'C' THEN 1 ELSE 0 END) AS doc_c,
                SUM(CASE WHEN status = 'И' THEN 1 ELSE 0 END) AS doc_info,
                SUM(CASE WHEN status IN ('Для согласования','На рассмотрении') THEN 1 ELSE 0 END) AS doc_review,
                SUM(CASE WHEN status = 'На корректировке' THEN 1 ELSE 0 END) AS doc_fix,
                SUM(CASE WHEN status = 'Аннулировано' THEN 1 ELSE 0 END) AS doc_annulled,
                SUM(CASE WHEN status IS NULL OR status = '' OR status = 'Размещено' THEN 1 ELSE 0 END) AS doc_misc
            FROM documents
            WHERE discipline IS NOT NULL
            GROUP BY discipline
        """, conn)

    df = comms.merge(docs, on="discipline", how="outer").fillna(0)

    df["Выполнено, %"] = df.apply(
        lambda r: round((r["closed"] + r["annulled"]) / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)

    df["Листы готовы, %"] = df.apply(
        lambda r: round((r["doc_a"] + r["doc_b"] + r["doc_info"])
                        / r["docs_total"] * 100, 1)
        if r["docs_total"] else 0.0, axis=1)

    return df.rename(columns={
        "discipline":  "Код",
        "complexes":   "Комплектов",
        "total":       "Всего замечаний",
        "closed":      "Закрыто",
        "annulled":    "Аннулировано",
        "active":      "Активных",
        "cat_1":       "Принято",
        "cat_2":       "Формальное",
        "cat_3":       "Доп.треб.",
        "cat_4":       "Не принято",
        "cat_none":    "Без категории",
        "docs_total":  "Всего листов",
        "doc_a":       "Статус A",
        "doc_b":       "Статус B",
        "doc_c":       "Статус C",
        "doc_info":    "Информац.",
        "doc_review":  "На согласовании",
        "doc_fix":     "Корректировка",
        "doc_annulled":"Аннул. листы",
        "doc_misc":    "Прочее",
    })


def _render_discipline_table(df: pd.DataFrame):
    st.subheader("Сводка по дисциплинам")
    if df.empty:
        st.info("Нет данных.")
        return

    view = df.copy()
    view.insert(1, "Наименование", view["Код"].map(discipline_name))

    ordered = [
        "Код", "Наименование", "Комплектов",
        "Всего замечаний", "Активных", "Закрыто", "Аннулировано", "Выполнено, %",
        "Всего листов",
        "Статус A", "Статус B", "Статус C", "Информац.",
        "На согласовании", "Корректировка", "Аннул. листы", "Прочее",
        "Листы готовы, %",
        "Принято", "Формальное", "Доп.треб.", "Не принято", "Без категории",
    ]
    view = view[[c for c in ordered if c in view.columns]]

    st.dataframe(
        view, use_container_width=True, hide_index=True,
        column_config={
            "Выполнено, %":    st.column_config.ProgressColumn(
                "Выполнено, %", min_value=0, max_value=100, format="%.1f%%"),
            "Листы готовы, %": st.column_config.ProgressColumn(
                "Листы готовы, %", min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    totals = {
        "Код": "ИТОГО",
        "Наименование": "",
        "Комплектов":       view["Комплектов"].sum(),
        "Всего замечаний":  view["Всего замечаний"].sum(),
        "Активных":         view["Активных"].sum(),
        "Закрыто":          view["Закрыто"].sum(),
        "Аннулировано":     view["Аннулировано"].sum(),
        "Всего листов":     view["Всего листов"].sum(),
        "Статус A":         view["Статус A"].sum(),
        "Статус B":         view["Статус B"].sum(),
        "Статус C":         view["Статус C"].sum(),
        "Информац.":        view["Информац."].sum(),
        "На согласовании":  view["На согласовании"].sum(),
        "Корректировка":    view["Корректировка"].sum(),
        "Аннул. листы":     view["Аннул. листы"].sum(),
        "Прочее":           view["Прочее"].sum(),
        "Принято":          view["Принято"].sum(),
        "Формальное":       view["Формальное"].sum(),
        "Доп.треб.":        view["Доп.треб."].sum(),
        "Не принято":       view["Не принято"].sum(),
        "Без категории":    view["Без категории"].sum(),
    }
    total = totals["Всего замечаний"]
    totals["Выполнено, %"] = (
        round((totals["Закрыто"] + totals["Аннулировано"]) / total * 100, 1)
        if total else 0.0)
    dtotal = totals["Всего листов"]
    totals["Листы готовы, %"] = (
        round((totals["Статус A"] + totals["Статус B"] + totals["Информац."])
              / dtotal * 100, 1)
        if dtotal else 0.0)

    st.markdown("**Итоги:**")
    st.dataframe(pd.DataFrame([totals]), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
#  Загрузчики для графиков
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_status_distribution() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                CASE WHEN status IS NULL OR status = '' THEN 'Не указан' ELSE status END AS "Статус",
                COUNT(*) AS "Количество"
            FROM comments GROUP BY "Статус" ORDER BY "Количество" DESC
        """, conn)


@st.cache_data(ttl=300, show_spinner=False)
def _load_sheet_status_distribution() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                CASE
                    WHEN UPPER(status) = 'A' THEN 'A — Утверждён'
                    WHEN UPPER(status) = 'B' THEN 'B — Готов к сдаче'
                    WHEN UPPER(status) = 'C' THEN 'C — В работе'
                    WHEN status = 'И' THEN 'И — Информационный'
                    WHEN status IN ('Для согласования', 'На рассмотрении') THEN 'На согласовании'
                    WHEN status = 'На корректировке' THEN 'Корректировка'
                    WHEN status = 'Аннулировано' THEN 'Аннулировано'
                    WHEN status IS NULL OR status = '' THEN 'Нет статуса'
                    ELSE 'Прочее'
                END AS "Статус листа",
                COUNT(*) AS "Количество"
            FROM documents GROUP BY "Статус листа" ORDER BY "Количество" DESC
        """, conn)


@st.cache_data(ttl=300, show_spinner=False)
def _load_top_complexes(limit: int = 15) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(f"""
            SELECT d.complex AS "Комплект", d.discipline AS "Дисциплина",
                   COUNT(*) AS "Активных"
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
            GROUP BY d.complex, d.discipline
            ORDER BY "Активных" DESC LIMIT {limit}
        """, conn)


# ---------------------------------------------------------------------------
#  Графики
# ---------------------------------------------------------------------------
def _render_charts(df_disc: pd.DataFrame):
    col1, col2 = st.columns(2)

    with col1:
        if not df_disc.empty:
            df_long = df_disc.melt(
                id_vars="Код",
                value_vars=["Закрыто", "Аннулировано", "Активных"],
                var_name="Показатель", value_name="Количество",
            )
            color_map = {
                "Закрыто":      STATUS_COLORS["Закрыто"],
                "Аннулировано": STATUS_COLORS["Аннулировано"],
                "Активных":     STATUS_COLORS["Новое"],
            }
            fig = px.bar(
                df_long, x="Код", y="Количество", color="Показатель",
                barmode="stack",
                color_discrete_map=color_map,
                category_orders={"Показатель": ["Закрыто", "Аннулировано", "Активных"]},
                title="Замечания по дисциплинам (по статусам)",
            )
            fig.update_layout(legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_замечания_по_дисциплинам", "ov_bar")

    with col2:
        dist = _load_status_distribution()
        if not dist.empty:
            fig = px.pie(
                dist, names="Статус", values="Количество", hole=0.45,
                color="Статус", color_discrete_map=STATUS_COLORS,
                title="Распределение замечаний по статусам",
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_распределение_по_статусам", "ov_status")

    col3, col4 = st.columns(2)

    with col3:
        top = _load_top_complexes(limit=15)
        if not top.empty:
            fig = px.bar(
                top.sort_values("Активных"),
                x="Активных", y="Комплект", orientation="h",
                color="Дисциплина",
                title="Топ-15 комплектов по активным замечаниям",
            )
            fig.update_layout(yaxis_title="", height=500)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_топ15_комплектов", "ov_top")

    with col4:
        sheets = _load_sheet_status_distribution()
        if not sheets.empty:
            fig = px.pie(
                sheets, names="Статус листа", values="Количество", hole=0.45,
                color="Статус листа", color_discrete_map=SHEET_STATUS_COLORS,
                title="Статусы листов чертежей",
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_статусы_листов", "ov_sheets")


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📊 Обзор")

    _render_kpi()
    st.divider()

    df_disc = _load_discipline_summary()
    _render_discipline_table(df_disc)
    st.divider()

    _render_charts(df_disc)

    st.divider()
    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_b:
        if st.button("🔄 Обновить данные", use_container_width=True):
            st.cache_data.clear()
            st.rerun()