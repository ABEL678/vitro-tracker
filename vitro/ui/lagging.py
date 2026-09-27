# vitro/ui/lagging.py
"""
⚠️ Отстающие — проблемные комплекты.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


@st.cache_data(ttl=600, show_spinner=False)
def _load_discipline_options() -> dict[str, str]:
    with get_conn() as conn:
        codes = [r["discipline"] for r in conn.execute(
            "SELECT DISTINCT discipline FROM documents "
            "WHERE discipline IS NOT NULL ORDER BY discipline")]
    return {c: f"{c} — {discipline_name(c)}" for c in codes}


@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_stats(disciplines: tuple = ()) -> pd.DataFrame:
    q_docs = """
        SELECT
            d.complex AS complex,
            d.discipline AS discipline,
            COUNT(DISTINCT d.id) AS docs_total,
            SUM(CASE WHEN UPPER(d.status) = 'A' THEN 1 ELSE 0 END) AS doc_a,
            SUM(CASE WHEN UPPER(d.status) = 'B' THEN 1 ELSE 0 END) AS doc_b,
            SUM(CASE WHEN UPPER(d.status) = 'C' THEN 1 ELSE 0 END) AS doc_c,
            SUM(CASE WHEN d.status = 'И' THEN 1 ELSE 0 END) AS doc_info
        FROM documents d
        WHERE d.complex IS NOT NULL
    """
    params_docs: list = []
    if disciplines:
        q_docs += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params_docs += list(disciplines)
    q_docs += " GROUP BY d.complex, d.discipline"

    with get_conn() as conn:
        docs_df = pd.read_sql(q_docs, conn, params=params_docs)

    q_comments = """
        SELECT
            d.complex AS complex,
            COUNT(c.id) AS comments_total,
            SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS comments_closed,
            SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS comments_annulled,
            SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS comments_active
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE d.complex IS NOT NULL
    """
    params_comments: list = []
    if disciplines:
        q_comments += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params_comments += list(disciplines)
    q_comments += " GROUP BY d.complex"

    with get_conn() as conn:
        comments_df = pd.read_sql(q_comments, conn, params=params_comments)

    df = docs_df.merge(comments_df, on="complex", how="outer").fillna(0)

    for col in ["docs_total", "doc_a", "doc_b", "doc_c", "doc_info",
                "comments_total", "comments_closed", "comments_annulled",
                "comments_active"]:
        df[col] = df[col].astype(int)

    df["comment_pct"] = df.apply(
        lambda r: round((r["comments_closed"] + r["comments_annulled"])
                        / r["comments_total"] * 100, 1)
        if r["comments_total"] else 0.0, axis=1)
    df["doc_pct"] = df.apply(
        lambda r: round((r["doc_a"] + r["doc_b"] + r["doc_info"])
                        / r["docs_total"] * 100, 1)
        if r["docs_total"] else 0.0, axis=1)

    return df


def _render_low_pct(df: pd.DataFrame):
    st.markdown("### 📉 Комплекты с низким % выполнения замечаний")
    if df.empty:
        st.info("Нет данных.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        min_comments = st.number_input("Минимум замечаний в комплекте",
                                       min_value=1, max_value=2000,
                                       value=20, step=5, key="lag_min_comments")
    with c2:
        max_pct = st.slider("Максимум % выполнения", min_value=0,
                            max_value=100, value=50, step=5, key="lag_max_pct")
    with c3:
        limit = st.number_input("Топ N", min_value=5, max_value=100,
                                value=20, step=5, key="lag_limit_pct")

    filtered = df[(df["comments_total"] >= min_comments) &
                  (df["comment_pct"] <= max_pct)].copy()

    if filtered.empty:
        st.info(f"Нет комплектов с ≥{min_comments} замечаний и выполнением ≤{max_pct}%.")
        return

    top = filtered.sort_values("comment_pct", ascending=True).head(int(limit))
    chart_df = top.sort_values("comment_pct", ascending=False)

    fig = px.bar(
        chart_df, x="comment_pct", y="complex", orientation="h",
        color="comment_pct",
        color_continuous_scale=["#E57373", "#FFB74D", "#FFD54F"],
        title=f"Топ-{limit} отстающих по % выполнения (≤{max_pct}%)",
        text="comment_pct",
        labels={"comment_pct": "% выполнения", "complex": "Комплект"},
    )
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    fig.update_layout(height=max(400, 25 * len(chart_df)),
                      coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Отстающие_по_проценту", "lag_pct")

    table = top[["complex", "discipline", "comments_total", "comments_active",
                 "comments_closed", "comments_annulled", "comment_pct",
                 "docs_total", "doc_pct"]].rename(columns={
        "complex": "Комплект", "discipline": "Дисциплина",
        "comments_total": "Всего замечаний", "comments_active": "Активных",
        "comments_closed": "Закрыто", "comments_annulled": "Аннулировано",
        "comment_pct": "% выполнения", "docs_total": "Листов",
        "doc_pct": "% листов"})
    st.dataframe(table, use_container_width=True, hide_index=True,
                 column_config={
                     "% выполнения": st.column_config.ProgressColumn(
                         "% выполнения", min_value=0, max_value=100, format="%.1f%%"),
                     "% листов": st.column_config.ProgressColumn(
                         "% листов", min_value=0, max_value=100, format="%.1f%%"),
                 })


def _render_high_active(df: pd.DataFrame):
    st.markdown("### 📊 Комплекты с наибольшим количеством активных замечаний")
    if df.empty:
        st.info("Нет данных.")
        return

    c1, c2 = st.columns(2)
    with c1:
        min_active = st.number_input("Минимум активных", min_value=1,
                                     max_value=2000, value=10, step=5,
                                     key="lag_min_active")
    with c2:
        limit = st.number_input("Топ N", min_value=5, max_value=100,
                                value=20, step=5, key="lag_limit_active")

    filtered = df[df["comments_active"] >= min_active].copy()
    if filtered.empty:
        st.info(f"Нет комплектов с ≥{min_active} активных.")
        return

    top = filtered.sort_values("comments_active", ascending=False).head(int(limit))
    chart_df = top.sort_values("comments_active", ascending=True)

    fig = px.bar(
        chart_df, x="comments_active", y="complex", orientation="h",
        color="comment_pct",
        color_continuous_scale=["#E57373", "#FFB74D", "#A5D6A7"],
        title=f"Топ-{limit} комплектов по активным замечаниям",
        text="comments_active",
        labels={"comments_active": "Активных", "complex": "Комплект",
                "comment_pct": "% выполнения"},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(400, 25 * len(chart_df)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Отстающие_по_активным", "lag_active")

    table = top[["complex", "discipline", "comments_active", "comments_total",
                 "comment_pct", "docs_total", "doc_pct"]].rename(columns={
        "complex": "Комплект", "discipline": "Дисциплина",
        "comments_active": "Активных", "comments_total": "Всего замечаний",
        "comment_pct": "% выполнения", "docs_total": "Листов",
        "doc_pct": "% листов"})
    st.dataframe(table, use_container_width=True, hide_index=True,
                 column_config={
                     "% выполнения": st.column_config.ProgressColumn(
                         "% выполнения", min_value=0, max_value=100, format="%.1f%%"),
                     "% листов": st.column_config.ProgressColumn(
                         "% листов", min_value=0, max_value=100, format="%.1f%%"),
                 })


@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_details(complex_code: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT c.id AS "ID",
                   REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS "Лист",
                   d.name AS "Название листа",
                   c.comment AS "Замечание",
                   c.status AS "Статус",
                   c.author AS "Автор",
                   strftime('%d.%m.%Y %H:%M:%S', c.created) AS "Создано",
                   c.category AS "Категория"
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE d.complex = ?
            ORDER BY CASE c.status
                WHEN 'Новое' THEN 1 WHEN 'Принято в работу' THEN 2
                WHEN 'Не принято' THEN 3 WHEN 'К обсуждению' THEN 4
                WHEN 'Выполнено' THEN 5 WHEN 'Закрыто' THEN 6 ELSE 7
            END, d.leaf, c.id
            LIMIT 500
        """, conn, params=(complex_code,))


def _render_drilldown(df: pd.DataFrame):
    st.markdown("### 🔍 Детали комплекта")
    if df.empty:
        st.info("Нет данных.")
        return

    complexes = sorted(df["complex"].dropna().unique())
    sel = st.selectbox("Выберите комплект", options=complexes,
                       key="lag_drill_complex",
                       placeholder="Начните вводить шифр...")
    if not sel:
        return

    row = df[df["complex"] == sel].iloc[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Всего замечаний", int(row["comments_total"]))
    c2.metric("Активных", int(row["comments_active"]))
    c3.metric("Закрыто", int(row["comments_closed"]))
    c4.metric("% выполнения", f"{row['comment_pct']}%")
    c5.metric("Листов", int(row["docs_total"]))

    st.caption(f"**Дисциплина:** {row['discipline']} — "
               f"{discipline_name(row['discipline'])} · **Листы:** "
               f"A={int(row['doc_a'])}, B={int(row['doc_b'])}, "
               f"C={int(row['doc_c'])}, И={int(row['doc_info'])} · "
               f"**% листов:** {row['doc_pct']}%")

    st.markdown(f"**Замечания комплекта `{sel}`** (первые 500)")
    details = _load_complex_details(sel)
    if details.empty:
        st.info("У комплекта нет замечаний.")
        return

    st.dataframe(details, use_container_width=True, hide_index=True,
                 height=500,
                 column_config={
                     "Замечание": st.column_config.TextColumn(width="large"),
                     "Создано": st.column_config.TextColumn(width="medium"),
                 })


def render():
    st.header("⚠️ Отстающие комплекты")
    st.caption("Комплекты с низким % выполнения и/или большим количеством "
               "активных замечаний.")

    disc_options = _load_discipline_options()
    c_disc, _ = st.columns([2, 3])
    with c_disc:
        sel_disc_labels = st.multiselect(
            "Дисциплина", options=list(disc_options.values()),
            placeholder="Все дисциплины", key="lag_disc")
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    df = _load_complex_stats(tuple(sel_disc) if sel_disc else ())
    if df.empty:
        st.info("Нет данных по комплектам.")
        return

    tab_pct, tab_active, tab_drill = st.tabs([
        "📉 По % выполнения", "📊 По активным", "🔍 Детали комплекта"])

    with tab_pct:
        _render_low_pct(df)
    with tab_active:
        _render_high_active(df)
    with tab_drill:
        _render_drilldown(df)