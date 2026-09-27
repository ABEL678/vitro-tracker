# vitro/ui/complexes.py
"""
🏗 Комплекты — таблица всех комплектов с пресетами для РП.
"""

import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"


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


# ---------------------------------------------------------------------------
#  Данные по комплектам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_table(disciplines: tuple = (), sections: tuple = ()) -> pd.DataFrame:
    # 1. Листы
    q_docs = """
        SELECT
            d.complex AS complex,
            d.discipline AS discipline,
            d.section AS section,
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
    if sections:
        q_docs += f" AND d.section IN ({','.join('?' * len(sections))})"
        params_docs += list(sections)
    q_docs += " GROUP BY d.complex, d.discipline, d.section"

    with get_conn() as conn:
        docs_df = pd.read_sql(q_docs, conn, params=params_docs)

    # 2. Замечания + последнее движение
    q_comments = """
        SELECT
            d.complex AS complex,
            COUNT(c.id) AS comments_total,
            SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS comments_closed,
            SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS comments_annulled,
            SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
                     THEN 1 ELSE 0 END) AS comments_active,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_1,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_2,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_3,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_4,
            SUM(CASE WHEN c.category IS NULL OR c.category = '' THEN 1 ELSE 0 END) AS cat_none,
            MAX(c.category_date) AS last_category_date
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE d.complex IS NOT NULL
    """
    params_comments: list = [CAT_1, CAT_2, CAT_3, CAT_4]
    if disciplines:
        q_comments += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params_comments += list(disciplines)
    if sections:
        q_comments += f" AND d.section IN ({','.join('?' * len(sections))})"
        params_comments += list(sections)
    q_comments += " GROUP BY d.complex"

    with get_conn() as conn:
        comments_df = pd.read_sql(q_comments, conn, params=params_comments)

    # 3. Названия комплектов
    with get_conn() as conn:
        complexes_df = pd.read_sql(
            "SELECT code, name FROM complexes", conn
        ).rename(columns={"code": "complex", "name": "complex_name"})

    # 4. Объединение
    df = docs_df.merge(comments_df, on="complex", how="outer").fillna(0)
    df = df.merge(complexes_df, on="complex", how="left")

    for col in ["docs_total", "doc_a", "doc_b", "doc_c", "doc_info",
                "comments_total", "comments_closed", "comments_annulled",
                "comments_active", "cat_1", "cat_2", "cat_3", "cat_4", "cat_none"]:
        df[col] = df[col].astype(int)

    df["comment_pct"] = df.apply(
        lambda r: round((r["comments_closed"] + r["comments_annulled"])
                        / r["comments_total"] * 100, 1)
        if r["comments_total"] else 0.0, axis=1)
    df["doc_pct"] = df.apply(
        lambda r: round((r["doc_a"] + r["doc_b"] + r["doc_info"])
                        / r["docs_total"] * 100, 1)
        if r["docs_total"] else 0.0, axis=1)

    # Цветовой статус
    def _status_color(pct):
        if pct < 30:   return "🔴"
        if pct < 60:   return "🟡"
        if pct < 90:   return "🟢"
        return "✅"
    df["status_icon"] = df["comment_pct"].apply(_status_color)

    # Форматируем дату последнего движения
    df["last_category_date"] = df["last_category_date"].fillna("").astype(str)
    df["last_category_date"] = df["last_category_date"].str[:10]

    return df


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    if df.empty:
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Комплектов", len(df))
    c2.metric("Листов", f"{int(df['docs_total'].sum()):,}".replace(",", " "))
    c3.metric("Замечаний", f"{int(df['comments_total'].sum()):,}".replace(",", " "))
    c4.metric("Ждут закрытия", f"{int(df['comments_active'].sum()):,}".replace(",", " "))

    total_c = int(df["comments_total"].sum())
    total_closed = int(df["comments_closed"].sum())
    total_annulled = int(df["comments_annulled"].sum())
    pct = round((total_closed + total_annulled) / total_c * 100, 1) if total_c else 0
    c5.metric("Общий % выполнения", f"{pct}%")


# ---------------------------------------------------------------------------
#  Пресеты для РП
# ---------------------------------------------------------------------------
def _apply_preset(df: pd.DataFrame, preset: str) -> pd.DataFrame:
    """Применяет быстрый пресет к таблице."""
    if preset == "📋 Все комплекты":
        return df

    if preset == "🔥 Пожарные (много замечаний, низкий %)":
        # >50 замечаний и <50% выполнения
        return df[(df["comments_total"] >= 50) & (df["comment_pct"] < 50)]

    if preset == "📉 Топ-20 отстающих":
        sub = df[df["comments_total"] >= 10]
        return sub.nsmallest(20, "comment_pct")

    if preset == "⚠️ Без движения (нет категорий)":
        return df[df["cat_none"] == df["comments_total"]]

    if preset == "🚧 Много листов в «C»":
        # Комплекты, где статус C больше 30% листов
        df["_c_share"] = df.apply(
            lambda r: r["doc_c"] / r["docs_total"] * 100 if r["docs_total"] else 0,
            axis=1)
        result = df[df["_c_share"] >= 30].drop(columns=["_c_share"])
        return result

    return df


# ---------------------------------------------------------------------------
#  Таблица
# ---------------------------------------------------------------------------
def _render_table(df: pd.DataFrame, sort_column: str, sort_desc: bool):
    if df.empty:
        st.info("Нет комплектов по заданным фильтрам.")
        return

    view = df.copy()

    display = view[[
        "status_icon", "complex", "complex_name", "discipline", "section",
        "docs_total", "doc_c", "doc_pct",
        "comments_total", "comments_active", "comments_closed",
        "comment_pct", "last_category_date",
        "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
    ]].rename(columns={
        "status_icon": "🚦",
        "complex": "Комплект",
        "complex_name": "Наименование",
        "discipline": "Дисциплина",
        "section": "Раздел",
        "docs_total": "Листов",
        "doc_c": "Из них C",
        "doc_pct": "% листов",
        "comments_total": "Замечаний",
        "comments_active": "Ждут закрытия",
        "comments_closed": "Закрыто",
        "comment_pct": "% выполнения",
        "last_category_date": "Последнее движение",
        "cat_1": "Принято",
        "cat_2": "Формальное",
        "cat_3": "Доп.треб.",
        "cat_4": "Не принято",
        "cat_none": "Без кат.",
    })

    # Сортировка из селектбокса
    if sort_column in display.columns:
        display = display.sort_values(sort_column, ascending=not sort_desc)

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "🚦": st.column_config.TextColumn("🚦", width="small",
                                              help="🔴 <30% · 🟡 30–60% · 🟢 60–90% · ✅ >90%"),
            "% листов": st.column_config.ProgressColumn(
                "% листов", min_value=0, max_value=100, format="%.1f%%"),
            "% выполнения": st.column_config.ProgressColumn(
                "% выполнения", min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    st.caption(f"Показано комплектов: **{len(display)}** из **{len(df)}**")


# ---------------------------------------------------------------------------
#  График
# ---------------------------------------------------------------------------
def _render_top_chart(df: pd.DataFrame):
    if df.empty:
        return

    st.markdown("### 📉 Топ-15 комплектов с наименьшим % выполнения")

    sub = df[df["comments_total"] >= 5].copy()
    if sub.empty:
        st.info("Нет комплектов с достаточным количеством замечаний.")
        return

    top = sub.nsmallest(15, "comment_pct").sort_values("comment_pct")

    fig = px.bar(
        top, x="comment_pct", y="complex", orientation="h",
        text="comment_pct",
        labels={"comment_pct": "% выполнения", "complex": ""},
        color="comment_pct",
        color_continuous_scale=["#E57373", "#FFD54F", "#A5D6A7"],
    )
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    fig.update_layout(height=max(350, 25 * len(top)),
                      coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Комплекты_топ_отстающих", "cx_top")


# ---------------------------------------------------------------------------
#  Экспорт
# ---------------------------------------------------------------------------
def _render_export(df: pd.DataFrame):
    if df.empty:
        return

    with st.expander("📥 Выгрузить в Excel", expanded=False):
        buf = io.BytesIO()
        export_df = df.drop(columns=["complex_name", "status_icon"],
                           errors="ignore")
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name="Комплекты")
        buf.seek(0)

        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Комплекты_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("🏗 Комплекты")
    st.caption("Все комплекты проекта. Используйте пресеты для типичных "
               "рабочих срезов.")

    # --- Фильтры ---
    disc_options = _load_discipline_options()

    c1, c2 = st.columns([1, 1])
    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина", options=list(disc_options.values()),
            placeholder="Все дисциплины", key="cx_disc")
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    section_options = _load_section_options(tuple(sel_disc) if sel_disc else ())

    with c2:
        if section_options:
            sel_section_labels = st.multiselect(
                "Раздел", options=list(section_options.values()),
                placeholder="Все разделы", key="cx_section")
            sel_section = [code for code, label in section_options.items()
                           if label in sel_section_labels]
        else:
            sel_section = []
            st.multiselect("Раздел", options=[],
                           placeholder="Разделы не применимы",
                           disabled=True, key="cx_section_empty")

    # --- Данные ---
    df = _load_complex_table(
        tuple(sel_disc) if sel_disc else (),
        tuple(sel_section) if sel_section else ())

    if df.empty:
        st.info("Нет комплектов по заданным фильтрам.")
        return

    # --- Пресеты ---
    st.markdown("##### 🎯 Быстрые пресеты для РП")
    preset = st.radio(
        "Пресет",
        options=[
            "📋 Все комплекты",
            "🔥 Пожарные (много замечаний, низкий %)",
            "📉 Топ-20 отстающих",
            "⚠️ Без движения (нет категорий)",
            "🚧 Много листов в «C»",
        ],
        horizontal=True,
        label_visibility="collapsed",
        key="cx_preset",
    )

    df = _apply_preset(df, preset)

    # --- KPI ---
    _render_kpi(df)

    st.divider()

    # --- Сортировка ---
    c_sort, c_dir = st.columns([3, 1])
    with c_sort:
        sort_column = st.selectbox(
            "Сортировать по",
            options=[
                "% выполнения",
                "Ждут закрытия",
                "Замечаний",
                "Комплект",
                "Последнее движение",
            ],
            key="cx_sort_col")
    with c_dir:
        sort_dir = st.radio(
            "Направление",
            options=["↑ возрастание", "↓ убывание"],
            label_visibility="collapsed",
            key="cx_sort_dir")
        sort_desc = sort_dir.startswith("↓")

    # --- Таблица ---
    _render_table(df, sort_column, sort_desc)

    st.divider()
    _render_top_chart(df)

    st.divider()
    _render_export(df)