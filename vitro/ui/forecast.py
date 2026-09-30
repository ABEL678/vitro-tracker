# vitro/ui/forecast.py
"""
🔮 Прогноз — экстраполяция текущих темпов.

Модель разделена на 2 независимых потока:
  1. НАША СТОРОНА — когда АТП ТЛП закроет свои 17 182 замечания.
  2. ЗАКАЗЧИК — когда он рассмотрит свои 12 369.

Плюс прогноз по 8 категориям отдельно.

Единый источник: `_load_all_categorized` из deadlines.py.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Единый источник активных
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_active_df() -> pd.DataFrame:
    """Активные замечания из _load_all_categorized (без abandoned)."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return df
    return df[df["category_flag"] != "abandoned"].copy()


# ---------------------------------------------------------------------------
#  Загрузка данных + темпы
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_data() -> dict:
    """
    Собирает данные для прогноза:
      - Средние темпы за 3 мес (выдача / наши ответы / закрытие)
      - Текущее состояние (по 8 категориям из _load_all_categorized)
      - Темпы по дисциплинам
    """
    from vitro.ui.deadlines import _load_all_categorized

    result = {}

    # =====================================================================
    #  1. Текущее состояние — из единого источника
    # =====================================================================
    cats_df = _load_all_categorized()
    if cats_df.empty:
        return {"empty": True}

    # ---- Общие цифры из БД (для контекста) ----
    with get_conn() as conn:
        base = dict(conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM comments
        """).fetchone())

    # ---- По 8 категориям ----
    counts = cats_df["category_flag"].value_counts().to_dict()

    # ---- Наша сторона ----
    ours_flags = [
        "new_overdue", "new_in_progress",
        "in_work_overdue", "in_work_in_progress",
        "rejected_overdue", "rejected_in_progress",
        "discussion_overdue", "discussion_in_progress",
    ]
    ours = cats_df[cats_df["category_flag"].isin(ours_flags)]
    n_ours = len(ours)
    n_ours_overdue = len(ours[ours["category_flag"].str.endswith("_overdue")])

    # ---- Сторона заказчика ----
    waiting_flags = [
        "waiting_customer", "waiting_customer_overdue",
        "waiting_customer_ontime",
    ]
    waiting = cats_df[cats_df["category_flag"].isin(waiting_flags)]
    chronic = cats_df[cats_df["category_flag"] == "waiting_customer_chronic"]
    closed_doc = cats_df[cats_df["category_flag"] == "closed_by_doc_status"]

    n_waiting = len(waiting)
    n_chronic = len(chronic)
    n_closed_doc = len(closed_doc)

    # ---- Архив ----
    abandoned = cats_df[cats_df["category_flag"] == "abandoned"]
    n_abandoned = len(abandoned)

    # Активные = всё, кроме abandoned
    active_count = len(cats_df[cats_df["category_flag"] != "abandoned"])

    result["totals"] = {
        "total": base["total"] or 0,
        "closed": base["closed"] or 0,
        "annulled": base["annulled"] or 0,
        "active": active_count,  # ← ИСПРАВЛЕНО
        # Наша сторона
        "ours": n_ours,
        "ours_overdue": n_ours_overdue,
        # Заказчик
        "waiting": n_waiting,
        "chronic": n_chronic,
        "closed_doc": n_closed_doc,
        # Архив
        "abandoned": n_abandoned,
        # 8 категорий
        "by_flag": counts,
    }

    # =====================================================================
    #  2. Темпы за 3 месяца
    # =====================================================================
    with get_conn() as conn:
        issue = pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # Наши ответы — fix_date + статус «Выполнено» или «Закрыто»
        fix = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Выполнено', 'Закрыто')
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # Закрытие заказчиком — только «Закрыто»
        closed = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status = 'Закрыто'
            GROUP BY ym
            ORDER BY ym
        """, conn)

    merged = pd.merge(
        issue.rename(columns={"n": "Выдано"}),
        fix.rename(columns={"n": "Отвечено"}),
        on="ym", how="outer",
    ).merge(
        closed.rename(columns={"n": "Закрыто"}),
        on="ym", how="outer",
    ).fillna(0).sort_values("ym")

    for col in ["Выдано", "Отвечено", "Закрыто"]:
        merged[col] = merged[col].astype(int)

    result["monthly"] = merged

    # Средние темпы за последние 3 месяца
    recent = merged.tail(3)
    result["avg_issue"] = recent["Выдано"].mean() if len(recent) else 0
    result["avg_fix"] = recent["Отвечено"].mean() if len(recent) else 0
    result["avg_closed"] = recent["Закрыто"].mean() if len(recent) else 0

    # =====================================================================
    #  3. По дисциплинам
    # =====================================================================
    by_disc = []
    for disc, g in cats_df.groupby("discipline"):
        if not disc:
            continue

        n_ours_d = g[g["category_flag"].isin(ours_flags)].shape[0]
        n_waiting_d = g[g["category_flag"].isin(waiting_flags)].shape[0]
        n_chronic_d = g[
            g["category_flag"] == "waiting_customer_chronic"
        ].shape[0]
        n_closed_d = g[
            g["category_flag"] == "closed_by_doc_status"
        ].shape[0]

        by_disc.append({
            "discipline": disc,
            "active_total": len(g),
            "ours": n_ours_d,
            "waiting": n_waiting_d,
            "chronic": n_chronic_d,
            "closed_doc": n_closed_d,
        })

    result["by_disc"] = pd.DataFrame(by_disc)

    return result


# ---------------------------------------------------------------------------
#  KPI — текущие темпы и состояние
# ---------------------------------------------------------------------------
def _render_kpi(data: dict) -> bool:
    if data.get("empty"):
        st.warning("Нет данных для прогноза.")
        return False

    monthly = data["monthly"]
    if len(monthly) < 3:
        st.warning("Мало данных для прогноза — нужно минимум 3 месяца.")
        return False

    totals = data["totals"]
    avg_issue = data["avg_issue"]
    avg_fix = data["avg_fix"]
    avg_closed = data["avg_closed"]

    st.markdown("##### 📊 Средние темпы (за последние 3 месяца)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Выдача заказчика",
        f"{int(avg_issue):,}/мес".replace(",", " "),
        help="Сколько новых замечаний приходит от заказчика в месяц.",
    )
    c2.metric(
        "Наши ответы",
        f"{int(avg_fix):,}/мес".replace(",", " "),
        help="Сколько замечаний АТП ТЛП переводит в «Выполнено» за месяц.",
    )
    c3.metric(
        "Приёмка заказчиком",
        f"{int(avg_closed):,}/мес".replace(",", " "),
        help="Сколько замечаний заказчик принимает (статус «Закрыто») "
             "за месяц.",
    )

    balance = avg_fix - avg_issue
    c4.metric(
        "Наш баланс",
        f"{int(balance):+,}/мес".replace(",", " "),
        delta="задолженность растёт" if balance < 0 else "разгребаем",
        delta_color="inverse" if balance < 0 else "normal",
        help="Если отрицательный — мы отвечаем меньше, чем приходит.",
    )

    st.divider()

    st.markdown("##### 🎯 Текущее состояние")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(
        "🔴 АТП ТЛП",
        f"{int(totals['ours']):,}".replace(",", " "),
        help=f"Из них просрочено: {totals['ours_overdue']:,}".replace(",", " "),
    )
    c2.metric(
        "🔵 Ждут заказчика",
        f"{int(totals['waiting']):,}".replace(",", " "),
    )
    c3.metric(
        "🔴 Хроника",
        f"{int(totals['chronic']):,}".replace(",", " "),
    )
    c4.metric(
        "🟢 Учтено A/B",
        f"{int(totals['closed_doc']):,}".replace(",", " "),
    )
    c5.metric(
        "🟡 Заброшено",
        f"{int(totals['abandoned']):,}".replace(",", " "),
    )
    c6.metric(
        "Активных всего",
        f"{int(totals['active']):,}".replace(",", " "),
        help="Без закрытых и аннулированных.",
    )

    st.caption(
        f"**Активных: {int(totals['active']):,}** — без закрытых "
        f"({int(totals['closed']):,}) и аннулированных "
        f"({int(totals['annulled']):,}).".replace(",", " ")
    )

    return True


# ---------------------------------------------------------------------------
#  Прогноз: 2 потока
# ---------------------------------------------------------------------------
def _build_stream_forecast(
    current: int,
    incoming: float,
    outgoing: float,
    months: int = 60,
) -> pd.DataFrame:
    """
    Прогноз одного потока.

    current   — текущее число замечаний в потоке
    incoming  — сколько добавляется в месяц (пополнение потока)
    outgoing  — сколько обрабатывается в месяц
    months    — максимум месяцев (60 = 5 лет)

    Возвращает DataFrame с ym и value.
    """
    today = date.today()
    rows = [{
        "ym": today.strftime("%Y-%m"),
        "value": current,
    }]

    value = current
    for i in range(1, months + 1):
        # Переход в следующий месяц
        d = today + timedelta(days=30 * i)
        ym = d.strftime("%Y-%m")

        value = value + incoming - outgoing
        if value < 0:
            value = 0

        rows.append({"ym": ym, "value": value})
        if value <= 0:
            break

    return pd.DataFrame(rows)


def _render_stream_forecast(data: dict):
    st.markdown("### 📈 Прогноз по двум потокам")
    st.caption(
        "Проект разделён на **два независимых потока**: "
        "наша сторона отвечает, заказчик рассматривает. "
        "Прогноз строится на средних темпах за 3 месяца."
    )

    totals = data["totals"]
    avg_issue = data["avg_issue"]
    avg_fix = data["avg_fix"]
    avg_closed = data["avg_closed"]

    # =====================================================================
    #  Поток 1: Наша сторона
    # =====================================================================
    st.markdown("#### 🔴 Поток 1 — Наша сторона")
    st.caption(
        "Здесь АТП ТЛП должен ответить. Приход — новые замечания. "
        "Расход — наши ответы. **Пока расход ≤ приход — долг не "
        "разгребается.**"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Активных у нас", f"{totals['ours']:,}".replace(",", " "))
    c2.metric("Приходит в месяц", f"{int(avg_issue):,}/мес"
              .replace(",", " "))
    c3.metric("Отвечаем в месяц", f"{int(avg_fix):,}/мес"
              .replace(",", " "))

    if avg_fix <= avg_issue:
        # Наш долг растёт — показать это явно
        delta = avg_fix - avg_issue
        st.error(
            f"🔴 **Мы не успеваем.** В среднем приходит "
            f"**{int(avg_issue):,}/мес**, отвечаем "
            f"**{int(avg_fix):,}/мес** — долг растёт на "
            f"**{int(-delta):,}/мес**. При текущем темпе "
            f"**поток никогда не закроется.**".replace(",", " ")
        )
    else:
        delta = avg_fix - avg_issue
        months = int(totals['ours'] / delta) if delta > 0 else 0
        eta = date.today() + timedelta(days=30 * months) if months else None
        st.success(
            f"✅ **Разгребаем.** Баланс "
            f"**−{int(delta):,}/мес**. Закроем "
            f"**{int(totals['ours']):,}** за **{months} мес.** "
            f"(к {eta.strftime('%m.%Y') if eta else '—'}).".replace(",", " ")
        )

    st.divider()

    # =====================================================================
    #  Поток 2: Заказчик
    # =====================================================================
    st.markdown("#### 🔵 Поток 2 — Сторона заказчика")
    st.caption(
        "Здесь мы ответили, ждём рассмотрения. Приход — наши новые "
        "ответы. Расход — заказчик закрывает. **Хроника — самая "
        "проблемная часть.**"
    )

    current_customer = totals["waiting"] + totals["chronic"] + totals["closed_doc"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ждут заказчика",
              f"{totals['waiting']:,}".replace(",", " "))
    c2.metric("Хроника",
              f"{totals['chronic']:,}".replace(",", " "))
    c3.metric("Учтено A/B",
              f"{totals['closed_doc']:,}".replace(",", " "))
    c4.metric(
        "Итого у заказчика",
        f"{current_customer:,}".replace(",", " "),
    )

    if avg_closed <= 0:
        st.warning(
            "⚠️ **Заказчик почти не закрывает замечания** "
            "(приёмка ≈ 0/мес). Формальное закрытие в Витрокад "
            "не производится. **Требуется эскалация.**"
        )
    else:
        # Сколько нужно, чтобы разгрести текущий долг заказчика
        # (не учитываем incoming, потому что приход = наши ответы,
        # а в модели — только динамика стороны заказчика)
        months = int(current_customer / avg_closed)
        eta = date.today() + timedelta(days=30 * months)
        st.info(
            f"📅 **При темпе {int(avg_closed):,}/мес** заказчик "
            f"разгребёт свои **{current_customer:,}** за **{months} мес.** "
            f"(к {eta.strftime('%m.%Y')}).".replace(",", " ")
        )

    st.divider()

    # =====================================================================
    #  Сводные графики
    # =====================================================================
    st.markdown("#### 📊 Прогноз: два потока рядом")

    # Строим прогноз для двух потоков при базовом темпе
    df_ours_fc = _build_stream_forecast(
        current=totals["ours"],
        incoming=avg_issue,
        outgoing=avg_fix,
    )
    df_cust_fc = _build_stream_forecast(
        current=current_customer,
        incoming=0,               # заказчик не пополняется извне
        outgoing=avg_closed,
    )

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df_ours_fc["ym"], y=df_ours_fc["value"],
        mode="lines+markers",
        name="🔴 АТП ТЛП (наша сторона)",
        line=dict(color="#E57373", width=3),
        marker=dict(size=6),
        hovertemplate="<b>АТП ТЛП</b><br>%{x}<br>%{y:,} замечаний"
                      "<extra></extra>",
    ))

    fig.add_trace(go.Scatter(
        x=df_cust_fc["ym"], y=df_cust_fc["value"],
        mode="lines+markers",
        name="🔵 Заказчик",
        line=dict(color="#64B5F6", width=3),
        marker=dict(size=6),
        hovertemplate="<b>Заказчик</b><br>%{x}<br>%{y:,} замечаний"
                      "<extra></extra>",
    ))

    fig.add_hline(
        y=0, line_dash="dash", line_color="#2E7D32", line_width=2,
    )

    fig.update_layout(
        height=500,
        xaxis_title="Месяц",
        yaxis_title="Замечаний в потоке",
        hovermode="x unified",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        margin=dict(l=60, r=60, t=60, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_2_потока", "fc_2streams",
                    width=1400, height=600)

    st.caption(
        "**Как читать:** если линия **наша сторона** идёт вниз — "
        "мы разгребаем. Если **вверх** — долг растёт. То же про "
        "**заказчика**. Обе линии должны сойтись к нулю."
    )


# ---------------------------------------------------------------------------
#  Сценарии ускорения нашей стороны
# ---------------------------------------------------------------------------
def _render_uplift_scenarios(data: dict):
    st.markdown("### 🚀 Сценарии ускорения: наша сторона")
    st.caption(
        "Что произойдёт с долгом **АТП ТЛП** при разном уровне "
        "усиления команды. Помогает ответить на вопрос: "
        "*«сколько нужно людей, чтобы разгрести за год?»*"
    )

    totals = data["totals"]
    avg_issue = data["avg_issue"]
    avg_fix = data["avg_fix"]
    current_ours = totals["ours"]

    if avg_fix <= 0 or avg_issue <= 0:
        st.warning("Недостаточно данных для сценариев.")
        return

    # Сколько нам нужно отвечать, чтобы разгрести за N месяцев
    # формула: current / N + avg_issue = needed_rate
    months_target = [12, 18, 24, 36, 60]
    rows_target = []
    for m in months_target:
        needed = current_ours / m + avg_issue
        uplift_pct = round((needed / avg_fix - 1) * 100)
        # Сколько «человек» (условных FTE) нужно
        fte_needed = needed / avg_fix
        rows_target.append({
            "Срок разгребания": f"{m} мес.",
            "Нужный темп ответов": f"{int(needed):,}/мес".replace(",", " "),
            "Усиление": f"+{uplift_pct}%",
            "Мощность команды": f"×{fte_needed:.1f}",
        })

    st.markdown("#### 🎯 Сколько нужно, чтобы разгрести за N месяцев")
    st.dataframe(
        pd.DataFrame(rows_target),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ---- Сценарии ускорения (от текущего темпа) ----
    st.markdown("#### 📊 Прогноз при разном уровне усиления")

    today = date.today()
    months_max = 60

    scenarios = [
        ("+0% (текущий)",   0, "#E57373"),  # красный
        ("+50%",            50, "#FFB74D"),  # оранжевый
        ("+100%",          100, "#FFD54F"),  # жёлтый
        ("+150%",          150, "#A5D6A7"),  # светло-зелёный
        ("+200%",          200, "#4CAF50"),  # зелёный
        ("+300%",          300, "#1B5E20"),  # тёмно-зелёный
    ]

    fig = go.Figure()
    summary_rows = []

    for name, uplift_pct, color in scenarios:
        rate = avg_fix * (1 + uplift_pct / 100)
        balance = rate - avg_issue  # положительный = разгребаем

        # Строим линию
        xs, ys = [today.strftime("%Y-%m")], [current_ours]
        val = current_ours
        resolved_month = None

        for i in range(1, months_max + 1):
            d = today + timedelta(days=30 * i)
            val = val + avg_issue - rate
            if val < 0:
                val = 0
            xs.append(d.strftime("%Y-%m"))
            ys.append(val)

            if val <= 0 and resolved_month is None:
                resolved_month = d.strftime("%m.%Y")
            if val <= 0:
                break

        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="lines+markers",
            name=name,
            line=dict(color=color, width=3),
            marker=dict(size=5),
            hovertemplate=(
                f"<b>{name}</b><br>%{{x}}<br>"
                "%{y:,} замечаний<extra></extra>"
            ),
        ))

        summary_rows.append({
            "Сценарий": name,
            "Темп ответов": f"{int(rate):,}/мес".replace(",", " "),
            "Баланс/мес": (
                f"−{int(balance):,}".replace(",", " ")
                if balance > 0 else
                f"+{int(-balance):,} (рост)".replace(",", " ")
            ),
            "Месяцев": (
                "никогда" if balance <= 0
                else f"{len(xs) - 1}"
            ),
            "Дата завершения": (
                "—" if balance <= 0 or resolved_month is None
                else resolved_month
            ),
        })

    fig.add_hline(
        y=0, line_dash="dash", line_color="#2E7D32", line_width=2,
        annotation_text="Закрыто", annotation_position="right",
        annotation_font_color="#2E7D32",
    )

    fig.update_layout(
        height=550,
        xaxis_title="Месяц",
        yaxis_title="Активных у АТП ТЛП",
        hovermode="x unified",
        legend=dict(
            orientation="v", yanchor="top", y=1,
            xanchor="left", x=1.02,
        ),
        margin=dict(l=60, r=200, t=40, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_сценарии_усиления", "fc_uplift",
                    width=1400, height=700)

    st.markdown("#### 📋 Таблица сценариев")
    st.dataframe(
        pd.DataFrame(summary_rows),
        use_container_width=True, hide_index=True,
    )

    # ---- Ключевой вывод ----
    st.divider()
    st.markdown("#### 💡 Что это значит")

    # Найти минимальное усиление, при котором разгребается за год
    if avg_issue >= avg_fix:
        deficit = avg_issue - avg_fix
        st.error(
            f"🔴 **Сейчас мы в дефиците.** Приходит "
            f"**{int(avg_issue):,}/мес**, отвечаем **{int(avg_fix):,}/мес** "
            f"— дефицит **{int(deficit):,}/мес**. "
            f"Чтобы выйти в ноль — нужно усиление минимум "
            f"**+{round(deficit / avg_fix * 100)}%** (×"
            f"{round((avg_issue) / avg_fix, 1)})."
            .replace(",", " ")
        )

    # Сколько нужно для закрытия за 12 мес
    needed_12 = current_ours / 12 + avg_issue
    uplift_12 = round((needed_12 / avg_fix - 1) * 100)
    fte_12 = needed_12 / avg_fix

    st.info(
        f"📅 **Чтобы закрыть {int(current_ours):,} за 12 месяцев** — "
        f"нужен темп **{int(needed_12):,}/мес**, усиление "
        f"**+{uplift_12}%** (×{fte_12:.1f} к текущей мощности)."
        .replace(",", " ")
    )

    needed_24 = current_ours / 24 + avg_issue
    uplift_24 = round((needed_24 / avg_fix - 1) * 100)
    fte_24 = needed_24 / avg_fix

    st.info(
        f"📅 **Чтобы закрыть за 24 месяца** — усиление "
        f"**+{uplift_24}%** (×{fte_24:.1f})."
        .replace(",", " ")
    )

# ---------------------------------------------------------------------------
#  Прогноз по 8 категориям
# ---------------------------------------------------------------------------
def _render_by_category(data: dict):
    st.markdown("### 📋 Когда уйдёт каждая из 8 категорий")
    st.caption(
        "Для каждой категории — средний темп за 3 месяца и прогноз, "
        "когда она закроется. Помогает приоритизировать работу."
    )

    totals = data["totals"]
    by_flag = totals["by_flag"]

    # Скорости по категориям (упрощение: берём долю от наших ответов)
    # 8 категорий × средняя скорость выхода
    # (в реальности каждая категория имеет свой темп, но у нас нет такой детализации в БД)
    avg_fix = data["avg_fix"]
    avg_closed = data["avg_closed"]

    # Приблизительный темп по каждой категории
    # — наша сторона: avg_fix (наши ответы)
    # — заказчик: avg_closed (рассмотрение)
    # — учтено A/B: тоже avg_closed (формальное закрытие)
    categories = [
        # (flag, label, count, rate)
        ("new_overdue", "🆕 Новое (просроч.)",
         by_flag.get("new_overdue", 0), avg_fix),
        ("new_in_progress", "🆕 Новое (в сроке)",
         by_flag.get("new_in_progress", 0), avg_fix),
        ("in_work_overdue", "🛠 В работе (просроч.)",
         by_flag.get("in_work_overdue", 0), avg_fix),
        ("in_work_in_progress", "🛠 В работе (в сроке)",
         by_flag.get("in_work_in_progress", 0), avg_fix),
        ("rejected_overdue", "🟪 Не принято (просроч.)",
         by_flag.get("rejected_overdue", 0), avg_fix),
        ("rejected_in_progress", "🟪 Не принято (в сроке)",
         by_flag.get("rejected_in_progress", 0), avg_fix),
        ("discussion_overdue", "🟣 К обсужд. (просроч.)",
         by_flag.get("discussion_overdue", 0), avg_fix),
        ("discussion_in_progress", "🟣 К обсужд. (в сроке)",
         by_flag.get("discussion_in_progress", 0), avg_fix),
        ("waiting_customer", "🔵 Ждут заказчика",
         by_flag.get("waiting_customer", 0), avg_closed),
        ("waiting_customer_overdue", "🔵 Ждут (просроч. 30–90)",
         by_flag.get("waiting_customer_overdue", 0), avg_closed),
        ("waiting_customer_ontime", "🔵 Ждут (в сроке)",
         by_flag.get("waiting_customer_ontime", 0), avg_closed),
        ("waiting_customer_chronic", "🔴 Хроника (>90)",
         by_flag.get("waiting_customer_chronic", 0), avg_closed),
        ("closed_by_doc_status", "🟢 Учтено A/B",
         by_flag.get("closed_by_doc_status", 0), avg_closed),
    ]

    rows = []
    for flag, label, count, rate in categories:
        if count == 0:
            continue

        if rate <= 0:
            months = "никогда"
            eta = "—"
        else:
            # доля этой категории в общем темпе
            # (упрощение: все категории движутся с одинаковой скоростью)
            m = int(count / rate)
            if m <= 0:
                m = 1
            d = date.today() + timedelta(days=30 * m)
            months = m
            eta = d.strftime("%m.%Y")

        rows.append({
            "Категория": label,
            "Замечаний": count,
            "Темп/мес": int(rate) if rate > 0 else 0,
            "Месяцев": months,
            "Прогноз": eta,
        })

    df = pd.DataFrame(rows).sort_values(
        "Замечаний", ascending=False)

    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "Замечаний": st.column_config.NumberColumn(format="%d"),
            "Темп/мес": st.column_config.NumberColumn(format="%d"),
        },
    )

    st.caption(
        "**Оговорка:** темп по категориям приблизительный — "
        "упрощение модели. Точный темп по каждой категории требует "
        "анализа истории изменений (пока нет)."
    )


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
def _render_by_discipline(data: dict):
    st.markdown("### 🏷 Покрытие темпа по дисциплинам")
    st.caption(
        "Сколько замечаний в каждой дисциплине, распределено по "
        "ответственным. Сортировка — по объёму у АТП ТЛП."
    )

    df = data["by_disc"]
    if df.empty:
        st.info("Нет данных по дисциплинам.")
        return

    df = df.copy()
    df["Наименование"] = df["discipline"].apply(discipline_name)
    df["pct_ours"] = df.apply(
        lambda r: round(r["ours"] / r["active_total"] * 100, 1)
        if r["active_total"] else 0,
        axis=1,
    )
    df = df.sort_values("ours", ascending=False)

    # ---- Стек-бар ----
    chart = df.sort_values("ours", ascending=True)
    fig = go.Figure()

    fig.add_trace(go.Bar(
        y=chart["discipline"], x=chart["ours"],
        name="🔴 АТП ТЛП",
        orientation="h",
        marker=dict(color="#E57373"),
    ))
    fig.add_trace(go.Bar(
        y=chart["discipline"], x=chart["waiting"],
        name="🔵 Ждут заказчика",
        orientation="h",
        marker=dict(color="#64B5F6"),
    ))
    fig.add_trace(go.Bar(
        y=chart["discipline"], x=chart["chronic"],
        name="🔴 Хроника",
        orientation="h",
        marker=dict(color="#7F0000"),
    ))
    fig.add_trace(go.Bar(
        y=chart["discipline"], x=chart["closed_doc"],
        name="🟢 Учтено A/B",
        orientation="h",
        marker=dict(color="#2E7D32"),
    ))

    fig.update_layout(
        barmode="stack",
        height=max(400, 35 * len(chart)),
        xaxis_title="Активных замечаний",
        yaxis_title="",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        margin=dict(l=120, r=60, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_по_дисциплинам", "fc_disc",
                    width=1400, height=600)

    # ---- Таблица ----
    table = df[[
        "discipline", "Наименование", "active_total",
        "ours", "waiting", "chronic", "closed_doc", "pct_ours",
    ]].rename(columns={
        "discipline": "Код",
        "active_total": "Всего активных",
        "ours": "🔴 АТП ТЛП",
        "waiting": "🔵 Ждут заказ.",
        "chronic": "🔴 Хроника",
        "closed_doc": "🟢 Учтено A/B",
        "pct_ours": "% на нас",
    })

    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "% на нас": st.column_config.ProgressColumn(
                "% на нас", min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    # ---- Итоги ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего активных",
              f"{int(df['active_total'].sum()):,}".replace(",", " "))
    c2.metric("🔴 АТП ТЛП",
              f"{int(df['ours'].sum()):,}".replace(",", " "))
    c3.metric("🔵 Ждут заказ.",
              f"{int(df['waiting'].sum()):,}".replace(",", " "))
    c4.metric("🔴 Хроника",
              f"{int(df['chronic'].sum()):,}".replace(",", " "))


# ---------------------------------------------------------------------------
#  Комплекты в зоне риска
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_risk_complexes() -> pd.DataFrame:
    """Комплекты с наибольшим объёмом просрочек АТП ТЛП."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    overdue_flags = [
        "new_overdue", "in_work_overdue",
        "rejected_overdue", "discussion_overdue",
    ]
    sub = active[active["category_flag"].isin(overdue_flags)]

    if sub.empty:
        return pd.DataFrame()

    top = (sub.groupby(["complex", "discipline"]).size()
             .reset_index(name="Просрочек")
             .sort_values("Просрочек", ascending=False)
             .head(20))
    return top


def _render_risk_complexes():
    st.markdown("### ⚠️ Комплекты в зоне риска")
    st.caption(
        "Комплекты с наибольшим числом **просрочек АТП ТЛП**. "
        "Единая логика с дашбордом и вкладкой «Отстающие»."
    )

    df = _load_risk_complexes()
    if df.empty:
        st.success("🎉 Нет просрочек АТП ТЛП.")
        return

    chart = df.sort_values("Просрочек", ascending=True)
    fig = px.bar(
        chart,
        x="Просрочек", y="complex", orientation="h",
        text="Просрочек",
        color="discipline",
        labels={"complex": "", "discipline": "Дисциплина"},
        title="Топ-20 комплектов по просрочкам АТП ТЛП",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(500, 28 * len(chart)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_зона_риска", "fc_risk",
                    width=1400, height=700)


# ---------------------------------------------------------------------------
#  Ключевые выводы
# ---------------------------------------------------------------------------
def _render_conclusions(data: dict):
    st.markdown("### 🎯 Ключевые выводы")

    totals = data["totals"]
    avg_issue = data["avg_issue"]
    avg_fix = data["avg_fix"]
    avg_closed = data["avg_closed"]

    # Наш баланс
    our_balance = avg_fix - avg_issue
    # Баланс заказчика
    cust_balance = avg_closed  # сколько заказчик закрывает в месяц

    c1, c2 = st.columns(2)

    with c1:
        if our_balance >= 0:
            st.success(
                f"✅ **Наш поток разгребается.** "
                f"Баланс +{int(our_balance):,}/мес.".replace(",", " ")
            )
        else:
            st.error(
                f"🔴 **Наш поток растёт.** "
                f"Дефицит {int(-our_balance):,}/мес. "
                f"Нужно ускориться.".replace(",", " ")
            )

    with c2:
        if avg_closed > 0:
            st.info(
                f"📅 **Заказчик закрывает {int(avg_closed):,}/мес.** "
                f"Этого хватит, чтобы разгрести "
                f"{int(totals['waiting'] + totals['chronic'] + totals['closed_doc']):,} "
                f"за ≈{int((totals['waiting'] + totals['chronic'] + totals['closed_doc']) / avg_closed)} мес."
                .replace(",", " ")
            )
        else:
            st.warning(
                "⚠️ **Заказчик не закрывает замечания.** "
                "Требуется эскалация."
            )

    st.divider()

    # Рекомендации
    st.markdown("#### 💡 Рекомендации")

    if our_balance < 0:
        needed_uplift = int(abs(our_balance))
        st.markdown(
            f"- 🔴 **Ускорить наши ответы на +{needed_uplift:,}/мес** "
            f"— иначе долг будет расти.".replace(",", " ")
        )

    if totals["chronic"] > 1000:
        st.markdown(
            f"- 🔴 **{totals['chronic']:,} хронических замечаний** — "
            f"письмо руководству заказчика на эскалацию."
            .replace(",", " ")
        )

    if totals["closed_doc"] > 1000:
        st.markdown(
            f"- 🟢 **{totals['closed_doc']:,} учтённых A/B** — "
            f"дожать заказчика на формальное закрытие."
            .replace(",", " ")
        )

    if totals["ours_overdue"] > 5000:
        st.markdown(
            f"- 🚨 **{totals['ours_overdue']:,} просрочек АТП ТЛП** — "
            f"критический уровень. Направить ресурсы.".replace(",", " ")
        )


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("🔮 Прогноз")
    st.caption(
        "Экстраполяция текущих темпов. Прогноз разделён на **два "
        "независимых потока** — наша сторона и заказчик. "
        "Данные синхронизированы с дашбордом."
    )

    # Кнопка «Обновить» убрана — данные из кэша (TTL 1 час).

    with st.spinner("Загрузка данных..."):
        data = _load_data()

    if not _render_kpi(data):
        return

    st.divider()

    # Прогноз по 2 потокам
    _render_stream_forecast(data)

    st.divider()

    # Сценарии ускорения
    _render_uplift_scenarios(data)

    st.divider()

    # Прогноз по 8 категориям
    _render_by_category(data)

    st.divider()

    # По дисциплинам
    _render_by_discipline(data)

    st.divider()

    # Комплекты в зоне риска
    _render_risk_complexes()

    st.divider()

    # Выводы
    _render_conclusions(data)

    st.divider()
    st.info(
        "💡 **Про модель:** линейная экстраполяция на основе средних "
        "темпов за 3 месяца. **Не учитывает** сезонность (отпуска, "
        "праздники), изменения команды и объёма работ. "
        "**Разделение на 2 потока** — ключевое отличие от старой "
        "модели: наша сторона и заказчик движутся с разной скоростью."
    )