# vitro/ui/dynamics.py
"""
📈 Динамика — история + выдача + закрытие замечаний.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Снимки
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
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
            "% выполнения", f"{pct_now}%",
            delta=f"{round(pct_now - pct_prev, 1)} п.п. за интервал",
        )
    else:
        cur = df[df["snapshot_date"] == dates[-1]]
        total_now = int(cur["total"].sum())
        closed_now = int(cur["closed"].sum())
        pct_now = round(closed_now / total_now * 100, 1) if total_now else 0
        c4.metric("% выполнения", f"{pct_now}%")

    if n_snapshots == 1:
        st.info("📌 Пока один снимок. Тренды появятся после второго запуска "
                "`sync_runner.py`.")

    return n_snapshots >= 2


# ---------------------------------------------------------------------------
#  Общая динамика
# ---------------------------------------------------------------------------
def _render_overall_chart(df: pd.DataFrame):
    st.markdown("### 📊 История по снимкам")

    agg = df.groupby("snapshot_date").agg(
        total=("total", "sum"),
        closed=("closed", "sum"),
        active=("active", "sum"),
        annulled=("annulled", "sum"),
    ).reset_index()
    agg["percent"] = ((agg["closed"] + agg["annulled"]) / agg["total"] * 100).round(1)

    if len(agg) < 2:
        cur = agg.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Всего замечаний", f"{int(cur['total']):,}".replace(",", " "))
        c2.metric("Закрыто", f"{int(cur['closed']):,}".replace(",", " "))
        c3.metric("Активных", f"{int(cur['active']):,}".replace(",", " "))
        c4.metric("Аннулировано", f"{int(cur['annulled']):,}".replace(",", " "))
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=agg["snapshot_date"], y=agg["total"],
                             mode="lines+markers", name="Всего",
                             line=dict(color="#64B5F6", width=3)))
    fig.add_trace(go.Scatter(x=agg["snapshot_date"], y=agg["closed"],
                             mode="lines+markers", name="Закрыто",
                             line=dict(color="#2E7D32", width=3)))
    fig.add_trace(go.Scatter(x=agg["snapshot_date"], y=agg["active"],
                             mode="lines+markers", name="Активных",
                             line=dict(color="#E57373", width=3)))
    fig.add_trace(go.Scatter(x=agg["snapshot_date"], y=agg["annulled"],
                             mode="lines+markers", name="Аннулировано",
                             line=dict(color="#BDBDBD", width=2, dash="dot")))

    fig.update_layout(title="Замечания по снимкам",
                      xaxis_title="Дата снимка", yaxis_title="Количество",
                      height=450, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Динамика_общая", "dyn_overall")

    fig2 = px.line(agg, x="snapshot_date", y="percent", markers=True,
                   title="% выполнения замечаний",
                   labels={"snapshot_date": "Дата", "percent": "% выполнения"})
    fig2.update_traces(line=dict(color="#2E7D32", width=3))
    fig2.update_layout(height=350)
    st.plotly_chart(fig2, use_container_width=True)
    download_plotly(fig2, "Динамика_процент", "dyn_pct")


# ---------------------------------------------------------------------------
#  По дисциплинам
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
            .rename(columns={"discipline": "Код", "name": "Дисциплина",
                             "total": "Всего", "closed": "Закрыто",
                             "annulled": "Аннулировано",
                             "percent": "% выполнения"}),
            use_container_width=True, hide_index=True,
        )
        return

    fig = px.line(by_disc, x="snapshot_date", y="percent",
                  color="discipline", markers=True,
                  labels={"snapshot_date": "Дата", "percent": "%",
                          "discipline": "Дисциплина"},
                  title="% выполнения по дисциплинам")
    fig.update_layout(height=500, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Динамика_по_дисциплинам", "dyn_disc")


# ---------------------------------------------------------------------------
#  Выдача/закрытие
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_issue_stats() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym ORDER BY ym
        """, conn)


@st.cache_data(ttl=300, show_spinner=False)
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


@st.cache_data(ttl=300, show_spinner=False)
def _load_fix_stats() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто', 'Выполнено')
            GROUP BY ym ORDER BY ym
        """, conn)


@st.cache_data(ttl=300, show_spinner=False)
def _load_fix_by_discipline() -> pd.DataFrame:
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


def _render_issue_and_fix_section():
    st.markdown("## 📤 Выдача и ✅ Закрытие замечаний")

    st.caption(
        "**Выдача** — по дате создания замечания (`created`). "
        "**Закрытие** — по фактической дате устранения (`fix_date`), "
        "только для закрытых и выполненных замечаний."
    )

    issue = _load_issue_stats()
    fix = _load_fix_stats()

    if issue.empty and fix.empty:
        st.info("Нет данных ни по выдаче, ни по закрытию.")
        return

    if fix.empty and not issue.empty:
        st.warning("⚠️ Нет данных по закрытию. Проверьте поле "
                   "`VitroBaseCommentFixDate` и запустите синхронизацию.")

    # ============ ВЫДАЧА ============
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
            fig.update_layout(height=420, xaxis_tickangle=-45, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_выдача", "dyn_issue")

        with col2:
            total_issue = int(issue["n"].sum())
            best_issue = issue.loc[issue["n"].idxmax()]
            worst_issue = issue.loc[issue["n"].idxmin()]
            st.metric("Всего выдано", f"{total_issue:,}".replace(",", " "))
            st.metric("Пик выдачи", best_issue["ym"],
                      delta=f"{int(best_issue['n']):,}".replace(",", " "),
                      delta_color="off")
            st.metric("Минимум", worst_issue["ym"],
                      delta=f"{int(worst_issue['n']):,}".replace(",", " "),
                      delta_color="off")

        by_disc = _load_issue_by_discipline()
        if not by_disc.empty:
            fig = px.bar(by_disc, x="ym", y="n", color="discipline",
                         barmode="stack",
                         labels={"ym": "Месяц", "n": "Выдано",
                                 "discipline": "Дисциплина"},
                         title="Выдача по дисциплинам")
            fig.update_layout(height=500, xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Динамика_выдача_по_дисциплинам", "dyn_issue_disc")

    st.divider()

    # ============ ЗАКРЫТИЕ ============
    st.markdown("### ✅ Закрытие замечаний по месяцам (по фактической дате)")

    if fix.empty:
        st.info("Данных по закрытию нет. Поле `fix_date` пустое в БД.")
        return

    col1, col2 = st.columns([2, 1])

    with col1:
        fig = px.bar(
            fix, x="ym", y="n", text="n",
            labels={"ym": "Месяц", "n": "Закрыто"},
            color_discrete_sequence=["#2E7D32"],
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(height=420, xaxis_tickangle=-45, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Динамика_закрытие", "dyn_fix")

    with col2:
        total_fix = int(fix["n"].sum())
        best_fix = fix.loc[fix["n"].idxmax()]
        worst_fix = fix.loc[fix["n"].idxmin()]
        st.metric("Всего закрыто", f"{total_fix:,}".replace(",", " "))
        st.metric("Пик закрытия", best_fix["ym"],
                  delta=f"{int(best_fix['n']):,}".replace(",", " "),
                  delta_color="off")
        st.metric("Минимум", worst_fix["ym"],
                  delta=f"{int(worst_fix['n']):,}".replace(",", " "),
                  delta_color="off")

    by_disc_fix = _load_fix_by_discipline()
    if not by_disc_fix.empty:
        fig = px.bar(by_disc_fix, x="ym", y="n", color="discipline",
                     barmode="stack",
                     labels={"ym": "Месяц", "n": "Закрыто",
                             "discipline": "Дисциплина"},
                     title="Закрытие по дисциплинам")
        fig.update_layout(height=500, xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Динамика_закрытие_по_дисциплинам", "dyn_fix_disc")

    st.divider()

    # ============ НАКОПИТЕЛЬНО ============
    st.markdown("### 📊 Накопительная динамика: выдано vs закрыто")

    issue_cum = issue.copy()
    issue_cum["cumulative"] = issue_cum["n"].cumsum()
    fix_cum = fix.copy()
    fix_cum["cumulative"] = fix_cum["n"].cumsum()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=issue_cum["ym"], y=issue_cum["cumulative"],
        mode="lines+markers", name="Всего выдано (накопительно)",
        line=dict(color="#64B5F6", width=3)))
    fig.add_trace(go.Scatter(
        x=fix_cum["ym"], y=fix_cum["cumulative"],
        mode="lines+markers", name="Всего закрыто (накопительно)",
        line=dict(color="#2E7D32", width=3)))

    fig.update_layout(
        title="Накопительно: выдано vs закрыто",
        xaxis_title="Месяц", yaxis_title="Всего замечаний",
        height=450, xaxis_tickangle=-45, hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Динамика_накопительно", "dyn_cumulative")

    st.caption("**Расстояние между линиями** — накопленная задолженность.")


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📈 Динамика")

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🔄 Обновить", key="dyn_refresh",
                     use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    st.caption("История по снимкам + выдача и закрытие замечаний по месяцам.")

    df = _load_snapshots()

    if not df.empty:
        has_multiple = _render_kpi(df)
        st.divider()

        if has_multiple:
            _render_overall_chart(df)
            st.divider()
            _render_by_discipline(df)
        else:
            _render_overall_chart(df)
            st.divider()
            _render_by_discipline(df)

    st.divider()
    _render_issue_and_fix_section()