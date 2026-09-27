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
    """Собирает всё, что нужно для прогноза."""
    result = {}

    with get_conn() as conn:
        # --- Общее состояние ---
        result["totals"] = dict(conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('Закрыто','Выполнено')
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN status IN ('Новое','Принято в работу',
                                          'Не принято','К обсуждению')
                         THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM comments
        """).fetchone())

        # --- Темпы выдачи по месяцам ---
        issue = pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # --- Темпы ответов по месяцам ---
        fix = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто','Выполнено')
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # --- Темпы закрытия по месяцам (Закрыто) ---
        closed = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS n
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status = 'Закрыто'
            GROUP BY ym
            ORDER BY ym
        """, conn)

        # --- По дисциплинам (для зоны риска) ---
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

    # Объединяем выдачи/ответы/закрытия в один DataFrame
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
    """Линейная экстраполяция на 6 месяцев вперёд."""
    monthly = data["monthly"]
    if monthly.empty or not kpi:
        return pd.DataFrame()

    # Последний месяц в данных
    last_ym = monthly["ym"].iloc[-1]
    last_date = datetime.strptime(last_ym + "-01", "%Y-%m-%d").date()

    # Генерируем 6 месяцев вперёд
    months_forward = []
    for i in range(1, 7):
        d = last_date + timedelta(days=30 * i)
        months_forward.append(d.strftime("%Y-%m"))

    avg_issue = kpi["avg_issue"]
    avg_fix = kpi["avg_fix"]
    current_active = kpi["active"]

    rows = []
    # Начинаем с текущего месяца (реальные данные)
    rows.append({
        "ym": last_ym,
        "Сценарий": "История",
        "Выдано": int(monthly["Выдано"].iloc[-1]),
        "Отвечено": int(monthly["Отвечено"].iloc[-1]),
        "Активных": int(current_active),
        "Накоплено": 0,
    })

    cumulative_backlog = 0
    active_now = current_active

    # Базовый сценарий
    for i, ym in enumerate(months_forward, start=1):
        cumulative_backlog += (avg_issue - avg_fix)
        active_now += (avg_issue - avg_fix)
        rows.append({
            "ym": ym,
            "Сценарий": "Базовый",
            "Выдано": int(avg_issue),
            "Отвечено": int(avg_fix),
            "Активных": int(max(active_now, 0)),
            "Накоплено": int(cumulative_backlog),
        })

    # Оптимистичный (+20% к темпам ответов)
    cumulative_opt = 0
    active_opt = current_active
    for ym in months_forward:
        cumulative_opt += (avg_issue - avg_fix * 1.2)
        active_opt += (avg_issue - avg_fix * 1.2)
        rows.append({
            "ym": ym,
            "Сценарий": "Оптимистичный",
            "Выдано": int(avg_issue),
            "Отвечено": int(avg_fix * 1.2),
            "Активных": int(max(active_opt, 0)),
            "Накоплено": int(cumulative_opt),
        })

    # Пессимистичный (−20%)
    cumulative_pess = 0
    active_pess = current_active
    for ym in months_forward:
        cumulative_pess += (avg_issue - avg_fix * 0.8)
        active_pess += (avg_issue - avg_fix * 0.8)
        rows.append({
            "ym": ym,
            "Сценарий": "Пессимистичный",
            "Выдано": int(avg_issue),
            "Отвечено": int(avg_fix * 0.8),
            "Активных": int(max(active_pess, 0)),
            "Накоплено": int(cumulative_pess),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
#  KPI прогноза
# ---------------------------------------------------------------------------
def _render_forecast_kpi(forecast: pd.DataFrame, kpi: dict):
    if forecast.empty:
        return

    st.markdown("##### 🔮 Прогноз")

    # Найти базовый сценарий через 6 месяцев
    base_6m = forecast[(forecast["Сценарий"] == "Базовый")].iloc[-1]
    opt_6m = forecast[(forecast["Сценарий"] == "Оптимистичный")].iloc[-1]
    pess_6m = forecast[(forecast["Сценарий"] == "Пессимистичный")].iloc[-1]

    active_now = kpi["active"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Активных сейчас",
              f"{int(active_now):,}".replace(",", " "))
    c2.metric("Базовый через 6 мес",
              f"{int(base_6m['Активных']):,}".replace(",", " "),
              delta=f"+{int(base_6m['Активных'] - active_now):,}"
                    .replace(",", " ") if base_6m['Активных'] > active_now else "стабильно",
              delta_color="inverse")
    c3.metric("Оптимистичный",
              f"{int(opt_6m['Активных']):,}".replace(",", " "),
              delta=f"+{int(opt_6m['Активных'] - active_now):,}"
                    .replace(",", " ") if opt_6m['Активных'] > active_now else "стабильно",
              delta_color="inverse")
    c4.metric("Пессимистичный",
              f"{int(pess_6m['Активных']):,}".replace(",", " "),
              delta=f"+{int(pess_6m['Активных'] - active_now):,}"
                    .replace(",", " ") if pess_6m['Активных'] > active_now else "стабильно",
              delta_color="inverse")


# ---------------------------------------------------------------------------
#  График прогноза
# ---------------------------------------------------------------------------
def _render_forecast_chart(forecast: pd.DataFrame, kpi: dict):
    st.markdown("### 📈 Прогноз накопления активных замечаний (6 месяцев)")

    if forecast.empty:
        st.info("Нет данных.")
        return

    # Фильтруем только нужные сценарии (без истории)
    fc = forecast[forecast["Сценарий"] != "История"]

    fig = go.Figure()

    color_map = {
        "Базовый": "#FF9800",
        "Оптимистичный": "#4CAF50",
        "Пессимистичный": "#E57373",
    }

    for scenario in ["Оптимистичный", "Базовый", "Пессимистичный"]:
        sub = fc[fc["Сценарий"] == scenario]
        fig.add_trace(go.Scatter(
            x=sub["ym"], y=sub["Активных"],
            mode="lines+markers",
            name=scenario,
            line=dict(color=color_map[scenario], width=3),
            marker=dict(size=8),
        ))

    # Точка «сейчас»
    fig.add_trace(go.Scatter(
        x=[forecast.iloc[0]["ym"]],
        y=[kpi["active"]],
        mode="markers",
        name="Сейчас",
        marker=dict(color="#1F4E78", size=14, symbol="diamond"),
    ))

    fig.update_layout(
        height=450,
        xaxis_title="Месяц",
        yaxis_title="Активных замечаний",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_активных", "forecast_active",
                    width=1400, height=600)

    # Вывод
    base_6m = fc[fc["Сценарий"] == "Базовый"].iloc[-1]
    growth = base_6m["Активных"] - kpi["active"]

    if growth > kpi["active"] * 0.5:
        st.error(
            f"🔴 **Базовый сценарий: рост на {int(growth):,} замечаний "
            f"за 6 месяцев.** Если темп не изменится, задолженность вырастет "
            f"с {int(kpi['active']):,} до {int(base_6m['Активных']):,}."
            .replace(",", " ")
        )
    elif growth > 0:
        st.warning(
            f"🟡 **Задолженность медленно растёт:** +{int(growth):,} "
            f"за 6 месяцев. Рекомендуется усилить команду."
            .replace(",", " ")
        )
    else:
        st.success(
            f"✅ **Тренд положительный:** задолженность не растёт. "
            f"Даже в пессимистичном сценарии команда справляется."
        )


# ---------------------------------------------------------------------------
#  «Когда разгребём задолженность»
# ---------------------------------------------------------------------------
def _render_cleanup_eta(kpi: dict):
    st.markdown("### ⏳ Когда разгребём текущую задолженность?")

    active = kpi["active"]
    avg_delta = kpi["avg_delta"]

    if active == 0:
        st.success("🎉 Задолженности нет!")
        return

    c1, c2 = st.columns(2)

    # Если дельта положительная — разгрести не получится
    if avg_delta >= 0:
        with c1:
            st.error(
                f"🔴 **При текущем темпе задолженность НЕ уменьшится.** "
                f"Ежемесячный прирост: **+{int(avg_delta)}** замечаний. "
                f"Выдаём больше, чем отвечаем."
            )
        with c2:
            # Сколько нужно ускориться
            current_fix = kpi["avg_fix"]
            target_fix = kpi["avg_issue"] + 200
            needed = round((target_fix / current_fix - 1) * 100)

            st.metric(
                "Нужно ускориться на",
                f"{needed}%",
                help=f"Сейчас отвечаем {int(current_fix)}/мес, "
                     f"нужно {int(target_fix)}/мес, чтобы разгребать",
            )
    else:
        # Разгребаем
        months = int(active / abs(avg_delta))
        eta_date = date.today() + timedelta(days=30 * months)

        with c1:
            st.success(
                f"✅ **Задолженность уменьшается на "
                f"{int(abs(avg_delta))}/мес.** "
                f"При текущем темпе разгребём через **{months} мес.**"
                .replace(",", " ")
            )
        with c2:
            st.metric("Прогноз «нуля»",
                      f"{eta_date.strftime('%m.%Y')}",
                      help=f"Месяцев до полного закрытия: {months}")

    st.caption(
        "**Допущения:** темп не меняется, заказчик не присылает новые "
        "замечания сверх текущего. На практике прогноз может сдвигаться."
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
    st.markdown("### 🏷 Прогноз по дисциплинам")

    df = data["by_disc"]
    if df.empty:
        st.info("Нет данных.")
        return

    df = df.copy()
    df["pct"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1) if r["total"] else 0,
        axis=1,
    )
    df["name"] = df["discipline"].apply(discipline_name)
    df = df.sort_values("active", ascending=False)

    fig = px.bar(
        df, x="discipline", y=["closed", "active"],
        barmode="stack",
        labels={"discipline": "Дисциплина", "value": "Замечаний",
                "variable": "Статус"},
        color_discrete_map={"closed": "#2E7D32", "active": "#E57373"},
        title="Закрыто vs Активных по дисциплинам",
    )
    fig.update_layout(
        height=450, xaxis_tickangle=-45,
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Прогноз_по_дисциплинам", "forecast_disc",
                    width=1400, height=600)


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