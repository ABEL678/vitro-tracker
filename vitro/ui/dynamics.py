# vitro/ui/dynamics.py
"""
📈 Динамика — потоки замечаний по месяцам.

Инструмент РП и Директора: сколько выдаётся, отвечаем, закрывается.
Каскадные фильтры: дисциплина → раздел → расположение → комплект.
Период: пресеты + календарь.
"""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name


# ---------------------------------------------------------------------------
#  Расположение (4-й блок шифра)
# ---------------------------------------------------------------------------
BLOCK_OPTIONS = {
    "Корпус 1":                    "1",
    "Корпус 2":                    "2",
    "Общие":                       "0",
    "Стилобат":                    "С",
    "Газовая котельная":           "ГК",
    "Генплан":                     "ГП",
    "Автомобильные дороги (УДС)":  "А",
}


def _extract_4th_block(complex_code) -> str:
    if not complex_code:
        return ""
    parts = str(complex_code).split("-")
    if len(parts) >= 4:
        return parts[3].strip().upper()
    return ""


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
    """Комплекты с учётом дисциплины, раздела и расположения."""
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
                WHERE section IN ({ph}) AND complex IS NOT NULL)""")
            params += list(sections)
        rows = conn.execute(f"""
            SELECT c.code, c.name FROM complexes c
            WHERE {' AND '.join(where)}
            ORDER BY c.code
        """, tuple(params)).fetchall()

    result = {r["code"]: f"{r['code']} — {r['name']}"
                       if r["name"] else r["code"]
            for r in rows}

    # Фильтр по расположению (в Python — 4-й блок)
    if block_codes:
        filtered = {}
        for code, label in result.items():
            block = _extract_4th_block(code)
            if block in block_codes:
                filtered[code] = label
        return filtered
    return result


# ---------------------------------------------------------------------------
#  WHERE по фильтрам
# ---------------------------------------------------------------------------
def _build_filter_sql(disciplines: tuple, sections: tuple,
                       kits: tuple, block_codes: tuple,
                       date_from: date, date_to: date,
                       date_col: str,
                       alias: str = "d") -> tuple[str, list]:
    parts = []
    params: list = []
    if disciplines:
        parts.append(
            f"{alias}.discipline IN ({','.join('?' * len(disciplines))})")
        params += list(disciplines)
    if sections:
        parts.append(
            f"{alias}.section IN ({','.join('?' * len(sections))})")
        params += list(sections)
    if kits:
        parts.append(
            f"{alias}.complex IN ({','.join('?' * len(kits))})")
        params += list(kits)
    if block_codes:
        # 4-й блок — через LIKE
        ors = []
        for bc in block_codes:
            ors.append(f"{alias}.complex LIKE '%-{bc}-%'")
            ors.append(f"{alias}.complex LIKE '%-{bc.lower()}-%'")
        parts.append("(" + " OR ".join(ors) + ")")
    if date_from:
        parts.append(f"{date_col} >= ?")
        params.append(date_from.isoformat())
    if date_to:
        parts.append(f"{date_col} <= ?")
        params.append((date_to + timedelta(days=1)).isoformat())
    return (" AND " + " AND ".join(parts)) if parts else "", params


# ---------------------------------------------------------------------------
#  Потоки по месяцам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_issue_stats(disciplines: tuple, sections: tuple,
                       kits: tuple, block_codes: tuple,
                       date_from: date, date_to: date) -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.created")
    q = f"""
        SELECT substr(c.created, 1, 7) AS ym, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.created IS NOT NULL AND c.created <> ''
          {where}
        GROUP BY ym ORDER BY ym
    """
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_our_answers_stats(disciplines: tuple, sections: tuple,
                              kits: tuple, block_codes: tuple,
                              date_from: date, date_to: date) -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date")
    q = f"""
        SELECT substr(c.fix_date, 1, 7) AS ym, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
          AND c.status IN ('Закрыто', 'Выполнено')
          {where}
        GROUP BY ym ORDER BY ym
    """
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_customer_closed_stats(disciplines: tuple, sections: tuple,
                                  kits: tuple, block_codes: tuple,
                                  date_from: date, date_to: date) -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date")
    q = f"""
        SELECT substr(c.fix_date, 1, 7) AS ym, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
          AND c.status = 'Закрыто'
          {where}
        GROUP BY ym ORDER BY ym
    """
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_issue_by_discipline(disciplines: tuple, sections: tuple,
                                kits: tuple, block_codes: tuple,
                                date_from: date, date_to: date) -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.created")
    q = f"""
        SELECT substr(c.created, 1, 7) AS ym,
               d.discipline AS discipline, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.created IS NOT NULL AND c.created <> ''
          AND d.discipline IS NOT NULL
          {where}
        GROUP BY ym, d.discipline ORDER BY ym, d.discipline
    """
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_our_answers_by_discipline(disciplines: tuple, sections: tuple,
                                      kits: tuple, block_codes: tuple,
                                      date_from: date, date_to: date) -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date")
    q = f"""
        SELECT substr(c.fix_date, 1, 7) AS ym,
               d.discipline AS discipline, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
          AND c.status IN ('Закрыто', 'Выполнено')
          AND d.discipline IS NOT NULL
          {where}
        GROUP BY ym, d.discipline ORDER BY ym, d.discipline
    """
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_customer_closed_by_discipline(disciplines: tuple, sections: tuple,
                                          kits: tuple, block_codes: tuple,
                                          date_from: date, date_to: date) -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date")
    q = f"""
        SELECT substr(c.fix_date, 1, 7) AS ym,
               d.discipline AS discipline, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
          AND c.status = 'Закрыто'
          AND d.discipline IS NOT NULL
          {where}
        GROUP BY ym, d.discipline ORDER BY ym, d.discipline
    """
    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(issue: pd.DataFrame,
                 our_answers: pd.DataFrame,
                 cust_closed: pd.DataFrame):
    total_issue = int(issue["n"].sum()) if not issue.empty else 0
    total_our = int(our_answers["n"].sum()) if not our_answers.empty else 0
    total_cust = int(cust_closed["n"].sum()) if not cust_closed.empty else 0

    st.markdown("##### 📦 Общий объём за период")

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Выдано замечаний", f"{total_issue:,}".replace(",", " "),
        help="Сколько замечаний поступило от заказчика за выбранный период.",
    )
    c2.metric(
        "Наши ответы", f"{total_our:,}".replace(",", " "),
        help="Сколько замечаний АТП ТЛП перевёл в «Выполнено» "
             "или «Закрыто» за период.",
    )
    c3.metric(
        "Закрыто заказчиком", f"{total_cust:,}".replace(",", " "),
        help="Сколько замечаний заказчик формально закрыл за период.",
    )


# ---------------------------------------------------------------------------
#  Потоки по месяцам
# ---------------------------------------------------------------------------
def _render_monthly_flow(issue: pd.DataFrame,
                          our_answers: pd.DataFrame,
                          cust_closed: pd.DataFrame):
    st.markdown("### 📊 Потоки по месяцам")

    frames = []
    if not issue.empty:
        df = issue.rename(columns={"n": "Выдано"}).copy()
        frames.append(df.set_index("ym"))
    if not our_answers.empty:
        df = our_answers.rename(columns={"n": "Наши ответы"}).copy()
        frames.append(df.set_index("ym"))
    if not cust_closed.empty:
        df = cust_closed.rename(columns={"n": "Закрыто заказчиком"}).copy()
        frames.append(df.set_index("ym"))

    if not frames:
        st.info("Нет данных за выбранный период.")
        return

    merged = pd.concat(frames, axis=1).fillna(0).sort_index()
    merged = merged.reset_index().rename(columns={"index": "ym"})

    fig = go.Figure()

    if "Выдано" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Выдано"],
            mode="lines+markers", name="📤 Выдано",
            line=dict(color="#64B5F6", width=3),
            marker=dict(size=7),
        ))

    if "Наши ответы" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Наши ответы"],
            mode="lines+markers", name="🔴 Наши ответы",
            line=dict(color="#E57373", width=3),
            marker=dict(size=7),
        ))

    if "Закрыто заказчиком" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Закрыто заказчиком"],
            mode="lines+markers", name="🟢 Закрыто заказчиком",
            line=dict(color="#2E7D32", width=3),
            marker=dict(size=7),
        ))

    fig.update_layout(
        title="Выдача / Наши ответы / Закрытие — по месяцам",
        xaxis_title="Месяц",
        yaxis_title="Замечаний",
        height=500,
        xaxis_tickangle=-45,
        hovermode="x unified",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
#  Накопительно
# ---------------------------------------------------------------------------
def _render_cumulative(issue: pd.DataFrame,
                        our_answers: pd.DataFrame,
                        cust_closed: pd.DataFrame):
    st.markdown("### 📈 Накопительно за период")

    frames = []
    if not issue.empty:
        df = issue.rename(columns={"n": "Выдано"}).copy()
        frames.append(df.set_index("ym"))
    if not our_answers.empty:
        df = our_answers.rename(columns={"n": "Наши ответы"}).copy()
        frames.append(df.set_index("ym"))
    if not cust_closed.empty:
        df = cust_closed.rename(columns={"n": "Закрыто заказчиком"}).copy()
        frames.append(df.set_index("ym"))

    if not frames:
        st.info("Нет данных за выбранный период.")
        return

    merged = pd.concat(frames, axis=1).fillna(0).sort_index()
    merged = merged.cumsum()
    merged = merged.reset_index().rename(columns={"index": "ym"})

    fig = go.Figure()

    if "Выдано" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Выдано"],
            mode="lines", name="📤 Всего выдано",
            line=dict(color="#64B5F6", width=3),
        ))

    if "Наши ответы" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Наши ответы"],
            mode="lines", name="🔴 Наши ответы",
            line=dict(color="#E57373", width=3),
        ))

    if "Закрыто заказчиком" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Закрыто заказчиком"],
            mode="lines", name="🟢 Закрыто заказчиком",
            line=dict(color="#2E7D32", width=3),
        ))

    fig.update_layout(
        title="Накопительно: выдано vs наши ответы vs закрыто",
        xaxis_title="Месяц",
        yaxis_title="Всего замечаний",
        height=500,
        xaxis_tickangle=-45,
        hovermode="x unified",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
def _render_by_discipline(disciplines: tuple, sections: tuple,
                            kits: tuple, block_codes: tuple,
                            date_from: date, date_to: date):
    st.markdown("### 🏷 По дисциплинам")

    tab_issue, tab_our, tab_cust = st.tabs([
        "📤 Выдача", "🔴 Наши ответы", "🟢 Закрыто заказчиком"
    ])

    with tab_issue:
        df = _load_issue_by_discipline(
            disciplines, sections, kits, block_codes, date_from, date_to)
        if df.empty:
            st.info("Нет данных.")
        else:
            df["label"] = df["discipline"].apply(
                lambda c: f"{c} — {discipline_name(c)}")
            fig = px.bar(
                df, x="ym", y="n", color="label",
                barmode="stack",
                labels={"ym": "Месяц", "n": "Выдано",
                        "label": "Дисциплина"},
            )
            fig.update_layout(height=500, xaxis_tickangle=-45,
                                legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)

    with tab_our:
        df = _load_our_answers_by_discipline(
            disciplines, sections, kits, block_codes, date_from, date_to)
        if df.empty:
            st.info("Нет данных.")
        else:
            df["label"] = df["discipline"].apply(
                lambda c: f"{c} — {discipline_name(c)}")
            fig = px.bar(
                df, x="ym", y="n", color="label",
                barmode="stack",
                labels={"ym": "Месяц", "n": "Ответов",
                        "label": "Дисциплина"},
            )
            fig.update_layout(height=500, xaxis_tickangle=-45,
                                legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)

    with tab_cust:
        df = _load_customer_closed_by_discipline(
            disciplines, sections, kits, block_codes, date_from, date_to)
        if df.empty:
            st.info("Нет данных.")
        else:
            df["label"] = df["discipline"].apply(
                lambda c: f"{c} — {discipline_name(c)}")
            fig = px.bar(
                df, x="ym", y="n", color="label",
                barmode="stack",
                labels={"ym": "Месяц", "n": "Закрыто",
                        "label": "Дисциплина"},
            )
            fig.update_layout(height=500, xaxis_tickangle=-45,
                                legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
#  Фильтр периода
# ---------------------------------------------------------------------------
_PRESETS = [
    ("3 мес.",  3),
    ("6 мес.",  6),
    ("12 мес.", 12),
    ("24 мес.", 24),
    ("Всё",     0),
]


def _render_period_filter() -> tuple[date, date]:
    today = date.today()
    min_date = date(2020, 1, 1)

    if "dyn_preset" not in st.session_state:
        st.session_state["dyn_preset"] = "12 мес."
    if "dyn_from_input" not in st.session_state:
        st.session_state["dyn_from_input"] = today - timedelta(days=365)
    if "dyn_to_input" not in st.session_state:
        st.session_state["dyn_to_input"] = today

    st.markdown("##### 📅 Период")

    preset_labels = [p[0] for p in _PRESETS]
    current = st.session_state["dyn_preset"]
    if current not in preset_labels:
        current = "12 мес."

    c1, c2, c3 = st.columns([2, 1, 1])

    with c1:
        preset = st.radio(
            "Пресет",
            options=preset_labels,
            index=preset_labels.index(current),
            horizontal=True,
            label_visibility="collapsed",
            key="dyn_preset_radio",
        )

    if preset != current:
        st.session_state["dyn_preset"] = preset
        if preset != "Всё":
            months = dict(_PRESETS)[preset]
            st.session_state["dyn_from_input"] = (
                today - timedelta(days=30 * months)
            )
            st.session_state["dyn_to_input"] = today
        else:
            st.session_state["dyn_from_input"] = min_date
            st.session_state["dyn_to_input"] = today
        st.rerun()

    with c2:
        date_from = st.date_input(
            "От",
            min_value=min_date,
            max_value=today,
            key="dyn_from_input",
        )
    with c3:
        date_to = st.date_input(
            "До",
            min_value=min_date,
            max_value=today,
            key="dyn_to_input",
        )

    return date_from, date_to


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📈 Динамика")
    st.caption(
        "Инструмент РП и Директора: потоки замечаний по месяцам."
    )

    # ===== Каскадные фильтры (как в Сроках) =====
    disc_options = _load_discipline_options()

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина",
            options=list(disc_options.values()),
            placeholder="Все",
            key="dyn_disc",
        )
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    section_options = _load_section_options(
        tuple(sel_disc) if sel_disc else ())
    with c2:
        if section_options:
            sel_section_labels = st.multiselect(
                "Раздел",
                options=list(section_options.values()),
                placeholder="Все",
                key="dyn_section",
            )
            sel_section = [code for code, label in section_options.items()
                            if label in sel_section_labels]
        else:
            sel_section = []
            st.multiselect("Раздел", options=[],
                            placeholder="—",
                            disabled=True, key="dyn_section_empty")

    with c3:
        sel_blocks = st.multiselect(
            "Расположение",
            options=list(BLOCK_OPTIONS.keys()),
            placeholder="Все",
            key="dyn_blocks",
        )
        block_codes = tuple(BLOCK_OPTIONS[b] for b in sel_blocks)

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
            key="dyn_kit",
        )
        sel_kit = [code for code, label in kit_options.items()
                    if label in sel_kit_labels]

    # ===== Период =====
    date_from, date_to = _render_period_filter()

    disc_t = tuple(sel_disc)
    sect_t = tuple(sel_section)
    kit_t = tuple(sel_kit)

    st.divider()

    with st.spinner("Загрузка..."):
        issue = _load_issue_stats(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to)
        our_answers = _load_our_answers_stats(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to)
        cust_closed = _load_customer_closed_stats(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to)

    _render_kpi(issue, our_answers, cust_closed)

    st.divider()

    _render_monthly_flow(issue, our_answers, cust_closed)

    st.divider()

    _render_cumulative(issue, our_answers, cust_closed)

    st.divider()

    _render_by_discipline(
        disc_t, sect_t, kit_t, block_codes, date_from, date_to)