# vitro/ui/dashboard.py
"""
📊 Дашборд РП — сводная страница состояния проекта.

Стиль: топ проблем + автокомментарии. Одна страница — вся картина.
Что внутри:
  - Светофор: 8 KPI в две строки.
  - Топ-5 проблем по каждой категории.
  - Таблица «Где болит» по дисциплинам с вердиктом.
  - Динамика 12 месяцев (выдача/закрытие/сальдо).
  - Блок «Что делать сегодня» — автогенерируемые действия.
  - Экспорт в PDF с графиками.
"""

import io
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.workdays import add_workdays, parse_date, workdays_between
from vitro.ui._utils import download_plotly
from vitro.pdf_builder import draw_header_footer


# ---------------------------------------------------------------------------
#  Загрузка всех данных одним запросом
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_full_state() -> dict:
    """
    Возвращает словарь со всеми данными для дашборда.
    Кэшируется на 5 минут.
    """
    today = date.today()
    result = {}

    with get_conn() as conn:
        # --- Общая сводка ---
        result["totals"] = dict(conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'Аннулировано' THEN 1 ELSE 0 END) AS annulled
            FROM comments
        """).fetchone())

        # --- Все активные замечания с данными для категоризации ---
        rows = conn.execute("""
            SELECT
                c.id, c.status, c.created, c.fix_date, c.category_date,
                d.discipline, d.complex
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению','Выполнено')
              AND c.created IS NOT NULL AND c.created <> ''
        """).fetchall()

    # --- Категоризация просрочек ---
    overdue_ours = 0
    waiting_customer = 0
    abandoned = 0
    in_progress = 0
    by_disc = {}  # {дисциплина: {"overdue": n, "waiting": n, "total": n}}

    for r in rows:
        created = parse_date(r["created"])
        if not created:
            continue

        fix_d = parse_date(r["fix_date"]) if r["fix_date"] else None
        cat_d = parse_date(r["category_date"]) if r["category_date"] else None
        status = r["status"]
        disc = r["discipline"] or "—"

        # Сроки
        due = add_workdays(created, 10)
        cust_due = add_workdays(fix_d, 10) if fix_d else None

        movements = [d for d in [created, fix_d, cat_d] if d]
        last_movement = max(movements) if movements else created
        days_since = (today - last_movement).days

        # Категоризация
        if fix_d and status == "Выполнено" and today > cust_due:
            waiting_customer += 1
            by_disc.setdefault(disc, {"overdue": 0, "waiting": 0, "total": 0})
            by_disc[disc]["waiting"] += 1
            by_disc[disc]["total"] += 1
        elif not fix_d and status != "Выполнено" and today > due:
            if days_since > 90:
                abandoned += 1
            else:
                overdue_ours += 1
            by_disc.setdefault(disc, {"overdue": 0, "waiting": 0, "total": 0})
            by_disc[disc]["overdue"] += 1
            by_disc[disc]["total"] += 1
        elif not fix_d and status != "Выполнено":
            in_progress += 1
            by_disc.setdefault(disc, {"overdue": 0, "waiting": 0, "total": 0})
            by_disc[disc]["total"] += 1

    result["overdue_ours"] = overdue_ours
    result["waiting_customer"] = waiting_customer
    result["abandoned"] = abandoned
    result["in_progress"] = in_progress
    result["by_disc"] = by_disc

    # --- Топ-5 комплектов по проблемам ---
    with get_conn() as conn:
        result["top_overdue_complex"] = pd.read_sql("""
            SELECT d.complex, COUNT(*) AS n
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
            GROUP BY d.complex
            ORDER BY n DESC
            LIMIT 5
        """, conn)

        result["top_waiting_authors"] = pd.read_sql("""
            SELECT c.author, COUNT(*) AS n
            FROM comments c
            WHERE c.status = 'Выполнено'
              AND c.author IS NOT NULL AND c.author <> ''
            GROUP BY c.author
            ORDER BY n DESC
            LIMIT 5
        """, conn)

        # --- Динамика 12 месяцев ---
        issue = pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym
        """, conn)

        fix = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто','Выполнено')
            GROUP BY ym
        """, conn)

    # Объединяем выдачу и закрытие
    monthly = pd.merge(
        issue.rename(columns={"n": "Выдано"}),
        fix.rename(columns={"n": "Закрыто"}),
        on="ym", how="outer",
    ).fillna(0).sort_values("ym")

    monthly["Выдано"] = monthly["Выдано"].astype(int)
    monthly["Закрыто"] = monthly["Закрыто"].astype(int)
    monthly["Сальдо"] = monthly["Выдано"] - monthly["Закрыто"]

    result["monthly"] = monthly.tail(12)

    return result


# ---------------------------------------------------------------------------
#  Светофор — 8 KPI в две строки
# ---------------------------------------------------------------------------
def _render_traffic_light(data: dict):
    totals = data["totals"]
    total = totals["total"] or 0
    closed = totals["closed"] or 0
    active = totals["active"] or 0
    annulled = totals["annulled"] or 0

    pct_closed = round((closed + annulled) / total * 100, 1) if total else 0

    # Ряд 1 — масштаб
    st.markdown("##### 📦 Масштаб проекта")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего замечаний", f"{total:,}".replace(",", " "))
    c2.metric("Закрыто", f"{closed:,}".replace(",", " "))
    c3.metric("Активных", f"{active:,}".replace(",", " "))
    c4.metric("% выполнения", f"{pct_closed}%")

    # Ряд 2 — проблемы (светофор)
    st.markdown("##### 🚦 Требует внимания")
    c1, c2, c3, c4 = st.columns(4)

    overdue = data["overdue_ours"]
    waiting = data["waiting_customer"]
    abandoned = data["abandoned"]
    in_progress = data["in_progress"]

    c1.metric(
        "🔴 Ждут нашего ответа",
        f"{overdue:,}".replace(",", " "),
        help="Просрочено >10 р.д. с нашей стороны",
    )
    c2.metric(
        "🔵 Ждут заказчика",
        f"{waiting:,}".replace(",", " "),
        help="Мы ответили, заказчик не рассмотрел >10 р.д.",
    )
    c3.metric(
        "🟡 Заброшено",
        f"{abandoned:,}".replace(",", " "),
        help=">90 дней без движения",
    )
    c4.metric(
        "🟢 В работе, в срок",
        f"{in_progress:,}".replace(",", " "),
        help="Активные, в пределах 10 р.д.",
    )


# ---------------------------------------------------------------------------
#  Топ-5 проблем
# ---------------------------------------------------------------------------
def _render_top_problems(data: dict):
    st.markdown("### 🔥 Топ-5 проблем")

    col1, col2 = st.columns(2)

    # --- Комплекты с максимумом просрочек ---
    with col1:
        st.markdown("##### 🏗 Комплекты с просрочками")
        top = data["top_overdue_complex"]
        if top.empty:
            st.info("Нет данных.")
        else:
            top = top.sort_values("n", ascending=True)
            fig = px.bar(
                top, x="n", y="complex", orientation="h",
                text="n",
                labels={"n": "Просрочек", "complex": ""},
                color_discrete_sequence=["#E57373"],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(300, 30 * len(top)))
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Дашборд_топ_комплектов", "dash_top_cx")

    # --- Авторы, чьи замечания ждут заказчика ---
    with col2:
        st.markdown("##### 👤 Авторы, чьи замечания ждут заказчика")
        top_auth = data["top_waiting_authors"]
        if top_auth.empty:
            st.info("Нет данных.")
        else:
            top_auth = top_auth.sort_values("n", ascending=True)
            fig = px.bar(
                top_auth, x="n", y="author", orientation="h",
                text="n",
                labels={"n": "Ожидают", "author": ""},
                color_discrete_sequence=["#64B5F6"],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(300, 30 * len(top_auth)))
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Дашборд_топ_авторов", "dash_top_auth")


# ---------------------------------------------------------------------------
#  Таблица «Где болит» по дисциплинам
# ---------------------------------------------------------------------------
def _render_discipline_table(data: dict):
    st.markdown("### 🏷 Где болит — по дисциплинам")

    by_disc = data["by_disc"]
    if not by_disc:
        st.info("Нет данных.")
        return

    rows = []
    for disc, s in by_disc.items():
        total = s["total"]
        overdue = s["overdue"]
        waiting = s["waiting"]

        # Вердикт
        if overdue > 100:
            verdict = "🔴 Проблема"
        elif overdue > 20 or waiting > 100:
            verdict = "🟡 Внимание"
        else:
            verdict = "🟢 Норма"

        rows.append({
            "Дисциплина": disc,
            "Наименование": discipline_name(disc),
            "🔴 Ждут нас": overdue,
            "🔵 Ждут заказчика": waiting,
            "Всего проблем": total,
            "Вердикт": verdict,
        })

    df = pd.DataFrame(rows).sort_values(
        ["🔴 Ждут нас", "🔵 Ждут заказчика"], ascending=False)

    st.dataframe(
        df, use_container_width=True, hide_index=True, height=420,
        column_config={
            "🔴 Ждут нас": st.column_config.NumberColumn(format="%d"),
            "🔵 Ждут заказчика": st.column_config.NumberColumn(format="%d"),
        },
    )


# ---------------------------------------------------------------------------
#  Динамика 12 месяцев
# ---------------------------------------------------------------------------
def _render_dynamics(data: dict):
    st.markdown("### 📈 Потоки: выдача, ответы, отставание")

    monthly = data["monthly"]
    if monthly.empty:
        st.info("Нет данных.")
        return

    st.caption(
        "**Выдача** — новые замечания от заказчика (вход). "
        "**Ответы** — наши ответы на ранее выданные (выход). "
        "**Отставание** — насколько мы не успеваем за темпом выдачи."
    )

    # --- График 1: выдача и ответы ---
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=monthly["ym"], y=monthly["Выдано"],
        name="Выдано замечаний",
        marker_color="#64B5F6",
        text=monthly["Выдано"], textposition="outside",
    ))
    fig.add_trace(go.Bar(
        x=monthly["ym"], y=monthly["Закрыто"],
        name="Наши ответы",
        marker_color="#2E7D32",
        text=monthly["Закрыто"], textposition="outside",
    ))

    fig.update_layout(
        barmode="group",
        title="Выдача vs Наши ответы (за месяц)",
        height=420, xaxis_tickangle=-45,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Дашборд_потоки", "dash_flows",
                    width=1400, height=600)

    # --- График 2: отставание по месяцам ---
    monthly["Отставание"] = monthly["Выдано"] - monthly["Закрыто"]

    colors = ["#E57373" if v > 0 else "#2E7D32"
              for v in monthly["Отставание"]]

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=monthly["ym"], y=monthly["Отставание"],
        marker_color=colors,
        text=monthly["Отставание"], textposition="outside",
        name="Отставание",
    ))
    fig2.add_hline(y=0, line_dash="dash", line_color="#888")
    fig2.update_layout(
        title="Отставание (Выдано − Отвечено). Красное — задолженность растёт",
        height=350, xaxis_tickangle=-45, showlegend=False,
    )
    st.plotly_chart(fig2, use_container_width=True)
    download_plotly(fig2, "Дашборд_отставание", "dash_delta",
                    width=1400, height=500)

    # --- График 3: кумулятивное отставание ---
    monthly_cum = monthly.copy()
    monthly_cum["Накоплено"] = monthly_cum["Отставание"].cumsum()

    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=monthly_cum["ym"], y=monthly_cum["Накоплено"],
        mode="lines+markers",
        line=dict(color="#E57373", width=3),
        fill="tozeroy",
        fillcolor="rgba(229,115,115,0.2)",
        name="Накопленное отставание",
    ))
    fig3.update_layout(
        title="Накопленное отставание за 12 месяцев",
        xaxis_title="Месяц",
        yaxis_title="Незакрытых замечаний",
        height=350, xaxis_tickangle=-45,
    )
    st.plotly_chart(fig3, use_container_width=True)
    download_plotly(fig3, "Дашборд_накопление", "dash_cum",
                    width=1400, height=500)

    st.caption(
        "**Как читать:** график 3 показывает, сколько замечаний накопилось "
        "за период. Линия вверх = команда отстаёт от темпа выдачи. "
        "Если тренд сохранится, к концу года задолженность вырастет ещё сильнее."
    )

    # --- Автоматическая интерпретация ---
    if len(monthly) >= 3:
        last3 = monthly.tail(3)
        total_delta = last3["Отставание"].sum()
        avg_delta = total_delta / 3

        if avg_delta > 500:
            st.error(
                f"🔴 **Команда отстаёт от темпа выдачи.** "
                f"За последние 3 месяца в среднем **+{int(avg_delta):,}** "
                f"незакрытых в месяц. Суммарно накопилось "
                f"**+{int(total_delta):,}** за квартал. "
                f"При сохранении темпа к концу года будет ещё больше."
                .replace(",", " ")
            )
        elif avg_delta < -500:
            st.success(
                f"✅ **Команда разгребает задолженность.** "
                f"В среднем **{int(avg_delta):,}** в месяц — закрываем "
                f"больше, чем выдаём.".replace(",", " ")
            )
        else:
            st.info(
                f"⚖️ **Баланс.** Отставание в среднем "
                f"{int(avg_delta):+,} в месяц — выдача и ответы "
                f"примерно совпадают.".replace(",", " ")
            )


# ---------------------------------------------------------------------------
#  Что делать сегодня — автогенерация
# ---------------------------------------------------------------------------
def _render_actions(data: dict):
    st.markdown("### 🎯 Что делать сегодня")

    overdue = data["overdue_ours"]
    waiting = data["waiting_customer"]
    abandoned = data["abandoned"]

    actions = []

    # Срочные действия
    if overdue > 0:
        actions.append({
            "priority": "🚨 СРОЧНО",
            "action": f"**{overdue:,} замечаний ждут нашего ответа >10 р.д.**".replace(",", " "),
            "detail": "Эскалация проектировщикам, разбор причин задержки.",
        })

    if abandoned > 500:
        actions.append({
            "priority": "🧹 АРХИВ",
            "action": f"**{abandoned:,} заброшенных замечаний** (>90 дней без движения)".replace(",", " "),
            "detail": "Письмо заказчику о снятии или пересогласовании.",
        })

    if waiting > 1000:
        actions.append({
            "priority": "⏸ ПРЕДЪЯВИТЬ",
            "action": f"**{waiting:,} замечаний ждут рассмотрения заказчиком**".replace(",", " "),
            "detail": "Письмо-напоминание с приложением списка.",
        })

    # Проверка топ-комплекта
    top_cx = data["top_overdue_complex"]
    if not top_cx.empty:
        worst = top_cx.iloc[0]
        actions.append({
            "priority": "🏗 КОМПЛЕКТ",
            "action": f"**{worst['complex']}** — {int(worst['n'])} просрочек".replace(",", " "),
            "detail": "Разбор с ответственным за комплекс.",
        })

    # Динамика
    monthly = data["monthly"]
    if len(monthly) >= 3:
        last3 = monthly.tail(3)
        total_delta = last3["Сальдо"].sum()
        if total_delta > 500:
            actions.append({
                "priority": "📉 ТРЕНД",
                "action": f"Задолженность растёт: **+{int(total_delta)}** за 3 месяца",
                "detail": "Усилить команду или сократить приём новых задач.",
            })

    if not actions:
        st.success("🎉 Все показатели в норме. Срочных действий не требуется.")
        return

    for a in actions:
        with st.container():
            col1, col2 = st.columns([1, 6])
            with col1:
                st.markdown(f"**{a['priority']}**")
            with col2:
                st.markdown(a["action"])
                st.caption(a["detail"])


# ---------------------------------------------------------------------------
#  Экспорт в PDF
# ---------------------------------------------------------------------------
def _render_export_pdf(data: dict):
    st.divider()

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("📄 Подготовить PDF", key="dash_pdf",
                     use_container_width=True):
            with st.spinner("Собираем PDF с графиками (10–20 сек)..."):
                try:
                    pdf_bytes = _build_dashboard_pdf(data)
                    st.session_state["dash_pdf_bytes"] = pdf_bytes
                    st.success("PDF готов")
                except Exception as e:
                    st.error(f"Ошибка: {e}")

    with col2:
        if "dash_pdf_bytes" in st.session_state:
            st.download_button(
                "⬇️ Скачать PDF",
                data=st.session_state["dash_pdf_bytes"],
                file_name=f"Дашборд_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )


def _build_dashboard_pdf(data: dict) -> bytes:
    """
    Сборка PDF-отчёта дашборда: KPI + таблица + графики.
    Графики генерируются через kaleido и вставляются как PNG.

    Особенности вёрстки:
      - Каждый график начинается с новой страницы (PageBreak) для читаемости.
      - Увеличенный margin слева (220) — чтобы длинные шифры комплектов
        не обрезались на горизонтальных барах.
      - Без эмодзи в тексте — DejaVu Sans их не поддерживает.
    """
    import io as _io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
        PageBreak,
    )
    from vitro.pdf_builder import ACTIVE_FONT, _trim_cell

    # =================================================================
    #  1. Подготовка данных
    # =================================================================
    totals = data["totals"]
    total = totals["total"] or 0
    closed = totals["closed"] or 0
    annulled = totals["annulled"] or 0
    pct = round((closed + annulled) / total * 100, 1) if total else 0

    kpi_rows = [
        ("Всего замечаний", f"{total:,}".replace(",", " ")),
        ("Закрыто", f"{closed:,}".replace(",", " ")),
        ("% выполнения", f"{pct}%"),
        ("", ""),
        ("Ждут нашего ответа", f"{data['overdue_ours']:,}".replace(",", " ")),
        ("Ждут заказчика", f"{data['waiting_customer']:,}".replace(",", " ")),
        ("Заброшено", f"{data['abandoned']:,}".replace(",", " ")),
        ("В работе, в срок", f"{data['in_progress']:,}".replace(",", " ")),
    ]

    by_disc = data["by_disc"]
    disc_rows = []
    for disc, s in by_disc.items():
        disc_rows.append({
            "Дисциплина": disc,
            "Наименование": discipline_name(disc),
            "Ждут нас": s["overdue"],
            "Ждут заказчика": s["waiting"],
            "Всего": s["total"],
        })
    disc_df = pd.DataFrame(disc_rows).sort_values(
        "Ждут нас", ascending=False) if disc_rows else pd.DataFrame()

    # =================================================================
    #  2. Генерация графиков как PNG (единый стиль)
    # =================================================================
    def _fig_to_png(fig, width: int = 1400, height: int = 600):
        """
        Конвертирует Plotly-фигуру в PNG с едиными настройками:
          - margin 220 слева — для длинных подписей;
          - margin 100 справа/снизу — чтобы метки и пики не обрезались;
          - font 16 — для читаемости в PDF.
        """
        try:
            fig.update_layout(
                margin=dict(l=220, r=100, t=60, b=100),
                font=dict(size=16),
                paper_bgcolor="white",
                plot_bgcolor="white",
            )
            return fig.to_image(format="png", width=width,
                                height=height, scale=1.5)
        except Exception:
            return None

    monthly = data["monthly"]
    images = {}

    if not monthly.empty:
        # -------------------------------------------------------------
        #  График 1: потоки (выдача vs ответы)
        # -------------------------------------------------------------
        fig1 = go.Figure()
        fig1.add_trace(go.Bar(
            x=monthly["ym"], y=monthly["Выдано"],
            name="Выдано замечаний",
            marker_color="#64B5F6",
            text=monthly["Выдано"], textposition="outside",
        ))
        fig1.add_trace(go.Bar(
            x=monthly["ym"], y=monthly["Закрыто"],
            name="Наши ответы",
            marker_color="#2E7D32",
            text=monthly["Закрыто"], textposition="outside",
        ))
        fig1.update_layout(
            barmode="group",
            xaxis_tickangle=-45,
            xaxis_title="Месяц",
            yaxis_title="Замечаний",
            legend=dict(orientation="h", y=-0.35,
                        x=0.5, xanchor="center"),
        )
        png = _fig_to_png(fig1, 1400, 600)
        if png:
            images["flows"] = png

        # -------------------------------------------------------------
        #  График 2: отставание по месяцам
        # -------------------------------------------------------------
        monthly_d = monthly.copy()
        monthly_d["Отставание"] = monthly_d["Выдано"] - monthly_d["Закрыто"]
        colors_list = ["#E57373" if v > 0 else "#2E7D32"
                       for v in monthly_d["Отставание"]]

        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=monthly_d["ym"], y=monthly_d["Отставание"],
            marker_color=colors_list,
            text=monthly_d["Отставание"], textposition="outside",
        ))
        fig2.add_hline(y=0, line_dash="dash", line_color="#888")
        fig2.update_layout(
            xaxis_tickangle=-45,
            xaxis_title="Месяц",
            yaxis_title="Отставание",
            showlegend=False,
        )
        png = _fig_to_png(fig2, 1400, 550)
        if png:
            images["delta"] = png

        # -------------------------------------------------------------
        #  График 3: накопленное отставание
        # -------------------------------------------------------------
        monthly_cum = monthly_d.copy()
        monthly_cum["Накоплено"] = monthly_cum["Отставание"].cumsum()

        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(
            x=monthly_cum["ym"], y=monthly_cum["Накоплено"],
            mode="lines+markers",
            line=dict(color="#E57373", width=4),
            fill="tozeroy",
            fillcolor="rgba(229,115,115,0.2)",
            marker=dict(size=10),
        ))
        fig3.update_layout(
            xaxis_tickangle=-45,
            xaxis_title="Месяц",
            yaxis_title="Накопленное отставание",
            showlegend=False,
        )
        png = _fig_to_png(fig3, 1400, 550)
        if png:
            images["cumulative"] = png

    # -------------------------------------------------------------
    #  График 4: топ-5 комплектов
    # -------------------------------------------------------------
    top_cx = data["top_overdue_complex"]
    if not top_cx.empty:
        top_cx_sorted = top_cx.sort_values("n", ascending=True)

        fig4 = px.bar(
            top_cx_sorted, x="n", y="complex", orientation="h",
            text="n",
            color_discrete_sequence=["#E57373"],
        )
        fig4.update_traces(textposition="outside")
        fig4.update_layout(
            xaxis_title="Количество просрочек",
            yaxis_title="",
        )
        png = _fig_to_png(fig4, 1400, 500)
        if png:
            images["top_complexes"] = png

    # =================================================================
    #  3. Сборка PDF
    # =================================================================
    buf = _io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=22 * mm,  # ← было 10
        bottomMargin=15 * mm,  # ← было 10
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"],
                        fontName=ACTIVE_FONT, fontSize=16,
                        textColor=colors.HexColor("#1F4E78"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontName=ACTIVE_FONT, fontSize=13,
                        textColor=colors.HexColor("#1F4E78"))
    h3 = ParagraphStyle("h3", parent=styles["Heading3"],
                        fontName=ACTIVE_FONT, fontSize=11,
                        textColor=colors.HexColor("#333333"))
    sub = ParagraphStyle("sub", parent=styles["Normal"],
                         fontName=ACTIVE_FONT, fontSize=9,
                         textColor=colors.grey)
    cell_style = ParagraphStyle("cell", parent=styles["Normal"],
                                fontName=ACTIVE_FONT, fontSize=10,
                                leading=13)

    story = [
        Paragraph("Дашборд руководителя проекта", h1),
        Paragraph(
            f"АТП ТЛП · сформировано {datetime.now():%d.%m.%Y %H:%M}",
            sub,
        ),
        Spacer(1, 6*mm),

        Paragraph("1. Ключевые показатели", h2),
        Spacer(1, 3*mm),
    ]

    # --- KPI таблица ---
    kpi_data = [[Paragraph("<b>Показатель</b>", cell_style),
                 Paragraph("<b>Значение</b>", cell_style)]]
    for k, v in kpi_rows:
        kpi_data.append([
            Paragraph(k or "&nbsp;", cell_style),
            Paragraph(v or "&nbsp;", cell_style),
        ])
    kpi_table = Table(kpi_data, colWidths=[110*mm, 50*mm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0070C0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F2F2")]),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 8*mm))

    # --- Таблица по дисциплинам ---
    if not disc_df.empty:
        story.append(Paragraph("2. Проблемы по дисциплинам", h2))
        story.append(Spacer(1, 3*mm))

        data_disc = [[Paragraph(f"<b>{c}</b>", cell_style)
                      for c in disc_df.columns]]
        for _, row in disc_df.iterrows():
            data_disc.append([Paragraph(_trim_cell(v), cell_style)
                              for v in row.values])
        n_cols = len(disc_df.columns)
        col_w = (doc.pagesize[0] - 24*mm) / n_cols
        disc_table = Table(data_disc, colWidths=[col_w] * n_cols,
                           repeatRows=1)
        disc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0070C0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(disc_table)
        story.append(Spacer(1, 8*mm))

    # =================================================================
    #  4. Графики — каждый с новой страницы
    # =================================================================
    if images:
        page_width = doc.pagesize[0] - 24*mm

        # --- График 3.1: потоки ---
        if "flows" in images:
            story.append(PageBreak())
            story.append(Paragraph("3. Динамика и графики", h2))
            story.append(Spacer(1, 4*mm))
            story.append(Paragraph(
                "3.1. Выдача замечаний vs Наши ответы", h3))
            story.append(Spacer(1, 4*mm))
            img = Image(_io.BytesIO(images["flows"]),
                        width=page_width, height=page_width * 0.42)
            story.append(img)

        # --- График 3.2: отставание ---
        if "delta" in images:
            story.append(PageBreak())
            story.append(Paragraph(
                "3.2. Отставание по месяцам "
                "(красное — задолженность растёт)", h3))
            story.append(Spacer(1, 4*mm))
            img = Image(_io.BytesIO(images["delta"]),
                        width=page_width, height=page_width * 0.42)
            story.append(img)

        # --- График 3.3: накопленное ---
        if "cumulative" in images:
            story.append(PageBreak())
            story.append(Paragraph(
                "3.3. Накопленное отставание", h3))
            story.append(Spacer(1, 4*mm))
            img = Image(_io.BytesIO(images["cumulative"]),
                        width=page_width, height=page_width * 0.42)
            story.append(img)

        # --- График 3.4: топ-5 комплектов ---
        if "top_complexes" in images:
            story.append(PageBreak())
            story.append(Paragraph(
                "3.4. Топ-5 комплектов с просрочками", h3))
            story.append(Spacer(1, 4*mm))
            img = Image(_io.BytesIO(images["top_complexes"]),
                        width=page_width,
                        height=page_width * 0.45)
            story.append(img)

    # =================================================================
    #  5. Вывод — без эмодзи
    # =================================================================
    story.append(PageBreak())
    story.append(Paragraph("4. Вывод", h2))
    story.append(Spacer(1, 4*mm))

    conclusions = []
    conclusions.append(
        f"Всего замечаний: <b>{total:,}</b>. "
        f"Закрыто: <b>{closed:,}</b> ({pct}%).".replace(",", " ")
    )

    if data["overdue_ours"] > 5000:
        conclusions.append(
            f"<b>КРИТИЧНО:</b> {data['overdue_ours']:,} замечаний "
            f"ждут нашего ответа более 10 рабочих дней.".replace(",", " ")
        )
    elif data["overdue_ours"] > 1000:
        conclusions.append(
            f"{data['overdue_ours']:,} замечаний ждут нашего ответа."
            .replace(",", " ")
        )

    if data["waiting_customer"] > 5000:
        conclusions.append(
            f"{data['waiting_customer']:,} замечаний ждут рассмотрения "
            f"заказчиком — готовим письмо-предъявление.".replace(",", " ")
        )

    if data["abandoned"] > 1000:
        conclusions.append(
            f"{data['abandoned']:,} заброшенных замечаний "
            f"(более 90 дней без движения) — кандидаты на снятие."
            .replace(",", " ")
        )

    if not monthly.empty and len(monthly) >= 3:
        last3 = monthly.tail(3).copy()
        last3["Отставание"] = last3["Выдано"] - last3["Закрыто"]
        total_delta = last3["Отставание"].sum()
        avg_delta = total_delta / 3

        if avg_delta > 500:
            conclusions.append(
                f"<b>Команда отстаёт от темпа выдачи.</b> "
                f"В среднем <b>+{int(avg_delta):,}</b> незакрытых в месяц. "
                f"При сохранении темпа к концу года задолженность вырастет."
                .replace(",", " ")
            )
        elif avg_delta < -500:
            conclusions.append(
                f"Команда разгребает задолженность: "
                f"{int(avg_delta):,} в месяц в среднем.".replace(",", " ")
            )

    for c in conclusions:
        story.append(Paragraph(f"• {c}", cell_style))
        story.append(Spacer(1, 3*mm))

    # =================================================================
    #  6. Финализация
    # =================================================================
    doc.build(
        story,
        onFirstPage=lambda c, d: draw_header_footer(
            c, d, "Дашборд РП — АТП ТЛП"),
        onLaterPages=lambda c, d: draw_header_footer(
            c, d, "Дашборд РП — АТП ТЛП"),
    )
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📊 Дашборд РП")
    st.caption(
        "Сводка состояния проекта на одной странице: масштаб, проблемы, "
        "действия. Обновляется каждые 5 минут."
    )

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🔄 Обновить", key="dash_refresh",
                     use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Загрузка данных..."):
        data = _load_full_state()

    # Светофор
    _render_traffic_light(data)

    st.divider()

    # Топ проблем
    _render_top_problems(data)

    st.divider()

    # Таблица дисциплин
    _render_discipline_table(data)

    st.divider()

    # Динамика
    _render_dynamics(data)

    st.divider()

    # Действия
    _render_actions(data)

    # Экспорт
    _render_export_pdf(data)