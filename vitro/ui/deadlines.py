# vitro/ui/deadlines.py
"""
⏰ Сроки — контроль соблюдения 10 рабочих дней.

Классификация (8 категорий):

НАША СТОРОНА (надо ответить мы):
  🆕 Новое — заказчик выдал, мы не брались
  🛠 Принято в работу — взяли, но не ответили
  🟪 Не принято — заказчик отклонил наш ответ, дорабатываем
  🟣 К обсуждению — спорное, обсуждаем

СТОРОНА ЗАКАЗЧИКА (мы ответили, он не рассмотрел):
  🔵 Ждут заказчика — < 90 р.д., лист не A/B
  🔴 Хронические — > 90 р.д., лист не A/B

ЗАКРЫТЫЕ / УЧТЁННЫЕ:
  🟢 Учтено (A/B) — лист утверждён, надо дожать на «Закрыто»

АРХИВ:
  🟡 Заброшено — > 90 календарных дней без движения

SLA = 10 рабочих дней (с учётом производственного календаря РФ).
"""

import io
from datetime import date, datetime

import pandas as pd
import plotly.express as px
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
CHRONIC_WORKDAYS = 90   # рабочих дней — хроническое ожидание заказчика


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
#  Главный расчёт — категоризация всех замечаний
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_all_categorized() -> pd.DataFrame:
    """
    Возвращает все активные замечания с рассчитанными полями
    и категорией (category_flag).

    Категории:
      НАША сторона:
        new_overdue / new_in_progress
        in_work_overdue / in_work_in_progress
        rejected_overdue / rejected_in_progress
        discussion_overdue / discussion_in_progress

      ЗАКАЗЧИК:
        waiting_customer / waiting_customer_overdue /
        waiting_customer_ontime (все — «Выполнено» < 90 р.д.)
        waiting_customer_chronic (> 90 р.д.)

      УЧТЁННЫЕ:
        closed_by_doc_status (лист A/B, статус «Выполнено»)

      АРХИВ:
        abandoned (> 90 календарных дней без движения)
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.id, c.doc_id, c.comment, c.status, c.author, c.created,
                c.fix_date, c.category, c.category_date,
                c.category_user, c.category_version,
                d.discipline, d.section, d.complex,
                REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS sheet,
                d.name AS sheet_name,
                d.status AS doc_status
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN (
                'Новое','Принято в работу','Не принято',
                'К обсуждению','Выполнено'
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
        doc_status = (r["doc_status"] or "").strip().upper()

        # Срок ответа с нашей стороны
        due = add_workdays(created, SLA_DAYS)

        # Срок рассмотрения заказчиком
        cust_due = add_workdays(fix_d, SLA_DAYS) if fix_d else None

        # Последнее движение
        movements = [d for d in [created, fix_d, cat_d] if d]
        last_movement = max(movements) if movements else created
        days_since_movement = (today - last_movement).days

        # Рабочих дней просрочки
        wd_overdue_work = workdays_between(due, today) if today > due else 0

        # Рабочих дней ожидания заказчика
        wd_waiting_customer = 0
        if cust_due and today > cust_due:
            wd_waiting_customer = workdays_between(cust_due, today)

        # ============================================================
        #  Классификация
        # ============================================================
        category = "other"

        # ---- 1. ЗАКАЗЧИК: статус «Выполнено» ----
        if status == "Выполнено":
            # 1a. Лист утверждён (A или B) — фактически принято
            if doc_status in ("A", "B"):
                category = "closed_by_doc_status"
            # 1b. Хронические (>90 р.д.)
            elif cust_due and wd_waiting_customer > CHRONIC_WORKDAYS:
                category = "waiting_customer_chronic"
            # 1c. Просроченные заказчиком (30-90 р.д.)
            elif cust_due and wd_waiting_customer > 30:
                category = "waiting_customer_overdue"
            # 1d. Свежие (10-30 р.д.)
            elif cust_due and today > cust_due:
                category = "waiting_customer"
            # 1e. Ещё в сроке
            else:
                category = "waiting_customer_ontime"

        # ---- 2. МЫ: «Не принято» (заказчик отклонил) ----
        elif status == "Не принято":
            if fix_d:
                reject_due = add_workdays(fix_d, SLA_DAYS)
                if today > reject_due:
                    if days_since_movement > ABANDONED_DAYS:
                        category = "abandoned"
                    else:
                        category = "rejected_overdue"
                else:
                    category = "rejected_in_progress"
            else:
                # Аномалия: «Не принято» без fix_date
                if today > due:
                    category = "rejected_overdue"
                else:
                    category = "rejected_in_progress"

        # ---- 3. МЫ: «Новое» ----
        elif status == "Новое":
            if today > due:
                if days_since_movement > ABANDONED_DAYS:
                    category = "abandoned"
                else:
                    category = "new_overdue"
            else:
                category = "new_in_progress"

        # ---- 4. МЫ: «Принято в работу» ----
        elif status == "Принято в работу":
            if today > due:
                if days_since_movement > ABANDONED_DAYS:
                    category = "abandoned"
                else:
                    category = "in_work_overdue"
            else:
                category = "in_work_in_progress"

        # ---- 5. МЫ: «К обсуждению» ----
        elif status == "К обсуждению":
            if today > due:
                if days_since_movement > ABANDONED_DAYS:
                    category = "abandoned"
                else:
                    category = "discussion_overdue"
            else:
                category = "discussion_in_progress"

        result.append({
            "id": r["id"],
            "comment": r["comment"],
            "status": status,
            "doc_status": doc_status,
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
            "category": r["category"],
            "category_date": r["category_date"],
            "category_date_d": cat_d,
            "category_user": r["category_user"],
            "category_version": r["category_version"],
            "due_date_d": due,
            "customer_due_d": cust_due,
            "days_overdue_work": wd_overdue_work,
            "days_waiting_customer": wd_waiting_customer,  # ← ВАЖНО
            "wd_since_created": workdays_between(created, today),
            "days_since_movement": days_since_movement,
            "category_flag": category,
        })

    return pd.DataFrame(result)


# ---------------------------------------------------------------------------
#  KPI — 2 ряда
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    if df.empty:
        st.info("Нет данных для расчёта сроков.")
        return

    def count_by(*flags):
        return df[df["category_flag"].isin(flags)].shape[0]

    # Наша сторона
    n_new = count_by("new_overdue", "new_in_progress")
    n_new_over = count_by("new_overdue")
    n_work = count_by("in_work_overdue", "in_work_in_progress")
    n_work_over = count_by("in_work_overdue")
    n_rej = count_by("rejected_overdue", "rejected_in_progress")
    n_rej_over = count_by("rejected_overdue")
    n_disc = count_by("discussion_overdue", "discussion_in_progress")
    n_disc_over = count_by("discussion_overdue")

    # Заказчик + архив
    n_wait = count_by("waiting_customer", "waiting_customer_overdue",
                      "waiting_customer_ontime")
    n_chronic = count_by("waiting_customer_chronic")
    n_closed = count_by("closed_by_doc_status")
    n_aband = count_by("abandoned")

    # =================================================================
    #  РЯД 1: НАША СТОРОНА
    # =================================================================
    st.markdown("##### 🔴 Наша сторона — ждут нашего ответа")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "🆕 Новое",
        f"{n_new:,}".replace(",", " "),
        delta=f"🔴 {n_new_over:,} просроч.".replace(",", " ")
              if n_new_over > 0 else None,
        delta_color="inverse",
        help="Заказчик выдал, мы не взяли в работу",
    )
    c2.metric(
        "🛠 Принято в работу",
        f"{n_work:,}".replace(",", " "),
        delta=f"🔴 {n_work_over:,} просроч.".replace(",", " ")
              if n_work_over > 0 else None,
        delta_color="inverse",
        help="Взяли, но не ответили",
    )
    c3.metric(
        "🟪 Не принято",
        f"{n_rej:,}".replace(",", " "),
        delta=f"🔴 {n_rej_over:,} просроч.".replace(",", " ")
              if n_rej_over > 0 else None,
        delta_color="inverse",
        help="Заказчик отклонил, дорабатываем",
    )
    c4.metric(
        "🟣 К обсуждению",
        f"{n_disc:,}".replace(",", " "),
        delta=f"🔴 {n_disc_over:,} просроч.".replace(",", " ")
              if n_disc_over > 0 else None,
        delta_color="inverse",
        help="Спорное, обсуждаем",
    )

    # =================================================================
    #  РЯД 2: ЗАКАЗЧИК + АРХИВ
    # =================================================================
    st.markdown("##### 🔵 На стороне заказчика + архив")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "🔵 Ждут заказчика",
        f"{n_wait:,}".replace(",", " "),
        help="Выполнено < 90 р.д., лист не A/B",
    )
    c2.metric(
        "🔴 Хронические",
        f"{n_chronic:,}".replace(",", " "),
        delta="эскалация" if n_chronic > 0 else None,
        delta_color="inverse",
        help="> 90 р.д. ожидания, лист не A/B",
    )
    c3.metric(
        "🟢 Учтено (лист A/B)",
        f"{n_closed:,}".replace(",", " "),
        help="Лист утверждён, надо дожать на «Закрыто»",
    )
    c4.metric(
        "🟡 Заброшено",
        f"{n_aband:,}".replace(",", " "),
        help="> 90 дней без движения",
    )


# ---------------------------------------------------------------------------
#  Универсальный рендер подкатегории
# ---------------------------------------------------------------------------
def _render_category(df: pd.DataFrame,
                     overdue_flag: str, in_progress_flag: str,
                     title: str, description: str):
    """Универсальный рендер для подкатегории."""
    st.markdown(f"### {title}")
    st.info(description)

    overdue = df[df["category_flag"] == overdue_flag].copy()
    in_progress = df[df["category_flag"] == in_progress_flag].copy()
    sub = pd.concat([overdue, in_progress], ignore_index=True)

    if sub.empty:
        st.success("🎉 Нет замечаний в этой категории.")
        return

    # Метрики
    c1, c2, c3 = st.columns(3)
    c1.metric("Всего", f"{len(sub):,}".replace(",", " "))
    c2.metric("🔴 Просрочено", f"{len(overdue):,}".replace(",", " "))
    c3.metric("🟢 В сроке", f"{len(in_progress):,}".replace(",", " "))

    st.divider()

    # Топ комплектов
    if not sub.empty:
        st.markdown("##### Топ-15 комплектов")
        top = (sub.groupby("complex").size()
                  .reset_index(name="Замечаний")
                  .sort_values("Замечаний", ascending=True)
                  .tail(15))

        fig = px.bar(
            top, x="Замечаний", y="complex", orientation="h",
            text="Замечаний",
            color_discrete_sequence=["#E57373"],
            labels={"complex": ""},
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(height=max(350, 25 * len(top)))
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, f"Сроки_{overdue_flag}_комплекты",
                        f"dl_{overdue_flag}_cx", width=1200, height=600)

    # Таблица
    st.markdown(f"##### 📋 Список ({len(sub):,})".replace(",", " "))

    cols = ["id", "discipline", "complex", "sheet", "comment",
            "author", "created", "days_overdue_work"]
    table = sub[[c for c in cols if c in sub.columns]].copy()
    table = table.rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "author": "Автор",
        "created": "Создано",
        "days_overdue_work": "Просрочено (р.д.)",
    })
    if "Просрочено (р.д.)" in table.columns:
        table = table.sort_values("Просрочено (р.д.)", ascending=False)

    st.dataframe(
        table.head(500),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Просрочено (р.д.)": st.column_config.NumberColumn(format="%d"),
        },
    )

    # Экспорт
    with st.expander("📥 Выгрузить в Excel", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Данные")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"{overdue_flag}_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Ждут заказчика (свежие + просроченные, без хронических)
# ---------------------------------------------------------------------------
def _render_waiting_customer(df: pd.DataFrame):
    """Под-вкладка «Ждут заказчика» — свежие + просроченные (без хронических)."""
    st.markdown("### 🔵 Ждут заказчика")
    st.info(
        "**Что это значит:** мы ответили (статус «Выполнено»), но заказчик "
        "не рассмотрел в течение 10 рабочих дней. Лист ещё **не получил** "
        "статус A или B. **Это его вина.**"
    )

    waiting_flags = [
        "waiting_customer",
        "waiting_customer_overdue",
        "waiting_customer_ontime",
    ]
    sub = df[df["category_flag"].isin(waiting_flags)].copy()

    if sub.empty:
        st.success("🎉 Нет замечаний, ждущих заказчика.")
        return

    # ============================================================
    #  KPI
    # ============================================================
    fresh = (sub["category_flag"] == "waiting_customer").sum()
    overdue = (sub["category_flag"] == "waiting_customer_overdue").sum()
    ontime = (sub["category_flag"] == "waiting_customer_ontime").sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего ждут", f"{len(sub):,}".replace(",", " "))
    c2.metric("🟡 Свежие (10–30 р.д.)",
              f"{fresh:,}".replace(",", " "))
    c3.metric("🟠 Просроченные (30–90 р.д.)",
              f"{overdue:,}".replace(",", " "))
    c4.metric("🟢 Ещё в сроке (≤10 р.д.)",
              f"{ontime:,}".replace(",", " "),
              help="Мы ответили недавно, заказчик ещё в сроке")

    st.divider()

    # ============================================================
    #  График распределения по возрасту
    # ============================================================
    st.markdown("##### 📊 Распределение по возрасту ожидания")

    # Группируем по бакету
    def _bucket(row):
        days = row["days_waiting_customer"]
        if days <= 10:
            return "🟢 ≤10 р.д."
        elif days <= 30:
            return "🟡 10–30 р.д."
        elif days <= 90:
            return "🟠 30–90 р.д."
        else:
            return "🔴 >90 р.д. (хроника)"

    sub["_bucket"] = sub.apply(_bucket, axis=1)

    bucket_counts = (sub.groupby("_bucket").size()
                        .reset_index(name="Количество"))

    order = ["🟢 ≤10 р.д.", "🟡 10–30 р.д.",
             "🟠 30–90 р.д.", "🔴 >90 р.д. (хроника)"]
    color_map = {
        "🟢 ≤10 р.д.": "#A5D6A7",
        "🟡 10–30 р.д.": "#FFD54F",
        "🟠 30–90 р.д.": "#FFB74D",
        "🔴 >90 р.д. (хроника)": "#E57373",
    }

    fig = px.bar(
        bucket_counts, x="_bucket", y="Количество",
        text="Количество",
        color="_bucket",
        color_discrete_map=color_map,
        category_orders={"_bucket": order},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(showlegend=False, height=350,
                       xaxis_title="", yaxis_title="Замечаний")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_ждут_заказчика_возраст", "dl_wait_age")

    st.divider()

    # ============================================================
    #  Топ-10 авторов
    # ============================================================
    st.markdown("##### 👤 Топ-10 авторов, чьи ответы ждут решения")

    top_auth = (sub.groupby("author").size()
                   .reset_index(name="Ожидают")
                   .sort_values("Ожидают", ascending=True)
                   .tail(10))

    if not top_auth.empty:
        fig = px.bar(
            top_auth, x="Ожидают", y="author", orientation="h",
            text="Ожидают",
            color_discrete_sequence=["#64B5F6"],
            labels={"author": ""},
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(height=max(300, 30 * len(top_auth)))
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Сроки_ждут_авторы", "dl_wait_authors")

    st.divider()

    # ============================================================
    #  Таблица
    # ============================================================
    st.markdown(f"##### 📋 Список ({len(sub):,})".replace(",", " "))

    # Сортируем по убыванию возраста ожидания
    sub = sub.sort_values("days_waiting_customer", ascending=False)

    # Явно формируем таблицу — без потери колонок
    table = sub[[
        "id", "discipline", "complex", "sheet",
        "comment", "author", "fix_date",
        "days_waiting_customer", "_bucket",
    ]].copy()

    table = table.rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "author": "Автор",
        "fix_date": "Наш ответ",
        "days_waiting_customer": "Ждём (р.д.)",
        "_bucket": "Возраст",
    })

    st.dataframe(
        table.head(500),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Ждём (р.д.)": st.column_config.NumberColumn(format="%d"),
        },
    )
    if len(table) > 500:
        st.caption(f"Показаны первые 500 из {len(table):,}.".replace(",", " "))

    # ============================================================
    #  Экспорт
    # ============================================================
    with st.expander("📧 Выгрузить для письма заказчику", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Ждут заказчика")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Ждут_заказчика_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )

# ---------------------------------------------------------------------------
#  Хронические
# ---------------------------------------------------------------------------
def _render_chronic(df: pd.DataFrame):
    st.markdown("### 🔴 Хронические — ждут заказчика >90 р.д.")
    st.warning(
        "**Что это значит:** мы ответили > 90 рабочих дней назад, "
        "но заказчик не рассмотрел. Лист не имеет статус A/B. "
        "Скорее всего, статус в Витрокад **просто не обновили**. "
        "**Рекомендуется:** массовое письмо с просьбой закрыть."
    )

    sub = df[df["category_flag"] == "waiting_customer_chronic"].copy()
    if sub.empty:
        st.success("🎉 Нет хронических замечаний.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Всего", f"{len(sub):,}".replace(",", " "))
    c2.metric("Средний возраст",
              f"{int(sub['days_waiting_customer'].mean())} р.д.")
    c3.metric("Максимум",
              f"{int(sub['days_waiting_customer'].max())} р.д.")

    st.divider()

    # Топ комплектов
    st.markdown("##### Топ-15 комплектов")
    top = (sub.groupby("complex").size()
              .reset_index(name="Хронических")
              .sort_values("Хронических", ascending=True)
              .tail(15))

    fig = px.bar(
        top, x="Хронических", y="complex", orientation="h",
        text="Хронических",
        color_discrete_sequence=["#7F0000"],
        labels={"complex": ""},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(350, 25 * len(top)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_хронические_комплекты", "dl_chronic")

    # Топ авторов
    st.markdown("##### Топ-10 авторов заказчика")
    top_auth = (sub.groupby("author").size()
                   .reset_index(name="Хронических")
                   .sort_values("Хронических", ascending=True)
                   .tail(10))

    fig = px.bar(
        top_auth, x="Хронических", y="author", orientation="h",
        text="Хронических",
        color_discrete_sequence=["#64B5F6"],
        labels={"author": ""},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(300, 30 * len(top_auth)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_хронические_авторы", "dl_chronic_auth")

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
        "author": "Автор",
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

    with st.expander("📧 Выгрузить для письма заказчику", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Хронические")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Хронические_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Учтено (лист A/B)
# ---------------------------------------------------------------------------
def _render_closed_by_doc(df: pd.DataFrame):
    """
    Под-вкладка «Учтено (A/B)».

    Это замечания в статусе «Выполнено», по которым лист получил статус:
      🟢 A — утверждён. Замечания фактически сняты, надо дожать
             заказчика на формальное «Закрыто» в Витрокад.
      🟡 B — готов к сдаче. Формально замечания НЕ сняты, заказчик
             может вернуть лист на доработку. Требует внимания.

    Категория замечания при этом может быть любой (не ограничиваем).
    """
    st.markdown("### 🟢 Учтено — лист получил A или B, замечания не закрыты")
    st.info(
        "**Что это значит:** мы дали ответ (статус «Выполнено»), "
        "и лист уже перешёл в статус **A** (утверждён) или **B** "
        "(готов к сдаче). Фактически замечание принято, но в Витрокад "
        "оно **ещё не закрыто**.\n\n"
        "**Действие:** массовое письмо заказчику с просьбой закрыть "
        "формально. Для листов **B** — дополнительно уточнить, "
        "не вернут ли лист на доработку."
    )

    sub = df[df["category_flag"] == "closed_by_doc_status"].copy()
    if sub.empty:
        st.info("Нет таких замечаний.")
        return

    # Разделяем по статусу листа
    sub_a = sub[sub["doc_status"] == "A"].copy()
    sub_b = sub[sub["doc_status"] == "B"].copy()
    sub_other = sub[~sub["doc_status"].isin(["A", "B"])].copy()

    # ============================================================
    #  KPI — общие + разбивка
    # ============================================================
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Всего учтено",
        f"{len(sub):,}".replace(",", " "),
        help="Лист в статусе A или B, замечание в статусе «Выполнено»",
    )
    c2.metric(
        "🟢 Лист A — утверждено",
        f"{len(sub_a):,}".replace(",", " "),
        help="Лист утверждён заказчиком. Замечания фактически сняты, "
             "надо только формально закрыть в Витрокад.",
    )
    c3.metric(
        "🟡 Лист B — к сдаче",
        f"{len(sub_b):,}".replace(",", " "),
        help="Лист готов к сдаче, но замечания формально НЕ сняты. "
             "Заказчик может вернуть лист на доработку.",
    )
    c4.metric(
        "Уникальных листов",
        sub["sheet"].nunique(),
        help="Сколько разных листов РД затронуто",
    )

    if not sub_other.empty:
        st.caption(
            f"⚠️ Замечаний с нестандартным статусом листа "
            f"(не A и не B): **{len(sub_other)}**"
        )

    st.divider()

    # ============================================================
    #  График 1: разбивка A vs B по дисциплинам (стек)
    # ============================================================
    st.markdown("##### 📊 Учтено по дисциплинам — A vs B")

    by_disc = sub.groupby(["discipline", "doc_status"]).size() \
                 .reset_index(name="Количество")

    if not by_disc.empty:
        color_map = {"A": "#2E7D32", "B": "#FFB74D"}
        fig = px.bar(
            by_disc, x="discipline", y="Количество",
            color="doc_status", barmode="stack",
            color_discrete_map=color_map,
            labels={"discipline": "Дисциплина",
                    "doc_status": "Статус листа"},
            text="Количество",
        )
        fig.update_traces(textposition="inside")
        fig.update_layout(
            height=400,
            legend_title_text="Статус листа",
            xaxis_tickangle=-45,
        )
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Сроки_учтено_дисциплины",
                        "dl_closed_disc", width=1400, height=600)

    st.divider()

    # ============================================================
    #  График 2: топ-15 комплектов (A vs B)
    # ============================================================
    st.markdown("##### 🏗 Топ-15 комплектов — где больше всего учтённых")

    by_cx = sub.groupby(["complex", "doc_status"]).size() \
               .reset_index(name="Количество")
    # Топ-15 по сумме
    total_by_cx = by_cx.groupby("complex")["Количество"].sum() \
                       .reset_index().sort_values("Количество",
                                                   ascending=False).head(15)
    by_cx = by_cx[by_cx["complex"].isin(total_by_cx["complex"])]

    if not by_cx.empty:
        fig = px.bar(
            by_cx, x="Количество", y="complex", orientation="h",
            color="doc_status", barmode="stack",
            color_discrete_map={"A": "#2E7D32", "B": "#FFB74D"},
            labels={"complex": "", "doc_status": "Статус листа"},
            text="Количество",
        )
        fig.update_traces(textposition="inside")
        fig.update_layout(
            height=max(400, 28 * by_cx["complex"].nunique()),
            legend_title_text="Статус листа",
        )
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Сроки_учтено_комплекты",
                        "dl_closed_cx", width=1400, height=700)

    st.divider()

    # ============================================================
    #  График 3: топ-10 авторов (A vs B)
    # ============================================================
    st.markdown("##### 👤 Топ-10 авторов замечаний — A vs B")

    by_auth = sub.groupby(["author", "doc_status"]).size() \
                 .reset_index(name="Количество")
    top_auth = by_auth.groupby("author")["Количество"].sum() \
                      .reset_index().sort_values("Количество",
                                                  ascending=False).head(10)
    by_auth = by_auth[by_auth["author"].isin(top_auth["author"])]

    if not by_auth.empty:
        fig = px.bar(
            by_auth, x="Количество", y="author", orientation="h",
            color="doc_status", barmode="stack",
            color_discrete_map={"A": "#2E7D32", "B": "#FFB74D"},
            labels={"author": "", "doc_status": "Статус листа"},
            text="Количество",
        )
        fig.update_traces(textposition="inside")
        fig.update_layout(
            height=max(350, 32 * by_auth["author"].nunique()),
            legend_title_text="Статус листа",
        )
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Сроки_учтено_авторы",
                        "dl_closed_auth", width=1400, height=600)

    st.divider()

    # ============================================================
    #  Таблица с фильтром A / B / всё
    # ============================================================
    st.markdown(f"##### 📋 Список ({len(sub):,})".replace(",", " "))

    # Фильтр по статусу листа
    flt = st.radio(
        "Показать:",
        options=["Все", "🟢 Только A", "🟡 Только B"],
        horizontal=True,
        key="dl_closed_filter",
    )
    if flt == "🟢 Только A":
        table_src = sub_a
    elif flt == "🟡 Только B":
        table_src = sub_b
    else:
        table_src = sub

    if table_src.empty:
        st.info("По выбранному фильтру нет данных.")
        return

    table = table_src[[
        "id", "discipline", "complex", "sheet", "sheet_name",
        "comment", "author", "doc_status", "fix_date",
    ]].copy().rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "sheet_name": "Название листа",
        "comment": "Замечание",
        "author": "Автор",
        "doc_status": "Статус листа",
        "fix_date": "Наш ответ",
    })

    st.dataframe(
        table.head(500),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Название листа": st.column_config.TextColumn(width="large"),
        },
    )
    if len(table) > 500:
        st.caption(f"Показаны первые 500 из {len(table):,}."
                   .replace(",", " "))

    # ============================================================
    #  Экспорт (по текущему фильтру)
    # ============================================================
    with st.expander("📧 Выгрузить для письма", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name="Учтено")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Учтено_{flt.replace('🟢 ','').replace('🟡 ','')}"
                      f"_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Заброшено
# ---------------------------------------------------------------------------
def _render_abandoned(df: pd.DataFrame):
    st.markdown("### 🟡 Заброшено (архив)")
    st.info(
        "**Что это значит:** замечания без движения >90 календарных дней "
        "в любом статусе. Кандидаты на снятие или пересогласование "
        "с заказчиком."
    )

    sub = df[df["category_flag"] == "abandoned"].copy()
    if sub.empty:
        st.success("🎉 Нет заброшенных замечаний.")
        return

    c1, c2 = st.columns(2)
    c1.metric("Всего заброшено", f"{len(sub):,}".replace(",", " "))
    c2.metric("Средний возраст",
              f"{int(sub['days_since_movement'].mean())} дн.")

    st.divider()

    # Разбивка по годам
    sub["Год"] = sub["created_d"].apply(lambda d: d.year)
    by_year = sub.groupby("Год").size().reset_index(name="Количество")

    fig = px.bar(
        by_year, x="Год", y="Количество",
        text="Количество",
        color_discrete_sequence=["#FFB74D"],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=350, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_заброшено_годы", "dl_ab_years")

    # Разбивка по статусу
    st.markdown("##### По статусам")
    by_status = sub.groupby("status").size().reset_index(name="Количество")
    by_status = by_status.sort_values("Количество", ascending=False)

    fig = px.bar(
        by_status, x="status", y="Количество",
        text="Количество",
        color_discrete_sequence=["#FFB74D"],
        labels={"status": "Статус"},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=300, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Сроки_заброшено_статусы", "dl_ab_statuses")

    # Таблица
    st.markdown(f"##### 📋 Список ({len(sub):,})".replace(",", " "))
    table = sub[[
        "id", "discipline", "complex", "sheet",
        "comment", "status", "author", "created", "days_since_movement",
    ]].copy().rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "status": "Статус",
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
        "Контроль 10 рабочих дней: ответы на замечания, рассмотрение "
        "заказчиком, архив. Все категории разделены по вине: "
        "**красные — наша**, **синие — заказчика**, **зелёные — учтено**."
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

    # 8 под-вкладок
    tabs = st.tabs([
        "🆕 Новое",
        "🛠 Принято в работу",
        "🟪 Не принято",
        "🟣 К обсуждению",
        "🔵 Ждут заказчика",
        "🔴 Хронические",
        "🟢 Учтено (A/B)",
        "🟡 Заброшено",
    ])

    with tabs[0]:
        _render_category(
            df, "new_overdue", "new_in_progress",
            "🆕 Новое — не взято в работу",
            "Заказчик выдал замечание, но мы ещё не взяли его в работу. "
            "Срок ответа — 10 рабочих дней.",
        )
    with tabs[1]:
        _render_category(
            df, "in_work_overdue", "in_work_in_progress",
            "🛠 Принято в работу — не ответили",
            "Мы взяли замечание, но ещё не дали ответ. "
            "Срок ответа — 10 рабочих дней.",
        )
    with tabs[2]:
        _render_category(
            df, "rejected_overdue", "rejected_in_progress",
            "🟪 Не принято — заказчик отклонил",
            "Мы ответили, заказчик проверил и **не принял** — вернул на "
            "доработку. Срок доработки — 10 рабочих дней с момента "
            "отклонения.",
        )
    with tabs[3]:
        _render_category(
            df, "discussion_overdue", "discussion_in_progress",
            "🟣 К обсуждению — спорное",
            "Замечание в обсуждении, спорное. Требует совещания.",
        )
    with tabs[4]:
        _render_waiting_customer(df)
    with tabs[5]:
        _render_chronic(df)
    with tabs[6]:
        _render_closed_by_doc(df)
    with tabs[7]:
        _render_abandoned(df)