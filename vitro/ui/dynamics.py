# vitro/ui/dynamics.py
"""
📈 Динамика — объём выдачи, выполнение плана и соблюдение сроков.

Инструмент РП и Директора: сколько выдаётся, отвечаем, закрывается
и какой процент ответов уложился в SLA (10 рабочих дней).
Фильтры: дисциплина → раздел → расположение → комплект → марка
(через render_standard_filters).
Период: пресеты (1 неделя / 1 месяц / …) + календарь.

Блоки вкладки:
  1. «Прогресс за прошедшую неделю» — метрики за 7 дней (выдано
     замечаний, выполнено, закрыто заказчиком, выгружено
     комплектов и листов) + прогресс-бары «доля недели от общего
     объёма за всё время».
     «Выгружено комплектов» — только комплекты, ПОЯВИВШИЕСЯ в
     Витро впервые за период (минимальная дата статуса их листов
     в периоде); повторные изменения статуса уже загруженных
     комплектов счётчик не увеличивают.
  2. «Общий объём за период» — замечания + впервые выгруженные
     комплекты / уникальные листы за выбранный период.
  3. «Выполнение плана» — бублики против Ведомости комплектов
     (plan_drawings / plan_kits): комплекты, листы + два бублика
     по замечаниям за период («Выполнено / Осталось» и
     «Закрыто заказчиком / Осталось», процент — в центре).
     «Выполнено» — уникальные листы и комплекты в Витро по
     sheets.unique_vitro_sheets — ЕДИНОМУ стандарту «Плана
     выдачи», поэтому цифры совпадают (11 312 / 424).
  4. «Соблюдение сроков» (SLA) + сворачиваемые «Детали»:
     топ-5 проблемных комплектов и разрез по дисциплинам
     скрыты в st.expander, чтобы не отвлекали.

Все запросы к БД идут через @versioned_cache — кэш, инвалидируемый
по версии данных (последняя синхронизация), без st.cache_data.clear().
Единый подсчёт листов: дублирующие SQL COUNT(*) убраны — факты
считаются pandas-ом от сырых строк documents через vitro.sheets
(см. _load_document_rows).

Патч 15 (период ↔ KPI ↔ план ↔ Сводка):
  1. Пресет периода и календарь жёстко синхронизированы
     (_render_period_filter): рассинхрон старых сессий самолечится,
     ручной ввод дат защищён флагом dyn_dates_manual. Именно
     рассинхрон (радио «Всё время», даты — 12 мес.) давал
     «KPI не меняется при переключении периода». Граница периода —
     строго «< начала следующего дня». Под «Общий объём за период»
     виден фактический период, по которому считаются метрики.
  2. «Выполнение плана»: в план добавлены комплекты «Только в
     Витро» (_load_plan) — как в «Плане выдачи», план 424 = 416
     (ведомость) + 8, цифры вкладок совпадают.
  3. Сводка: плитка «Прочие статусы» + строка арифметики —
     11 312 = (A+B+C+И) + прочие (на согласовании, готовится,
     аннулировано).

Патч 16 (терминология): «Наши ответы» → «Выполнено».
  Метрика «Наши ответы» (Выполнено + Закрыто) вводила в
  заблуждение: смешивала два разных статуса. Теперь «Выполнено»
  — ТОЛЬКО замечания со статусом «Выполнено» (АТП ТЛП выполнил
  и направил заказчику на согласование, заказчик ещё не
  закрыл); «Закрыто заказчиком» считается отдельно. Запросы
  считают только status = 'Выполнено' (_load_weekly_stats,
  _load_totals_all, _load_our_answers_stats,
  _load_our_answers_by_discipline).
  Сходимость: Выполнено + Закрыто заказчиком = все снятые
  замечания. «Выполнено» в Динамике шире, чем «со статусом
  Выполнено» в Сводке: там часть замечаний со статусом
  «Выполнено» уходит в «К листам A» и «К аннулированным».
"""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro._cache import versioned_cache
from vitro.disciplines import discipline_name
from vitro.sheets import is_valid_kit_code, unique_vitro_sheets
from vitro.sheets import _norm_complex as _norm_kit_code
from vitro.workdays import workdays_between
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
      our_answers — выполнено (comments.fix_date, статус
                    «Выполнено»; заказчик ещё не закрыл);
      cust_closed — закрыто заказчиком (comments.fix_date, «Закрыто»);
      kits        — комплекты, ПОЯВИВШИЕСЯ в Витро ВПЕРВЫЕ за период;
      sheets      — уникальные листы, чья последняя ревизия
                    датирована периодом.

    Патч 14:
      * «Выгружено комплектов» — только комплекты, у которых
        МИНИМАЛЬНАЯ дата статуса листов (MIN(status_date) по всем
        ревизиям, шифр нормализован как в sheets) попала в период.
        Раньше считались ВСЕ комплекты с любым изменением —
        уже загруженные задували счётчик.
      * Листы — единый стандарт «Плана выдачи»
        (sheets.unique_vitro_sheets): дедуп ревизий, «липкое»
        аннулирование, нормализация шифров, отсечение мусорных.

    В таблице documents нет колонки created, поэтому датировка
    листов — по substr(status_date, 1, 10) (формат ISO).
    """
    to_exclusive = (date_to + timedelta(days=1)).isoformat()
    lo = date_from.isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN c.created >= ? AND c.created < ?
                                  THEN 1 ELSE 0 END), 0) AS issued,
                COALESCE(SUM(CASE WHEN c.fix_date >= ? AND c.fix_date < ?
                                   AND c.status = 'Выполнено'
                                  THEN 1 ELSE 0 END), 0) AS our_answers,
                COALESCE(SUM(CASE WHEN c.fix_date >= ? AND c.fix_date < ?
                                   AND c.status = 'Закрыто'
                                  THEN 1 ELSE 0 END), 0) AS cust_closed
            FROM comments c
        """, (lo, to_exclusive) * 3).fetchone()

    # --- documents: единый стандарт «Плана выдачи» (без SQL COUNT) ---
    df = _load_document_rows()

    # комплекты, впервые появившиеся в Витро в периоде:
    dated = df[(df["status_day"] != "")
               & df["complex"].map(is_valid_kit_code)]
    if dated.empty:
        kits = 0
    else:
        first = dated.groupby("complex")["status_day"].min()
        kits = int(((first >= lo) & (first < to_exclusive)).sum())

    # уникальные листы с датой последней ревизии в периоде:
    sheets = 0
    for r in unique_vitro_sheets(df.to_dict("records")):
        day = str(r.get("status_date") or "")[:10]
        if day and lo <= day < to_exclusive:
            sheets += 1

    return {
        "issued":      int(row["issued"] or 0),
        "our_answers": int(row["our_answers"] or 0),
        "cust_closed": int(row["cust_closed"] or 0),
        "kits":        kits,
        "sheets":      sheets,
    }


@versioned_cache(ttl=600)
def _load_totals_all() -> dict:
    """Общий объём за всё время — база для долей в «Прогрессе недели».

    sheets/kits — единый стандарт «Плана выдачи»
    (sheets.unique_vitro_sheets): листы = «Листов в Витро»,
    комплекты = валидные комплекты в Витро.
    """
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS issued,
                   COALESCE(SUM(CASE WHEN c.fix_date IS NOT NULL
                                      AND c.fix_date <> ''
                                      AND c.status = 'Выполнено'
                                     THEN 1 ELSE 0 END), 0)
                       AS our_answers,
                   COALESCE(SUM(CASE WHEN c.fix_date IS NOT NULL
                                      AND c.fix_date <> ''
                                      AND c.status = 'Закрыто'
                                     THEN 1 ELSE 0 END), 0)
                       AS cust_closed
            FROM comments c
        """).fetchone()

    uniq = _load_unique_documents()
    return {
        "issued":      int(row["issued"] or 0),
        "our_answers": int(row["our_answers"] or 0),
        "cust_closed": int(row["cust_closed"] or 0),
        "kits":        int(uniq["complex"].nunique()) if not uniq.empty else 0,
        "sheets":      int(len(uniq)) if not uniq.empty else 0,
    }


def _render_weekly_progress():
    """Блок «Прогресс за прошедшую неделю» — в самом верху вкладки.

    Метрики + прогресс-бары: сколько за неделю сделано от общего
    объёма за всё время (в %) — патч 14.
    """
    st.markdown("### 🗓 Прогресс за прошедшую неделю")
    st.caption(
        "Статистика по всему проекту за последние 7 дней. "
        "Полосы под метриками — доля недели от общего объёма "
        "за всё время (%)."
    )

    today = date.today()
    stats = _load_weekly_stats(today - timedelta(days=7), today)
    totals = _load_totals_all()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "📤 Выдано замечаний", _fmt(stats["issued"]),
        help="Замечания заказчика, созданные за последние 7 дней.",
    )
    c2.metric(
        "🟠 Выполнено", _fmt(stats["our_answers"]),
        help="Замечания, которые АТП ТЛП выполнил и направил "
             "заказчику на согласование (статус «Выполнено») "
             "за последние 7 дней. Заказчик их ещё не закрыл.",
    )
    c3.metric(
        "🟢 Закрыто заказчиком", _fmt(stats["cust_closed"]),
        help="Замечания, формально закрытые заказчиком "
             "за последние 7 дней.",
    )
    c4.metric(
        "🏗 Выгружено комплектов", _fmt(stats["kits"]),
        help="Комплекты, ПОЯВИВШИЕСЯ в Витро впервые за последние "
             "7 дней (первая дата статуса их листов). Повторные "
             "изменения статуса уже загруженных комплектов "
             "не считаются.",
    )
    c5.metric(
        "📄 Выгружено листов", _fmt(stats["sheets"]),
        help="Уникальные листы РД (последняя ревизия, дедуп как в "
             "«Плане выдачи»), датированные последними 7 днями.",
    )

    bars = [
        (c1, stats["issued"], totals["issued"]),
        (c2, stats["our_answers"], totals["our_answers"]),
        (c3, stats["cust_closed"], totals["cust_closed"]),
        (c4, stats["kits"], totals["kits"]),
        (c5, stats["sheets"], totals["sheets"]),
    ]
    for col, done, total in bars:
        frac = (done / total) if total else 0.0
        try:
            col.progress(min(max(frac, 0.0), 1.0))
        except Exception:
            pass
        col.caption(f"{frac:.1%} от {_fmt(total)} всего")


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
        # Патч 15: строго «< начала следующего дня», а не «<=».
        # Для колонок с чистыми датами (без времени) «<= следующего
        # дня» неверно включало бы ВЕСЬ следующий день
        # ('2026-11-12' <= '2026-11-12' — TRUE); для меток времени
        # оба варианта равны. '<' — однозначная граница «до конца
        # date_to», та же, что в _load_weekly_stats.
        parts.append(f"{date_col} < ?")
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
          AND c.status = 'Выполнено'
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
    """Выгружено КОМПЛЕКТОВ (впервые) и УНИКАЛЬНЫХ листов за период.

    Патч 14:
      * Комплекты — только те, чья ПЕРВАЯ выгрузка в Витро
        (минимальная дата статуса по всем ревизиям их листов,
        с учётом фильтров) попала в выбранный период. Повторные
        изменения статуса уже загруженных комплектов больше не
        увеличивают счётчик.
      * Листы — единый стандарт «Плана выдачи»
        (sheets.unique_vitro_sheets): дедуп ревизий по
        complex + sheet_number, «липкое» аннулирование,
        нормализация шифров, отсечение мусорных. Датировка —
        по дате последней ревизии. Учитывает каскадные фильтры.
        Листы без даты статуса в период не попадают
        (их невозможно датировать).
    """
    df = _load_document_rows()
    df = _filter_doc_frame(df, disciplines, sections, kits, block_codes)
    if df.empty:
        return {"kits": 0, "sheets": 0}

    lo = date_from.isoformat() if date_from else ""
    hi = date_to.isoformat() if date_to else ""

    # --- листы: уникальные (последняя ревизия), датированные периодом
    sheets = 0
    for r in unique_vitro_sheets(df.to_dict("records")):
        day = str(r.get("status_date") or "")[:10]
        if not day:
            continue
        if lo and day < lo:
            continue
        if hi and day > hi:
            continue
        sheets += 1

    # --- комплекты: впервые появившиеся в Витро в периоде
    dated = df[(df["status_day"] != "")
               & df["complex"].map(is_valid_kit_code)]
    if dated.empty:
        kits = 0
    else:
        first = dated.groupby("complex")["status_day"].min()
        in_period = first <= hi if hi else first.notna()
        if lo:
            in_period = in_period & (first >= lo)
        kits = int(in_period.sum())

    return {"kits": kits, "sheets": sheets}


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
          AND c.status = 'Выполнено'
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
                 doc_volume: dict,
                 date_from: date | None = None,
                 date_to: date | None = None):
    """KPI «Общий объём за период» (патч 15).

    Все пять метрик считаются ТОЛЬКО за выбранный период:
    замечания/ответы/закрытия приходят из _load_issue_stats /
    _load_our_answers_stats / _load_customer_closed_stats, куда
    date_from/date_to передаются из _render_period_filter()
    (см. render()), комплекты/листы — из _load_doc_volume с теми же
    датами. Патч 15 добавляет подпись с ФАКТИЧЕСКИМ периодом — чтобы
    расхождение «пресет vs цифры» было видно сразу (раньше радио
    показывало «Всё время», а запросы шли за 12 месяцев).
    """
    total_issue = int(issue["n"].sum()) if not issue.empty else 0
    total_our = int(our_answers["n"].sum()) if not our_answers.empty else 0
    total_cust = int(cust_closed["n"].sum()) if not cust_closed.empty else 0

    st.markdown("##### 📦 Общий объём за период")
    if date_from and date_to:
        st.caption(
            f"Период: **{date_from.strftime('%d.%m.%Y')} — "
            f"{date_to.strftime('%d.%m.%Y')}** — все метрики ниже "
            "отфильтрованы по нему."
        )

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Выдано замечаний", _fmt(total_issue),
        help="Сколько замечаний поступило от заказчика за выбранный период.",
    )
    c2.metric(
        "Выполнено", _fmt(total_our),
        help="Замечания, которые АТП ТЛП выполнил и направил "
             "заказчику на согласование (статус «Выполнено»). "
             "Заказчик их ещё не закрыл.\n\n"
             "Вместе с «Закрыто заказчиком» показывает все снятые "
             "замечания.",
    )
    c3.metric(
        "Закрыто заказчиком", _fmt(total_cust),
        help="Сколько замечаний заказчик формально закрыл за период.",
    )

    k1, k2 = st.columns(2)
    k1.metric(
        "🏗 Выгружено комплектов", _fmt(doc_volume["kits"]),
        help="Комплекты, ПОЯВИВШИЕСЯ в Витро впервые за выбранный "
             "период (первая дата статуса их листов). Повторные "
             "изменения статуса уже загруженных комплектов "
             "не считаются.",
    )
    k2.metric(
        "📄 Выгружено листов", _fmt(doc_volume["sheets"]),
        help="Уникальные листы РД (последняя ревизия, дедуп как в "
             "«Плане выдачи»), датированные выбранным периодом.",
    )


# ---------------------------------------------------------------------------
#  Уникальные листы, план (Ведомость) и бублики выполнения
# ---------------------------------------------------------------------------
_PLAN_TABLE = "plan_kits"              # Ведомость комплектов (по комплектам)
_PLAN_DRAWINGS_TABLE = "plan_drawings"  # Ведомость: чертежи по комплектам

_DOC_COLS = ["id", "complex", "discipline", "section", "status",
             "revision", "status_date", "sheet_number"]


@versioned_cache(ttl=3600)
def _load_document_rows() -> pd.DataFrame:
    """Сырые строки documents (ВСЕ ревизии) — единая база подсчётов.

    Дублирующие SQL COUNT(*) убраны (патч 14): и «выполнено» для
    бубликов, и «выгружено за период», и «всего за всё время»
    считаются pandas-ом от ЭТИХ строк через vitro.sheets.

    complex сразу нормализуется как в sheets (_norm_complex: trim,
    верхний регистр, латинские двойники → кириллица), поэтому
    комплект не делится между написаниями шифра при группировках
    (MIN(status_date) по комплекту, фильтр по комплектам).
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, complex, discipline, section, status,
                   revision, status_date, sheet_number
            FROM documents
            WHERE complex IS NOT NULL
        """).fetchall()

    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        df = pd.DataFrame(columns=_DOC_COLS)
    else:
        df["complex"] = df["complex"].map(_norm_kit_code)
    df["status_day"] = df["status_date"].fillna("").astype(str).str[:10]
    return df


@versioned_cache(ttl=3600)
def _load_unique_documents() -> pd.DataFrame:
    """Уникальные листы Витро (последняя ревизия) одним кадром.

    Единый источник «факта» для бубликов и метрик «Выгружено …»:
    sheets.unique_vitro_sheets — тот же код, что в «Плане выдачи»
    (дедуп ревизий по complex + sheet_number, «липкое» аннулирование,
    нормализация шифров, отсечение мусорных), поэтому «Листов всего»
    и «Комплектов» совпадают с «Планом выдачи» (11 312 / 424).
    """
    kept = unique_vitro_sheets(
        _load_document_rows().to_dict("records"))
    df = pd.DataFrame(kept)
    if df.empty:
        return pd.DataFrame(columns=["complex", "discipline",
                                     "section", "status_day"])
    df["status_day"] = df["status_date"].fillna("").astype(str).str[:10]
    return df


def _filter_doc_frame(df: pd.DataFrame,
                      disciplines: tuple, sections: tuple,
                      kits: tuple, block_codes: tuple) -> pd.DataFrame:
    """Фильтры Дисциплина → Раздел → Комплект → Расположение (pandas).

    Расположение — по 4-му блоку шифра (как в deadlines._apply_filters).
    """
    out = df
    if out.empty:
        return out
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        out = out[out["complex"].isin(kits)]
    if block_codes:
        blocks = out["complex"].apply(_extract_4th_block)
        out = out[blocks.isin(block_codes)]
    return out


@versioned_cache(ttl=600)
def _load_plan(disciplines: tuple, sections: tuple,
               kits: tuple, block_codes: tuple) -> dict:
    """План комплектов/листов из Ведомости — с учётом фильтров.

    Комплекты — ВСЕ шифры из plan_drawings, включая комплекты без
    чертежей (как в «Плане выдачи»). План листов — число чертежей
    на комплект. Комплекты из plan_kits, которых нет в
    plan_drawings, добавляются с планом листов 0 (чтобы не
    задваивать план). Если plan_drawings нет — fallback на
    plan_kits целиком (complex / plan_kits / plan_sheets), как
    раньше.

    Патч 15 — фикс расхождения 416 против 424:
      * шифры plan_drawings / plan_kits нормализуются
        (_norm_complex: trim, регистр, латинские двойники) — так же,
        как шифры факта в _load_document_rows, поэтому «шифр в
        ведомости» и «шифр в Витро» всегда совпадают;
      * в план добавляются комплекты «Только в Витро» — есть в
        documents, но отсутствуют в Ведомости (план листов 0).
        «План выдачи» включает их в «Комплектов всего» (чекбокс
        «Только в Витро» включён по умолчанию), поэтому там 424 =
        416 (ведомость) + 8 (только в Витро), а бублик «Динамики»
        показывал план 416. После патча цифры совпадают: план 424,
        выполнено 296, осталось 128.

    Патч 14: мусорные шифры (is_valid_kit_code — та же проверка,
    что в «Плане выдачи») исключаются из плана, как и раньше
    отсекались в его таблице.

    Фильтры применяются внутри:
      kits и block_codes — по шифру комплекта;
      disciplines и sections — через пересечение с documents
      (_load_scope_codes): в ведомости этих колонок нет.
    """
    scope = None
    if disciplines or sections:
        scope = _load_scope_codes(disciplines, sections)

    plan: dict[str, dict] = {}

    with get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN (?, ?)",
            (_PLAN_DRAWINGS_TABLE, _PLAN_TABLE),
        ).fetchall()}

        def _code_column(table: str):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            return next(
                (c for c in ("complex", "code", "kit", "cipher", "kit_code")
                 if c in cols), None)

        drawings_used = False
        if _PLAN_DRAWINGS_TABLE in tables:
            code_col = _code_column(_PLAN_DRAWINGS_TABLE)
            if code_col:
                drawings_used = True
                for r in conn.execute(f"""
                        SELECT "{code_col}" AS code, COUNT(*) AS sheets
                        FROM {_PLAN_DRAWINGS_TABLE}
                        GROUP BY 1
                """):
                    # Патч 15: нормализация — как у факта (_load_document_rows)
                    code = _norm_kit_code(r["code"])
                    if code and is_valid_kit_code(code):
                        plan[code] = {"kits": 1,
                                      "sheets": max(0, int(r["sheets"] or 0))}

        if drawings_used:
            # Комплекты ведомости без чертежей — из plan_kits,
            # план листов для них 0 (план листов уже учтён строками
            # plan_drawings, задваивать нельзя)
            if _PLAN_TABLE in tables:
                code_col = _code_column(_PLAN_TABLE)
                if code_col:
                    for r in conn.execute(
                            f'SELECT "{code_col}" AS code FROM {_PLAN_TABLE}'):
                        # Патч 15: нормализация — как у plan_drawings
                        code = _norm_kit_code(r["code"])
                        if (code and code not in plan
                                and is_valid_kit_code(code)):
                            plan[code] = {"kits": 1, "sheets": 0}
        elif _PLAN_TABLE in tables:
            # plan_drawings нет/пуста — старый путь: план из plan_kits
            cols = [r[1] for r in conn.execute(
                f"PRAGMA table_info({_PLAN_TABLE})")]
            code_col = next(
                (c for c in ("complex", "code", "kit", "cipher", "kit_code")
                 if c in cols), None)
            kits_col = next(
                (c for c in ("plan_kits", "kits", "kits_plan", "kit_plan")
                 if c in cols), None)
            sheets_col = next(
                (c for c in ("plan_sheets", "sheets", "sheets_plan",
                             "sheet_plan") if c in cols), None)
            if code_col:
                parts = [f'"{code_col}" AS code']
                parts.append(f'COALESCE("{kits_col}", 0) AS kits' if kits_col
                             else "0 AS kits")
                parts.append(f'COALESCE("{sheets_col}", 0) AS sheets'
                             if sheets_col else "0 AS sheets")
                for r in conn.execute(
                        f"SELECT {', '.join(parts)} FROM {_PLAN_TABLE}"):
                    code = _norm_kit_code(r["code"])  # патч 15
                    if not code or not is_valid_kit_code(code):
                        continue
                    try:
                        k = int(float(r["kits"] or 0))
                        s = int(float(r["sheets"] or 0))
                    except (ValueError, TypeError):
                        k, s = 0, 0
                    plan[code] = {"kits": max(0, k), "sheets": max(0, s)}

    # Патч 15: комплекты «Только в Витро» — есть в documents, но их
    # нет в Ведомости. «План выдачи» включает их в «Комплектов всего»
    # (чекбокс «Только в Витро» включён по умолчанию), поэтому без
    # этого шага план «Динамики» был меньше его плана (416 против
    # 424). План листов для них 0 — их листы уже учтены в факте
    # (_load_doc_totals), задваивать нельзя. Добавляются только когда
    # Ведомость вообще загружена: без неё остаётся честное
    # «План не задан».
    if plan:
        for code in _load_unique_documents()["complex"].unique():
            if (code and code not in plan
                    and is_valid_kit_code(str(code))):
                plan[code] = {"kits": 1, "sheets": 0}

    # Фильтры — Дисциплина → Раздел → Комплект → Расположение
    out: dict[str, dict] = {}
    for code, vals in plan.items():
        if kits and code not in kits:
            continue
        if block_codes and _extract_4th_block(code) not in block_codes:
            continue
        if scope is not None and code not in scope:
            continue
        out[code] = vals
    return out


@versioned_cache(ttl=3600)
def _load_doc_totals(disciplines: tuple, sections: tuple,
                     kits: tuple, block_codes: tuple) -> dict:
    """Сколько комплектов и УНИКАЛЬНЫХ листов уже есть в Витро.

    «Выполнено» для бубликов сравнения с планом. Дедуп ревизий —
    vitro.sheets.dedup_document_rows (как в «Плане выдачи»),
    поэтому цифры сходятся с ним.
    """
    df = _load_unique_documents()
    df = _filter_doc_frame(df, disciplines, sections, kits, block_codes)
    if df.empty:
        return {"kits": 0, "sheets": 0}
    return {"kits": int(df["complex"].nunique()),
            "sheets": int(len(df))}


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


def _build_donut_figure(done: int, plan_total: int,
                        done_label: str = "Выполнено",
                        over_label: str = "Перевыполнено",
                        done_color: str = "#2E7D32"):
    """Бублик «сделано / осталось» с процентом в центре.

    Возвращает (fig, процент, осталось/перевыполнено, признак_перевыполнения).

    done_label/over_label/done_color — подписи и цвет сегмента
    «сделано» (для бубликов по замечаниям: «Выполнено» /
    «Закрыто заказчиком»). Сегмент «Осталось» — серый, «сверх» —
    оранжевый, как и раньше.
    """
    remaining = max(0, plan_total - done)
    overage = max(0, done - plan_total)  # перевыполнение
    pct = int(round(done / plan_total * 100)) if plan_total else 0
    is_over = done > plan_total

    if is_over:
        # Перевыполнение: показываем «сделано» + «сверх»
        df = pd.DataFrame({
            "Категория": [done_label, over_label],
            "Значение": [plan_total, overage],
        })
        color_map = {done_label: done_color, over_label: "#FFA726"}
    else:
        # Норма: показываем «сделано» + «Осталось»
        df = pd.DataFrame({
            "Категория": [done_label, "Осталось"],
            "Значение": [done, remaining],
        })
        color_map = {done_label: done_color, "Осталось": "#B0BEC5"}

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


def _render_remark_donuts(remark_totals: dict | None):
    """Бублики по замечаниям за период (патч 14).

    «Выполнено / Осталось» и «Закрыто заказчиком / Осталось» —
    от общего числа выданных за период замечаний, процент в центре.
    Не зависят от Ведомости — рисуются даже если план не задан.
    """
    issued_total = int((remark_totals or {}).get("issued") or 0)
    if issued_total <= 0:
        return

    st.markdown("#### Замечания за период: выполнение и закрытие")
    st.caption(
        f"От общего числа замечаний, выданных за выбранный период "
        f"({_fmt(issued_total)}). «Осталось» — не выполнено и не "
        f"закрыто. Выполненные и закрытые замечания, выданные до "
        f"периода, дают долю выше 100% (сегмент «Сверх периода»)."
    )

    our_total = int((remark_totals or {}).get("our_answers") or 0)
    cust_total = int((remark_totals or {}).get("cust_closed") or 0)

    r1, r2 = st.columns(2)

    with r1:
        fig, pct, status_text, is_over = _build_donut_figure(
            our_total, issued_total,
            done_label="Выполнено",
            over_label="Сверх периода",
            done_color="#F9A825",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key="dyn_donut_our")
        if is_over:
            st.markdown(
                f"<div style='text-align:center'>Выполнено "
                f"<b>{_fmt(our_total)}</b> из "
                f"<b>{_fmt(issued_total)}</b> выданных — "
                f"<b style='color:#FFA726'>+{_fmt(status_text)} "
                f"сверх периода</b></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='text-align:center'>Выполнено "
                f"<b>{_fmt(our_total)}</b> из "
                f"<b>{_fmt(issued_total)}</b> выданных — "
                f"осталось <b>{_fmt(status_text)}</b></div>",
                unsafe_allow_html=True,
            )

    with r2:
        fig, pct, status_text, is_over = _build_donut_figure(
            cust_total, issued_total,
            done_label="Закрыто заказчиком",
            over_label="Сверх периода",
            done_color="#2E7D32",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key="dyn_donut_cust")
        if is_over:
            st.markdown(
                f"<div style='text-align:center'>Закрыто заказчиком "
                f"<b>{_fmt(cust_total)}</b> из "
                f"<b>{_fmt(issued_total)}</b> выданных — "
                f"<b style='color:#FFA726'>+{_fmt(status_text)} "
                f"сверх периода</b></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='text-align:center'>Закрыто заказчиком "
                f"<b>{_fmt(cust_total)}</b> из "
                f"<b>{_fmt(issued_total)}</b> выданных — "
                f"осталось <b>{_fmt(status_text)}</b></div>",
                unsafe_allow_html=True,
            )


def _render_progress_donuts(disciplines: tuple, sections: tuple,
                            kits: tuple, block_codes: tuple,
                            remark_totals: dict | None = None):
    """Бублики сравнения с планом: комплекты, листы и замечания.

    «Выполнено» — уникальные листы/комплекты в Витро
    (_load_doc_totals: дедуп ревизий как в «Плане выдачи»),
    «Осталось» — план из Ведомости минус выполненное.
    Фильтры (Дисциплина, Раздел, Комплект, Расположение)
    передаются и в план (_load_plan), и в факт (_load_doc_totals).

    Патч 14: если передан remark_totals (issued / our_answers /
    cust_closed за выбранный период, с учётом фильтров), добавляются
    два бублика по замечаниям — они рисуются даже когда Ведомость
    не задана (не зависят от плана).
    """
    st.markdown("### 🎯 Выполнение плана (Ведомость комплектов)")

    plan = _load_plan(disciplines, sections, kits, block_codes)
    if not plan:
        st.info(
            "План не задан: таблицы `plan_drawings` / `plan_kits` "
            "не найдены или пусты. Загрузите Ведомость комплектов — "
            "и бублики появятся."
        )
        _render_remark_donuts(remark_totals)
        return

    plan_kits_total = sum(v["kits"] for v in plan.values())
    plan_sheets_total = sum(v["sheets"] for v in plan.values())

    if plan_kits_total == 0 and plan_sheets_total == 0:
        st.info(
            "В Ведомости нет плановых значений для выбранной "
            "комбинации фильтров."
        )
        _render_remark_donuts(remark_totals)
        return

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

    # ---- Бублики по замечаниям за период (патч 14) ----
    _render_remark_donuts(remark_totals)


# ---------------------------------------------------------------------------
#  Соблюдение сроков (SLA)
# ---------------------------------------------------------------------------
SLA_ON_TIME_DAYS = 10   # ≤10 р.д. — ответ дан в срок
SLA_LATE_DAYS = 30      # 10–30 р.д. — опоздание; >30 — критично

SLA_MIN_MONTH_N = 5     # мин. закрытых за месяц, чтобы считать тренд
SLA_MIN_KIT_N = 3       # мин. закрытых по комплекту для топ-5

SLA_BUCKET_ORDER = ["В срок", "Опоздание", "Критично"]

SLA_BUCKET_COLORS = {
    "В срок":    "#2E7D32",   # зелёный
    "Опоздание": "#F9A825",   # жёлтый
    "Критично":  "#C62828",   # красный
}


def _closing_bucket(created_d, fix_d):
    """Бакет скорости нашего ответа по факту закрытия.

    Пороги те же, что у our_bucket из _load_all_categorized
    (SLA 10 р.д.): ≤10 — «В срок», ≤30 — «Опоздание», >30 —
    «Критично» (бакеты 30–90 и >90 слиты).
    """
    if not created_d or not fix_d:
        return None
    wd = workdays_between(created_d, fix_d)
    if wd <= SLA_ON_TIME_DAYS:
        return "В срок"
    if wd <= SLA_LATE_DAYS:
        return "Опоздание"
    return "Критично"


@st.cache_data(ttl=3600, show_spinner=False)
def _load_closed_with_sla() -> pd.DataFrame:
    """Закрытые нами замечания («Выполнено») с бакетом скорости ответа.

    База — единый источник _load_all_categorized() из deadlines.py
    (кэш 3600 c): новых запросов к БД вкладка не делает.
    """
    from vitro.ui.deadlines import _load_all_categorized as _src
    base = _src()

    if base.empty:
        return pd.DataFrame(
            columns=["ym", "complex", "discipline", "section", "bucket"])

    closed = base[
        (base["status"] == "Выполнено")
        & base["fix_date_d"].notna()
        & (base["holder"] != "annulled")
    ]

    out = pd.DataFrame({
        "ym": closed["created_d"].map(
            lambda d: d.strftime("%Y-%m") if d else None),
        "complex": closed["complex"],
        "discipline": closed["discipline"],
        "section": closed["section"],
    })
    out["bucket"] = [
        _closing_bucket(c, f)
        for c, f in zip(closed["created_d"], closed["fix_date_d"])
    ]
    return out.dropna(subset=["ym"]).reset_index(drop=True)


def _filter_sla_frame(df: pd.DataFrame,
                      disc_t: tuple, sect_t: tuple,
                      kit_t: tuple, block_codes: tuple) -> pd.DataFrame:
    """Фильтры Дисциплина → Раздел → Комплект → Расположение (в pandas)."""
    out = df
    if out.empty:
        return out
    if disc_t:
        out = out[out["discipline"].isin(disc_t)]
    if sect_t:
        out = out[out["section"].isin(sect_t)]
    if kit_t:
        out = out[out["complex"].isin(kit_t)]
    if block_codes:
        blocks = out["complex"].apply(_extract_4th_block)
        out = out[blocks.isin(block_codes)]
    return out


def _shift_month(d: date, k: int) -> date:
    """Первый день месяца со сдвигом k месяцев от месяца даты d."""
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    return date(y, m, 1)


def _sla_on_time_share(df: pd.DataFrame):
    """Доля «В срок» среди закрытых замечаний (или None)."""
    if df.empty:
        return None
    return float((df["bucket"] == "В срок").mean())


def _sla_monthly_pct(df: pd.DataFrame) -> pd.DataFrame:
    """Доля «В срок» и число закрытых по месяцам выдачи."""
    g = (df.groupby("ym")
           .agg(n=("bucket", "size"),
                ontime=("bucket", lambda b: int((b == "В срок").sum())))
           .sort_index())
    g["pct"] = g["ontime"] / g["n"]
    return g


def _render_sla_compliance(disc_t: tuple, sect_t: tuple,
                           kit_t: tuple, block_codes: tuple,
                           date_from: date, date_to: date):
    """Блок «Соблюдение сроков»: главный показатель + тренд,
    100%-столбцы по месяцам выдачи и топ-5 проблемных комплектов."""
    st.markdown("### ⏱ Соблюдение сроков")
    st.caption(
        "«В срок» — ответ за ≤10 р.д., «Опоздание» — 10–30 р.д., "
        "«Критично» — >30 р.д. Учитываются замечания, по которым "
        "АТП ТЛП уже дал ответ (статус «Выполнено»)."
    )

    base = _load_closed_with_sla()
    sdf = _filter_sla_frame(base, disc_t, sect_t, kit_t, block_codes)

    if sdf.empty:
        st.info("Нет закрытых замечаний с учётом фильтров.")
        return

    today = date.today()
    ym_3m = _shift_month(today, -2).strftime("%Y-%m")
    recent = sdf[sdf["ym"] >= ym_3m]

    # ===== 1. Главный показатель + тренд =====
    pct3 = _sla_on_time_share(recent)

    delta = None
    monthly = _sla_monthly_pct(sdf)
    solid = monthly[monthly["n"] >= SLA_MIN_MONTH_N]
    if len(solid) >= 2:
        delta = (solid["pct"].iloc[-1] - solid["pct"].iloc[-2]) * 100

    st.metric(
        "Закрыто в срок (≤10 р.д.) за последние 3 месяца",
        f"{pct3:.0%}" if pct3 is not None else "—",
        delta=round(delta, 1) if delta is not None else None,
        help="Среди замечаний, выданных за последние 3 месяца "
             "и уже закрытых («Выполнено»). Тренд — изменение доли "
             "«в срок» в процентных пунктах к предыдущему месяцу "
             f"(месяцы с ≥{SLA_MIN_MONTH_N} закрытыми замечаниями).",
    )

    # ===== 2. Столбцы с накоплением по месяцам (100%) =====
    st.markdown("#### Доля ответов по скорости закрытия")
    st.caption("По месяцу выдачи замечания, 0–100%.")

    win = sdf[(sdf["ym"] >= date_from.strftime("%Y-%m"))
              & (sdf["ym"] <= date_to.strftime("%Y-%m"))]
    if win.empty:
        st.info("Нет закрытых замечаний за выбранный период.")
    else:
        counts = (win.groupby(["ym", "bucket"]).size()
                  .unstack(fill_value=0)
                  .reindex(columns=SLA_BUCKET_ORDER, fill_value=0))
        shares = counts.div(counts.sum(axis=1), axis=0)

        plot_df = shares.reset_index().melt(
            id_vars="ym", var_name="Скорость ответа",
            value_name="share")
        plot_df = plot_df[plot_df["share"] > 0]

        fig = px.bar(
            plot_df, x="ym", y="share", color="Скорость ответа",
            barmode="stack", text_auto=".0%",
            category_orders={"Скорость ответа": SLA_BUCKET_ORDER},
            color_discrete_map=SLA_BUCKET_COLORS,
            labels={"ym": "Месяц выдачи замечания",
                    "share": "Доля закрытых"},
        )
        fig.update_layout(
            height=450,
            xaxis_tickangle=-45,
            yaxis_tickformat=".0%",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1,
            ),
        )
        st.plotly_chart(fig, use_container_width=True,
                        key="dyn_sla_monthly")

    # ===== 3. Топ-5 проблемных комплектов (Детали, патч 14) =====
    # Неинформативен на основном экране (показывает в основном
    # комплекты со 100% критичных) — спрятан в сворачиваемый expander.
    with st.expander("🚩 Детали: топ-5 проблемных комплектов",
                     expanded=False):
        st.caption(
            "Наибольшая доля ответов позже 30 р.д. среди закрытых "
            f"за последние 3 месяца (от {SLA_MIN_KIT_N} закрытых)."
        )

        if recent.empty:
            st.info("Нет закрытых замечаний за последние 3 месяца.")
            return

        kits_df = recent.copy()
        kits_df["complex"] = kits_df["complex"].fillna("(без комплекта)")
        kit_stats = (kits_df.groupby("complex")
                     .agg(total=("bucket", "size"),
                          late=("bucket",
                                lambda b: int((b == "Критично").sum()))))
        kit_stats = kit_stats[kit_stats["total"] >= SLA_MIN_KIT_N]
        if kit_stats.empty:
            st.info("Недостаточно данных: нет комплектов с "
                    f"{SLA_MIN_KIT_N}+ закрытыми замечаниями за 3 месяца.")
            return

        kit_stats["share_late"] = kit_stats["late"] / kit_stats["total"]
        kit_stats = (kit_stats.sort_values(["share_late", "total"],
                                           ascending=False)
                     .head(5).reset_index())

        st.dataframe(
            kit_stats,
            column_config={
                "complex": st.column_config.TextColumn("Комплект"),
                "total": st.column_config.NumberColumn("Закрыто"),
                "late": st.column_config.NumberColumn("Критично (>30 р.д.)"),
                "share_late": st.column_config.ProgressColumn(
                    "Доля критичных", min_value=0.0, max_value=1.0,
                    format="percent"),
            },
            hide_index=True,
            use_container_width=True,
        )


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
        df = our_answers.rename(columns={"n": "Выполнено"}).copy()
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

    if "Выполнено" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Выполнено"],
            mode="lines", name="🟠 Выполнено",
            line=dict(color="#F9A825", width=3),
        ))

    if "Закрыто заказчиком" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Закрыто заказчиком"],
            mode="lines", name="🟢 Закрыто заказчиком",
            line=dict(color="#2E7D32", width=3),
        ))

    fig.update_layout(
        title="Накопительно: выдано vs выполнено vs закрыто",
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
    """Разрез по дисциплинам — «Детали» (патч 14).

    Перегруженный (9 цветов, стек по месяцам) и не отвечает на вопрос
    «Успеваем ли мы?» — на основном экране не нужен: рендерится
    внутри сворачиваемого expander из render().
    """
    st.markdown("#### 🏷 По дисциплинам")

    tab_issue, tab_our, tab_cust = st.tabs([
        "📤 Выдача", "🟠 Выполнено", "🟢 Закрыто заказчиком"
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
                labels={"ym": "Месяц", "n": "Выполнено",
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
    """Фильтр периода: пресет + календарь, ЖЁСТКО синхронизированы.

    Патч 15 — фикс «KPI не реагирует на переключение периода».
    Причина: пресет и календарь могли РАСХОДИТЬСЯ — в долгой сессии
    (Streamlit хранит session_state, пока открыта вкладка) оставались
    старые даты при переключённом пресете, и радио показывало
    «Всё время», а запросы шли за 12 месяцев (на скриншоте: радио
    «Всё время», даты 11.11.2025–11.11.2026, комплектов 256, а не 424).

    Правила синхронизации:
      1. Пресет — источник правды. Сменился пресет → даты
         перезаписываются диапазоном пресета, флаг ручного ввода
         сбрасывается.
      2. Самолечение рассинхрона: если даты НЕ соответствуют пресету
         и пользователь не вводил их вручную (флаг dyn_dates_manual
         сброшен) — даты тихо приводятся к диапазону пресета. Лечит
         сессии, пережившие старые версии вкладки, и смену суток
         (пресеты «N мес.» всегда означают «последние N дней от
         сегодняшнего»).
      3. Ручной ввод дат (on_change на календарях) ставит флаг
         dyn_dates_manual — после этого самолечение не трогает даты,
         пока пользователь снова не переключит пресет.
    """
    today = date.today()
    min_date = date(2020, 1, 1)

    if "dyn_preset" not in st.session_state:
        st.session_state["dyn_preset"] = "12 мес."
    if "dyn_from_input" not in st.session_state:
        st.session_state["dyn_from_input"] = today - timedelta(days=365)
    if "dyn_to_input" not in st.session_state:
        st.session_state["dyn_to_input"] = today
    if "dyn_dates_manual" not in st.session_state:
        st.session_state["dyn_dates_manual"] = False

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

    # Диапазон, соответствующий выбранному пресету (источник правды)
    days = dict(_PRESETS)[preset]
    exp_from = min_date if not days else today - timedelta(days=days)
    exp_to = today

    def _mark_manual_dates():
        """Пользователь правит календарь вручную → самолечение выключается."""
        st.session_state["dyn_dates_manual"] = True

    # 1. Пресет сменился → даты пересчитываются из пресета
    if preset != st.session_state.get("dyn_preset"):
        st.session_state["dyn_preset"] = preset
        st.session_state["dyn_dates_manual"] = False
        st.session_state["dyn_from_input"] = exp_from
        st.session_state["dyn_to_input"] = exp_to
        st.rerun()

    # 2. Самолечение рассинхрона (старые сессии, смена суток):
    #    пресет тот же, но даты ему не соответствуют, и пользователь
    #    не задавал их вручную — тихо приводим к диапазону пресета.
    if (not st.session_state.get("dyn_dates_manual")
            and (st.session_state.get("dyn_from_input") != exp_from
                 or st.session_state.get("dyn_to_input") != exp_to)):
        st.session_state["dyn_from_input"] = exp_from
        st.session_state["dyn_to_input"] = exp_to
        st.rerun()

    # 3. Ручной ввод дат — только флаг, значения не трогаем
    with c2:
        date_from = st.date_input(
            "От",
            min_value=min_date,
            max_value=today,
            key="dyn_from_input",
            on_change=_mark_manual_dates,
        )
    with c3:
        date_to = st.date_input(
            "До",
            min_value=min_date,
            max_value=today,
            key="dyn_to_input",
            on_change=_mark_manual_dates,
        )

    return date_from, date_to


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📈 Динамика")
    st.caption(
        "Объём выдачи, выполнение плана, потоки замечаний "
        "и соблюдение сроков ответа."
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

    _render_kpi(issue, our_answers, cust_closed, doc_volume,
                date_from, date_to)

    remark_totals = {
        "issued": int(issue["n"].sum()) if not issue.empty else 0,
        "our_answers": (int(our_answers["n"].sum())
                        if not our_answers.empty else 0),
        "cust_closed": (int(cust_closed["n"].sum())
                        if not cust_closed.empty else 0),
    }
    _render_progress_donuts(disc_t, sect_t, kit_t, block_codes,
                            remark_totals=remark_totals)

    st.divider()

    _render_sla_compliance(disc_t, sect_t, kit_t, block_codes,
                           date_from, date_to)

    st.divider()

    _render_cumulative(issue, our_answers, cust_closed)

    st.divider()

    # ===== Детали: по дисциплинам (патч 14) =====
    # Перегруженный разрез (9 цветов, стек по месяцам) спрятан в
    # сворачиваемый expander, чтобы не отвлекал от основного экрана.
    with st.expander("🏷 Детали: по дисциплинам", expanded=False):
        _render_by_discipline(
            disc_t, sect_t, kit_t, block_codes, date_from, date_to,
            marka=marka_t)