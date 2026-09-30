# vitro/ui/dynamics.py
"""
📈 Динамика — история + выдача + рассмотрение замечаний.

Два независимых потока:
  🔴 НАШИ ОТВЕТЫ — fix_date + статус «Выполнено» или «Закрыто»
  🟢 ЗАКРЫТО ЗАКАЗЧИКОМ — fix_date + статус «Закрыто»

ВАЖНО: снимки history_summary формируются старым sync_runner
и могут не совпадать с дашбордом по «Активным / % выполнения».
Добавлена пометка в UI.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Снимки (без изменений)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_snapshots() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql("""
            SELECT h.snapshot_date, h.complex, h.total, h.closed,
                   h.annulled, h.active, h.percent
            FROM history_summary h
            ORDER BY h.snapshot_date, h.complex
        """, conn)
        complexes = pd.read_sql(
            "SELECT code, discipline FROM complexes", conn)

    if df.empty:
        return df

    df = df.merge(complexes, left_on="complex", right_on="code",
                  how="left").drop(columns=["code"], errors="ignore")
    df["discipline"] = df["discipline"].fillna("—")
    return df


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame) -> bool:
    if df.empty:
        st.warning("Нет снимков истории. Запустите `sync_runner.py`.")
        return False

    dates = sorted(df["snapshot_date"].unique())
    n_snapshots = len(dates)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Снимков", n_snapshots)
    c2.metric("Первый", dates[0])
    c3.metric("Последний", dates[-1])

    if n_snapshots >= 2:
        last, prev = dates[-1], dates[-2]
        cur = df[df["snapshot_date"] == last]
        old = df[df["snapshot_date"] == prev]
        total_now = int(cur["total"].sum())
        total_prev = int(old["total"].sum())
        closed_now = int(cur["closed"].sum())
        closed_prev = int(old["closed"].sum())
        pct_now = round(closed_now / total_now * 100, 1) if total_now else 0
        pct_prev = round(closed_prev / total_prev * 100, 1) if total_prev else 0
        c4.metric(
            "% выполнения (по снимкам)", f"{pct_now}%",
            delta=f"{round(pct_now - pct_prev, 1)} п.п. за интервал",
        )
    else:
        cur = df[df["snapshot_date"] == dates[-1]]
        total_now = int(cur["total"].sum())
        closed_now = int(cur["closed"].sum())
        pct_now = round(closed_now / total_now * 100, 1) if total_now else 0
        c4.metric("% выполнения (по снимкам)", f"{pct_now}%")

    st.info(
        "ℹ️ **Снимки — это архив на дату.** Они фиксируют состояние на "
        "момент запуска синхронизации и включают замечания в статусе "
        "«Выполнено» в число закрытых. **Цифры по снимкам и по "
        "дашборду могут расходиться** — на дашборде «Закрыто» "
        "означает только формально закрытые замечания."
    )

    if n_snapshots == 1:
        st.warning("📌 Пока один снимок. Тренды появятся после "
                   "следующего запуска синхронизации.")

    return n_snapshots >= 2


# ---------------------------------------------------------------------------
#  Общая динамика по снимкам (без изменений)
# ---------------------------------------------------------------------------
def _render_overall_chart(df: pd.DataFrame):
    st.markdown("### 📊 История по снимкам")

    agg = df.groupby("snapshot_date").agg(
        total=("total", "sum"),
        closed=("closed", "sum"),
        active=("active", "sum"),
        annulled=("annulled", "sum"),
    ).reset_index()
    agg["percent"] = ((agg["closed"] + agg["annulled"])
                       / agg["total"] * 100).round(1)

    if len(agg) < 2:
        cur = agg.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Всего замечаний",
                  f"{int(cur['total']):,}".replace(",", " "))
        c2.metric("Закрыто (по снимку)",
                  f"{int(cur['closed']):,}".replace(",", " "))
        c3.metric("Активных (по снимку)",
                  f"{int(cur['active']):,}".replace(",", " "))
        c4.metric("Аннулировано",
                  f"{int(cur['annulled']):,}".replace(",", " "))
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=agg["snapshot_date"], y=agg["total"],
        mode="lines+markers", name="Всего",
        line=dict(color="#64B5F6", width=3)))
    fig.add_trace(go.Scatter(
        x=agg["snapshot_date"], y=agg["closed"],
        mode="lines+markers", name="Закрыто (по снимку)",
        line=dict(color="#2E7D32", width=3)))
    fig.add_trace(go.Scatter(
        x=agg["snapshot_date"], y=agg["active"],
        mode="lines+markers", name="Активных (по снимку)",
        line=dict(color="#E57373", width=3)))
    fig.add_trace(go.Scatter(
        x=agg["snapshot_date"], y=agg["annulled"],
        mode="lines+markers", name="Аннулировано",
        line=dict(color="#BDBDBD", width=2, dash="dot")))

    fig.update_layout(
        title="Замечания по снимкам",
        xaxis_title="Дата снимка", yaxis_title="Количество",
        height=450, hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Динамика_общая", "dyn_overall")

    fig2 = px.line(
        agg, x="snapshot_date", y="percent", markers=True,
        title="% выполнения замечаний (по снимкам)",
        labels={"snapshot_date": "Дата", "percent": "% выполнения"},
    )
    fig2.update_traces(line=dict(color="#2E7D32", width=3))
    fig2.update_layout(height=350)
    st.plotly_chart(fig2, use_container_width=True)
    download_plotly(fig2, "Динамика_процент", "dyn_pct")


# ---------------------------------------------------------------------------
#  По дисциплинам (без изменений)
# ---------------------------------------------------------------------------
def _render_by_discipline(df: pd.DataFrame):
    st.markdown("### 🏷 История по дисциплинам")

    if df.empty or df["discipline"].nunique() == 0:
        st.info("Нет данных по дисциплинам.")
        return

    by_disc = df.groupby(["snapshot_date", "discipline"]).agg(
        total=("total", "sum"),
        closed=("closed", "sum"),
        annulled=("annulled", "sum"),
    ).reset_index()
    by_disc["percent"] = (
        (by_disc["closed"] + by_disc["annulled"]) / by_disc["total"] * 100
    ).round(1)

    if len(by_disc["snapshot_date"].unique()) < 2:
        last_date = by_disc["snapshot_date"].max()
        current = by_disc[by_disc["snapshot_date"] == last_date].copy()
        current["name"] = current["discipline"].apply(discipline_name)
        current = current.sort_values("percent", ascending=False)
        st.dataframe(
            current[["discipline", "name", "total", "closed",
                     "annulled", "percent"]]
            .rename(columns={
                "discipline": "Код", "name": "Дисциплина",
                "total": "Всего", "closed": "Закрыто (снимок)",
                "annulled": "Аннулировано",
                "percent": "% выполнения",
            }),
            use_container_width=True, hide_index=True,
        )
        return

    fig = px.line(
        by_disc, x="snapshot_date", y="percent",
        color="discipline", markers=True,
        labels={"snapshot_date": "Дата", "percent": "%",
                "discipline": "Дисциплина"},
        title="% выполнения по дисциплинам (по снимкам)",
    )
    fig.update_layout(height=500, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Динамика_по_дисциплинам", "dyn_disc")


# ---------------------------------------------------------------------------
#  Загрузчики для выдачи и рассмотрения
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_issue_stats() -> pd.DataFrame:
    """Выдача замечаний по месяцам (created)."""
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym ORDER BY ym
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_issue_by_discipline() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(c.created, 1, 7) AS ym,
                   d.discipline AS discipline, COUNT(*) AS n
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.created IS NOT NULL AND c.created <> ''
              AND d.discipline IS NOT NULL
            GROUP BY ym, d.discipline ORDER BY ym, d.discipline
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_our_answers_stats() -> pd.DataFrame:
    """
    НАШИ ОТВЕТЫ по месяцам.
    fix_date + статус «Выполнено» или «Закрыто».
    """
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто', 'Выполнено')
            GROUP BY ym ORDER BY ym
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_our_answers_by_discipline() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(c.fix_date, 1, 7) AS ym,
                   d.discipline AS discipline, COUNT(*) AS n
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
              AND c.status IN ('Закрыто', 'Выполнено')
              AND d.discipline IS NOT NULL
            GROUP BY ym, d.discipline ORDER BY ym, d.discipline
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_customer_closed_stats() -> pd.DataFrame:
    """
    ЗАКРЫТО ЗАКАЗЧИКОМ по месяцам.
    fix_date + статус «Закрыто» (только формально закрытые).
    """
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status = 'Закрыто'
            GROUP BY ym ORDER BY ym
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_customer_closed_by_discipline() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(c.fix_date, 1, 7) AS ym,
                   d.discipline AS discipline, COUNT(*) AS n
            FROM comments c JOIN documents d ON c.doc_id = d.id
            WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
              AND c.status = 'Закрыто'
              AND d.discipline IS NOT NULL
            GROUP BY ym, d.discipline ORDER BY ym, d.discipline
        """, conn)


# ---------------------------------------------------------------------------
#  Выдача и рассмотрение
# ---------------------------------------------------------------------------
def _render_issue_and_fix_section():
    st.markdown("## 📤 Выдача и рассмотрение замечаний")

    st.caption(
        "**Выдача** — по дате, когда замечание появилось в базе. "
        "**Наши ответы** — АТП ТЛП дал ответ (статус «Выполнено» или "
        "«Закрыто»). "
        "**Закрыто заказчиком** — заказчик формально закрыл замечание "
        "(статус «Закрыто»)."
    )

    issue = _load_issue_stats()
    our_answers = _load_our_answers_stats()
    cust_closed = _load_customer_closed_stats()

    if issue.empty and our_answers.empty:
        st.info("Нет данных ни по выдаче, ни по ответам.")
        return

    # =====================================================================
    #  ВЫДАЧА
    # =====================================================================
    st.markdown("### 📤 Выдача замечаний по месяцам")

    if not issue.empty:
        col1, col2 = st.columns([2, 1])

        with col1:
            fig = px.bar(
                issue, x="ym", y="n", text="n",
                labels={"ym": "Месяц", "n": "Выдано"},
                color_discrete_sequence=["#64B5F6"],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=420, xaxis_tickangle=-45,
                              showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_выдача", "dyn_issue")

        with col2:
            total_issue = int(issue["n"].sum())
            best_issue = issue.loc[issue["n"].idxmax()]
            worst_issue = issue.loc[issue["n"].idxmin()]
            st.metric("Всего выдано",
                      f"{total_issue:,}".replace(",", " "))
            st.metric("Пик выдачи", best_issue["ym"],
                      delta=f"{int(best_issue['n']):,}".replace(",", " "),
                      delta_color="off")
            st.metric("Минимум", worst_issue["ym"],
                      delta=f"{int(worst_issue['n']):,}".replace(",", " "),
                      delta_color="off")

        by_disc = _load_issue_by_discipline()
        if not by_disc.empty:
            fig = px.bar(
                by_disc, x="ym", y="n", color="discipline",
                barmode="stack",
                labels={"ym": "Месяц", "n": "Выдано",
                        "discipline": "Дисциплина"},
                title="Выдача по дисциплинам",
            )
            fig.update_layout(height=500, xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_выдача_по_дисциплинам",
                            "dyn_issue_disc")

    st.divider()

    # =====================================================================
    #  НАШИ ОТВЕТЫ
    # =====================================================================
    st.markdown("### 🔴 Наши ответы по месяцам")
    st.caption(
        "АТП ТЛП ответил на замечание (статус «Выполнено» или «Закрыто»). "
        "Это **наша работа**, независимо от того, рассмотрел ли заказчик."
    )

    if our_answers.empty:
        st.info("Данных по нашим ответам нет.")
    else:
        col1, col2 = st.columns([2, 1])

        with col1:
            fig = px.bar(
                our_answers, x="ym", y="n", text="n",
                labels={"ym": "Месяц", "n": "Ответов"},
                color_discrete_sequence=["#E57373"],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=420, xaxis_tickangle=-45,
                              showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_наши_ответы", "dyn_our")

        with col2:
            total_our = int(our_answers["n"].sum())
            best_our = our_answers.loc[our_answers["n"].idxmax()]
            worst_our = our_answers.loc[our_answers["n"].idxmin()]
            st.metric("Всего ответов",
                      f"{total_our:,}".replace(",", " "))
            st.metric("Пик ответов", best_our["ym"],
                      delta=f"{int(best_our['n']):,}".replace(",", " "),
                      delta_color="off")
            st.metric("Минимум", worst_our["ym"],
                      delta=f"{int(worst_our['n']):,}".replace(",", " "),
                      delta_color="off")

        by_disc_our = _load_our_answers_by_discipline()
        if not by_disc_our.empty:
            fig = px.bar(
                by_disc_our, x="ym", y="n", color="discipline",
                barmode="stack",
                labels={"ym": "Месяц", "n": "Ответов",
                        "discipline": "Дисциплина"},
                title="Наши ответы по дисциплинам",
            )
            fig.update_layout(height=500, xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_ответы_по_дисциплинам",
                            "dyn_our_disc")

    st.divider()

    # =====================================================================
    #  ЗАКРЫТО ЗАКАЗЧИКОМ
    # =====================================================================
    st.markdown("### 🟢 Закрыто заказчиком по месяцам")
    st.caption(
        "Заказчик рассмотрел наш ответ и **формально закрыл** замечание "
        "(статус «Закрыто»). Это **его работа**."
    )

    if cust_closed.empty:
        st.info("Данных по закрытию заказчиком нет.")
    else:
        col1, col2 = st.columns([2, 1])

        with col1:
            fig = px.bar(
                cust_closed, x="ym", y="n", text="n",
                labels={"ym": "Месяц", "n": "Закрыто заказчиком"},
                color_discrete_sequence=["#2E7D32"],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=420, xaxis_tickangle=-45,
                              showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_закрыто_заказчиком", "dyn_cust")

        with col2:
            total_cust = int(cust_closed["n"].sum())
            best_cust = cust_closed.loc[cust_closed["n"].idxmax()]
            worst_cust = cust_closed.loc[cust_closed["n"].idxmin()]
            st.metric("Всего закрыто",
                      f"{total_cust:,}".replace(",", " "))
            st.metric("Пик закрытия", best_cust["ym"],
                      delta=f"{int(best_cust['n']):,}".replace(",", " "),
                      delta_color="off")
            st.metric("Минимум", worst_cust["ym"],
                      delta=f"{int(worst_cust['n']):,}".replace(",", " "),
                      delta_color="off")

        by_disc_cust = _load_customer_closed_by_discipline()
        if not by_disc_cust.empty:
            fig = px.bar(
                by_disc_cust, x="ym", y="n", color="discipline",
                barmode="stack",
                labels={"ym": "Месяц", "n": "Закрыто",
                        "discipline": "Дисциплина"},
                title="Закрыто заказчиком по дисциплинам",
            )
            fig.update_layout(height=500, xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_закрыто_дисциплины",
                            "dyn_cust_disc")

    st.divider()

    # =====================================================================
    #  НАКОПИТЕЛЬНО — ТРИ ЛИНИИ
    # =====================================================================
    st.markdown("### 📊 Накопительная динамика")

    st.caption(
        "**Расстояние между линиями** — накопленная задолженность. "
        "**Выдано vs Наши ответы** — наша работа. "
        "**Наши ответы vs Закрыто заказчиком** — работа заказчика."
    )

    fig = go.Figure()

    if not issue.empty:
        issue_cum = issue.copy()
        issue_cum["cumulative"] = issue_cum["n"].cumsum()
        fig.add_trace(go.Scatter(
            x=issue_cum["ym"], y=issue_cum["cumulative"],
            mode="lines+markers", name="📤 Всего выдано",
            line=dict(color="#64B5F6", width=3),
        ))

    if not our_answers.empty:
        our_cum = our_answers.copy()
        our_cum["cumulative"] = our_cum["n"].cumsum()
        fig.add_trace(go.Scatter(
            x=our_cum["ym"], y=our_cum["cumulative"],
            mode="lines+markers", name="🔴 Наши ответы",
            line=dict(color="#E57373", width=3),
        ))

    if not cust_closed.empty:
        cust_cum = cust_closed.copy()
        cust_cum["cumulative"] = cust_cum["n"].cumsum()
        fig.add_trace(go.Scatter(
            x=cust_cum["ym"], y=cust_cum["cumulative"],
            mode="lines+markers", name="🟢 Закрыто заказчиком",
            line=dict(color="#2E7D32", width=3),
        ))

    fig.update_layout(
        title="Накопительно: выдано vs наши ответы vs закрыто",
        xaxis_title="Месяц", yaxis_title="Всего замечаний",
        height=500, xaxis_tickangle=-45, hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Динамика_накопительно", "dyn_cumulative")

    st.caption(
        "**Как читать:** если **Выдано** растёт быстрее **Наших ответов** "
        "— наша задолженность растёт. Если **Наши ответы** растут быстрее "
        "**Закрыто заказчиком** — задолженность на стороне заказчика."
    )


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📈 Динамика")

    # Кнопка «Обновить» убрана — данные из кэша (TTL 1 час).

    st.caption(
        "История по снимкам + выдача и рассмотрение замечаний по месяцам. "
        "**Два независимых потока:** наши ответы и закрытие заказчиком."
    )

    df = _load_snapshots()

    if not df.empty:
        has_multiple = _render_kpi(df)
        st.divider()

        _render_overall_chart(df)
        st.divider()
        _render_by_discipline(df)
        st.divider()

    _render_issue_and_fix_section()