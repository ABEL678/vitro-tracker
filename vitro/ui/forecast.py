# vitro/ui/forecast.py
"""
🔮 Прогноз — экстраполяция текущих темпов.

Методика:
  - Берём средние темпы выдачи и ответов за последние 3 месяца.
  - Экстраполируем на 6 месяцев вперёд.
  - Показываем 3 сценария: базовый, оптимистичный (+20%), пессимистичный (−20%).
  - Считаем, когда разгребём задолженность и сколько будет активных к концу года.

Оговорки:
  - Прогноз линейный, без сезонности.
  - Не учитывает изменения в команде или объёме работ.
  - Погрешность ±20%.
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
#  Загрузка и подготовка данных
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_data() -> dict:
    """
    Собирает все данные для прогноза.

    Использует категоризацию из deadlines для точного подсчёта
    реально активных (без A/B-учтённых и хронических).
    """
    from vitro.ui.deadlines import _load_all_categorized

    result = {}

    # =====================================================================
    #  1. Категоризация из deadlines (единый источник истины)
    # =====================================================================
    cats_df = _load_all_categorized()

    with get_conn() as conn:
        # Базовые метрики из БД
        base = dict(conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('Закрыто','Выполнено')
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM comments
        """).fetchone())

        # =================================================================
        #  2. Темпы выдачи (по created)
        # =================================================================
        issue = pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # =================================================================
        #  3. Темпы наших ответов (по fix_date + статус)
        # =================================================================
        fix = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто', 'Выполнено')
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # =================================================================
        #  4. Темпы закрытия заказчиком (только «Закрыто»)
        # =================================================================
        closed = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status = 'Закрыто'
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # =================================================================
        #  5. По дисциплинам (для прогноза покрытия)
        # =================================================================
        by_disc = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено')
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу',
                                            'Не принято','К обсуждению')
                         THEN 1 ELSE 0 END) AS active
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
            ORDER BY d.discipline
        """, conn)

    # =====================================================================
    #  6. Реально активные (с учётом категорий из deadlines)
    # =====================================================================
    if not cats_df.empty:
        # Реально активные = наша сторона + ждут заказчика (без хроники)
        real_active_flags = [
            # Наша сторона
            "new_overdue", "new_in_progress",
            "in_work_overdue", "in_work_in_progress",
            "rejected_overdue", "rejected_in_progress",
            "discussion_overdue", "discussion_in_progress",
            # Заказчик (без хронических)
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime",
        ]

        real_active_df = cats_df[cats_df["category_flag"].isin(real_active_flags)]
        real_active = len(real_active_df)

        # Учтённые (лист A/B) — фактически приняты, но не закрыты формально
        closed_by_doc = cats_df[
            cats_df["category_flag"] == "closed_by_doc_status"
        ].shape[0]

        # Хронические (>90 р.д.) — ждут заказчика, но не реальные
        chronic = cats_df[
            cats_df["category_flag"] == "waiting_customer_chronic"
        ].shape[0]

        # Наша сторона (сумма 4 категорий)
        ours = cats_df[cats_df["category_flag"].isin([
            "new_overdue", "new_in_progress",
            "in_work_overdue", "in_work_in_progress",
            "rejected_overdue", "rejected_in_progress",
            "discussion_overdue", "discussion_in_progress",
        ])].shape[0]

        # Ждут заказчика (без хроники)
        waiting = cats_df[cats_df["category_flag"].isin([
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime",
        ])].shape[0]
    else:
        real_active = closed_by_doc = chronic = ours = waiting = 0

    result["totals"] = {
        "total": base["total"] or 0,
        "closed": base["closed"] or 0,
        "annulled": base["annulled"] or 0,
        "active": real_active,
        # Расширенные категории
        "ours": ours,
        "waiting": waiting,
        "closed_by_doc": closed_by_doc,
        "chronic": chronic,
    }

    # =====================================================================
    #  7. Объединение выдачи/ответов/закрытий в одну таблицу по месяцам
    # =====================================================================
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

    merged["Отставание"] = merged["Выдано"] - merged["Отвечено"]

    result["monthly"] = merged
    result["by_disc"] = by_disc

    return result

# ---------------------------------------------------------------------------
#  KPI — средние темпы
# ---------------------------------------------------------------------------
def _render_kpi(data: dict) -> dict:
    monthly = data["monthly"]

    if len(monthly) < 3:
        st.warning("Мало данных для прогноза — нужно минимум 3 месяца.")
        return {}

    # Последние 3 месяца с выдачей
    recent = monthly.tail(3)
    avg_issue = recent["Выдано"].mean()
    avg_fix = recent["Отвечено"].mean()
    avg_closed = recent["Закрыто"].mean()
    avg_delta = avg_issue - avg_fix

    # Текущее состояние
    totals = data["totals"]
    active = totals["active"] or 0
    total = totals["total"] or 0
    closed = totals["closed"] or 0

    st.markdown("##### 📊 Средние темпы (за последние 3 месяца)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Выдача", f"{int(avg_issue):,}/мес".replace(",", " "),
              help="Сколько новых замечаний приходит от заказчика")
    c2.metric("Наши ответы", f"{int(avg_fix):,}/мес".replace(",", " "),
              help="Сколько замечаний мы закрываем ответом")
    c3.metric("Приёмка заказчиком",
              f"{int(avg_closed):,}/мес".replace(",", " "),
              help="Сколько замечаний заказчик принял (статус «Закрыто»)")
    c4.metric("Отставание", f"{int(avg_delta):+,}/мес".replace(",", " "),
              delta="плохо" if avg_delta > 0 else "хорошо",
              delta_color="inverse" if avg_delta > 0 else "normal",
              help="Если положительное — задолженность растёт")

    # Разъяснение
    totals = data["totals"]
    st.caption(
        f"**Активных сейчас: {int(totals['active']):,}** "
        f"(из них **наша сторона: {int(totals['ours']):,}**, "
        f"**ждут заказчика: {int(totals['waiting']):,}**).\n"
        f"Не учитываются: **{int(totals['closed_by_doc']):,}** фактически "
        f"принятых (лист A/B) и **{int(totals['chronic']):,}** хронических "
        f"(> 90 р.д.) — они не влияют на прогноз.".replace(",", " ")
    )

    return {
        "avg_issue": avg_issue,
        "avg_fix": avg_fix,
        "avg_closed": avg_closed,
        "avg_delta": avg_delta,
        "active": active,
        "total": total,
        "closed": closed,
    }


# ---------------------------------------------------------------------------
#  Прогноз на 6 месяцев вперёд
# ---------------------------------------------------------------------------
def _build_forecast(data: dict, kpi: dict) -> pd.DataFrame:
    """
    Экстраполяция активных замечаний при разных сценариях ускорения.

    Для каждого сценария (uplift %) рассчитываем, как меняется
    количество активных замечаний по месяцам вперёд до нуля.
    """
    monthly = data["monthly"]
    if monthly.empty or not kpi:
        return pd.DataFrame()

    avg_issue = kpi["avg_issue"]
    avg_fix = kpi["avg_fix"]
    current_active = kpi["active"]

    # Последний месяц в данных
    last_ym = monthly["ym"].iloc[-1]
    last_date = datetime.strptime(last_ym + "-01", "%Y-%m-%d").date()

    # Максимум 60 месяцев (5 лет) — чтобы график не тянулся бесконечно
    MAX_MONTHS = 60

    # Сценарии ускорения (в %)
    scenarios = [
        ("+0% (текущий)", 0, "#E57373"),
        ("+50%", 50, "#FFB74D"),
        ("+100%", 100, "#FFD54F"),
        ("+150%", 150, "#A5D6A7"),
        ("+200%", 200, "#4CAF50"),
        ("+300%", 300, "#1B5E20"),
    ]

    rows = []

    # Точка «сейчас»
    rows.append({
        "ym": last_ym,
        "Сценарий": "Сейчас",
        "Активных": int(current_active),
        "Цвет": "#1F4E78",
    })

    for scenario_name, uplift_pct, color in scenarios:
        # Темп ответов с учётом ускорения
        rate = avg_fix * (1 + uplift_pct / 100)
        delta = avg_issue - rate  # положительное = задолженность растёт

        active = current_active
        for i in range(1, MAX_MONTHS + 1):
            # Через 30*i дней от последнего месяца
            d = last_date + timedelta(days=30 * i)
            ym = d.strftime("%Y-%m")

            active = active + (avg_issue - rate)
            if active < 0:
                active = 0

            rows.append({
                "ym": ym,
                "Сценарий": scenario_name,
                "Активных": int(active),
                "Цвет": color,
            })

            # Прекращаем, когда дошли до нуля
            if active <= 0:
                break

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
#  KPI прогноза
# ---------------------------------------------------------------------------
def _render_forecast_kpi(forecast: pd.DataFrame, kpi: dict):
    """Компактные метрики текущего состояния."""
    if forecast.empty:
        return

    active_now = kpi["active"]
    avg_issue = kpi["avg_issue"]
    avg_fix = kpi["avg_fix"]
    avg_delta = avg_issue - avg_fix

    st.markdown("##### 🔮 Текущее состояние")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Активных сейчас",
              f"{int(active_now):,}".replace(",", " "))
    c2.metric("Приходит в месяц",
              f"{int(avg_issue):,}".replace(",", " "))
    c3.metric("Отвечаем в месяц",
              f"{int(avg_fix):,}".replace(",", " "))
    c4.metric(
        "Баланс",
        f"{int(avg_delta):+,}".replace(",", " "),
        delta="задолженность растёт" if avg_delta > 0 else "разгребаем",
        delta_color="inverse" if avg_delta > 0 else "normal",
    )


# ---------------------------------------------------------------------------
#  График прогноза
# ---------------------------------------------------------------------------
def _render_forecast_chart(forecast: pd.DataFrame, kpi: dict):
    """График прогноза полного устранения замечаний при разных сценариях."""
    st.markdown("### 📈 Прогноз полного устранения замечаний")

    if forecast.empty:
        st.info("Нет данных.")
        return

    # Убираем точку «Сейчас» из линий — она только для отметки
    fc = forecast[forecast["Сценарий"] != "Сейчас"].copy()
    if fc.empty:
        return

    # Цвета по сценариям (первое вхождение)
    color_map = (
        fc[["Сценарий", "Цвет"]]
        .drop_duplicates("Сценарий")
        .set_index("Сценарий")["Цвет"]
        .to_dict()
    )

    fig = go.Figure()

    # Точка «Сейчас»
    fig.add_trace(go.Scatter(
        x=[forecast.iloc[0]["ym"]],
        y=[kpi["active"]],
        mode="markers",
        name="Сейчас",
        marker=dict(color="#1F4E78", size=14, symbol="diamond",
                    line=dict(color="white", width=2)),
        hovertemplate="<b>Сейчас</b><br>%{y:,} активных<extra></extra>",
    ))

    # Линии сценариев
    for scenario in fc["Сценарий"].unique():
        sub = fc[fc["Сценарий"] == scenario]
        color = color_map.get(scenario, "#888")

        # Определяем дату завершения (последняя точка на нуле)
        last_row = sub[sub["Активных"] == 0]
        end_label = ""
        if not last_row.empty:
            end_ym = last_row.iloc[0]["ym"]
            end_label = f" → 0 к {end_ym}"

        fig.add_trace(go.Scatter(
            x=sub["ym"], y=sub["Активных"],
            mode="lines+markers",
            name=scenario + end_label,
            line=dict(color=color, width=3),
            marker=dict(size=6),
            hovertemplate=(
                f"<b>{scenario}</b><br>"
                "%{x}<br>%{y:,} активных<extra></extra>"
            ),
        ))

    # Горизонтальная линия y=0 — «всё закрыто»
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="#2E7D32",
        line_width=2,
        annotation_text="Процесс завершён",
        annotation_position="right",
        annotation_font_color="#2E7D32",
        annotation_font_size=11,
    )

    fig.update_layout(
        height=550,
        xaxis_title="Месяц",
        yaxis_title="Активных замечаний",
        hovermode="x unified",
        legend=dict(
            orientation="v",
            yanchor="top", y=1,
            xanchor="left", x=1.02,
            font=dict(size=11),
        ),
        margin=dict(l=60, r=200, t=40, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_устранения", "forecast_cleanup",
                    width=1400, height=700)

    # Пояснение
    st.caption(
        "**Как читать:** каждая линия — сценарий ускорения команды. "
        "**Пересечение с пунктирной линией** — месяц, когда все замечания "
        "будут закрыты. **Красная линия (+0%)** — текущий темп: "
        "задолженность не уменьшается, процесс никогда не завершится. "
        "**Зелёные линии** — реалистичные сценарии с разным уровнем ресурсов."
    )

    # Сводка по сценариям
    st.markdown("#### 📊 Сценарии завершения")

    summary = []
    for scenario in fc["Сценарий"].unique():
        sub = fc[fc["Сценарий"] == scenario]
        last = sub.iloc[-1]

        if last["Активных"] == 0:
            # Нашли дату завершения
            end_ym = last["ym"]
            months = len(sub)
            summary.append({
                "Сценарий": scenario,
                "Месяцев": months,
                "Дата завершения": end_ym,
            })
        else:
            summary.append({
                "Сценарий": scenario,
                "Месяцев": "—",
                "Дата завершения": f"не завершится",
            })

    if summary:
        df_summary = pd.DataFrame(summary)
        st.dataframe(df_summary, use_container_width=True, hide_index=True)

    # Вывод
    st.divider()
    st.markdown("#### 🎯 Ключевые выводы")

    # Проверяем, сколько сценариев доходят до нуля
    resolved = [s for s in summary if s["Месяцев"] != "—"]

    if not resolved:
        st.error(
            "🔴 **Ни один сценарий не завершает процесс за 5 лет.** "
            "Темп ответов нужно увеличить значительно — минимум в "
            "2 раза. Обратитесь к руководству за расширением команды."
        )
    else:
        fastest = min(resolved, key=lambda x: x["Месяцев"])
        slowest = max(resolved, key=lambda x: x["Месяцев"])

        c1, c2 = st.columns(2)
        c1.metric(
            "Самый быстрый сценарий",
            fastest["Сценарий"],
            delta=f"{fastest['Месяцев']} мес. → {fastest['Дата завершения']}",
            delta_color="off",
        )
        c2.metric(
            "Самый долгий из реалистичных",
            slowest["Сценарий"],
            delta=f"{slowest['Месяцев']} мес. → {slowest['Дата завершения']}",
            delta_color="off",
        )

        st.success(
            f"✅ **Реалистичные сценарии:** проект можно завершить за "
            f"**{fastest['Месяцев']}–{slowest['Месяцев']}** месяцев "
            f"при ускорении команды."
        )


# ---------------------------------------------------------------------------
#  «Когда разгребём задолженность»
# ---------------------------------------------------------------------------
def _render_cleanup_eta(kpi: dict):
    """
    Отвечает на вопрос: «Когда завершится процесс устранения замечаний?»

    Три сценария:
      1. Текущий темп — никогда.
      2. Целевой темп (+20%) — через N месяцев.
      3. Ускорение x раз — через M месяцев.
    """
    st.markdown("### ⏳ Когда завершится устранение замечаний?")

    active = kpi["active"]
    avg_issue = kpi["avg_issue"]
    avg_fix = kpi["avg_fix"]
    avg_delta = kpi["avg_delta"]

    # Сценарий 1: текущий темп
    st.markdown("#### 🔴 Сценарий 1: текущий темп")

    c1, c2, c3 = st.columns(3)
    c1.metric("Активных сейчас", f"{int(active):,}".replace(",", " "))
    c2.metric("Выдача в месяц", f"{int(avg_issue):,}/мес".replace(",", " "))
    c3.metric("Наши ответы в месяц", f"{int(avg_fix):,}/мес".replace(",", " "))

    if avg_delta >= 0:
        st.error(
            f"🔴 **Процесс НЕ завершится в текущем темпе.** "
            f"Задолженность растёт на **+{int(avg_delta):,}/мес**. "
            f"Мы отвечаем на **{int(avg_fix)}** замечаний, а приходит "
            f"**{int(avg_issue)}** — не успеваем.".replace(",", " ")
        )
    else:
        months = int(active / abs(avg_delta))
        eta = date.today() + timedelta(days=30 * months)
        st.success(
            f"✅ **Процесс завершится через {months} мес.** "
            f"При текущем темпе задолженность уменьшается на "
            f"**{int(abs(avg_delta))}/мес**."
            .replace(",", " ")
        )
        st.info(f"📅 Ожидаемая дата завершения: **{eta.strftime('%m.%Y')}**")

    st.divider()

    # Сценарий 2: целевой темп (выдача + 20%)
    st.markdown("#### 🟢 Сценарий 2: целевой темп (разгребаем)")

    target_rate = avg_issue * 1.2
    needed_uplift = round((target_rate / avg_fix - 1) * 100)

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Целевой темп ответов",
        f"{int(target_rate):,}/мес".replace(",", " "),
        help="Выдача × 1.2 — чтобы задолженность уменьшалась",
    )
    c2.metric(
        "Нужно ускориться на",
        f"{needed_uplift}%",
        delta="проблема" if needed_uplift > 100 else None,
        delta_color="inverse",
    )

    # Сколько месяцев до нуля при целевом темпе
    cleanup_speed = target_rate - avg_issue
    if cleanup_speed > 0:
        months_target = int(active / cleanup_speed)
        eta_target = date.today() + timedelta(days=30 * months_target)

        c3.metric(
            "Закроем через",
            f"{months_target} мес.",
            help=f"Ожидаемая дата: {eta_target.strftime('%m.%Y')}",
        )

        st.success(
            f"✅ **Если ускоримся на {needed_uplift}%** — закроем "
            f"все {int(active):,} замечаний за **{months_target} мес.** "
            f"(к {eta_target.strftime('%m.%Y')})."
            .replace(",", " ")
        )
    else:
        st.warning("Не удалось рассчитать — данные недостаточны.")

    st.divider()

    # Сценарий 3: разные варианты ускорения
    st.markdown("#### 📊 Таблица сценариев ускорения")

    rows = []
    for uplift_pct in [0, 20, 50, 100, 150, 200]:
        rate = avg_fix * (1 + uplift_pct / 100)
        delta = rate - avg_issue

        if delta > 0:
            months = int(active / delta)
            eta = date.today() + timedelta(days=30 * months)
            rows.append({
                "Ускорение": f"+{uplift_pct}%",
                "Темп ответов": f"{int(rate):,}/мес".replace(",", " "),
                "Скорость разгребания": f"−{int(delta):,}/мес".replace(",", " "),
                "Месяцев до нуля": months,
                "Дата завершения": eta.strftime("%m.%Y"),
            })
        else:
            rows.append({
                "Ускорение": f"+{uplift_pct}%",
                "Темп ответов": f"{int(rate):,}/мес".replace(",", " "),
                "Скорость разгребания": f"+{int(-delta):,}/мес (рост)".replace(",", " "),
                "Месяцев до нуля": "никогда",
                "Дата завершения": "—",
            })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption(
        "**Как читать:** таблица показывает, при каком ускорении команды "
        "мы закроем все замечания и когда. Строка с **+0%** — это текущий "
        "темп (задолженность растёт). Строки с положительными процентами — "
        "разные сценарии ускорения."
    )

    st.divider()

    # Практические рекомендации
    st.markdown("#### 💡 Что это значит")

    if needed_uplift > 100:
        st.error(
            f"🔴 **Нужно ускориться в **{round(target_rate / avg_fix, 1)} раза**. "
            f"Текущего состава команды недостаточно — требуется "
            f"дополнительно **{round((target_rate - avg_fix) / avg_fix, 1)}×** "
            f"к мощности. Варианты:\n\n"
            f"1. Привлечь **{int((target_rate - avg_fix) / avg_fix * 10)}** "
            f"дополнительных специалистов\n"
            f"2. Автоматизировать типовые ответы\n"
            f"3. Пересмотреть процесс приёмки замечаний с заказчиком"
        )
    elif needed_uplift > 50:
        st.warning(
            f"🟡 **Нужно ускориться на {needed_uplift}%**. "
            f"Это возможно при небольшой оптимизации процесса: "
            f"дополнительный специалист или перераспределение нагрузки."
        )
    elif needed_uplift > 0:
        st.success(
            f"✅ **Достаточно ускориться на {needed_uplift}%** — это "
            f"реалистично без расширения команды. Можно разгрести."
        )
    else:
        st.success(
            "✅ Команда уже разгребает задолженность. "
            "Темп достаточный."
        )


# ---------------------------------------------------------------------------
#  Комплекты в зоне риска
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_risk_complexes() -> pd.DataFrame:
    """Комплекты с низким % и большим объёмом активных."""
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                d.complex,
                d.discipline,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено')
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу',
                                            'Не принято','К обсуждению')
                         THEN 1 ELSE 0 END) AS active
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
            GROUP BY d.complex, d.discipline
            HAVING active >= 20
            ORDER BY active DESC
            LIMIT 20
        """, conn)


def _render_risk_complexes():
    st.markdown("### ⚠️ Комплекты в зоне риска")
    st.caption(
        "Комплекты с наибольшим числом активных замечаний. "
        "При текущем темпе эти комплекты закроются последними."
    )

    df = _load_risk_complexes()
    if df.empty:
        st.info("Нет данных.")
        return

    df["pct"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1) if r["total"] else 0,
        axis=1,
    )

    df = df.sort_values("active", ascending=True)

    fig = px.bar(
        df, x="active", y="complex", orientation="h",
        text="active",
        color="pct",
        color_continuous_scale=["#E57373", "#FFB74D", "#A5D6A7"],
        labels={"active": "Активных замечаний",
                "complex": "",
                "pct": "% выполнения"},
        title="Топ-20 комплектов по остатку работы",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(500, 25 * len(df)),
                      coloraxis_showscale=True)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_зона_риска", "forecast_risk",
                    width=1400, height=700)

    # Таблица
    table = df[["complex", "discipline", "total", "closed", "active", "pct"]]
    table.columns = ["Комплект", "Дисциплина", "Всего",
                     "Закрыто", "Активных", "% выполнения"]
    table = table.sort_values("Активных", ascending=False)

    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "% выполнения": st.column_config.ProgressColumn(
                "% выполнения", min_value=0, max_value=100, format="%.1f%%"),
        },
    )


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
def _render_by_discipline(data: dict):
    """
    Прогноз по дисциплинам с акцентом на % покрытия темпа.
    """
    st.markdown("### 🏷 Покрытие темпа по дисциплинам")

    st.caption(
        "**Что это значит:** для каждой дисциплины сравниваем, сколько "
        "замечаний приходит (выдача) и сколько мы успеваем закрыть (ответы). "
        "**100%** — успеваем закрывать все новые замечания, долг не растёт. "
        "**<50%** — отстаём сильно."
    )

    df_disc = data["by_disc"]

    if df_disc.empty:
        st.info("Нет данных по дисциплинам.")
        return

    # Темпы за последние 3 месяца
    with get_conn() as conn:
        rates = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                SUM(CASE WHEN substr(c.created, 1, 7) >=
                         strftime('%Y-%m', 'now', '-3 months')
                         THEN 1 ELSE 0 END) AS issue_3m,
                SUM(CASE WHEN substr(c.fix_date, 1, 7) >=
                         strftime('%Y-%m', 'now', '-3 months')
                         AND c.status IN ('Закрыто', 'Выполнено')
                         THEN 1 ELSE 0 END) AS fix_3m
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
        """, conn)

    if rates.empty:
        st.warning("Нет данных по темпам за последние 3 месяца.")
        return

    df = df_disc.merge(rates, on="discipline", how="left").fillna(0)

    df["issue_month"] = (df["issue_3m"] / 3).round(0).astype(int)
    df["fix_month"] = (df["fix_3m"] / 3).round(0).astype(int)

    df["coverage"] = df.apply(
        lambda r: round(r["fix_month"] / r["issue_month"] * 100, 1)
        if r["issue_month"] > 0 else 100.0,
        axis=1,
    )
    df["delta_month"] = df["issue_month"] - df["fix_month"]
    df["pct"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1) if r["total"] else 0,
        axis=1,
    )

    df = df.sort_values("coverage", ascending=True)

    # =====================================================================
    #  ГРАФИК: % покрытия
    # =====================================================================
    st.markdown("#### 📊 Покрытие темпа по дисциплинам")

    def _coverage_color(c):
        if c >= 90:  return "#2E7D32"
        if c >= 70:  return "#A5D6A7"
        if c >= 50:  return "#FFD54F"
        if c >= 30:  return "#FFB74D"
        return "#E57373"

    chart_df = df.copy()
    chart_df["color"] = chart_df["coverage"].apply(_coverage_color)
    chart_df = chart_df.sort_values("coverage", ascending=True)

    fig = go.Figure()
    for _, row in chart_df.iterrows():
        fig.add_trace(go.Bar(
            x=[row["coverage"]],
            y=[row["discipline"]],
            orientation="h",
            marker=dict(color=row["color"]),
            text=[f"{row['coverage']:.0f}%"],
            textposition="outside",
            hovertemplate=(
                f"<b>{row['discipline']}</b><br>"
                f"Приходит: {row['issue_month']}/мес<br>"
                f"Отвечаем: {row['fix_month']}/мес<br>"
                f"Покрытие: {row['coverage']:.1f}%<br>"
                f"Активных: {row['active']:,}"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    fig.add_vline(
        x=100, line_dash="dash", line_color="#2E7D32", line_width=2,
        annotation_text="100% — успеваем",
        annotation_position="top right",
        annotation_font_color="#2E7D32", annotation_font_size=11,
    )

    fig.update_layout(
        height=max(400, 40 * len(chart_df)),
        xaxis_title="Покрытие темпа, % (отвечаем / приходит)",
        yaxis_title="",
        bargap=0.3,
        xaxis=dict(range=[0, 130]),
        margin=dict(l=120, r=60, t=30, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_покрытие_по_дисциплинам",
                    "fc_disc_coverage", width=1400, height=600)

    st.caption(
        "**Как читать:** полоса **до 100%** — дисциплина успевает "
        "закрывать новые замечания. **Меньше 100%** — задолженность растёт."
    )

    # =====================================================================
    #  ГРУППЫ
    # =====================================================================
    st.divider()
    st.markdown("#### 🎯 Группировка по состоянию")

    good = df[df["coverage"] >= 80]
    ok = df[(df["coverage"] >= 50) & (df["coverage"] < 80)]
    bad = df[df["coverage"] < 50]

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"##### 🟢 Успевают ({len(good)})")
        if good.empty:
            st.caption("Нет таких дисциплин.")
        else:
            for _, r in good.iterrows():
                st.caption(f"**{r['discipline']}** — {r['coverage']:.0f}%")
    with c2:
        st.markdown(f"##### 🟡 Частично ({len(ok)})")
        if ok.empty:
            st.caption("Нет таких дисциплин.")
        else:
            for _, r in ok.iterrows():
                st.caption(f"**{r['discipline']}** — {r['coverage']:.0f}%")
    with c3:
        st.markdown(f"##### 🔴 Отстают ({len(bad)})")
        if bad.empty:
            st.caption("Нет таких дисциплин.")
        else:
            for _, r in bad.iterrows():
                st.caption(f"**{r['discipline']}** — {r['coverage']:.0f}%")

    # =====================================================================
    #  ТАБЛИЦА
    # =====================================================================
    st.divider()
    st.markdown("#### 📋 Детали")

    table = df[[
        "discipline", "total", "closed", "pct", "active",
        "issue_month", "fix_month", "delta_month", "coverage",
    ]].copy()

    def _status(c):
        if c >= 90: return "🟢"
        if c >= 70: return "🟡"
        if c >= 50: return "🟠"
        return "🔴"

    table.insert(0, "🚦", table["coverage"].apply(_status))

    table = table.rename(columns={
        "discipline": "Дисциплина",
        "total": "Всего",
        "closed": "Закрыто",
        "pct": "% вып.",
        "active": "Активных",
        "issue_month": "Приходит/мес",
        "fix_month": "Отвечаем/мес",
        "delta_month": "Баланс/мес",
        "coverage": "Покрытие %",
    })

    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "% вып.": st.column_config.ProgressColumn(
                "% вып.", min_value=0, max_value=100, format="%.1f%%"),
            "Покрытие %": st.column_config.ProgressColumn(
                "Покрытие %", min_value=0, max_value=130, format="%.1f%%"),
            "Баланс/мес": st.column_config.NumberColumn(
                "Баланс/мес", format="%+d"),
        },
    )

    # =====================================================================
    #  ВЫВОД
    # =====================================================================
    st.divider()
    st.markdown("#### 🎯 Ключевые выводы")

    total_issue = df["issue_month"].sum()
    total_fix = df["fix_month"].sum()
    overall_coverage = round(total_fix / total_issue * 100, 1) if total_issue else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Приходит/мес", f"{int(total_issue):,}".replace(",", " "))
    c2.metric("Отвечаем/мес", f"{int(total_fix):,}".replace(",", " "))
    c3.metric("Общее покрытие", f"{overall_coverage}%",
              delta="отстаём" if overall_coverage < 100 else "успеваем",
              delta_color="inverse" if overall_coverage < 100 else "normal")

    if overall_coverage >= 90:
        st.success(
            "✅ Команда почти успевает. Небольшая оптимизация — и выйдем в ноль."
        )
    elif overall_coverage >= 60:
        st.warning(
            f"🟡 **Отставание на {100 - overall_coverage:.0f}%**. "
            f"Нужен 1–2 дополнительных специалиста или оптимизация процесса."
        )
    else:
        st.error(
            f"🔴 **Критическое отставание.** Покрытие всего "
            f"**{overall_coverage:.0f}%** — команда отвечает только на "
            f"{overall_coverage:.0f}% новых замечаний. Задолженность растёт. "
            f"Требуется:\n\n"
            f"1. Расширение команды в **{round(100 / overall_coverage, 1)}×**\n"
            f"2. Пересмотр процесса приёмки с заказчиком\n"
            f"3. Эскалация к руководству"
        )

def _status_icon(pct: float, delta: int, months) -> str:
    """Светофор для дисциплины."""
    if months is None:
        return "🔴"  # никогда не закроется
    if delta > 0:
        return "🟠"  # задолженность растёт
    if pct < 50:
        return "🟡"  # низкий %, но разгребаем
    return "🟢"       # всё в порядке


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("🔮 Прогноз")
    st.caption(
        "Экстраполяция текущих темпов выдачи и ответов на 6 месяцев вперёд. "
        "Прогноз — линейный, без учёта сезонности и изменений команды."
    )

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🔄 Обновить", key="fc_refresh",
                     use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Загрузка данных..."):
        data = _load_data()

    if data["monthly"].empty:
        st.warning("Нет данных для прогноза.")
        return

    # KPI — текущие темпы
    kpi = _render_kpi(data)

    if not kpi:
        return

    st.divider()

    # Прогноз
    forecast = _build_forecast(data, kpi)
    _render_forecast_kpi(forecast, kpi)

    st.divider()

    # График
    _render_forecast_chart(forecast, kpi)

    st.divider()

    # Когда разгребём
    _render_cleanup_eta(kpi)

    st.divider()

    # По дисциплинам
    _render_by_discipline(data)

    st.divider()

    # Зона риска
    _render_risk_complexes()

    # Дисклеймер
    st.divider()
    st.info(
        "💡 **Про прогноз:** это линейная экстраполяция на основе средних "
        "темпов за последние 3 месяца. Прогноз **не учитывает:** изменения "
        "в команде, сезонность (отпуска, праздники), изменение объёма работ "
        "от заказчика. Реальность может отличаться на ±20%."
    )