# vitro/ui/dynamics.py
"""
📈 Динамика — потоки замечаний по месяцам.

Инструмент РП и Директора: сколько выдаётся, отвечаем, закрывается.
Фильтры: дисциплина → раздел → расположение → комплект → марка
(через render_standard_filters).
Период: пресеты (1 неделя / 1 месяц / …) + календарь.

Блоки вкладки:
  1. «Прогресс за прошедшую неделю» — метрики за 7 дней: выдано
     замечаний, наши ответы, закрыто заказчиком, выгружено
     комплектов и листов (documents.status_date).
  2. «Общий объём за период» — замечания + выгруженные
     комплекты/листы за выбранный период.
  3. «Выполнение плана» — бублики «выполнено / осталось» против
     Ведомости комплектов (таблица plan_kits).

Все НОВЫЕ запросы к БД идут через @versioned_cache — кэш,
инвалидируемый по версии данных (последняя синхронизация),
без st.cache_data.clear().
"""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro._cache import versioned_cache
from vitro.disciplines import discipline_name
from vitro.ui._filters import render_standard_filters


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


def _fmt(n: int) -> str:
    """Формат числа с пробелами-разделителями тысяч: 12 345."""
    return f"{n:,}".replace(",", " ")


# ---------------------------------------------------------------------------
#  Прогресс за прошедшую неделю
# ---------------------------------------------------------------------------
@versioned_cache(ttl=600)
def _load_weekly_stats(date_from: date, date_to: date) -> dict:
    """Статистика за период по всему проекту (без фильтров).

    Ключи словаря:
      issued      — выдано замечаний (comments.created);
      our_answers — наши ответы (comments.fix_date, статус
                    «Выполнено» или «Закрыто»);
      cust_closed — закрыто заказчиком (comments.fix_date, «Закрыто»);
      kits        — выгружено комплектов (documents, status_date);
      sheets      — выгружено листов (documents, status_date).

    В таблице documents нет колонки created, поэтому датировка
    листов — по substr(status_date, 1, 10) (формат ISO).
    """
    to_exclusive = (date_to + timedelta(days=1)).isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN c.created >= ? AND c.created < ?
                                  THEN 1 ELSE 0 END), 0) AS issued,
                COALESCE(SUM(CASE WHEN c.fix_date >= ? AND c.fix_date < ?
                                   AND c.status IN ('Закрыто', 'Выполнено')
                                  THEN 1 ELSE 0 END), 0) AS our_answers,
                COALESCE(SUM(CASE WHEN c.fix_date >= ? AND c.fix_date < ?
                                   AND c.status = 'Закрыто'
                                  THEN 1 ELSE 0 END), 0) AS cust_closed
            FROM comments c
        """, (date_from.isoformat(), to_exclusive) * 3).fetchone()

        drow = conn.execute("""
            SELECT COUNT(DISTINCT complex) AS kits, COUNT(*) AS sheets
            FROM documents
            WHERE substr(status_date, 1, 10) >= ?
              AND substr(status_date, 1, 10) < ?
        """, (date_from.isoformat(), to_exclusive)).fetchone()

    return {
        "issued":      int(row["issued"] or 0),
        "our_answers": int(row["our_answers"] or 0),
        "cust_closed": int(row["cust_closed"] or 0),
        "kits":        int(drow["kits"] or 0),
        "sheets":      int(drow["sheets"] or 0),
    }


def _render_weekly_progress():
    """Блок «Прогресс за прошедшую неделю» — в самом верху вкладки."""
    st.markdown("### 🗓 Прогресс за прошедшую неделю")
    st.caption("Статистика по всему проекту за последние 7 дней.")

    today = date.today()
    stats = _load_weekly_stats(today - timedelta(days=7), today)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "📤 Выдано замечаний", _fmt(stats["issued"]),
        help="Замечания заказчика, созданные за последние 7 дней.",
    )
    c2.metric(
        "🔴 Наши ответы", _fmt(stats["our_answers"]),
        help="Замечания, переведённые АТП ТЛП в «Выполнено» "
             "или «Закрыто» за последние 7 дней.",
    )
    c3.metric(
        "🟢 Закрыто заказчиком", _fmt(stats["cust_closed"]),
        help="Замечания, формально закрытые заказчиком "
             "за последние 7 дней.",
    )
    c4.metric(
        "🏗 Выгружено комплектов", _fmt(stats["kits"]),
        help="Комплекты, у которых за последние 7 дней изменился "
             "статус в Витро (по дате статуса листа).",
    )
    c5.metric(
        "📄 Выгружено листов", _fmt(stats["sheets"]),
        help="Листы РД, выгруженные или обновлённые в Витро "
             "за последние 7 дней (по дате статуса листа).",
    )


# ---------------------------------------------------------------------------
#  WHERE по фильтрам
# ---------------------------------------------------------------------------
def _build_filter_sql(disciplines: tuple, sections: tuple,
                       kits: tuple, block_codes: tuple,
                       date_from: date, date_to: date,
                       date_col: str,
                       alias: str = "d",
                       marka: str = "") -> tuple[str, list]:
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
    if marka:
        # 2-й блок (марка) — строгое совпадение через LIKE:
        # '%-КЖ1-%' найдёт только КЖ1, не найдёт КЖ11 или КЖ10.
        m = marka.strip().upper()
        if m:
            parts.append(f"{alias}.complex LIKE '%-{m}-%'")
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
                       date_from: date, date_to: date,
                       marka: str = "") -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.created", marka=marka)
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
                              date_from: date, date_to: date,
                              marka: str = "") -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date", marka=marka)
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
                                  date_from: date, date_to: date,
                                  marka: str = "") -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date", marka=marka)
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


@versioned_cache(ttl=3600)
def _load_doc_volume(disciplines: tuple, sections: tuple,
                     kits: tuple, block_codes: tuple,
                     date_from: date, date_to: date) -> dict:
    """Выгружено комплектов и листов за период (documents.status_date).

    Учитывает каскадные фильтры. Листы без даты статуса
    в период не попадают (их невозможно датировать).
    """
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "substr(d.status_date, 1, 10)")
    q = f"""
        SELECT COUNT(DISTINCT d.complex) AS kits, COUNT(*) AS sheets
        FROM documents d
        WHERE d.complex IS NOT NULL {where}
    """
    with get_conn() as conn:
        row = conn.execute(q, tuple(params)).fetchone()
    return {"kits": int(row["kits"] or 0), "sheets": int(row["sheets"] or 0)}


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_issue_by_discipline(disciplines: tuple, sections: tuple,
                                kits: tuple, block_codes: tuple,
                                date_from: date, date_to: date,
                                marka: str = "") -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.created", marka=marka)
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
                                      date_from: date, date_to: date,
                                      marka: str = "") -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date", marka=marka)
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
                                          date_from: date, date_to: date,
                                          marka: str = "") -> pd.DataFrame:
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date", marka=marka)
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
                 cust_closed: pd.DataFrame,
                 doc_volume: dict):
    total_issue = int(issue["n"].sum()) if not issue.empty else 0
    total_our = int(our_answers["n"].sum()) if not our_answers.empty else 0
    total_cust = int(cust_closed["n"].sum()) if not cust_closed.empty else 0

    st.markdown("##### 📦 Общий объём за период")

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Выдано замечаний", _fmt(total_issue),
        help="Сколько замечаний поступило от заказчика за выбранный период.",
    )
    c2.metric(
        "Наши ответы", _fmt(total_our),
        help="Сколько замечаний АТП ТЛП перевёл в «Выполнено» "
             "или «Закрыто» за период.",
    )
    c3.metric(
        "Закрыто заказчиком", _fmt(total_cust),
        help="Сколько замечаний заказчик формально закрыл за период.",
    )

    k1, k2 = st.columns(2)
    k1.metric(
        "🏗 Выгружено комплектов", _fmt(doc_volume["kits"]),
        help="Комплекты, у которых за выбранный период изменился статус "
             "в Витро (по дате статуса листа).",
    )
    k2.metric(
        "📄 Выгружено листов", _fmt(doc_volume["sheets"]),
        help="Листы РД, чей статус в Витро изменился за выбранный период "
             "(по дате статуса листа).",
    )


# ---------------------------------------------------------------------------
#  План (Ведомость комплектов) и бублики выполнения
# ---------------------------------------------------------------------------
_PLAN_TABLE = "plan_kits"


@versioned_cache(ttl=600)
def _load_plan(
    disciplines: tuple = (),
    sections: tuple = (),
    kits: tuple = (),
    block_codes: tuple = (),
) -> dict:
    """План комплектов/листов из plan_drawings с фильтрацией.

    Структура БД:
      plan_drawings.kit_code → complexes.code (JOIN);
      plan_drawings.qty — количество листов.

    Фильтры: discipline, section, kits, block_codes (4-й блок).
    Возвращает {code: {'kits': 1, 'sheets': N}}.
    """
    with get_conn() as conn:
        where_parts, params = [], []

        # discipline
        if disciplines:
            where_parts.append(
                f"c.discipline IN ({','.join('?' * len(disciplines))})")
            params += list(disciplines)

        # section
        if sections:
            ph = ",".join("?" * len(sections))
            where_parts.append(
                f"c.code IN (SELECT DISTINCT complex FROM documents "
                f"WHERE section IN ({ph}))")
            params += list(sections)

        # kits (прямое указание)
        if kits:
            ph = ",".join("?" * len(kits))
            where_parts.append(f"c.code IN ({ph})")
            params += list(kits)

        where = ""
        if where_parts:
            where = " WHERE " + " AND ".join(where_parts)

        # Основной запрос: сумма qty по kit_code
        q = f"""
            SELECT pd.kit_code, SUM(pd.qty) AS total_sheets
            FROM plan_drawings pd
            JOIN complexes c ON pd.kit_code = c.code
            {where}
            GROUP BY pd.kit_code
        """
        rows = conn.execute(q, tuple(params)).fetchall()

    plan: dict[str, dict] = {}
    for r in rows:
        code = str(r["kit_code"] or "").strip()
        if not code:
            continue
        # Дополнительная фильтрация по block_codes (в Python)
        if block_codes:
            block = _extract_4th_block(code)
            if block not in block_codes:
                continue
        try:
            s = int(float(r["total_sheets"] or 0))
        except (ValueError, TypeError):
            s = 0
        plan[code] = {"kits": 1, "sheets": max(0, s)}
    return plan


@versioned_cache(ttl=3600)
def _load_doc_totals(disciplines: tuple, sections: tuple,
                     kits: tuple, block_codes: tuple) -> dict:
    """Сколько комплектов и листов УЖЕ есть в Витро (всего, без периода).

    «Выполнено» для бубликов сравнения с планом.
    """
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        None, None, "d.status_date")
    q = f"""
        SELECT COUNT(DISTINCT d.complex) AS kits, COUNT(*) AS sheets
        FROM documents d
        WHERE d.complex IS NOT NULL {where}
    """
    with get_conn() as conn:
        row = conn.execute(q, tuple(params)).fetchone()
    return {"kits": int(row["kits"] or 0), "sheets": int(row["sheets"] or 0)}


@versioned_cache(ttl=600)
def _load_scope_codes(disciplines: tuple, sections: tuple) -> frozenset:
    """Комплекты, встречающиеся в documents с учётом дисциплин/разделов.

    Нужно, чтобы сузить план при фильтрации по дисциплине/разделу:
    в plan_kits этих колонок может не быть.
    """
    where_parts, params = [], []
    if disciplines:
        where_parts.append(
            f"discipline IN ({','.join('?' * len(disciplines))})")
        params += list(disciplines)
    if sections:
        where_parts.append(
            f"section IN ({','.join('?' * len(sections))})")
        params += list(sections)
    where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT complex FROM documents{where}",
            tuple(params),
        ).fetchall()
    return frozenset(r["complex"] for r in rows if r["complex"])


def _build_donut_figure(done: int, plan_total: int):
    """Бублик «выполнено / осталось» с процентом в центре.

    Возвращает (fig, процент, осталось/перевыполнено, текст_статус).
    """
    remaining = max(0, plan_total - done)
    overage = max(0, done - plan_total)  # перевыполнение
    pct = int(round(done / plan_total * 100)) if plan_total else 0
    is_over = done > plan_total

    if is_over:
        # Перевыполнение: показываем «Выполнено» + «Перевыполнено»
        df = pd.DataFrame({
            "Категория": ["Выполнено", "Перевыполнено"],
            "Значение": [plan_total, overage],
        })
        color_map = {"Выполнено": "#2E7D32", "Перевыполнено": "#FFA726"}
    else:
        # Норма: показываем «Выполнено» + «Осталось»
        df = pd.DataFrame({
            "Категория": ["Выполнено", "Осталось"],
            "Значение": [done, remaining],
        })
        color_map = {"Выполнено": "#2E7D32", "Осталось": "#B0BEC5"}

    fig = px.pie(
        df,
        values="Значение",
        names="Категория",
        hole=0.45,
        color="Категория",
        color_discrete_map=color_map,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="value",
        hovertemplate="%{label}: %{value}<extra></extra>",
    )
    fig.add_annotation(
        text=f"<b>{pct}%</b>",
        x=0.5, y=0.5, xref="paper", yref="paper",
        showarrow=False,
        font=dict(size=26, color="#263238"),
    )
    fig.update_layout(
        showlegend=False,
        margin=dict(t=10, b=10, l=10, r=10),
        height=260,
    )
    # status_text: "перевыполнено на N" или "осталось N"
    status_text = overage if is_over else remaining
    return fig, pct, status_text, is_over


def _render_progress_donuts(disciplines: tuple, sections: tuple,
                            kits: tuple, block_codes: tuple):
    """Бублики сравнения с планом: комплекты и листы.

    «Выполнено» — сколько уже есть в Витро (_load_doc_totals),
    «Осталось» — план из Ведомости комплектов (plan_drawings) минус
    выполненное. Фильтрация плана — внутри _load_plan.
    """
    st.markdown("### 🎯 Выполнение плана (Ведомость комплектов)")

    # Фильтр плана происходит внутри _load_plan
    plan = _load_plan(disciplines, sections, kits, block_codes)
    if not plan:
        st.info(
            "План не задан: таблица `plan_kits` не найдена или пуста. "
            "Загрузите Ведомость комплектов — и бублики появятся."
        )
        return

    if not plan:
        st.info(
            "Нет плановых значений для выбранной комбинации фильтров."
        )
        return

    # kit_codes и суммы — из отфильтрованного плана
    kit_codes = tuple(code for code, vals in plan.items())

    # Плановые листы — сумма qty из plan_drawings по kit_codes
    with get_conn() as conn:
        ph = ",".join("?" * len(kit_codes))
        plan_rows = conn.execute(
            f"SELECT SUM(qty) AS total_sheets FROM plan_drawings "
            f"WHERE kit_code IN ({ph})", kit_codes
        ).fetchone()
        plan_sheets_total = int(plan_rows["total_sheets"] or 0)

    # Плановые комплекты = количество уникальных kit_codes
    plan_kits_total = len(kit_codes)

    totals = _load_doc_totals(disciplines, sections, kits, block_codes)

    col1, col2 = st.columns(2)

    with col1:
        fig, pct, status_text, is_over = _build_donut_figure(
            totals["kits"], plan_kits_total)
        st.plotly_chart(fig, use_container_width=True, key="dyn_donut_kits")
        if is_over:
            st.markdown(
                f"<div style='text-align:center'>Выполнено "
                f"<b>{_fmt(totals['kits'])}</b> из "
                f"<b>{_fmt(plan_kits_total)}</b> (план) — "
                f"<b style='color:#FFA726'>перевыполнено "
                f"на {_fmt(status_text)}</b></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='text-align:center'>Выполнено "
                f"<b>{_fmt(totals['kits'])}</b> из "
                f"<b>{_fmt(plan_kits_total)}</b> (план) — "
                f"осталось <b>{_fmt(status_text)}</b></div>",
                unsafe_allow_html=True,
            )

    with col2:
        fig, pct, status_text, is_over = _build_donut_figure(
            totals["sheets"], plan_sheets_total)
        st.plotly_chart(fig, use_container_width=True, key="dyn_donut_sheets")
        if is_over:
            st.markdown(
                f"<div style='text-align:center'>Выполнено "
                f"<b>{_fmt(totals['sheets'])}</b> из "
                f"<b>{_fmt(plan_sheets_total)}</b> (план) — "
                f"<b style='color:#FFA726'>перевыполнено "
                f"на {_fmt(status_text)}</b></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='text-align:center'>Выполнено "
                f"<b>{_fmt(totals['sheets'])}</b> из "
                f"<b>{_fmt(plan_sheets_total)}</b> (план) — "
                f"осталось <b>{_fmt(status_text)}</b></div>",
                unsafe_allow_html=True,
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
                            date_from: date, date_to: date,
                            marka: str = ""):
    st.markdown("### 🏷 По дисциплинам")

    tab_issue, tab_our, tab_cust = st.tabs([
        "📤 Выдача", "🔴 Наши ответы", "🟢 Закрыто заказчиком"
    ])

    with tab_issue:
        df = _load_issue_by_discipline(
            disciplines, sections, kits, block_codes, date_from, date_to,
            marka=marka)
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
            disciplines, sections, kits, block_codes, date_from, date_to,
            marka=marka)
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
            disciplines, sections, kits, block_codes, date_from, date_to,
            marka=marka)
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
    # (подпись, дней назад; 0 = всё время)
    ("1 неделя",  7),
    ("1 месяц",   30),
    ("3 мес.",    90),
    ("6 мес.",    180),
    ("12 мес.",   365),
    ("24 мес.",   730),
    ("Всё время", 0),
]

# Старые сессии могли сохранить прежние подписи пресетов
_PRESET_ALIASES = {"Всё": "Всё время"}


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
    current = _PRESET_ALIASES.get(current, current)
    if current not in preset_labels:
        current = "12 мес."

    # В старых сессиях мог остаться пресет с прежней подписью («Всё»)
    if (st.session_state.get("dyn_preset_radio") is not None
            and st.session_state["dyn_preset_radio"] not in preset_labels):
        del st.session_state["dyn_preset_radio"]

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
        days = dict(_PRESETS)[preset]
        if days:
            st.session_state["dyn_from_input"] = (
                today - timedelta(days=days)
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

    # ===== Прогресс за прошедшую неделю =====
    _render_weekly_progress()

    st.divider()

    # ===== Единый стандарт фильтров (как в Сводке) =====
    flt = render_standard_filters(
        key_prefix="dyn_",
        show_remark_status=False,
        show_sheet_status=False,
    )
    disc_t = flt["disc"]
    sect_t = flt["section"]
    kit_t = flt["kits"]
    block_codes = flt["block_codes"]
    marka_t = flt["marka"]

    # ===== Период =====
    date_from, date_to = _render_period_filter()

    st.divider()

    with st.spinner("Загрузка..."):
        issue = _load_issue_stats(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to,
            marka=marka_t)
        our_answers = _load_our_answers_stats(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to,
            marka=marka_t)
        cust_closed = _load_customer_closed_stats(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to,
            marka=marka_t)
        doc_volume = _load_doc_volume(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to)

    _render_kpi(issue, our_answers, cust_closed, doc_volume)

    _render_progress_donuts(disc_t, sect_t, kit_t, block_codes)

    st.divider()

    _render_monthly_flow(issue, our_answers, cust_closed)

    st.divider()

    _render_cumulative(issue, our_answers, cust_closed)

    st.divider()

    _render_by_discipline(
        disc_t, sect_t, kit_t, block_codes, date_from, date_to,
        marka=marka_t)