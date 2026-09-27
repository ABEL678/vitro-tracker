# vitro/ui/deadlines.py
"""
⏰ Сроки — контроль соблюдения 10 рабочих дней.

Три под-вкладки:
  🔴 Ждут нашего ответа — мы просрочили ответ заказчику.
  🔵 Ждут заказчика — мы ответили, заказчик не рассмотрел.
  🟡 Заброшено — >90 дней без движения, архив.

KPI сверху: 4 категории + общая динамика.
"""

import io
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.workdays import add_workdays, parse_date, workdays_between
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
SLA_DAYS = 10           # рабочих дней
FRESH_DAYS = 90         # календарных дней — «свежая» просрочка
ABANDONED_DAYS = 90     # календарных дней без движения — «заброшено»


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
#  Основной расчёт — категоризация замечаний
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_all_categorized() -> pd.DataFrame:
    """
    Возвращает все замечания с рассчитанными полями:
      - created_d, fix_date_d, cat_date_d — как date
      - due_date_d — срок (+10 р.д. от created)
      - customer_due_d — срок рассмотрения заказчиком (+10 р.д. от fix_date)
      - days_overdue_work — рабочих дней просрочки
      - days_waiting_customer — рабочих дней ожидания
      - days_since_movement — календарных дней с последнего движения
      - category — категория проблемы
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.id, c.doc_id, c.comment, c.status, c.author, c.created,
                c.fix_date, c.category_date, c.category,
                d.discipline, d.section, d.complex,
                REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS sheet,
                d.name AS sheet_name
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN (
                'Новое','Принято в работу','Не принято','К обсуждению','Выполнено'
            )
              AND c.created IS NOT NULL AND c.created <> ''
        """).fetchall()

    today = date.today()
    result = []

    for r in rows:
        created = parse_date(r["created"])
        if not created:
            continue

        fix_d = parse_date(r["fix_date"]) if r["fix_date"] else None
        cat_d = parse_date(r["category_date"]) if r["category_date"] else None
        status = r["status"]

        # Срок ответа с нашей стороны: created + 10 р.д.
        due = add_workdays(created, SLA_DAYS)

        # Срок рассмотрения заказчиком: fix_date + 10 р.д.
        cust_due = add_workdays(fix_d, SLA_DAYS) if fix_d else None

        # Движение
        movements = [d for d in [created, fix_d, cat_d] if d]
        last_movement = max(movements) if movements else created
        days_since_movement = (today - last_movement).days

        # Рабочих дней прошло
        wd_since_created = workdays_between(created, today)
        wd_overdue_work = workdays_between(due, today) if today > due else 0
        wd_waiting_customer = 0
        if cust_due and today > cust_due:
            wd_waiting_customer = workdays_between(cust_due, today)

        # Классификация
        category = None
        if fix_d and status == "Выполнено" and today > cust_due:
            category = "waiting_customer"
        elif not fix_d and status != "Выполнено" and today > due:
            if days_since_movement > ABANDONED_DAYS:
                category = "abandoned"
            else:
                category = "overdue_ours"
        elif not fix_d and status != "Выполнено":
            category = "in_progress"
        elif fix_d and status == "Выполнено":
            category = "waiting_customer_ontime"  # ещё не просрочен заказчик
        else:
            category = "other"

        result.append({
            "id": r["id"],
            "comment": r["comment"],
            "status": status,
            "author": r["author"],
            "discipline": r["discipline"],
            "section": r["section"],
            "complex": r["complex"],
            "sheet": r["sheet"],
            "sheet_name": r["sheet_name"],
            "created": r["created"],
            "created_d": created,
            "fix_date": r["fix_date"],
            "fix_date_d": fix_d,
            "category_date_d": cat_d,
            "due_date_d": due,
            "customer_due_d": cust_due,
            "days_overdue_work": wd_overdue_work,
            "days_waiting_customer": wd_waiting_customer,
            "wd_since_created": wd_since_created,
            "days_since_movement": days_since_movement,
            "category_flag": category,
        })

    return pd.DataFrame(result)


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    if df.empty:
        st.info("Нет данных для расчёта сроков.")
        return

    overdue_ours = df[df["category_flag"] == "overdue_ours"]
    waiting = df[df["category_flag"] == "waiting_customer"]
    abandoned = df[df["category_flag"] == "abandoned"]
    in_progress = df[df["category_flag"] == "in_progress"]

    st.markdown("##### 📊 Классификация по срокам")

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(
        "🔴 Ждут нашего ответа",
        f"{len(overdue_ours):,}".replace(",", " "),
        delta=f"свежих: {len(overdue_ours):,}".replace(",", " "),
        delta_color="off",
        help=f"Просрочили ответ. Из них без движения >90д: {len(abandoned)}",
    )
    c2.metric(
        "🔵 Ждут заказчика",
        f"{len(waiting):,}".replace(",", " "),
        help="Мы ответили, заказчик не рассмотрел >10 р.д.",
    )
    c3.metric(
        "🟡 Заброшено",
        f"{len(abandoned):,}".replace(",", " "),
        help=">90 дней без движения",
    )
    c4.metric(
        "🟢 В работе, в срок",
        f"{len(in_progress):,}".replace(",", " "),
        help="Активные замечания в пределах 10 р.д.",
    )
    total = len(df)
    c5.metric(
        "Всего проверено",
        f"{total:,}".replace(",", " "),
    )


# ---------------------------------------------------------------------------
#  График «Динамика по месяцам»
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_monthly_dynamics() -> pd.DataFrame:
    """
    Динамика по месяцам: сколько замечаний ждут нашего ответа.
    Разделено на свежие (<90 дней) и заброшенные (>90 дней).
    """
    df = _load_all_categorized()
    if df.empty:
        return pd.DataFrame()

    # Оба типа: и свежие просрочки, и заброшенные
    sub = df[df["category_flag"].isin(["overdue_ours", "abandoned"])].copy()
    if sub.empty:
        return pd.DataFrame()

    sub["ym"] = sub["created_d"].apply(lambda d: d.strftime("%Y-%m"))
    sub["Тип"] = sub["category_flag"].map({
        "overdue_ours": "Свежие",
        "abandoned": "Заброшенные",
    })

    # Группировка по месяцу и типу
    monthly = sub.groupby(["ym", "Тип"]).size().reset_index(name="Количество")
    return monthly


@st.cache_data(ttl=300, show_spinner=False)
def _load_waiting_dynamics() -> pd.DataFrame:
    """
    Динамика по месяцам: сколько замечаний ждут рассмотрения заказчиком.
    Группировка по месяцу нашего ответа (fix_date).
    """
    df = _load_all_categorized()
    if df.empty:
        return pd.DataFrame()

    waiting = df[df["category_flag"] == "waiting_customer"].copy()
    if waiting.empty:
        return pd.DataFrame()

    waiting = waiting[waiting["fix_date_d"].notna()].copy()
    if waiting.empty:
        return pd.DataFrame()

    waiting["ym"] = waiting["fix_date_d"].apply(lambda d: d.strftime("%Y-%m"))
    monthly = waiting.groupby("ym").size().reset_index(name="Количество")
    return monthly


def _render_dynamics_chart():
    """Два графика рядом: наша вина vs вина заказчика. Общий диапазон X."""
    monthly_ours = _load_monthly_dynamics()
    monthly_wait = _load_waiting_dynamics()

    if monthly_ours.empty and monthly_wait.empty:
        return

    st.markdown("### 📈 Динамика по месяцам")

    # --- Собираем все месяцы ---
    all_months = set()
    if not monthly_ours.empty:
        all_months.update(monthly_ours["ym"].tolist())
    if not monthly_wait.empty:
        all_months.update(monthly_wait["ym"].tolist())

    months_sorted = sorted(all_months)
    # Для левого графика — все месяцы, для правого — все
    common_months = months_sorted

    # --- Левый: стек свежие + заброшенные ---
    col1, col2 = st.columns(2)

    with col1:
        if not monthly_ours.empty:
            # Приводим к общему списку месяцев
            pivot = (monthly_ours
                     .pivot_table(index="ym", columns="Тип",
                                  values="Количество", fill_value=0)
                     .reindex(common_months, fill_value=0)
                     .reset_index())
            # Убеждаемся, что обе колонки есть
            for col in ["Свежие", "Заброшенные"]:
                if col not in pivot.columns:
                    pivot[col] = 0

            fig = px.bar(
                pivot, x="ym", y=["Свежие", "Заброшенные"],
                barmode="stack",
                labels={"ym": "Месяц выдачи", "value": "Замечаний",
                        "variable": "Тип"},
                color_discrete_map={
                    "Свежие": "#E57373",
                    "Заброшенные": "#7F0000",
                },
                category_orders={
                    "variable": ["Свежие", "Заброшенные"],
                },
            )
            fig.update_layout(
                title="🔴 Ждут нашего ответа",
                height=420, xaxis_tickangle=-45,
                legend=dict(orientation="h", yanchor="bottom",
                            y=1.02, xanchor="right", x=1),
                bargap=0.4,
                xaxis=dict(type="category", categoryorder="array",
                           categoryarray=common_months),
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Сроки_динамика_наши", "dl_dyn_ours",
                            width=1200, height=600)
        else:
            st.info("Нет замечаний, ждущих нашего ответа.")

    # --- Правый: ждут заказчика ---
    with col2:
        if not monthly_wait.empty:
            wait_filled = (monthly_wait
                           .set_index("ym")
                           .reindex(common_months, fill_value=0)
                           .reset_index())
            fig = px.bar(
                wait_filled, x="ym", y="Количество",
                text="Количество",
                labels={"ym": "Месяц нашего ответа",
                        "Количество": "Замечаний"},
                color_discrete_sequence=["#64B5F6"],
            )
            fig.update_traces(textposition="outside", width=0.6)
            fig.update_layout(
                title="🔵 Ждут заказчика",
                height=420, xaxis_tickangle=-45, showlegend=False,
                bargap=0.4,
                xaxis=dict(type="category", categoryorder="array",
                           categoryarray=common_months),
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Сроки_динамика_заказчик", "dl_dyn_cust",
                            width=1200, height=600)
        else:
            st.info("Нет замечаний, ждущих заказчика.")

    st.caption(
        "**Левый график** — стек: 🟥 свежие (<90 дней) + 🟫 заброшенные "
        "(>90 дней без движения). **Правый** — ждут рассмотрения заказчиком."
    )

    # --- Интерпретация ---
    st.markdown("#### 🧭 Что это значит")

    # Считаем итоги
    total_ours = int(monthly_ours["Количество"].sum()) if not monthly_ours.empty else 0
    total_wait = int(monthly_wait["Количество"].sum()) if not monthly_wait.empty else 0

    # Отдельно свежие и заброшенные
    if not monthly_ours.empty:
        fresh = int(monthly_ours[monthly_ours["Тип"] == "Свежие"]["Количество"].sum())
        aband = int(monthly_ours[monthly_ours["Тип"] == "Заброшенные"]["Количество"].sum())
    else:
        fresh, aband = 0, 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ждут нас — свежие", f"{fresh:,}".replace(",", " "))
    c2.metric("Ждут нас — заброшено", f"{aband:,}".replace(",", " "))
    c3.metric("Ждут заказчика", f"{total_wait:,}".replace(",", " "))
    ratio = round(total_ours / total_wait, 2) if total_wait else 0
    c4.metric(
        "Соотношение (наша / его)",
        f"{ratio}×",
        help="Если >1 — мы работаем хуже. Если <1 — заказчик тормозит больше.",
    )

    # Текстовый вывод
    if total_ours > total_wait * 1.2:
        st.error(
            f"🔴 **Узкое место — мы.** Ждущих нашего ответа "
            f"в **{ratio}×** больше, чем ждущих заказчика. "
            f"Нужно усилить отработку замечаний."
        )
    elif total_wait > total_ours * 1.2:
        inv = round(total_wait / total_ours, 2) if total_ours else 0
        st.info(
            f"🔵 **Узкое место — заказчик.** Ждущих его рассмотрения в "
            f"**{inv}×** больше, чем ждущих нас. Готовим письмо-предъявление."
        )
    else:
        st.success(
            "⚖️ **Баланс.** Мы и заказчик работаем примерно в одном темпе."
        )


# ---------------------------------------------------------------------------
#  Под-вкладка 1: Ждут нашего ответа
# ---------------------------------------------------------------------------
def _render_overdue_ours(df: pd.DataFrame):
    st.markdown("### 🔴 Ждут нашего ответа")
    st.caption(
        "Активные замечания, где мы не ответили в течение 10 рабочих дней. "
        "Не включая архивные (>90 дней без движения)."
    )

    sub = df[df["category_flag"] == "overdue_ours"].copy()
    if sub.empty:
        st.success("🎉 Нет замечаний, ждущих нашего ответа.")
        return

    # Слайдер по возрасту просрочки
    c1, c2 = st.columns([2, 2])
    with c1:
        min_overdue = st.slider(
            "Минимум рабочих дней просрочки",
            min_value=0, max_value=90, value=0, step=5,
            key="dl_ours_min_overdue",
        )
    with c2:
        max_overdue = st.number_input(
            "Максимум (рабочих дней просрочки)",
            min_value=1, max_value=500, value=90, step=10,
            key="dl_ours_max_overdue",
        )

    sub = sub[(sub["days_overdue_work"] >= min_overdue) &
              (sub["days_overdue_work"] <= max_overdue)]

    if sub.empty:
        st.info("Нет замечаний в заданном диапазоне.")
        return

    # Фильтры по дисциплине/комплекту
    disc_options = _load_discipline_options()
    c3, c4 = st.columns(2)
    with c3:
        sel_disc_labels = st.multiselect(
            "Дисциплина",
            options=list(disc_options.values()),
            placeholder="Все дисциплины",
            key="dl_ours_disc",
        )
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]
        if sel_disc:
            sub = sub[sub["discipline"].isin(sel_disc)]

    with c4:
        complexes = sorted(sub["complex"].dropna().unique())
        sel_kit = st.multiselect(
            "Комплект", options=complexes,
            placeholder="Все комплекты", key="dl_ours_kit")
        if sel_kit:
            sub = sub[sub["complex"].isin(sel_kit)]

    if sub.empty:
        st.info("Нет данных с заданными фильтрами.")
        return

    # Топ-15 комплектов
    st.markdown("##### 🔥 Топ-15 комплектов по количеству просрочек")
    top = (sub.groupby("complex").size()
              .reset_index(name="Просрочек")
              .sort_values("Просрочек", ascending=True)
              .tail(15))

    fig = px.bar(
        top, x="Просрочек", y="complex", orientation="h",
        text="Просрочек",
        labels={"complex": ""},
        color_discrete_sequence=["#E57373"],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(350, 25 * len(top)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_топ_комплектов", "dl_top_cx")

    # Таблица
    st.markdown(f"##### 📋 Список ({len(sub):,} замечаний)".replace(",", " "))

    table = sub[[
        "id", "discipline", "complex", "sheet",
        "comment", "author", "created", "days_overdue_work",
    ]].copy().rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "author": "Автор",
        "created": "Создано",
        "days_overdue_work": "Просрочено (р.д.)",
    })
    table = table.sort_values("Просрочено (р.д.)", ascending=False)

    st.dataframe(
        table.head(500),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Просрочено (р.д.)": st.column_config.NumberColumn(
                "Просрочено (р.д.)", format="%d"),
        },
    )
    if len(table) > 500:
        st.caption(f"Показаны первые 500 из {len(table):,}.".replace(",", " "))

    # Экспорт
    with st.expander("📥 Выгрузить в Excel", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Сроки")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Сроки_ждут_ответа_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Под-вкладка 2: Ждут заказчика
# ---------------------------------------------------------------------------
def _render_waiting_customer(df: pd.DataFrame):
    st.markdown("### 🔵 Ждут рассмотрения заказчиком")
    st.caption(
        "Мы ответили, заказчик не рассмотрел замечание в течение 10 рабочих дней. "
        "Это **не наша вина** — готовим письмо-предъявление."
    )

    sub = df[df["category_flag"] == "waiting_customer"].copy()
    if sub.empty:
        st.success("🎉 Нет замечаний, ждущих заказчика.")
        return

    c1, c2 = st.columns([2, 2])
    with c1:
        min_wait = st.slider(
            "Минимум рабочих дней ожидания",
            min_value=0, max_value=180, value=10, step=5,
            key="dl_wait_min",
        )
    with c2:
        max_wait = st.number_input(
            "Максимум (рабочих дней ожидания)",
            min_value=1, max_value=1000, value=180, step=10,
            key="dl_wait_max",
        )

    sub = sub[(sub["days_waiting_customer"] >= min_wait) &
              (sub["days_waiting_customer"] <= max_wait)]

    if sub.empty:
        st.info("Нет данных в заданном диапазоне.")
        return

    st.metric("Всего ждём рассмотрения", f"{len(sub):,}".replace(",", " "))

    # Топ авторов заказчика
    st.markdown("##### 👤 Топ-10 авторов, чьи замечания ждут")
    top_authors = (sub.groupby("author").size()
                      .reset_index(name="Ожидают")
                      .sort_values("Ожидают", ascending=True)
                      .tail(10))

    fig = px.bar(
        top_authors, x="Ожидают", y="author", orientation="h",
        text="Ожидают",
        labels={"author": ""},
        color_discrete_sequence=["#64B5F6"],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(300, 30 * len(top_authors)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_ждут_авторы", "dl_wait_authors")

    # Таблица
    st.markdown(f"##### 📋 Список ({len(sub):,})".replace(",", " "))
    table = sub[[
        "id", "discipline", "complex", "sheet",
        "comment", "author", "fix_date", "days_waiting_customer",
    ]].copy().rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "author": "Автор замечания",
        "fix_date": "Наш ответ",
        "days_waiting_customer": "Ждём (р.д.)",
    })
    table = table.sort_values("Ждём (р.д.)", ascending=False)

    st.dataframe(
        table.head(500),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Ждём (р.д.)": st.column_config.NumberColumn(format="%d"),
        },
    )

    # Экспорт для письма
    with st.expander("📥 Выгрузить для письма заказчику", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Ждут заказчика")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Письмо_заказчику_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Под-вкладка 3: Заброшено
# ---------------------------------------------------------------------------
def _render_abandoned(df: pd.DataFrame):
    st.markdown("### 🟡 Заброшено (архив)")
    st.caption(
        "Замечания без движения >90 дней. Кандидаты на снятие или "
        "пересогласование с заказчиком."
    )

    sub = df[df["category_flag"] == "abandoned"].copy()
    if sub.empty:
        st.success("🎉 Нет заброшенных замечаний.")
        return

    c1, c2 = st.columns(2)
    with c1:
        min_days = st.slider(
            "Минимум дней без движения",
            min_value=90, max_value=730, value=90, step=10,
            key="dl_ab_min",
        )
    with c2:
        year_filter = st.multiselect(
            "Год создания",
            options=sorted(sub["created_d"].apply(lambda d: d.year).unique()),
            placeholder="Все годы",
            key="dl_ab_year",
        )

    sub = sub[sub["days_since_movement"] >= min_days]
    if year_filter:
        sub = sub[sub["created_d"].apply(lambda d: d.year).isin(year_filter)]

    if sub.empty:
        st.info("Нет данных.")
        return

    st.metric("Всего заброшено", f"{len(sub):,}".replace(",", " "))

    # Разбивка по годам
    st.markdown("##### 📅 Разбивка по годам создания")
    sub_year = sub.copy()
    sub_year["Год"] = sub_year["created_d"].apply(lambda d: d.year)
    by_year = sub_year.groupby("Год").size().reset_index(name="Количество")

    fig = px.bar(
        by_year, x="Год", y="Количество",
        text="Количество",
        color_discrete_sequence=["#FFB74D"],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=350, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_заброшено_годы", "dl_ab_years")

    # Таблица
    st.markdown(f"##### 📋 Список ({len(sub):,})".replace(",", " "))
    table = sub[[
        "id", "discipline", "complex", "sheet",
        "comment", "author", "created", "days_since_movement",
    ]].copy().rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "author": "Автор",
        "created": "Создано",
        "days_since_movement": "Без движения (дн.)",
    })
    table = table.sort_values("Без движения (дн.)", ascending=False)

    st.dataframe(
        table.head(500),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Без движения (дн.)": st.column_config.NumberColumn(format="%d"),
        },
    )

    with st.expander("📥 Выгрузить в Excel", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Заброшено")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Заброшено_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("⏰ Сроки")
    st.caption(
        "Контроль соблюдения 10 рабочих дней: ответ на замечания, "
        "рассмотрение ответов заказчиком, архивные замечания."
    )

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🔄 Обновить", key="dl_refresh",
                     use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Загрузка и категоризация..."):
        df = _load_all_categorized()

    if df.empty:
        st.warning("Нет данных. Запустите синхронизацию.")
        return

    # KPI
    _render_kpi(df)

    st.divider()

    # Динамика
    _render_dynamics_chart()

    st.divider()

    # Под-вкладки
    tab1, tab2, tab3 = st.tabs([
        "🔴 Ждут нашего ответа",
        "🔵 Ждут заказчика",
        "🟡 Заброшено",
    ])

    with tab1:
        _render_overdue_ours(df)
    with tab2:
        _render_waiting_customer(df)
    with tab3:
        _render_abandoned(df)