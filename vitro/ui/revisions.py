# vitro/ui/revisions.py
"""
🔁 Ревизии — аналитика по перевыпускам листов РД.

Ключевые метрики:
  - Средняя ревизия листа (по проекту / дисциплине / комплекту).
  - Листы с ревизией > 10 — «зона турбулентности».
  - Связь: ревизий на одно закрытое замечание — эффективность процесса.

Логика подсчёта:
  - Ревизия листа = MAX(CAST(revision AS INTEGER)) для каждого leaf.
  - Исключаются: 'xx', 'XX' (аннулированные), '999' (placeholder),
    status = 'Аннулировано'.
"""

import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Пороги интерпретации
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "avg_good": 5,      # средняя ревизия < 5 — норма
    "avg_warn": 10,     # 5–10 — тревога
    "max_good": 5,      # макс < 5 — норма
    "max_warn": 10,     # 5–10 — тревога
    "pct_over_10_good": 5,   # < 5% листов с rev > 10 — норма
    "pct_over_10_warn": 15,  # 5–15% — тревога, > 15% — критично
}


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
#  Главные метрики по проекту
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_project_metrics() -> dict:
    """
    Глобальные метрики по ревизиям (по всему проекту).
    Считаем по каждому уникальному leaf: MAX(revision).
    """
    with get_conn() as conn:
        row = conn.execute("""
            WITH actual_rev AS (
                SELECT
                    leaf,
                    MAX(CAST(revision AS INTEGER)) AS max_rev
                FROM documents
                WHERE revision <> '' AND revision IS NOT NULL
                  AND revision NOT IN ('xx', 'XX')
                  AND revision <> '999'
                  AND status <> 'Аннулировано'
                GROUP BY leaf
            )
            SELECT
                COUNT(*) AS total_sheets,
                ROUND(AVG(max_rev), 2) AS avg_rev,
                MAX(max_rev) AS max_rev,
                SUM(CASE WHEN max_rev > 10 THEN 1 ELSE 0 END) AS over_10,
                SUM(CASE WHEN max_rev > 5 THEN 1 ELSE 0 END) AS over_5,
                SUM(CASE WHEN max_rev = 0 THEN 1 ELSE 0 END) AS at_zero
            FROM actual_rev
        """).fetchone()

    return {
        "total_sheets": row["total_sheets"] or 0,
        "avg_rev": row["avg_rev"] or 0,
        "max_rev": row["max_rev"] or 0,
        "over_10": row["over_10"] or 0,
        "over_5": row["over_5"] or 0,
        "at_zero": row["at_zero"] or 0,
    }


# ---------------------------------------------------------------------------
#  Гистограмма распределения
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_histogram() -> pd.DataFrame:
    """Сколько листов на каждой ревизии 0, 1, 2, ..., 30+."""
    with get_conn() as conn:
        return pd.read_sql("""
            WITH actual_rev AS (
                SELECT
                    leaf,
                    MAX(CAST(revision AS INTEGER)) AS max_rev
                FROM documents
                WHERE revision <> '' AND revision IS NOT NULL
                  AND revision NOT IN ('xx', 'XX')
                  AND revision <> '999'
                  AND status <> 'Аннулировано'
                GROUP BY leaf
            )
            SELECT
                CASE WHEN max_rev > 30 THEN 30 ELSE max_rev END AS rev_bucket,
                COUNT(*) AS n
            FROM actual_rev
            GROUP BY rev_bucket
            ORDER BY rev_bucket
        """, conn)


# ---------------------------------------------------------------------------
#  По комплектам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_by_complex() -> pd.DataFrame:
    """Сводка по каждому комплекту: средняя/макс ревизия, кол-во листов."""
    with get_conn() as conn:
        return pd.read_sql("""
            WITH actual_rev AS (
                SELECT
                    leaf,
                    complex,
                    MAX(CAST(revision AS INTEGER)) AS max_rev
                FROM documents
                WHERE revision <> '' AND revision IS NOT NULL
                  AND revision NOT IN ('xx', 'XX')
                  AND revision <> '999'
                  AND status <> 'Аннулировано'
                  AND complex IS NOT NULL
                GROUP BY leaf, complex
            )
            SELECT
                complex,
                COUNT(*) AS n_sheets,
                ROUND(AVG(max_rev), 1) AS avg_rev,
                MAX(max_rev) AS max_rev,
                SUM(CASE WHEN max_rev > 10 THEN 1 ELSE 0 END) AS over_10
            FROM actual_rev
            GROUP BY complex
            HAVING n_sheets >= 5
            ORDER BY avg_rev DESC
        """, conn)


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_by_discipline() -> pd.DataFrame:
    """Сводка по каждой дисциплине."""
    with get_conn() as conn:
        return pd.read_sql("""
            WITH actual_rev AS (
                SELECT
                    d.leaf,
                    d.discipline,
                    MAX(CAST(d.revision AS INTEGER)) AS max_rev
                FROM documents d
                WHERE d.revision <> '' AND d.revision IS NOT NULL
                  AND d.revision NOT IN ('xx', 'XX')
                  AND d.revision <> '999'
                  AND d.status <> 'Аннулировано'
                  AND d.discipline IS NOT NULL
                GROUP BY d.leaf, d.discipline
            )
            SELECT
                discipline,
                COUNT(*) AS n_sheets,
                ROUND(AVG(max_rev), 1) AS avg_rev,
                MAX(max_rev) AS max_rev,
                SUM(CASE WHEN max_rev > 10 THEN 1 ELSE 0 END) AS over_10
            FROM actual_rev
            GROUP BY discipline
            ORDER BY avg_rev DESC
        """, conn)


# ---------------------------------------------------------------------------
#  Связь ревизий с замечаниями
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_revisions_per_issue() -> pd.DataFrame:
    """
    Метрика: сколько ревизий приходится на одно замечание по комплекту.
    Показывает эффективность процесса — много ревизий при малом числе
    замечаний = плохой процесс.
    """
    with get_conn() as conn:
        return pd.read_sql("""
            WITH actual_rev AS (
                SELECT
                    complex,
                    SUM(CAST(revision AS INTEGER)) AS sum_rev,
                    COUNT(*) AS n_sheets
                FROM (
                    SELECT complex, leaf,
                           MAX(CAST(revision AS INTEGER)) AS revision
                    FROM documents
                    WHERE revision <> '' AND revision IS NOT NULL
                      AND revision NOT IN ('xx', 'XX')
                      AND revision <> '999'
                      AND status <> 'Аннулировано'
                      AND complex IS NOT NULL
                    GROUP BY complex, leaf
                )
                GROUP BY complex
            ),
            issue_stats AS (
                SELECT
                    d.complex,
                    COUNT(c.id) AS n_issues,
                    SUM(CASE WHEN c.status IN ('Закрыто','Выполнено')
                             THEN 1 ELSE 0 END) AS n_closed
                FROM comments c
                JOIN documents d ON c.doc_id = d.id
                WHERE d.complex IS NOT NULL
                GROUP BY d.complex
            )
            SELECT
                r.complex,
                r.n_sheets,
                r.sum_rev,
                i.n_issues,
                i.n_closed,
                ROUND(CAST(r.sum_rev AS REAL) / NULLIF(i.n_issues, 0), 2)
                    AS rev_per_issue
            FROM actual_rev r
            JOIN issue_stats i ON i.complex = r.complex
            WHERE i.n_issues >= 10
            ORDER BY rev_per_issue DESC
        """, conn)


# ---------------------------------------------------------------------------
#  Листы для drill-down
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_sheets_for_complex(complex_code: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            WITH actual_rev AS (
                SELECT
                    leaf,
                    MAX(CAST(revision AS INTEGER)) AS max_rev,
                    COUNT(*) AS n_versions
                FROM documents
                WHERE complex = ?
                  AND revision <> '' AND revision IS NOT NULL
                  AND revision NOT IN ('xx', 'XX')
                  AND revision <> '999'
                  AND status <> 'Аннулировано'
                GROUP BY leaf
            )
            SELECT
                leaf AS "Лист",
                max_rev AS "Ревизия",
                n_versions AS "Версий в БД"
            FROM actual_rev
            ORDER BY max_rev DESC, leaf
        """, conn, params=(complex_code,))


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(m: dict):
    total = m["total_sheets"]
    if total == 0:
        st.warning("Нет данных по ревизиям.")
        return

    pct_over_10 = round(m["over_10"] / total * 100, 1)
    pct_over_5 = round(m["over_5"] / total * 100, 1)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Всего листов", f"{total:,}".replace(",", " "))
    c2.metric("Средняя ревизия", f"{m['avg_rev']}")
    c3.metric("Максимальная", m["max_rev"])
    c4.metric(
        "Листов с rev > 5",
        f"{m['over_5']:,}".replace(",", " "),
        delta=f"{pct_over_5}%",
        delta_color="off",
    )
    c5.metric(
        "Листов с rev > 10",
        f"{m['over_10']:,}".replace(",", " "),
        delta=f"{pct_over_10}%",
        delta_color="inverse" if pct_over_10 > THRESHOLDS["pct_over_10_good"] else "off",
    )


# ---------------------------------------------------------------------------
#  Интерпретация
# ---------------------------------------------------------------------------
def _render_interpretation(m: dict):
    st.markdown("### 🧭 Что это значит")

    total = m["total_sheets"]
    if total == 0:
        return

    pct_over_10 = m["over_10"] / total * 100
    avg = m["avg_rev"]

    # Вердикт по средней ревизии
    if avg < THRESHOLDS["avg_good"]:
        st.success(
            f"✅ **Средняя ревизия {avg}** — процесс в норме. "
            f"Большинство листов перевыпускались 1–4 раза."
        )
    elif avg < THRESHOLDS["avg_warn"]:
        st.warning(
            f"🟡 **Средняя ревизия {avg}** — есть зона турбулентности. "
            f"Стоит разобрать комплекты-чемпионы."
        )
    else:
        st.error(
            f"🔴 **Средняя ревизия {avg}** — критично высокая. "
            f"Системная проблема с процессом согласования."
        )

    # Вердикт по листам с rev > 10
    if pct_over_10 < THRESHOLDS["pct_over_10_good"]:
        st.success(
            f"✅ Листов с ревизией > 10 всего **{pct_over_10:.1f}%** — "
            f"это единичные случаи."
        )
    elif pct_over_10 < THRESHOLDS["pct_over_10_warn"]:
        st.warning(
            f"🟡 Листов с ревизией > 10 — **{pct_over_10:.1f}%**. "
            f"Есть заметная группа «долгожителей»."
        )
    else:
        st.error(
            f"🔴 Листов с ревизией > 10 — **{pct_over_10:.1f}%**. "
            f"Каждый пятый лист перевыпускается многократно. "
            f"Это системный сигнал."
        )


# ---------------------------------------------------------------------------
#  Графики
# ---------------------------------------------------------------------------
def _render_histogram():
    st.markdown("### 📊 Распределение листов по ревизиям")

    hist = _load_histogram()
    if hist.empty:
        st.info("Нет данных.")
        return

    hist = hist.rename(columns={"rev_bucket": "Ревизия", "n": "Листов"})

    fig = px.bar(
        hist, x="Ревизия", y="Листов",
        text="Листов",
        color="Ревизия",
        color_continuous_scale=[
            (0, "#A5D6A7"),
            (0.15, "#FFD54F"),
            (0.4, "#FFB74D"),
            (0.7, "#E57373"),
            (1.0, "#7F0000"),
        ],
        title="Сколько листов на каждой ревизии (0–30)",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=420, showlegend=False, coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Ревизии_гистограмма", "rev_hist")

    st.caption(
        "**Как читать:** пик в районе 0–3 — норма. Длинный хвост справа "
        "(10+) — проблемная зона, где листы перевыпускаются многократно."
    )


def _render_by_discipline():
    st.markdown("### 🏷 Средняя ревизия по дисциплинам")

    df = _load_by_discipline()
    if df.empty:
        st.info("Нет данных.")
        return

    df["name"] = df["discipline"].apply(discipline_name)
    df = df.sort_values("avg_rev", ascending=True)

    fig = px.bar(
        df, x="avg_rev", y="discipline", orientation="h",
        text="avg_rev",
        color="avg_rev",
        color_continuous_scale=[
            (0, "#A5D6A7"), (0.3, "#FFD54F"),
            (0.6, "#FFB74D"), (1.0, "#E57373"),
        ],
        labels={"avg_rev": "Средняя ревизия", "discipline": ""},
    )
    fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
    fig.update_layout(
        height=max(350, 30 * len(df)),
        coloraxis_showscale=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Ревизии_по_дисциплинам", "rev_disc")

    # Таблица с деталями
    table = df[["discipline", "name", "n_sheets", "avg_rev",
                "max_rev", "over_10"]].rename(columns={
        "discipline": "Код",
        "name": "Дисциплина",
        "n_sheets": "Листов",
        "avg_rev": "Средняя рев.",
        "max_rev": "Макс. рев.",
        "over_10": "Листов > 10",
    })
    st.dataframe(table, use_container_width=True, hide_index=True)


def _render_by_complex():
    st.markdown("### 🏗 Топ-20 комплектов по средней ревизии")

    df = _load_by_complex()
    if df.empty:
        st.info("Нет данных.")
        return

    top = df.head(20).sort_values("avg_rev", ascending=True)

    fig = px.bar(
        top, x="avg_rev", y="complex", orientation="h",
        text="avg_rev",
        color="avg_rev",
        color_continuous_scale=[
            (0, "#A5D6A7"), (0.3, "#FFD54F"),
            (0.6, "#FFB74D"), (1.0, "#E57373"),
        ],
        labels={"avg_rev": "Средняя ревизия", "complex": ""},
    )
    fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
    fig.update_layout(
        height=max(400, 25 * len(top)),
        coloraxis_showscale=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Ревизии_топ_комплектов", "rev_top_cx")


def _render_revisions_per_issue():
    st.markdown("### 📉 Ревизий на одно замечание (эффективность процесса)")

    df = _load_revisions_per_issue()
    if df.empty:
        st.info("Нет данных.")
        return

    # Норма: около 0.2–0.5 (1 ревизия на 2–5 замечаний).
    # > 1 — тревога (ревизий больше, чем замечаний).
    df["verdict"] = df["rev_per_issue"].apply(
        lambda x: "🔴 >1" if x > 1 else ("🟡 0.5–1" if x > 0.5 else "🟢 <0.5")
    )

    top = df.head(20).sort_values("rev_per_issue", ascending=True)

    fig = px.bar(
        top, x="rev_per_issue", y="complex", orientation="h",
        text="rev_per_issue",
        color="rev_per_issue",
        color_continuous_scale=[
            (0, "#A5D6A7"), (0.5, "#FFD54F"),
            (0.7, "#FFB74D"), (1.0, "#E57373"),
        ],
        labels={"rev_per_issue": "Ревизий / замечаний", "complex": ""},
    )
    fig.update_traces(texttemplate="%{text:.2f}", textposition="outside")
    fig.update_layout(
        height=max(400, 25 * len(top)),
        coloraxis_showscale=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Ревизии_на_замечание", "rev_per_issue")

    st.caption(
        "**Как читать:** сколько ревизий приходится на одно замечание по "
        "комплекту. \n"
        "- **< 0.5** — эффективно (одна ревизия закрывает 2+ замечания).\n"
        "- **0.5–1** — норма.\n"
        "- **> 1** — плохо (ревизий больше, чем замечаний)."
    )


# ---------------------------------------------------------------------------
#  Drill-down
# ---------------------------------------------------------------------------
def _render_drilldown():
    st.markdown("### 🔍 Детали комплекта")

    df = _load_by_complex()
    if df.empty:
        st.info("Нет данных.")
        return

    complexes = sorted(df["complex"].unique())
    sel = st.selectbox(
        "Выберите комплект", options=complexes,
        key="rev_drill_complex",
        placeholder="Начните вводить шифр...",
    )
    if not sel:
        return

    sheets = _load_sheets_for_complex(sel)
    if sheets.empty:
        st.info("Нет листов у комплекта.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Листов", len(sheets))
    c2.metric("Средняя ревизия", round(sheets["Ревизия"].mean(), 1))
    c3.metric("Макс. ревизия", int(sheets["Ревизия"].max()))

    st.dataframe(sheets.head(200), use_container_width=True,
                 hide_index=True, height=400)
    if len(sheets) > 200:
        st.caption(f"Показаны первые 200 из {len(sheets)}.")


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("🔁 Ревизии")
    st.caption(
        "Аналитика по перевыпускам листов РД. Показывает, где процесс "
        "согласования затянут и требует вмешательства."
    )

    # Кнопка «Обновить» убрана — данные из кэша (TTL 1 час).

    with st.spinner("Загрузка данных по ревизиям..."):
        metrics = _load_project_metrics()

    _render_kpi(metrics)

    st.divider()
    _render_interpretation(metrics)

    st.divider()
    _render_histogram()

    st.divider()
    _render_by_discipline()

    st.divider()
    _render_by_complex()

    st.divider()
    _render_revisions_per_issue()

    st.divider()
    _render_drilldown()