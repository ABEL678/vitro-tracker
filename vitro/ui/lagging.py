# vitro/ui/lagging.py
"""
⚠️ Отстающие комплекты — контроль проблемных мест.

4 под-вкладки:
  📉 По % разбора           — где мало категоризировано.
  🚨 По просрочкам АТП ТЛП  — топ по просрочке с нашей стороны.
  ⏸ По ожиданию заказчика   — топ по waiting_customer + chronic.
  🔍 Детали комплекта       — drill-down с разбивкой по 8 категориям.

KPI вкладки синхронизированы с дашбордом через единый источник —
`_load_all_categorized` из deadlines.py.
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
#  Справочники
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _load_discipline_options() -> dict[str, str]:
    with get_conn() as conn:
        codes = [r["discipline"] for r in conn.execute(
            "SELECT DISTINCT discipline FROM documents "
            "WHERE discipline IS NOT NULL ORDER BY discipline")]
    return {c: f"{c} — {discipline_name(c)}" for c in codes}


# ---------------------------------------------------------------------------
#  Единый источник активных
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_active_df() -> pd.DataFrame:
    """Активные замечания из _load_all_categorized (без abandoned)."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return df
    return df[df["category_flag"] != "abandoned"].copy()


# ---------------------------------------------------------------------------
#  Сводка по комплектам (из единого источника + листы)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_stats(disciplines: tuple = ()) -> pd.DataFrame:
    """
    Возвращает по каждому комплекту:
      - comments_total  — все замечания (по всей базе)
      - comments_closed — «Закрыто»
      - comments_annulled — «Аннулировано»
      - active_total    — активные (5 статусов)
      - ours            — АТП ТЛП
      - waiting         — Ждут заказчика
      - chronic         — Хроника
      - closed_doc      — Учтено (A/B)
      - categorized     — сколько активных разобрано (есть категория)
      - uncategorized   — сколько активных без категории
      - docs_total / doc_a / doc_b / doc_c / doc_info — листы
    """
    active = _load_active_df()

    # Фильтр по дисциплинам (если задан)
    if disciplines and not active.empty:
        active = active[active["discipline"].isin(disciplines)].copy()

    # ---- Листы по комплектам ----
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

        # ---- Общие цифры по всей базе ----
        q_base = """
            SELECT
                d.complex AS complex,
                COUNT(c.id) AS comments_total,
                SUM(CASE WHEN c.status = 'Закрыто' THEN 1 ELSE 0 END) AS comments_closed,
                SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS comments_annulled
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
        """
        params_base: list = []
        if disciplines:
            q_base += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
            params_base += list(disciplines)
        q_base += " GROUP BY d.complex"

        base_df = pd.read_sql(q_base, conn, params=params_base)

    # ---- Активные цифры из единого источника ----
    if not active.empty:
        active_c = active.copy()
        active_c["_is_cat"] = (
            active_c["category"].notna()
            & (active_c["category"].astype(str).str.strip() != "")
        )

        rows = []
        for cx, sub in active_c.groupby("complex"):
            row = {"complex": cx, "discipline": sub["discipline"].iloc[0] if len(sub) else None}

            row["active_total"] = len(sub)
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

            # Категоризация
            row["categorized"] = int(sub["_is_cat"].sum())
            row["uncategorized"] = len(sub) - row["categorized"]

            rows.append(row)

        active_cx = pd.DataFrame(rows)
    else:
        active_cx = pd.DataFrame(columns=[
            "complex", "discipline", "active_total", "ours", "waiting",
            "chronic", "closed_doc", "categorized", "uncategorized",
        ])

    # ---- Склейка ----
    df = docs_df.merge(base_df, on="complex", how="outer")
    df = df.merge(active_cx, on="complex", how="outer",
                  suffixes=("", "_y"))

    # Если discipline задублировалась — оставляем первую непустую
    if "discipline_y" in df.columns:
        df["discipline"] = df["discipline"].fillna(df["discipline_y"])
        df = df.drop(columns=["discipline_y"])

    df = df.fillna(0)

    # ---- Числовые типы ----
    int_cols = [
        "docs_total", "doc_a", "doc_b", "doc_c", "doc_info",
        "comments_total", "comments_closed", "comments_annulled",
        "active_total", "ours", "waiting", "chronic", "closed_doc",
        "categorized", "uncategorized",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = df[col].astype(int)

    # ---- Проценты ----
    df["comment_pct_closed"] = df.apply(
        lambda r: round(r["comments_closed"] / r["comments_total"] * 100, 1)
        if r["comments_total"] else 0.0,
        axis=1,
    )
    df["cat_pct"] = df.apply(
        lambda r: round(r["categorized"] / r["active_total"] * 100, 1)
        if r["active_total"] else 0.0,
        axis=1,
    )
    df["doc_pct"] = df.apply(
        lambda r: round((r["doc_a"] + r["doc_b"]) / r["docs_total"] * 100, 1)
        if r["docs_total"] else 0.0,
        axis=1,
    )

    return df


# ---------------------------------------------------------------------------
#  KPI вкладки
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    if df.empty:
        return

    total_active = int(df["active_total"].sum())
    total_ours = int(df["ours"].sum())
    total_waiting = int(df["waiting"].sum())
    total_chronic = int(df["chronic"].sum())
    total_closed = int(df["closed_doc"].sum())

    st.markdown("##### 📊 Сводка по отстающим")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Активных замечаний",
        f"{total_active:,}".replace(",", " "),
        help="Все активные замечания по выбранным комплектам.",
    )
    c2.metric(
        "🔴 АТП ТЛП",
        f"{total_ours:,}".replace(",", " "),
        help="Замечания, ожидающие ответа АТП ТЛП.",
    )
    c3.metric(
        "🔵 Ждут заказчика",
        f"{total_waiting:,}".replace(",", " "),
        help="АТП ТЛП ответил, заказчик не рассмотрел.",
    )
    c4.metric(
        "🔴 Хроника",
        f"{total_chronic:,}".replace(",", " "),
        help="Заказчик не рассматривает >90 р.д.",
    )
    c5.metric(
        "🟢 Учтено A/B",
        f"{total_closed:,}".replace(",", " "),
        help="Лист A или B, замечание не закрыто формально.",
    )


# ---------------------------------------------------------------------------
#  Таб 1: По % разбора (категоризация)
# ---------------------------------------------------------------------------
def _render_low_pct(df: pd.DataFrame):
    st.markdown("### 📉 Комплекты с низким % разбора (категоризации)")
    st.caption(
        "Комплекты, где **много активных замечаний**, но **мало** "
        "имеют назначенную категорию. Сюда стоит направить "
        "специалистов."
    )

    if df.empty:
        st.info("Нет данных.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        min_active = st.number_input(
            "Минимум активных замечаний",
            min_value=1, max_value=2000, value=20, step=5,
            key="lag_min_active_pct",
        )
    with c2:
        max_pct = st.slider(
            "Максимум % разбора",
            min_value=0, max_value=100, value=50, step=5,
            key="lag_max_pct",
        )
    with c3:
        limit = st.number_input(
            "Топ N",
            min_value=5, max_value=100, value=20, step=5,
            key="lag_limit_pct",
        )

    filtered = df[
        (df["active_total"] >= min_active)
        & (df["cat_pct"] <= max_pct)
    ].copy()

    if filtered.empty:
        st.info(
            f"Нет комплектов с ≥{min_active} активных и разбором ≤{max_pct}%."
        )
        return

    top = filtered.sort_values("cat_pct", ascending=True).head(int(limit))
    chart_df = top.sort_values("cat_pct", ascending=False)

    fig = px.bar(
        chart_df, x="cat_pct", y="complex", orientation="h",
        color="cat_pct",
        color_continuous_scale=["#E57373", "#FFB74D", "#FFD54F"],
        title=f"Топ-{limit} комплектов по низкому % разбора (≤{max_pct}%)",
        text="cat_pct",
        labels={"cat_pct": "% разбора", "complex": "Комплект"},
    )
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    fig.update_layout(
        height=max(400, 25 * len(chart_df)),
        coloraxis_showscale=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Отстающие_по_разбору", "lag_cat_pct")

    table = top[[
        "complex", "discipline", "active_total", "categorized",
        "uncategorized", "cat_pct",
    ]].rename(columns={
        "complex": "Комплект",
        "discipline": "Дисциплина",
        "active_total": "Активных",
        "categorized": "Разобрано",
        "uncategorized": "Осталось",
        "cat_pct": "% разбора",
    })
    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "% разбора": st.column_config.ProgressColumn(
                "% разбора", min_value=0, max_value=100, format="%.1f%%"),
        },
    )


# ---------------------------------------------------------------------------
#  Таб 2: По просрочкам АТП ТЛП
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_overdue_by_complex(disciplines: tuple = ()) -> pd.DataFrame:
    """Топ комплектов по количеству просрочек АТП ТЛП."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    if disciplines:
        active = active[active["discipline"].isin(disciplines)]

    overdue_flags = [
        "new_overdue", "in_work_overdue",
        "rejected_overdue", "discussion_overdue",
    ]
    sub = active[active["category_flag"].isin(overdue_flags)]

    if sub.empty:
        return pd.DataFrame()

    # Разбивка по 4 категориям просрочки
    def _flag_short(flag):
        return {
            "new_overdue": "Новое (просроч.)",
            "in_work_overdue": "В работе (просроч.)",
            "rejected_overdue": "Не принято (просроч.)",
            "discussion_overdue": "К обсужд. (просроч.)",
        }.get(flag, flag)

    sub = sub.copy()
    sub["_flag_short"] = sub["category_flag"].map(_flag_short)

    pivot = sub.pivot_table(
        index=["complex", "discipline"],
        columns="_flag_short",
        values="id",
        aggfunc="count",
        fill_value=0,
    )

    col_order = [
        "Новое (просроч.)",
        "В работе (просроч.)",
        "Не принято (просроч.)",
        "К обсужд. (просроч.)",
    ]
    pivot = pivot[[c for c in col_order if c in pivot.columns]]
    pivot["Всего просрочено"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("Всего просрочено", ascending=False)

    return pivot.reset_index()


def _render_overdue(df_stats: pd.DataFrame,
                    disciplines: tuple = ()):
    st.markdown("### 🚨 Комплекты с просрочками АТП ТЛП")
    st.caption(
        "Комплекты, где **много замечаний просрочено** с нашей стороны "
        "(>10 рабочих дней с момента выдачи/ответа). "
        "Единая логика с топ-10 на дашборде."
    )

    df_ov = _load_overdue_by_complex(disciplines)
    if df_ov.empty:
        st.success("🎉 Нет просрочек АТП ТЛП в выбранных дисциплинах.")
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        min_over = st.number_input(
            "Минимум просрочек",
            min_value=1, max_value=2000, value=10, step=5,
            key="lag_min_over",
        )
    with c2:
        limit = st.number_input(
            "Топ N",
            min_value=5, max_value=100, value=20, step=5,
            key="lag_limit_over",
        )

    filtered = df_ov[df_ov["Всего просрочено"] >= min_over].head(int(limit))
    if filtered.empty:
        st.info(f"Нет комплектов с ≥{min_over} просрочек.")
        return

    # Стек-бар по 4 категориям
    flag_cols = [c for c in filtered.columns
                 if c.endswith("(просроч.)")]
    long = filtered.melt(
        id_vars=["complex", "discipline", "Всего просрочено"],
        value_vars=flag_cols,
        var_name="Категория",
        value_name="Количество",
    )

    color_map = {
        "Новое (просроч.)":      "#64B5F6",
        "В работе (просроч.)":   "#FFD54F",
        "Не принято (просроч.)": "#E57373",
        "К обсужд. (просроч.)":  "#CE93D8",
    }

    chart_df = long.sort_values("Всего просрочено", ascending=True)

    fig = px.bar(
        chart_df, x="Количество", y="complex", orientation="h",
        color="Категория", barmode="stack",
        color_discrete_map=color_map,
        title=f"Топ-{limit} комплектов по просрочкам АТП ТЛП",
        labels={"complex": ""},
    )
    fig.update_layout(
        height=max(400, 28 * filtered["complex"].nunique()),
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Отстающие_просрочки_стек", "lag_over_stack",
                    width=1400, height=700)

    # Таблица
    table = filtered.rename(columns={
        "complex": "Комплект",
        "discipline": "Дисциплина",
    })
    st.dataframe(table, use_container_width=True, hide_index=True)

    # Экспорт
    with st.expander("📥 Выгрузить в Excel", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Просрочки")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Отстающие_просрочки_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
            key="lag_dl_overdue",
        )


# ---------------------------------------------------------------------------
#  Таб 3: По ожиданию заказчика
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_waiting_by_complex(disciplines: tuple = ()) -> pd.DataFrame:
    """Топ комплектов по количеству замечаний, ждущих заказчика."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    if disciplines:
        active = active[active["discipline"].isin(disciplines)]

    sub = active[active["category_flag"].isin([
        "waiting_customer", "waiting_customer_overdue",
        "waiting_customer_ontime", "waiting_customer_chronic",
    ])].copy()

    if sub.empty:
        return pd.DataFrame()

    # Свежие (10-30) / просроченные (30-90) / хроника (>90)
    def _bucket(row):
        days = row["days_waiting_customer"]
        if row["category_flag"] == "waiting_customer_chronic":
            return "🔴 Хроника (>90)"
        if days > 30:
            return "🟠 Просрочено (30–90)"
        if days > 10:
            return "🟡 Свежие (10–30)"
        return "🟢 В сроке (≤10)"

    sub["_bucket"] = sub.apply(_bucket, axis=1)

    pivot = sub.pivot_table(
        index=["complex", "discipline"],
        columns="_bucket",
        values="id",
        aggfunc="count",
        fill_value=0,
    )

    col_order = [
        "🟢 В сроке (≤10)", "🟡 Свежие (10–30)",
        "🟠 Просрочено (30–90)", "🔴 Хроника (>90)",
    ]
    pivot = pivot[[c for c in col_order if c in pivot.columns]]
    pivot["Всего ждут"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("Всего ждут", ascending=False)

    return pivot.reset_index()


def _render_waiting(df_stats: pd.DataFrame,
                    disciplines: tuple = ()):
    st.markdown("### ⏸ Комплекты, где заказчик задерживает рассмотрение")
    st.caption(
        "Комплекты, где **много замечаний ждут заказчика** — АТП ТЛП "
        "ответил, но ответ не рассмотрен. Красные — эскалация."
    )

    df_wait = _load_waiting_by_complex(disciplines)
    if df_wait.empty:
        st.success("🎉 Нет замечаний, ждущих заказчика.")
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        min_wait = st.number_input(
            "Минимум ждущих",
            min_value=1, max_value=2000, value=10, step=5,
            key="lag_min_wait",
        )
    with c2:
        limit = st.number_input(
            "Топ N",
            min_value=5, max_value=100, value=20, step=5,
            key="lag_limit_wait",
        )

    filtered = df_wait[df_wait["Всего ждут"] >= min_wait].head(int(limit))
    if filtered.empty:
        st.info(f"Нет комплектов с ≥{min_wait} ждущих.")
        return

    bucket_cols = [c for c in filtered.columns
                   if c.startswith(("🟢", "🟡", "🟠", "🔴"))]
    long = filtered.melt(
        id_vars=["complex", "discipline", "Всего ждут"],
        value_vars=bucket_cols,
        var_name="Возраст",
        value_name="Количество",
    )

    color_map = {
        "🟢 В сроке (≤10)":      "#A5D6A7",
        "🟡 Свежие (10–30)":     "#FFD54F",
        "🟠 Просрочено (30–90)": "#FFB74D",
        "🔴 Хроника (>90)":      "#E57373",
    }

    chart_df = long.sort_values("Всего ждут", ascending=True)

    fig = px.bar(
        chart_df, x="Количество", y="complex", orientation="h",
        color="Возраст", barmode="stack",
        color_discrete_map=color_map,
        title=f"Топ-{limit} комплектов по ожиданию заказчика",
        labels={"complex": ""},
    )
    fig.update_layout(
        height=max(400, 28 * filtered["complex"].nunique()),
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Отстающие_ждут_стек", "lag_wait_stack",
                    width=1400, height=700)

    table = filtered.rename(columns={
        "complex": "Комплект",
        "discipline": "Дисциплина",
    })
    st.dataframe(table, use_container_width=True, hide_index=True)

    with st.expander("📧 Выгрузить для письма заказчику", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Ждут заказчика")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Ждут_заказчика_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
            key="lag_dl_waiting",
        )


# ---------------------------------------------------------------------------
#  Таб 4: Детали комплекта
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_details(complex_code: str) -> pd.DataFrame:
    """Замечания комплекта с привязкой к категориям из _load_all_categorized."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()

    if df.empty:
        return pd.DataFrame()

    sub = df[df["complex"] == complex_code].copy()
    if sub.empty:
        return pd.DataFrame()

    # Порядок: сначала активные, потом прочие
    flag_order = {
        "new_overdue": 1, "new_in_progress": 2,
        "in_work_overdue": 3, "in_work_in_progress": 4,
        "rejected_overdue": 5, "rejected_in_progress": 6,
        "discussion_overdue": 7, "discussion_in_progress": 8,
        "waiting_customer_chronic": 9,
        "waiting_customer_overdue": 10,
        "waiting_customer": 11,
        "waiting_customer_ontime": 12,
        "closed_by_doc_status": 13,
        "abandoned": 14,
    }
    sub["_order"] = sub["category_flag"].map(flag_order).fillna(99)
    sub = sub.sort_values(["_order", "id"])

    # Отображаемые колонки
    view = sub[[
        "id", "discipline", "complex", "sheet", "sheet_name",
        "comment", "status", "doc_status", "author", "created",
        "category", "category_user",
    ]].copy()

    view = view.rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "sheet_name": "Название листа",
        "comment": "Замечание",
        "status": "Статус замечания",
        "doc_status": "Статус листа",
        "author": "Автор",
        "created": "Создано",
        "category": "Категория",
        "category_user": "Кто категоризировал",
    })

    return view


def _render_drilldown(df: pd.DataFrame, disciplines: tuple = ()):
    st.markdown("### 🔍 Детали комплекта")

    if df.empty:
        st.info("Нет данных.")
        return

    complexes = sorted(df["complex"].dropna().unique())
    sel = st.selectbox(
        "Выберите комплект",
        options=complexes,
        key="lag_drill_complex",
        placeholder="Начните вводить шифр...",
    )
    if not sel:
        return

    row = df[df["complex"] == sel].iloc[0]

    # ---- KPI комплекта ----
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Всего замечаний", int(row["comments_total"]))
    c2.metric("Активных", int(row["active_total"]))
    c3.metric("🔴 АТП ТЛП", int(row["ours"]))
    c4.metric("🔵 Ждут заказ.", int(row["waiting"]))
    c5.metric("🔴 Хроника", int(row["chronic"]))
    c6.metric("🟢 Учтено A/B", int(row["closed_doc"]))

    st.caption(
        f"**Дисциплина:** {row['discipline']} — "
        f"{discipline_name(row['discipline'])} · "
        f"**Листы:** A={int(row['doc_a'])}, B={int(row['doc_b'])}, "
        f"C={int(row['doc_c'])}, И={int(row['doc_info'])} "
        f"(всего {int(row['docs_total'])}) · "
        f"**% разбора:** {row['cat_pct']}%"
    )

    # ---- Разбивка по категориям ----
    st.markdown("##### 📊 Активные замечания по категориям")
    st.caption(
        "8 категорий из deadlines.py. Показывает, что именно держит "
        "комплект."
    )

    # ---- Таблица замечаний ----
    st.markdown(f"##### 📋 Замечания комплекта `{sel}`")
    details = _load_complex_details(sel)

    if details.empty:
        st.info("У комплекта нет замечаний.")
        return

    st.dataframe(
        details, use_container_width=True, hide_index=True,
        height=600,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Название листа": st.column_config.TextColumn(width="large"),
        },
    )

    with st.expander("📥 Выгрузить замечания в Excel", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            details.to_excel(writer, index=False, sheet_name="Замечания")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"{sel}_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
            key="lag_dl_drilldown",
        )


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("⚠️ Отстающие комплекты")
    st.caption(
        "Проблемные комплекты: где мало разобрано, где много просрочек "
        "с нашей стороны, где заказчик задерживает рассмотрение."
    )

    # Фильтр по дисциплине
    disc_options = _load_discipline_options()
    c_disc, _ = st.columns([2, 3])
    with c_disc:
        sel_disc_labels = st.multiselect(
            "Дисциплина",
            options=list(disc_options.values()),
            placeholder="Все дисциплины",
            key="lag_disc",
        )
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    disciplines_tuple = tuple(sel_disc) if sel_disc else ()

    with st.spinner("Загрузка данных..."):
        df = _load_complex_stats(disciplines_tuple)

    if df.empty:
        st.info("Нет данных по комплектам.")
        return

    # KPI
    _render_kpi(df)

    st.divider()

    # 4 под-вкладки
    tab_pct, tab_overdue, tab_waiting, tab_drill = st.tabs([
        "📉 По % разбора",
        "🚨 По просрочкам АТП ТЛП",
        "⏸ По ожиданию заказчика",
        "🔍 Детали комплекта",
    ])

    with tab_pct:
        _render_low_pct(df)
    with tab_overdue:
        _render_overdue(df, disciplines_tuple)
    with tab_waiting:
        _render_waiting(df, disciplines_tuple)
    with tab_drill:
        _render_drilldown(df, disciplines_tuple)