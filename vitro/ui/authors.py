# vitro/ui/authors.py
"""
👤 Авторы — аналитика по инженерам.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly, safe_filename


CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

CAT_COLORS = {
    "Принято":       "#C6EFCE",
    "Формальное":    "#FFEB9C",
    "Доп.треб.":     "#BDD7EE",
    "Не принято":    "#FFC7CE",
    "Без категории": "#D9D9D9",
}


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
                  AND discipline IN ({ph}) ORDER BY section
            """, tuple(disciplines)).fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                ORDER BY section
            """).fetchall()
    return {r["section"]: r["section"] for r in rows}


@st.cache_data(ttl=600, show_spinner=False)
def _load_kit_options(disciplines: tuple = (), sections: tuple = ()) -> dict[str, str]:
    with get_conn() as conn:
        where = ["c.code IS NOT NULL"]
        params: list = []
        if disciplines:
            where.append(f"c.discipline IN ({','.join('?' * len(disciplines))})")
            params += list(disciplines)
        if sections:
            ph = ",".join("?" * len(sections))
            where.append(f"""c.code IN (
                SELECT DISTINCT complex FROM documents
                WHERE section IN ({ph}) AND complex IS NOT NULL)""")
            params += list(sections)
        rows = conn.execute(f"""
            SELECT c.code, c.name FROM complexes c
            WHERE {' AND '.join(where)} ORDER BY c.code
        """, tuple(params)).fetchall()
    return {r["code"]: f"{r['code']} — {r['name']}" if r["name"] else r["code"]
            for r in rows}


def _build_filter_clause(disciplines=(), sections=(), kits=()):
    where = []
    params: list = []
    if disciplines:
        where.append(f"d.discipline IN ({','.join('?' * len(disciplines))})")
        params += list(disciplines)
    if sections:
        where.append(f"d.section IN ({','.join('?' * len(sections))})")
        params += list(sections)
    if kits:
        where.append(f"d.complex IN ({','.join('?' * len(kits))})")
        params += list(kits)
    clause = (" AND " + " AND ".join(where)) if where else ""
    return clause, params


@st.cache_data(ttl=300, show_spinner=False)
def _load_category_kpi(disciplines: tuple = (), sections: tuple = (),
                       kits: tuple = ()) -> dict:
    clause, params = _build_filter_clause(disciplines, sections, kits)
    q = f"""
        SELECT COUNT(*) AS total,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_1,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_2,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_3,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_4,
            SUM(CASE WHEN c.category IS NULL OR c.category = '' THEN 1 ELSE 0 END) AS cat_none
        FROM comments c JOIN documents d ON c.doc_id = d.id
        WHERE c.author IS NOT NULL AND c.author <> '' {clause}
    """
    all_params = [CAT_1, CAT_2, CAT_3, CAT_4] + params
    with get_conn() as conn:
        row = conn.execute(q, all_params).fetchone()
        authors = conn.execute(f"""
            SELECT COUNT(DISTINCT c.author) AS n
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.author IS NOT NULL AND c.author <> '' {clause}
        """, params).fetchone()["n"]
    return {"authors": authors or 0, "total": row["total"] or 0,
            "cat_1": row["cat_1"] or 0, "cat_2": row["cat_2"] or 0,
            "cat_3": row["cat_3"] or 0, "cat_4": row["cat_4"] or 0,
            "cat_none": row["cat_none"] or 0}


def _render_kpi(k: dict):
    total = k["total"]
    if total == 0:
        st.info("Нет данных по авторам с учётом фильтров.")
        return

    def pct(n):
        return round(n / total * 100, 1) if total else 0.0

    st.markdown("##### 📦 Общий объём")
    c1, c2 = st.columns(2)
    c1.metric("Авторов", k["authors"])
    c2.metric("Всего замечаний", f"{total:,}".replace(",", " "))

    st.markdown("##### 🏷 Категории замечаний")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Принято/корректное", f"{k['cat_1']:,}".replace(",", " "),
              delta=f"{pct(k['cat_1'])}%", delta_color="off")
    c2.metric("Формальное/нет влияния", f"{k['cat_2']:,}".replace(",", " "),
              delta=f"{pct(k['cat_2'])}%", delta_color="off")
    c3.metric("Доп.треб./нет в ТЗ", f"{k['cat_3']:,}".replace(",", " "),
              delta=f"{pct(k['cat_3'])}%", delta_color="off")
    c4.metric("Не принято/нарушение ТНПА", f"{k['cat_4']:,}".replace(",", " "),
              delta=f"{pct(k['cat_4'])}%", delta_color="off")
    c5.metric("Без категории", f"{k['cat_none']:,}".replace(",", " "),
              delta=f"{pct(k['cat_none'])}%", delta_color="off")


@st.cache_data(ttl=300, show_spinner=False)
def _load_authors(disciplines: tuple = (), sections: tuple = (),
                  kits: tuple = ()) -> pd.DataFrame:
    clause, params = _build_filter_clause(disciplines, sections, kits)
    q = f"""
        SELECT c.author AS author, COUNT(*) AS total,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_1,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_2,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_3,
            SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_4,
            SUM(CASE WHEN c.category IS NULL OR c.category = '' THEN 1 ELSE 0 END) AS cat_none,
            SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS closed,
            SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS annulled
        FROM comments c JOIN documents d ON c.doc_id = d.id
        WHERE c.author IS NOT NULL AND c.author <> '' {clause}
        GROUP BY c.author
    """
    all_params = [CAT_1, CAT_2, CAT_3, CAT_4] + params
    with get_conn() as conn:
        df = pd.read_sql(q, conn, params=all_params)
    if df.empty:
        return df
    df["reject_pct"] = df.apply(
        lambda r: round(r["cat_4"] / r["total"] * 100, 1) if r["total"] else 0.0,
        axis=1)
    df["categorized_pct"] = df.apply(
        lambda r: round((r["total"] - r["cat_none"]) / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)
    df["active_pct"] = df.apply(
        lambda r: round(r["active"] / r["total"] * 100, 1) if r["total"] else 0.0,
        axis=1)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _load_author_timing() -> pd.DataFrame:
    """
    Метрики сроков по авторам с учётом категорий.

    Возвращает:
      - avg_our_response_days — сколько мы отвечаем
      - avg_customer_wait_days — сколько ждёт заказчик (без хронических)
      - avg_chronic_days — сколько висят хронические
      - pct_overdue — % реально просроченных заказчиком
      - n_waiting — сколько ждут заказчика (активных)
      - n_chronic — сколько хронических
      - n_closed_by_doc — сколько фактически принято (лист A/B)
    """
    from vitro.ui.deadlines import _load_all_categorized

    df = _load_all_categorized()
    if df.empty:
        return pd.DataFrame()

    # Группируем по автору
    rows = []
    for author, group in df.groupby("author"):
        if not author:
            continue

        # Замечания с нашим ответом
        with_fix = group[group["fix_date_d"].notna()]

        # 1. Наше среднее время ответа (через Python, а не pandas .dt)
        if not with_fix.empty:
            our_times = []
            for _, row in with_fix.iterrows():
                fix_d = row["fix_date_d"]
                created_d = row["created_d"]
                if fix_d is not None and created_d is not None:
                    try:
                        delta = (fix_d - created_d).days
                        if delta >= 0:
                            our_times.append(delta)
                    except (TypeError, AttributeError):
                        pass
            avg_our = round(sum(our_times) / len(our_times), 1) if our_times else 0
        else:
            avg_our = 0

        # 2. Реально ждут заказчика (< 90 р.д.)
        waiting = group[group["category_flag"].isin([
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime",
        ])]
        if not waiting.empty:
            avg_wait = round(waiting["days_waiting_customer"].mean(), 1)
        else:
            avg_wait = 0

        # 3. Хронические (> 90 р.д.)
        chronic = group[group["category_flag"] == "waiting_customer_chronic"]
        if not chronic.empty:
            avg_chronic = round(chronic["days_waiting_customer"].mean(), 1)
        else:
            avg_chronic = 0

        # 4. Учтено (лист A/B)
        closed_doc = group[group["category_flag"] == "closed_by_doc_status"]

        # 5. Просрочено заказчиком (% от активных «Выполнено»)
        total_waiting = len(waiting) + len(chronic)
        pct_overdue = (
            round(len(waiting[waiting["days_waiting_customer"] > 10])
                  / total_waiting * 100, 1)
            if total_waiting else 0
        )

        rows.append({
            "author": author,
            "n_total": len(group),
            "n_waiting": len(waiting),
            "n_chronic": len(chronic),
            "n_closed_by_doc": len(closed_doc),
            "avg_our_response_days": avg_our,
            "avg_customer_wait_days": avg_wait,
            "avg_chronic_days": avg_chronic,
            "pct_overdue": pct_overdue,
        })

    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def _load_author_by_discipline(author: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT d.discipline AS discipline, COUNT(*) AS n,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_1,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_2,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_3,
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS cat_4,
                SUM(CASE WHEN c.category IS NULL OR c.category = '' THEN 1 ELSE 0 END) AS cat_none
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.author = ? AND d.discipline IS NOT NULL
            GROUP BY d.discipline ORDER BY n DESC
        """, conn, params=(CAT_1, CAT_2, CAT_3, CAT_4, author))


@st.cache_data(ttl=300, show_spinner=False)
def _load_author_comments(author: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT c.id AS "ID", d.complex AS "Комплект",
                REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS "Лист",
                c.comment AS "Замечание", c.status AS "Статус",
                strftime('%d.%m.%Y %H:%M:%S', c.created) AS "Создано",
                c.category AS "Категория"
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.author = ? ORDER BY c.created DESC LIMIT 500
        """, conn, params=(author,))


def _render_top_authors(df: pd.DataFrame, limit: int = 20):
    st.markdown(f"### 🏆 Топ-{limit} авторов по количеству замечаний")
    if df.empty:
        st.info("Нет данных.")
        return
    top = df.nlargest(limit, "total").sort_values("total", ascending=True)
    fig = px.bar(top, x="total", y="author", orientation="h", text="total",
                 labels={"total": "Замечаний", "author": "Автор"},
                 color_discrete_sequence=["#64B5F6"])
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(400, 25 * len(top)), yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Авторы_топ", "auth_top")


def _render_quality(df: pd.DataFrame, limit: int = 20):
    st.markdown(f"### 🎯 Качество замечаний — топ-{limit} авторов")
    if df.empty:
        st.info("Нет данных.")
        return
    top = df.nlargest(limit, "total").copy()
    df_long = top.melt(
        id_vars="author",
        value_vars=["cat_1", "cat_2", "cat_3", "cat_4", "cat_none"],
        var_name="cat_key", value_name="Количество")
    mapping = {"cat_1": "Принято", "cat_2": "Формальное",
               "cat_3": "Доп.треб.", "cat_4": "Не принято",
               "cat_none": "Без категории"}
    df_long["Категория"] = df_long["cat_key"].map(mapping)
    order = top.sort_values("total", ascending=False)["author"].tolist()

    fig = px.bar(df_long, x="author", y="Количество", color="Категория",
                 barmode="stack", color_discrete_map=CAT_COLORS,
                 category_orders={"author": order,
                                  "Категория": ["Принято", "Формальное",
                                                "Доп.треб.", "Не принято",
                                                "Без категории"]},
                 labels={"author": "Автор"})
    fig.update_layout(xaxis_tickangle=-45, height=500, legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Авторы_качество", "auth_quality")

    table = top[["author", "total", "cat_1", "cat_2", "cat_3", "cat_4",
                 "cat_none", "reject_pct", "active_pct"]].rename(columns={
        "author": "Автор", "total": "Всего", "cat_1": "Принято",
        "cat_2": "Формальное", "cat_3": "Доп.треб.", "cat_4": "Не принято",
        "cat_none": "Без категории", "reject_pct": "% непринятых",
        "active_pct": "% активных"}).sort_values("Всего", ascending=False)
    st.dataframe(table, use_container_width=True, hide_index=True,
                 column_config={
                     "% непринятых": st.column_config.NumberColumn(format="%.1f%%"),
                     "% активных": st.column_config.NumberColumn(format="%.1f%%"),
                 })


def _render_author_timing():
    """Сроки по авторам — разделены на «тянут сейчас» и «хроника»."""
    st.markdown("### ⏸ Сроки рассмотрения по авторам")

    df = _load_author_timing()
    if df.empty:
        st.info("Нет данных по срокам.")
        return

    # Отсеиваем авторов с малым объёмом
    df = df[df["n_waiting"] + df["n_chronic"] >= 5].copy()
    if df.empty:
        st.info("Нет данных для отображения.")
        return

    st.caption(
        "**Две разные проблемы:**\n\n"
        "🟠 **Мы медленно отвечаем** — по замечаниям этих авторов наша "
        "команда тратит много времени. Это **наша** проблема.\n\n"
        "🔵 **Заказчик тянет сейчас** — замечания в реальном ожидании "
        "(< 90 р.д.). Это **его текущая** проблема.\n\n"
        "🔴 **Хронические** — висят > 90 р.д. Скорее всего, статус в "
        "Витрокад просто **не обновили**."
    )

    st.divider()

    # =====================================================================
    #  БЛОК 1: Мы медленно отвечаем
    # =====================================================================
    st.markdown("#### 🟠 Мы медленно отвечаем")
    st.caption("Топ-10 авторов, по чьим замечаниям **наша команда** "
               "тратит больше всего времени на ответ.")

    our_slow = df.nlargest(10, "avg_our_response_days").sort_values(
        "avg_our_response_days", ascending=True)

    fig_ours = px.bar(
        our_slow,
        x="avg_our_response_days",
        y="author",
        orientation="h",
        text="avg_our_response_days",
        labels={"avg_our_response_days": "Дней до нашего ответа",
                "author": ""},
        color="avg_our_response_days",
        color_continuous_scale=["#FFD54F", "#FFB74D", "#E57373"],
    )
    fig_ours.update_traces(texttemplate="%{text:.0f} дн.",
                           textposition="outside")
    fig_ours.add_vline(
        x=14, line_dash="dash", line_color="#2E7D32", line_width=2,
        annotation_text="Цель — 14 дн.",
        annotation_position="top right",
        annotation_font_color="#2E7D32", annotation_font_size=11,
    )
    fig_ours.update_layout(
        height=max(350, 35 * len(our_slow)),
        coloraxis_showscale=False,
        margin=dict(l=180, r=80, t=20, b=40),
    )
    st.plotly_chart(fig_ours, use_container_width=True)
    download_plotly(fig_ours, "Авторы_мы_медленные", "auth_ours",
                    width=1400, height=600)

    over_14 = (our_slow["avg_our_response_days"] > 14).sum()
    if over_14 > 0:
        st.warning(
            f"⚠️ По замечаниям **{over_14} из 10** авторов мы отвечаем "
            f"дольше **14 дней**."
        )
    else:
        st.success("✅ По всем топ-авторам мы отвечаем в пределах нормы.")

    st.divider()

    # =====================================================================
    #  БЛОК 2: Заказчик тянет СЕЙЧАС (без хронических)
    # =====================================================================
    st.markdown("#### 🔵 Заказчик тянет сейчас")
    st.caption(
        "Топ-10 авторов, чьи ответы **они держат у себя прямо сейчас** "
        "(< 90 р.д. — реальное ожидание). Хронические (> 90 р.д.) — "
        "в отдельном блоке ниже."
    )

    cust_slow = df[df["n_waiting"] > 0].nlargest(
        10, "avg_customer_wait_days").sort_values(
        "avg_customer_wait_days", ascending=True)

    if cust_slow.empty:
        st.success("🎉 Нет активного ожидания.")
    else:
        fig_cust = px.bar(
            cust_slow,
            x="avg_customer_wait_days",
            y="author",
            orientation="h",
            text="avg_customer_wait_days",
            labels={"avg_customer_wait_days": "Дней ожидания",
                    "author": ""},
            color="avg_customer_wait_days",
            color_continuous_scale=["#A5D6A7", "#FFD54F", "#E57373"],
        )
        fig_cust.update_traces(texttemplate="%{text:.0f} дн.",
                               textposition="outside")
        fig_cust.add_vline(
            x=14, line_dash="dash", line_color="#2E7D32", line_width=2,
            annotation_text="SLA 14 дн.",
            annotation_position="top right",
            annotation_font_color="#2E7D32", annotation_font_size=11,
        )
        fig_cust.update_layout(
            height=max(350, 35 * len(cust_slow)),
            coloraxis_showscale=False,
            margin=dict(l=180, r=80, t=20, b=40),
        )
        st.plotly_chart(fig_cust, use_container_width=True)
        download_plotly(fig_cust, "Авторы_заказчик_тянет", "auth_cust",
                        width=1400, height=600)

        overdue = (cust_slow["avg_customer_wait_days"] > 14).sum()
        if overdue > 0:
            st.error(
                f"🔴 **{overdue} из 10** авторов держат наши ответы "
                f"дольше **14 дней**. Готовим письмо-предъявление."
            )

    st.divider()

    # =====================================================================
    #  БЛОК 3: Хронические
    # =====================================================================
    st.markdown("#### 🔴 Хронические — висят > 90 р.д.")
    st.caption(
        "Эти замечания **не «ждут рассмотрения»** — они, скорее всего, "
        "уже решены, но статус в Витрокад не обновлён. "
        "Нужно письмо с просьбой закрыть формально."
    )

    chronic_slow = df[df["n_chronic"] > 0].nlargest(
        10, "avg_chronic_days").sort_values(
        "avg_chronic_days", ascending=True)

    if chronic_slow.empty:
        st.success("🎉 Нет хронических замечаний.")
    else:
        fig_chronic = px.bar(
            chronic_slow,
            x="avg_chronic_days",
            y="author",
            orientation="h",
            text="avg_chronic_days",
            labels={"avg_chronic_days": "Дней (хроника)",
                    "author": ""},
            color_discrete_sequence=["#7F0000"],
        )
        fig_chronic.update_traces(texttemplate="%{text:.0f} дн.",
                                  textposition="outside")
        fig_chronic.update_layout(
            height=max(350, 35 * len(chronic_slow)),
            margin=dict(l=180, r=80, t=20, b=40),
        )
        st.plotly_chart(fig_chronic, use_container_width=True)
        download_plotly(fig_chronic, "Авторы_хронические", "auth_chronic",
                        width=1400, height=600)

    st.divider()

    # =====================================================================
    #  СВОДНАЯ ТАБЛИЦА
    # =====================================================================
    st.markdown("#### 📋 Полная таблица по авторам")

    table = df[[
        "author", "n_total", "n_waiting", "n_chronic", "n_closed_by_doc",
        "avg_our_response_days", "avg_customer_wait_days",
        "avg_chronic_days", "pct_overdue",
    ]].copy()

    table = table.rename(columns={
        "author": "Автор",
        "n_total": "Всего",
        "n_waiting": "Ждут сейчас",
        "n_chronic": "Хроника",
        "n_closed_by_doc": "Учтено (A/B)",
        "avg_our_response_days": "Мы отвечаем (дн.)",
        "avg_customer_wait_days": "Ждём заказчика (дн.)",
        "avg_chronic_days": "Хроника (дн.)",
        "pct_overdue": "% просрочки",
    })

    # Сортируем по «ждут сейчас» + хроника
    table["_sort"] = table["Ждут сейчас"] + table["Хроника"]
    table = table.sort_values("_sort", ascending=False).drop(columns=["_sort"])

    st.dataframe(
        table.head(30),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Мы отвечаем (дн.)": st.column_config.NumberColumn(
                "Мы отвечаем (дн.)", format="%.1f"),
            "Ждём заказчика (дн.)": st.column_config.NumberColumn(
                "Ждём заказчика (дн.)", format="%.1f"),
            "Хроника (дн.)": st.column_config.NumberColumn(
                "Хроника (дн.)", format="%.1f"),
            "% просрочки": st.column_config.ProgressColumn(
                "% просрочки", min_value=0, max_value=100,
                format="%.0f%%"),
        },
    )

    # =====================================================================
    #  ИТОГОВЫЙ ВЫВОД
    # =====================================================================
    st.divider()
    st.markdown("#### 🎯 Что делать")

    n_our_slow = (df["avg_our_response_days"] > 14).sum()
    n_cust_slow = ((df["avg_customer_wait_days"] > 14) &
                   (df["n_waiting"] > 0)).sum()
    n_chronic_total = df["n_chronic"].sum()
    n_closed_total = df["n_closed_by_doc"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟠 Мы тормозим", n_our_slow,
              help="Авторов, где мы отвечаем > 14 дн.")
    c2.metric("🔵 Заказчик тянет", n_cust_slow,
              help="Авторов, где он держит > 14 дн. сейчас")
    c3.metric("🔴 Хронических",
              f"{n_chronic_total:,}".replace(",", " "),
              help="Замечаний висят > 90 р.д.")
    c4.metric("🟢 Учтено (A/B)",
              f"{n_closed_total:,}".replace(",", " "),
              help="Фактически принято")

    actions = []
    if n_our_slow > 0:
        actions.append(
            f"🟠 **{n_our_slow} авторов** — мы отвечаем им медленно. "
            f"Разобраться с проектировщиками."
        )
    if n_cust_slow > 0:
        actions.append(
            f"🔵 **{n_cust_slow} авторов** — заказчик тянет рассмотрение. "
            f"Письмо-предъявление."
        )
    if n_chronic_total > 0:
        actions.append(
            f"🔴 **{n_chronic_total:,} замечаний хронических** — "
            f"письмо на формальное закрытие.".replace(",", " ")
        )

    for a in actions:
        st.markdown(f"- {a}")


def _render_drilldown(df: pd.DataFrame):
    st.markdown("### 🔍 Детали автора")
    if df.empty:
        st.info("Нет данных.")
        return
    authors = sorted(df["author"].dropna().unique())
    sel = st.selectbox("Выберите автора", options=authors,
                       key="author_drill",
                       placeholder="Начните вводить имя...")
    if not sel:
        return
    row = df[df["author"] == sel].iloc[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Всего замечаний", int(row["total"]))
    c2.metric("Активных", int(row["active"]))
    c3.metric("Закрыто", int(row["closed"]))
    c4.metric("% непринятых", f"{row['reject_pct']}%")
    c5.metric("С категорией", f"{row['categorized_pct']}%")
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        cat_data = pd.DataFrame([
            {"Категория": "Принято", "Количество": int(row["cat_1"])},
            {"Категория": "Формальное", "Количество": int(row["cat_2"])},
            {"Категория": "Доп.треб.", "Количество": int(row["cat_3"])},
            {"Категория": "Не принято", "Количество": int(row["cat_4"])},
            {"Категория": "Без категории", "Количество": int(row["cat_none"])},
        ])
        cat_data = cat_data[cat_data["Количество"] > 0]
        if not cat_data.empty:
            fig = px.pie(cat_data, names="Категория", values="Количество",
                         hole=0.45, color="Категория",
                         color_discrete_map=CAT_COLORS,
                         title="Категории замечаний автора")
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, f"Автор_{safe_filename(sel)}_категории", "auth_drill_pie")

    with col2:
        by_disc = _load_author_by_discipline(sel)
        if not by_disc.empty:
            by_disc["label"] = by_disc["discipline"].apply(
                lambda c: f"{c} — {discipline_name(c)}")
            fig = px.bar(by_disc.sort_values("n", ascending=True),
                         x="n", y="label", orientation="h", text="n",
                         labels={"n": "Замечаний", "label": ""},
                         color_discrete_sequence=["#9575CD"])
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(300, 25 * len(by_disc)))
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, f"Автор_{safe_filename(sel)}_дисциплины", "auth_drill_disc")

    st.divider()
    st.markdown(f"**Последние 500 замечаний автора `{sel}`**")
    details = _load_author_comments(sel)
    if details.empty:
        st.info("У автора нет замечаний.")
        return
    st.dataframe(details, use_container_width=True, hide_index=True,
                 height=500,
                 column_config={
                     "Замечание": st.column_config.TextColumn(width="large"),
                     "Создано": st.column_config.TextColumn(width="medium"),
                 })


def render():
    st.header("👤 Авторы замечаний")
    st.caption("Аналитика по инженерам: активность, качество, категории.")

    disc_options = _load_discipline_options()

    c1, c2, c3 = st.columns(3)
    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина", options=list(disc_options.values()),
            placeholder="Все дисциплины", key="authors_disc")
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    section_options = _load_section_options(tuple(sel_disc) if sel_disc else ())

    with c2:
        if section_options:
            sel_section_labels = st.multiselect(
                "Раздел", options=list(section_options.values()),
                placeholder="Все разделы", key="authors_section")
            sel_section = [code for code, label in section_options.items()
                           if label in sel_section_labels]
        else:
            sel_section = []
            st.multiselect("Раздел", options=[],
                           placeholder="Разделы не применимы",
                           disabled=True, key="authors_section_empty")

    kit_options = _load_kit_options(
        tuple(sel_disc) if sel_disc else (),
        tuple(sel_section) if sel_section else ())

    with c3:
        sel_kit_labels = st.multiselect(
            "Комплект", options=list(kit_options.values()),
            placeholder="Все комплекты", key="authors_kit")
        sel_kit = [code for code, label in kit_options.items()
                   if label in sel_kit_labels]

    disc_t = tuple(sel_disc) if sel_disc else ()
    sect_t = tuple(sel_section) if sel_section else ()
    kit_t  = tuple(sel_kit) if sel_kit else ()

    kpi = _load_category_kpi(disc_t, sect_t, kit_t)
    _render_kpi(kpi)

    df = _load_authors(disc_t, sect_t, kit_t)
    if df.empty:
        st.info("Нет данных по авторам с учётом фильтров.")
        return

    st.divider()
    limit = st.slider("Сколько авторов показать в топе",
                      min_value=5, max_value=50, value=20, step=5,
                      key="authors_limit")

    _render_top_authors(df, limit)
    st.divider()
    _render_quality(df, limit)
    st.divider()
    _render_author_timing()  # ← НОВОЕ
    st.divider()
    _render_drilldown(df)