# vitro/ui/dashboard.py
"""
📊 Дашборд РП — сводная страница состояния проекта.

Стиль: топ проблем + автокомментарии. Одна страница — вся картина.
Что внутри:
  - Светофор: масштаб + АТП ТЛП + заказчик/архив (3 ряда KPI).
  - Топ-5 проблем по каждой категории.
  - Таблица «Где болит» по дисциплинам с вердиктом.
  - Динамика 12 месяцев (выдача/ответы/сальдо).
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
    Возвращает все данные для дашборда.

    Использует единый источник — `_load_all_categorized` из deadlines.py,
    чтобы все цифры сходились с вкладками «Сроки» и «Авторы».
    """
    from vitro.ui.deadlines import _load_all_categorized

    result = {}

    # =====================================================================
    #  1. Категоризация (единый источник истины)
    # =====================================================================
    cats_df = _load_all_categorized()

    if not cats_df.empty:
        def count_by(*flags):
            return cats_df[cats_df["category_flag"].isin(flags)].shape[0]

        # АТП ТЛП (4 категории)
        result["new_total"] = count_by("new_overdue", "new_in_progress")
        result["new_overdue"] = count_by("new_overdue")

        result["in_work_total"] = count_by("in_work_overdue",
                                           "in_work_in_progress")
        result["in_work_overdue"] = count_by("in_work_overdue")

        result["rejected_total"] = count_by("rejected_overdue",
                                             "rejected_in_progress")
        result["rejected_overdue"] = count_by("rejected_overdue")

        result["discussion_total"] = count_by("discussion_overdue",
                                                "discussion_in_progress")
        result["discussion_overdue"] = count_by("discussion_overdue")

        # АТП ТЛП — всего и просрочено
        ours_total = (result["new_total"] +
                      result["in_work_total"] +
                      result["rejected_total"] +
                      result["discussion_total"])
        ours_overdue = (result["new_overdue"] +
                        result["in_work_overdue"] +
                        result["rejected_overdue"] +
                        result["discussion_overdue"])
        result["ours_total"] = ours_total
        result["ours_overdue"] = ours_overdue

        # Заказчик
        result["waiting_customer"] = count_by(
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime")
        result["chronic"] = count_by("waiting_customer_chronic")
        result["waiting_total"] = result["waiting_customer"] + result["chronic"]

        # Учтённые (A/B) — раздельно
        result["closed_by_doc"] = count_by("closed_by_doc_status")
        result["closed_by_doc_a"] = cats_df[
            (cats_df["category_flag"] == "closed_by_doc_status")
            & (cats_df["doc_status"] == "A")
        ].shape[0]
        result["closed_by_doc_b"] = cats_df[
            (cats_df["category_flag"] == "closed_by_doc_status")
            & (cats_df["doc_status"] == "B")
        ].shape[0]

        # Архив
        result["abandoned"] = count_by("abandoned")

        # Активные (5 статусов = наши + ждут заказчика + хроника + A/B)
        result["active_total"] = (ours_total +
                                    result["waiting_customer"] +
                                    result["chronic"] +
                                    result["closed_by_doc"])
    else:
        for k in ["new_total", "new_overdue", "in_work_total",
                   "in_work_overdue", "rejected_total", "rejected_overdue",
                   "discussion_total", "discussion_overdue",
                   "ours_total", "ours_overdue",
                   "waiting_customer", "chronic", "waiting_total",
                   "closed_by_doc", "closed_by_doc_a", "closed_by_doc_b",
                   "abandoned", "active_total"]:
            result[k] = 0

    # =====================================================================
    #  2. Общая сводка (всего/закрыто/аннулировано) — для справки
    # =====================================================================
    with get_conn() as conn:
        base = dict(conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('Закрыто', 'Выполнено')
                         THEN 1 ELSE 0 END) AS closed_or_wait,
                SUM(CASE WHEN status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM comments
        """).fetchone())

    result["total_all"] = base["total"] or 0
    result["closed"] = base["closed"] or 0
    result["annulled"] = base["annulled"] or 0
    result["closed_for_ref"] = result["closed"] + result["annulled"]

    # =====================================================================
    #  3. По дисциплинам (для таблицы «Где болит»)
    # =====================================================================
    if not cats_df.empty:
        by_disc = {}
        for disc in cats_df["discipline"].dropna().unique():
            if not disc:
                continue
            sub = cats_df[cats_df["discipline"] == disc]

            ours = sub[sub["category_flag"].isin([
                "new_overdue", "new_in_progress",
                "in_work_overdue", "in_work_in_progress",
                "rejected_overdue", "rejected_in_progress",
                "discussion_overdue", "discussion_in_progress",
            ])].shape[0]

            waiting = sub[sub["category_flag"].isin([
                "waiting_customer", "waiting_customer_overdue",
                "waiting_customer_ontime",
            ])].shape[0]

            chronic = sub[sub["category_flag"] ==
                            "waiting_customer_chronic"].shape[0]
            closed_doc = sub[sub["category_flag"] ==
                                "closed_by_doc_status"].shape[0]
            abandoned = sub[sub["category_flag"] == "abandoned"].shape[0]

            by_disc[disc] = {
                "ours": ours,
                "waiting": waiting,
                "chronic": chronic,
                "closed_doc": closed_doc,
                "abandoned": abandoned,
                "total": len(sub),
            }
        result["by_disc"] = by_disc
    else:
        result["by_disc"] = {}

    # =====================================================================
    #  4. Топ-10 комплектов по просрочкам (АТП ТЛП)
    # =====================================================================
    if not cats_df.empty:
        overdue = cats_df[cats_df["category_flag"].isin([
            "new_overdue", "in_work_overdue",
            "rejected_overdue", "discussion_overdue",
        ])]

        top_cx = (overdue.groupby("complex").size()
                  .reset_index(name="n")
                  .sort_values("n", ascending=False)
                  .head(10))
        result["top_overdue_complex"] = top_cx
    else:
        result["top_overdue_complex"] = pd.DataFrame()

    # =====================================================================
    #  5. Топ-10 авторов, чьи замечания ждут заказчика
    # =====================================================================
    if not cats_df.empty:
        waiting_df = cats_df[cats_df["category_flag"].isin([
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime", "waiting_customer_chronic",
        ])]

        top_auth = (waiting_df.groupby("author").size()
                    .reset_index(name="n")
                    .sort_values("n", ascending=False)
                    .head(10))
        result["top_waiting_authors"] = top_auth
    else:
        result["top_waiting_authors"] = pd.DataFrame()

    # =====================================================================
    #  6. Динамика 12 месяцев (выдача / ответы / закрытие)
    # =====================================================================
    with get_conn() as conn:
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
              AND status IN ('Закрыто', 'Выполнено')
            GROUP BY ym
        """, conn)

    monthly = pd.merge(
        issue.rename(columns={"n": "Выдано"}),
        fix.rename(columns={"n": "Отвечено"}),
        on="ym", how="outer",
    ).fillna(0).sort_values("ym")

    monthly["Выдано"] = monthly["Выдано"].astype(int)
    monthly["Отвечено"] = monthly["Отвечено"].astype(int)
    monthly["Отставание"] = monthly["Выдано"] - monthly["Отвечено"]

    result["monthly"] = monthly.tail(12)

    return result


# ---------------------------------------------------------------------------
#  Светофор — 3 ряда KPI
# ---------------------------------------------------------------------------
def _render_traffic_light(data: dict):
    """Три ряда KPI: масштаб, АТП ТЛП, заказчик."""

    # =================================================================
    #  Ряд 1 — Масштаб проекта
    # =================================================================
    st.markdown("##### 📦 Масштаб проекта")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Всего замечаний",
              f"{data['total_all']:,}".replace(",", " "),
              help="Все замечания в базе, включая закрытые и "
                   "аннулированные.")
    c2.metric("Закрыто",
              f"{data['closed']:,}".replace(",", " "),
              help="Замечания со статусом «Закрыто» — работа "
                   "полностью завершена.")
    c3.metric("Активных",
              f"{data['active_total']:,}".replace(",", " "),
              help="АТП ТЛП + ждут заказчика + хронические + "
                   "учтено (A/B). Без закрытых и аннулированных.")
    c4.metric("Аннулировано",
              f"{data['annulled']:,}".replace(",", " "),
              help="Замечания, снятые заказчиком или отменённые.")
    pct_closed = (
        round(data["closed"] / data["total_all"] * 100, 1)
        if data["total_all"] else 0
    )
    c5.metric("% закрыто", f"{pct_closed}%",
              help="Доля закрытых от всех замечаний в базе.")

    # =================================================================
    #  Ряд 2 — АТП ТЛП
    # =================================================================
    st.markdown("##### 🔵 АТП ТЛП — ждут ответа проектировщика")
    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric(
        "🆕 Новое",
        f"{data['new_total']:,}".replace(",", " "),
        delta=f"🔴 {data['new_overdue']:,}".replace(",", " ")
              if data['new_overdue'] > 0 else None,
        delta_color="inverse",
        help="Замечание выдано заказчиком, но АТП ТЛП ещё не взял "
             "его в работу. Красным — просрочено (>10 р.д.).",
    )
    c2.metric(
        "🛠 В работе",
        f"{data['in_work_total']:,}".replace(",", " "),
        delta=f"🔴 {data['in_work_overdue']:,}".replace(",", " ")
              if data['in_work_overdue'] > 0 else None,
        delta_color="inverse",
        help="Замечание взято в работу АТП ТЛП, но ответ пока не дан. "
             "Красным — просрочено (>10 р.д.).",
    )
    c3.metric(
        "🟪 Не принято",
        f"{data['rejected_total']:,}".replace(",", " "),
        delta=f"🔴 {data['rejected_overdue']:,}".replace(",", " ")
              if data['rejected_overdue'] > 0 else None,
        delta_color="inverse",
        help="Заказчик отклонил ответ АТП ТЛП и вернул на доработку. "
             "Красным — просрочено (>10 р.д.).",
    )
    c4.metric(
        "🟣 К обсуждению",
        f"{data['discussion_total']:,}".replace(",", " "),
        delta=f"🔴 {data['discussion_overdue']:,}".replace(",", " ")
              if data['discussion_overdue'] > 0 else None,
        delta_color="inverse",
        help="Спорное замечание, требует совещания сторон. "
             "Красным — просрочено (>10 р.д.).",
    )
    c5.metric(
        "📊 Итого АТП ТЛП",
        f"{data['ours_total']:,}".replace(",", " "),
        help="Все замечания, ожидающие ответа от АТП ТЛП. "
             "Сумма 4 категорий: Новое + В работе + Не принято + "
             "К обсуждению.",
    )
    c6.metric(
        "🔴 Из них просрочено",
        f"{data['ours_overdue']:,}".replace(",", " "),
        delta="требует внимания" if data['ours_overdue'] > 0 else None,
        delta_color="inverse",
        help="Замечания АТП ТЛП, у которых срок ответа (10 р.д.) "
             "уже истёк.",
    )

    # =================================================================
    #  Ряд 3 — Заказчик + архив
    # =================================================================
    st.markdown("##### 🔵 На стороне заказчика + архив")
    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric(
        "🔵 Ждут заказчика",
        f"{data['waiting_customer']:,}".replace(",", " "),
        help="АТП ТЛП дал ответ (статус «Выполнено»), но заказчик "
             "ещё не рассмотрел. Срок ожидания — менее 90 р.д., "
             "лист ещё не получил статус A или B.",
    )
    c2.metric(
        "🔴 Хронические",
        f"{data['chronic']:,}".replace(",", " "),
        delta="эскалация" if data['chronic'] > 0 else None,
        delta_color="inverse",
        help="Заказчик не рассматривает ответ более 90 р.д. "
             "Требуется эскалация — письмо руководству заказчика.",
    )
    c3.metric(
        "🟢 Учтено — A",
        f"{data['closed_by_doc_a']:,}".replace(",", " "),
        help="Лист утверждён заказчиком (статус A). Замечания "
             "фактически сняты, осталось формально закрыть в Витрокад.",
    )
    c4.metric(
        "🟡 Учтено — B",
        f"{data['closed_by_doc_b']:,}".replace(",", " "),
        help="Лист готов к сдаче (статус B). Формально замечания "
             "НЕ сняты, заказчик может вернуть лист на доработку. "
             "Требует внимания.",
    )
    c5.metric(
        "🟡 Заброшено",
        f"{data['abandoned']:,}".replace(",", " "),
        help="Замечания без движения более 90 календарных дней. "
             "Кандидаты на снятие или пересогласование.",
    )
    c6.metric(
        "📊 Итого заказчик",
        f"{data['waiting_total']:,}".replace(",", " "),
        help="Замечания, ожидающие действия от заказчика: "
             "Ждут заказчика + Хронические. Учтённые (A/B) сюда "
             "не входят — они уже отработаны АТП ТЛП.",
    )


# ---------------------------------------------------------------------------
#  Топ-5 проблем
# ---------------------------------------------------------------------------
def _render_top_problems(data: dict):
    st.markdown("### 🔥 Топ-10 проблем")

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
        st.markdown("##### 👤 Авторы, чьи замечания ждут ответа")
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
    """Таблица «Где болит» по дисциплинам."""
    st.markdown("### 🏷 Где болит — по дисциплинам")

    by_disc = data["by_disc"]
    if not by_disc:
        st.info("Нет данных.")
        return

    rows = []
    for disc, s in by_disc.items():
        ours = s["ours"]
        waiting = s["waiting"]
        chronic = s["chronic"]
        closed_doc = s["closed_doc"]

        # Вердикт
        if ours > 500:
            verdict = "🔴 Проблема"
        elif ours > 100 or chronic > 500:
            verdict = "🟡 Внимание"
        else:
            verdict = "🟢 Норма"

        rows.append({
            "Дисциплина": disc,
            "Наименование": discipline_name(disc),
            "🔴 АТП ТЛП": ours,
            "🔵 Ждут заказчика": waiting,
            "🔴 Хронические": chronic,
            "🟢 Учтено (A/B)": closed_doc,
            "Всего активных": s["total"],
            "Вердикт": verdict,
        })

    df = pd.DataFrame(rows).sort_values(
        ["🔴 АТП ТЛП", "🔴 Хронические"], ascending=False)

    st.dataframe(
        df, use_container_width=True, hide_index=True, height=420,
        column_config={
            "🔴 АТП ТЛП": st.column_config.NumberColumn(format="%d"),
            "🔵 Ждут заказчика": st.column_config.NumberColumn(format="%d"),
            "🔴 Хронические": st.column_config.NumberColumn(format="%d"),
            "🟢 Учтено (A/B)": st.column_config.NumberColumn(format="%d"),
        },
    )


# ---------------------------------------------------------------------------
#  Динамика 12 месяцев
# ---------------------------------------------------------------------------
def _render_dynamics(data: dict):
    """Динамика за 12 месяцев: выдача, ответы, отставание."""
    st.markdown("### 📈 Потоки: выдача, ответы, отставание")

    monthly = data["monthly"]
    if monthly.empty:
        st.info("Нет данных.")
        return

    st.caption(
        "**Выдача** — новые замечания от заказчика (вход). "
        "**Ответы** — ответы АТП ТЛП на ранее выданные (выход). "
        "**Отставание** — насколько АТП ТЛП не успевает за темпом выдачи."
    )

    # =================================================================
    #  График 1: Выдача vs Ответы
    # =================================================================
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=monthly["ym"], y=monthly["Выдано"],
        name="Выдано замечаний",
        marker_color="#64B5F6",
        text=monthly["Выдано"], textposition="outside",
    ))
    fig.add_trace(go.Bar(
        x=monthly["ym"], y=monthly["Отвечено"],
        name="Ответы АТП ТЛП",
        marker_color="#2E7D32",
        text=monthly["Отвечено"], textposition="outside",
    ))

    fig.update_layout(
        barmode="group",
        title="Выдача vs Ответы АТП ТЛП (за месяц)",
        height=420, xaxis_tickangle=-45,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Дашборд_потоки", "dash_flows",
                    width=1400, height=600)

    # =================================================================
    #  График 2: Отставание по месяцам
    # =================================================================
    monthly_d = monthly.copy()
    monthly_d["Отставание"] = monthly_d["Выдано"] - monthly_d["Отвечено"]

    colors_list = [
        "#E57373" if v > 0 else "#2E7D32"
        for v in monthly_d["Отставание"]
    ]

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=monthly_d["ym"], y=monthly_d["Отставание"],
        marker_color=colors_list,
        text=monthly_d["Отставание"], textposition="outside",
        name="Отставание",
    ))
    fig2.add_hline(y=0, line_dash="dash", line_color="#888")
    fig2.update_layout(
        title="Отставание (Выдано − Отвечено). "
              "Красное — задолженность растёт",
        height=350, xaxis_tickangle=-45, showlegend=False,
    )
    st.plotly_chart(fig2, use_container_width=True)
    download_plotly(fig2, "Дашборд_отставание", "dash_delta",
                    width=1400, height=500)

    # =================================================================
    #  График 3: Накопленное отставание
    # =================================================================
    monthly_cum = monthly_d.copy()
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
        "за период. Линия вверх = АТП ТЛП отстаёт от темпа выдачи."
    )

    # =================================================================
    #  Автоматическая интерпретация
    # =================================================================
    if len(monthly) >= 3:
        last3 = monthly.tail(3).copy()
        last3["Отставание"] = last3["Выдано"] - last3["Отвечено"]
        total_delta = last3["Отставание"].sum()
        avg_delta = total_delta / 3

        if avg_delta > 500:
            st.error(
                f"🔴 **АТП ТЛП отстаёт от темпа выдачи.** "
                f"За последние 3 месяца в среднем **+{int(avg_delta):,}** "
                f"незакрытых в месяц. Суммарно накопилось "
                f"**+{int(total_delta):,}** за квартал."
                .replace(",", " ")
            )
        elif avg_delta < -500:
            st.success(
                f"✅ **АТП ТЛП разгребает задолженность.** "
                f"В среднем **{int(avg_delta):,}** в месяц — закрываем "
                f"больше, чем выдаём.".replace(",", " ")
            )
        else:
            st.info(
                f"⚖️ **Баланс.** Отставание в среднем "
                f"{int(avg_delta):+,} в месяц.".replace(",", " ")
            )


# ---------------------------------------------------------------------------
#  Что делать сегодня — автогенерация
# ---------------------------------------------------------------------------
def _render_actions(data: dict):
    """Что делать сегодня — автогенерация действий."""
    st.divider()
    st.markdown("#### 🎯 Что делать сегодня")

    new_overdue = data.get("new_overdue", 0)
    in_work_overdue = data.get("in_work_overdue", 0)
    rejected_overdue = data.get("rejected_overdue", 0)
    waiting = data.get("waiting_customer", 0)
    chronic = data.get("chronic", 0)
    closed_a = data.get("closed_by_doc_a", 0)
    closed_b = data.get("closed_by_doc_b", 0)
    abandoned = data.get("abandoned", 0)

    actions = []

    if new_overdue > 0:
        actions.append({
            "priority": "🚨 СРОЧНО",
            "action": f"**{new_overdue:,} замечаний в «Новое» просрочено**"
                      .replace(",", " "),
            "detail": "АТП ТЛП не взял их в работу. Назначить "
                      "исполнителей.",
        })

    if in_work_overdue > 0:
        actions.append({
            "priority": "🛠 УСКОРИТЬ",
            "action": f"**{in_work_overdue:,} замечаний в «Принято "
                      f"в работу» просрочено**".replace(",", " "),
            "detail": "АТП ТЛП работает, но медленно. Ускорить ответы.",
        })

    if rejected_overdue > 0:
        actions.append({
            "priority": "🟪 РАЗОБРАТЬ",
            "action": f"**{rejected_overdue:,} отклонённых замечаний "
                      f"просрочено**".replace(",", " "),
            "detail": "Заказчик не принял ответы АТП ТЛП. Доработать.",
        })

    if waiting > 0:
        actions.append({
            "priority": "⏸ ПРЕДЪЯВИТЬ",
            "action": f"**{waiting:,} замечаний ждут заказчика**"
                      .replace(",", " "),
            "detail": "Письмо-напоминание с приложением списка.",
        })

    if chronic > 0:
        actions.append({
            "priority": "🔴 ЭСКАЛАЦИЯ",
            "action": f"**{chronic:,} хронических (> 90 р.д.)**"
                      .replace(",", " "),
            "detail": "Письмо руководству заказчика.",
        })

    if closed_a > 0:
        actions.append({
            "priority": "🟢 ЗАКРЫТЬ",
            "action": f"**{closed_a:,} учтено по листу A**"
                      .replace(",", " "),
            "detail": "Лист утверждён. Дожать заказчика на «Закрыто» "
                      "в Витрокад.",
        })

    if closed_b > 0:
        actions.append({
            "priority": "🟡 ПРОВЕРИТЬ",
            "action": f"**{closed_b:,} учтено по листу B**"
                      .replace(",", " "),
            "detail": "Лист готов к сдаче, но замечания формально "
                      "не сняты. Уточнить у заказчика — не вернут ли "
                      "на доработку.",
        })

    if abandoned > 100:
        actions.append({
            "priority": "🧹 АРХИВ",
            "action": f"**{abandoned:,} заброшенных (>90 дней)**"
                      .replace(",", " "),
            "detail": "Письмо о снятии или пересогласовании.",
        })

    if not actions:
        st.success("🎉 Все показатели в норме. Срочных действий нет.")
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
      - Каждый график начинается с новой страницы (PageBreak).
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
    total_all = data["total_all"] or 0
    closed = data["closed"] or 0
    annulled = data["annulled"] or 0
    active = data["active_total"] or 0
    pct = round(closed / total_all * 100, 1) if total_all else 0

    # KPI-строки для PDF (без эмодзи)
    kpi_rows = [
        ("Масштаб проекта", ""),
        ("Всего замечаний", f"{total_all:,}".replace(",", " ")),
        ("Закрыто", f"{closed:,}".replace(",", " ")),
        ("Активных", f"{active:,}".replace(",", " ")),
        ("Аннулировано", f"{annulled:,}".replace(",", " ")),
        ("% закрыто", f"{pct}%"),
        ("", ""),
        ("АТП ТЛП — ждут ответа", ""),
        ("Новое", f"{data['new_total']:,}".replace(",", " ")),
        ("  в т.ч. просрочено", f"{data['new_overdue']:,}".replace(",", " ")),
        ("В работе", f"{data['in_work_total']:,}".replace(",", " ")),
        ("  в т.ч. просрочено",
         f"{data['in_work_overdue']:,}".replace(",", " ")),
        ("Не принято", f"{data['rejected_total']:,}".replace(",", " ")),
        ("  в т.ч. просрочено",
         f"{data['rejected_overdue']:,}".replace(",", " ")),
        ("К обсуждению", f"{data['discussion_total']:,}".replace(",", " ")),
        ("  в т.ч. просрочено",
         f"{data['discussion_overdue']:,}".replace(",", " ")),
        ("Итого АТП ТЛП", f"{data['ours_total']:,}".replace(",", " ")),
        ("  из них просрочено",
         f"{data['ours_overdue']:,}".replace(",", " ")),
        ("", ""),
        ("На стороне заказчика + архив", ""),
        ("Ждут заказчика",
         f"{data['waiting_customer']:,}".replace(",", " ")),
        ("Хронические", f"{data['chronic']:,}".replace(",", " ")),
        ("Учтено - A (лист утверждён)",
         f"{data['closed_by_doc_a']:,}".replace(",", " ")),
        ("Учтено - B (лист к сдаче)",
         f"{data['closed_by_doc_b']:,}".replace(",", " ")),
        ("Заброшено", f"{data['abandoned']:,}".replace(",", " ")),
        ("Итого заказчик",
         f"{data['waiting_total']:,}".replace(",", " ")),
    ]

    by_disc = data["by_disc"]
    disc_rows = []
    for disc, s in by_disc.items():
        disc_rows.append({
            "Дисциплина": disc,
            "Наименование": discipline_name(disc),
            "АТП ТЛП": s["ours"],
            "Ждут заказчика": s["waiting"],
            "Хронические": s["chronic"],
            "Учтено A/B": s["closed_doc"],
            "Всего": s["total"],
        })
    disc_df = pd.DataFrame(disc_rows).sort_values(
        "АТП ТЛП", ascending=False) if disc_rows else pd.DataFrame()

    # =================================================================
    #  2. Генерация графиков как PNG
    # =================================================================
    def _fig_to_png(fig, width: int = 1400, height: int = 600):
        """
        Конвертирует Plotly-фигуру в PNG с едиными настройками:
          - margin 220 слева — для длинных подписей;
          - margin 100 справа/снизу — чтобы метки не обрезались;
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
            x=monthly["ym"], y=monthly["Отвечено"],
            name="Ответы АТП ТЛП",
            marker_color="#2E7D32",
            text=monthly["Отвечено"], textposition="outside",
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
        monthly_d["Отставание"] = monthly_d["Выдано"] - monthly_d["Отвечено"]
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
        topMargin=22 * mm,
        bottomMargin=15 * mm,
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
                "3.1. Выдача замечаний vs Ответы АТП ТЛП", h3))
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
    #  5. Вывод
    # =================================================================
    story.append(PageBreak())
    story.append(Paragraph("4. Вывод", h2))
    story.append(Spacer(1, 4*mm))

    conclusions = []
    conclusions.append(
        f"Всего замечаний: <b>{total_all:,}</b>. "
        f"Закрыто: <b>{closed:,}</b> ({pct}%).".replace(",", " ")
    )

    if data["ours_overdue"] > 5000:
        conclusions.append(
            f"<b>КРИТИЧНО:</b> {data['ours_overdue']:,} замечаний "
            f"ждут ответа АТП ТЛП более 10 рабочих дней."
            .replace(",", " ")
        )
    elif data["ours_overdue"] > 1000:
        conclusions.append(
            f"{data['ours_overdue']:,} замечаний ждут ответа АТП ТЛП."
            .replace(",", " ")
        )

    if data["waiting_customer"] > 5000:
        conclusions.append(
            f"{data['waiting_customer']:,} замечаний ждут рассмотрения "
            f"заказчиком — готовим письмо-предъявление.".replace(",", " ")
        )

    if data["closed_by_doc_b"] > 0:
        conclusions.append(
            f"{data['closed_by_doc_b']:,} замечаний учтено по листам B — "
            f"формально не сняты, заказчик может вернуть на доработку."
            .replace(",", " ")
        )

    if data["abandoned"] > 1000:
        conclusions.append(
            f"{data['abandoned']:,} заброшенных замечаний "
            f"(более 90 дней без движения) — кандидаты на снятие."
            .replace(",", " ")
        )

    if not monthly.empty and len(monthly) >= 3:
        last3 = monthly.tail(3).copy()
        last3["Отставание"] = last3["Выдано"] - last3["Отвечено"]
        total_delta = last3["Отставание"].sum()
        avg_delta = total_delta / 3

        if avg_delta > 500:
            conclusions.append(
                f"<b>АТП ТЛП отстаёт от темпа выдачи.</b> "
                f"В среднем <b>+{int(avg_delta):,}</b> незакрытых в "
                f"месяц. При сохранении темпа к концу года "
                f"задолженность вырастет.".replace(",", " ")
            )
        elif avg_delta < -500:
            conclusions.append(
                f"АТП ТЛП разгребает задолженность: "
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