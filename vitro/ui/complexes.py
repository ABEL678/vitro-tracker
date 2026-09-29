# vitro/ui/complexes.py
"""
🏗 Комплекты — таблица всех комплектов с пресетами для РП.

Все оперативные цифры (активные, категории, ответственные)
синхронизированы с дашбордом через `_load_all_categorized`.
Листы — из БД (documents).
"""

import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Константы категорий
# ---------------------------------------------------------------------------
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
#  Единый источник активных замечаний
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_active_df() -> pd.DataFrame:
    """Активные замечания из deadlines._load_all_categorized."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return df
    return df[df["category_flag"] != "abandoned"].copy()


# ---------------------------------------------------------------------------
#  Таблица комплектов
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_table(disciplines: tuple = (),
                         sections: tuple = ()) -> pd.DataFrame:
    """
    Сводка по комплектам:
      - листы (из documents)
      - замечания — общие из БД (всего/закрыто/аннулировано)
      - активные + категории + ответственные — из _load_all_categorized
    """
    # ---- 1. Листы ----
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

        # ---- 2. Замечания — общие из БД ----
        q_base = """
            SELECT
                d.complex AS complex,
                COUNT(c.id) AS comments_total,
                SUM(CASE WHEN c.status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS comments_closed,
                SUM(CASE WHEN c.status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS comments_annulled
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
        """
        params_base: list = []
        if disciplines:
            q_base += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
            params_base += list(disciplines)
        if sections:
            q_base += f" AND d.section IN ({','.join('?' * len(sections))})"
            params_base += list(sections)
        q_base += " GROUP BY d.complex"

        base_df = pd.read_sql(q_base, conn, params=params_base)

        # ---- 3. Названия комплектов ----
        complexes_df = pd.read_sql(
            "SELECT code, name FROM complexes", conn
        ).rename(columns={"code": "complex", "name": "complex_name"})

    # ---- 4. Активные из единого источника ----
    active = _load_active_df()
    if disciplines and not active.empty:
        active = active[active["discipline"].isin(disciplines)]
    if sections and not active.empty:
        active = active[active["section"].isin(sections)]

    if not active.empty:
        active_c = active.copy()
        active_c["Категория"] = (
            active_c["category"].fillna("Без категории")
            .replace("", "Без категории")
        )

        rows = []
        for cx, sub in active_c.groupby("complex"):
            row = {"complex": cx}

            row["comments_active"] = len(sub)

            # Категории
            cc = sub["Категория"].value_counts()
            row["cat_1"] = int(cc.get(CAT_1, 0))
            row["cat_2"] = int(cc.get(CAT_2, 0))
            row["cat_3"] = int(cc.get(CAT_3, 0))
            row["cat_4"] = int(cc.get(CAT_4, 0))
            row["cat_none"] = int(cc.get("Без категории", 0))

            # Ответственные
            row["ours"] = sub[sub["category_flag"].isin([
                "new_overdue", "new_in_progress",
                "in_work_overdue", "in_work_in_progress",
                "rejected_overdue", "rejected_in_progress",
                "discussion_overdue", "discussion_in_progress",
            ])].shape[0]
            row["waiting"] = sub[sub["category_flag"].isin([
                "waiting_customer", "waiting_customer_overdue",
                "waiting_customer_ontime",
            ])].shape[0]
            row["chronic"] = sub[
                sub["category_flag"] == "waiting_customer_chronic"
            ].shape[0]
            row["closed_doc"] = sub[
                sub["category_flag"] == "closed_by_doc_status"
            ].shape[0]

            # Просрочки АТП ТЛП (для пресета)
            row["ours_overdue"] = sub[sub["category_flag"].isin([
                "new_overdue", "in_work_overdue",
                "rejected_overdue", "discussion_overdue",
            ])].shape[0]

            rows.append(row)

        active_cx = pd.DataFrame(rows)
    else:
        active_cx = pd.DataFrame(columns=[
            "complex", "comments_active",
            "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
            "ours", "waiting", "chronic", "closed_doc", "ours_overdue",
        ])

    # ---- 5. Склейка ----
    df = docs_df.merge(base_df, on="complex", how="outer")
    df = df.merge(active_cx, on="complex", how="outer")
    df = df.merge(complexes_df, on="complex", how="left")
    df = df.fillna(0)

    # ---- 6. Типы ----
    int_cols = [
        "docs_total", "doc_a", "doc_b", "doc_c", "doc_info",
        "comments_total", "comments_closed", "comments_annulled",
        "comments_active", "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
        "ours", "waiting", "chronic", "closed_doc", "ours_overdue",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = df[col].astype(int)

    # ---- 7. Проценты ----
    df["comment_pct"] = df.apply(
        lambda r: round(r["comments_closed"] / r["comments_total"] * 100, 1)
        if r["comments_total"] else 0.0,
        axis=1,
    )
    df["doc_pct"] = df.apply(
        lambda r: round((r["doc_a"] + r["doc_b"]) / r["docs_total"] * 100, 1)
        if r["docs_total"] else 0.0,
        axis=1,
    )
    df["cat_pct"] = df.apply(
        lambda r: round((r["comments_active"] - r["cat_none"])
                        / r["comments_active"] * 100, 1)
        if r["comments_active"] else 0.0,
        axis=1,
    )

    # ---- 8. Цветовой статус (% закрыто формально) ----
    def _status_color(pct):
        if pct < 30:   return "🔴"
        if pct < 60:   return "🟡"
        if pct < 90:   return "🟢"
        return "✅"
    df["status_icon"] = df["comment_pct"].apply(_status_color)

    return df


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    if df.empty:
        return

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Комплектов", f"{len(df):,}".replace(",", " "))
    c2.metric(
        "Листов",
        f"{int(df['docs_total'].sum()):,}".replace(",", " "),
        help="Всего листов РД во всех комплектах.",
    )
    c3.metric(
        "Всего замечаний",
        f"{int(df['comments_total'].sum()):,}".replace(",", " "),
        help="По всей базе, включая закрытые и аннулированные.",
    )
    c4.metric(
        "Активных",
        f"{int(df['comments_active'].sum()):,}".replace(",", " "),
        help="5 активных статусов, без закрытых и аннулированных.",
    )
    c5.metric(
        "Закрыто",
        f"{int(df['comments_closed'].sum()):,}".replace(",", " "),
        help="Формально закрытые замечания.",
    )
    total = int(df["comments_total"].sum())
    closed = int(df["comments_closed"].sum())
    annulled = int(df["comments_annulled"].sum())
    pct = round((closed + annulled) / total * 100, 1) if total else 0
    c6.metric(
        "% закрыто формально",
        f"{pct}%",
        help="Закрыто + аннулировано от общего числа.",
    )


# ---------------------------------------------------------------------------
#  Пресеты
# ---------------------------------------------------------------------------
def _apply_preset(df: pd.DataFrame, preset: str) -> pd.DataFrame:
    if preset == "📋 Все комплекты":
        return df

    if preset == "🔥 Пожарные (≥50 замечаний, % закрыто < 50)":
        return df[(df["comments_total"] >= 50) & (df["comment_pct"] < 50)]

    if preset == "📉 Топ-20 отстающих по % закрыто":
        sub = df[df["comments_total"] >= 10]
        return sub.nsmallest(20, "comment_pct")

    if preset == "⚠️ Без разбора (нет категорий)":
        return df[(df["comments_active"] > 0)
                   & (df["cat_none"] == df["comments_active"])]

    if preset == "🚨 Новые просрочки АТП ТЛП":
        sub = df[df["ours_overdue"] > 0].copy()
        return sub.sort_values("ours_overdue", ascending=False).head(30)

    if preset == "🔴 Хроника (много > 90 р.д.)":
        sub = df[df["chronic"] > 0].copy()
        return sub.sort_values("chronic", ascending=False).head(30)

    if preset == "🟢 Много учтённых A/B":
        sub = df[df["closed_doc"] > 0].copy()
        return sub.sort_values("closed_doc", ascending=False).head(30)

    if preset == "🚧 Много листов в «C»":
        sub = df.copy()
        sub["_c_share"] = sub.apply(
            lambda r: r["doc_c"] / r["docs_total"] * 100
            if r["docs_total"] else 0, axis=1)
        result = sub[sub["_c_share"] >= 30].drop(columns=["_c_share"])
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
        # Листы
        "docs_total", "doc_a", "doc_b", "doc_c", "doc_info", "doc_pct",
        # Замечания общие
        "comments_total", "comments_active", "comments_closed",
        "comment_pct",
        # Ответственные
        "ours", "waiting", "chronic", "closed_doc",
        # Категории (активные)
        "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
    ]].rename(columns={
        "status_icon": "🚦",
        "complex": "Комплект",
        "complex_name": "Наименование",
        "discipline": "Дисциплина",
        "section": "Раздел",
        # Листы
        "docs_total": "Листов",
        "doc_a": "A",
        "doc_b": "B",
        "doc_c": "C",
        "doc_info": "И",
        "doc_pct": "% A+B",
        # Замечания
        "comments_total": "Всего",
        "comments_active": "Активных",
        "comments_closed": "Закрыто",
        "comment_pct": "% закрыто",
        # Ответственные
        "ours": "🔴 АТП ТЛП",
        "waiting": "🔵 Ждут",
        "chronic": "🔴 Хроника",
        "closed_doc": "🟢 Учтено A/B",
        # Категории
        "cat_1": "Принято",
        "cat_2": "Формальное",
        "cat_3": "Доп.треб.",
        "cat_4": "Не принято",
        "cat_none": "Без кат.",
    })

    if sort_column in display.columns:
        display = display.sort_values(sort_column, ascending=not sort_desc)

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "🚦": st.column_config.TextColumn(
                "🚦", width="small",
                help="🔴 <30% · 🟡 30–60% · 🟢 60–90% · ✅ >90% "
                     "(% закрыто формально)"),
            "% A+B": st.column_config.ProgressColumn(
                "% A+B", min_value=0, max_value=100, format="%.1f%%"),
            "% закрыто": st.column_config.ProgressColumn(
                "% закрыто", min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    st.caption(f"Показано комплектов: **{len(display)}** из **{len(df)}**")


# ---------------------------------------------------------------------------
#  Два графика рядом
# ---------------------------------------------------------------------------
def _render_top_charts(df: pd.DataFrame):
    if df.empty:
        return

    col_left, col_right = st.columns(2)

    # =================================================================
    #  Левый: Топ-15 по числу неразобранных замечаний
    # =================================================================
    with col_left:
        st.markdown("### 📉 Топ-15 комплектов по числу неразобранных")
        st.caption(
            "Комплекты, где **больше всего активных замечаний "
            "без категории**. Показывает, куда направить специалистов. "
            "Сортировка по **абсолютному числу**, а не по %."
        )

        sub = df[df["cat_none"] >= 20].copy()

        if sub.empty:
            st.success(
                "🎉 Нет комплектов с ≥20 неразобранных активных замечаний."
            )
        else:
            top = sub.nlargest(15, "cat_none").sort_values(
                "cat_none", ascending=True)

            # Подпись: число + % разбора
            top["_label"] = top.apply(
                lambda r: f"{int(r['cat_none'])} ({r['cat_pct']:.0f}%)",
                axis=1,
            )

            fig = px.bar(
                top, x="cat_none", y="complex", orientation="h",
                text="_label",
                labels={"cat_none": "Неразобранных", "complex": ""},
                color="cat_pct",
                color_continuous_scale=["#E57373", "#FFD54F", "#A5D6A7"],
                hover_data={
                    "cat_none": True,
                    "cat_pct": ":.1f",
                    "comments_active": True,
                },
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                height=max(400, 28 * len(top)),
                coloraxis_showscale=True,
                coloraxis_colorbar=dict(title="% разбора"),
                margin=dict(l=200, r=120, t=20, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(
                fig, "Комплекты_топ_неразобранных", "cx_top_uncat",
                width=1200, height=600,
            )

    # =================================================================
    #  Правый: Топ-15 по просрочкам АТП ТЛП
    # =================================================================
    with col_right:
        st.markdown("### 🚨 Топ-15 по просрочкам АТП ТЛП")
        st.caption(
            "Комплекты с наибольшим числом **просроченных с нашей "
            "стороны** замечаний (>10 р.д.). Единая логика с дашбордом."
        )

        sub = df[df["ours_overdue"] > 0].copy()
        if sub.empty:
            st.success("🎉 Нет просрочек по выбранным фильтрам.")
        else:
            top = sub.nlargest(15, "ours_overdue").sort_values(
                "ours_overdue", ascending=True)
            fig = px.bar(
                top, x="ours_overdue", y="complex", orientation="h",
                text="ours_overdue",
                labels={"ours_overdue": "Просрочено", "complex": ""},
                color="ours_overdue",
                color_continuous_scale=["#FFD54F", "#FFB74D", "#E57373"],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(400, 28 * len(top)),
                                coloraxis_showscale=False,
                                margin=dict(l=200, r=80, t=20, b=40))
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Комплекты_топ_просрочек", "cx_overdue",
                            width=1200, height=600)


# ---------------------------------------------------------------------------
#  Экспорт
# ---------------------------------------------------------------------------
def _render_export(df: pd.DataFrame):
    if df.empty:
        return

    with st.expander("📥 Выгрузить таблицу в Excel", expanded=False):
        buf = io.BytesIO()
        export_df = df.drop(
            columns=["complex_name", "status_icon"],
            errors="ignore",
        )
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
            key="cx_download",
        )
        st.caption(f"В выгрузке: **{len(export_df)}** строк"
                   .replace(",", " "))


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("🏗 Комплекты")
    st.caption(
        "Все комплекты проекта. Используйте пресеты для типичных "
        "рабочих срезов. Оперативные цифры синхронизированы "
        "с дашбордом."
    )

    # --- Фильтры ---
    disc_options = _load_discipline_options()

    c1, c2 = st.columns([1, 1])
    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина", options=list(disc_options.values()),
            placeholder="Все дисциплины", key="cx_disc")
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    section_options = _load_section_options(
        tuple(sel_disc) if sel_disc else ())

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
    with st.spinner("Загрузка данных..."):
        df = _load_complex_table(
            tuple(sel_disc) if sel_disc else (),
            tuple(sel_section) if sel_section else ())

    if df.empty:
        st.info("Нет комплектов по заданным фильтрам.")
        return

    # --- KPI ---
    _render_kpi(df)

    st.divider()

    # --- Пресеты ---
    st.markdown("##### 🎯 Быстрые пресеты для РП")
    preset = st.radio(
        "Пресет",
        options=[
            "📋 Все комплекты",
            "🔥 Пожарные (≥50 замечаний, % закрыто < 50)",
            "📉 Топ-20 отстающих по % закрыто",
            "⚠️ Без разбора (нет категорий)",
            "🚨 Новые просрочки АТП ТЛП",
            "🔴 Хроника (много > 90 р.д.)",
            "🟢 Много учтённых A/B",
            "🚧 Много листов в «C»",
        ],
        horizontal=True,
        label_visibility="collapsed",
        key="cx_preset",
    )

    df_filtered = _apply_preset(df, preset)

    # --- Сортировка ---
    c_sort, c_dir = st.columns([3, 1])
    with c_sort:
        sort_column = st.selectbox(
            "Сортировать по",
            options=[
                "% закрыто",
                "Активных",
                "Всего",
                "🔴 АТП ТЛП",
                "🔴 Хроника",
                "🟢 Учтено A/B",
                "Комплект",
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
    _render_table(df_filtered, sort_column, sort_desc)

    st.divider()

    # --- Два графика ---
    _render_top_charts(df_filtered)

    st.divider()

    # --- Экспорт ---
    _render_export(df_filtered)