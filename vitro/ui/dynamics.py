# vitro/ui/dynamics.py
"""
📈 Динамика — объём выдачи, выполнение плана и потоки замечаний.

Инструмент РП и Директора: сколько выдаётся, выполняется,
закрывается и как соотносится план выдачи с фактом.
Фильтры: дисциплина → раздел → расположение → комплект → марка
(через render_standard_filters).
Период: пресеты (1 неделя / 1 месяц / …) + календарь.

Блоки вкладки:
  1. «Прогресс за прошедшую неделю» — 7 плиток за последние
     7 дней: выдано замечаний, выполнено, закрыто заказчиком,
     выгружено комплектов и листов, листов со статусом B,
     листов со статусом A.
     «Выгружено комплектов» — только комплекты, ПОЯВИВШИЕСЯ в
     Витро впервые за период (минимальная дата статуса их листов
     в периоде); повторные изменения статуса уже загруженных
     комплектов счётчик не увеличивают.
  2. «Общий объём за период» — замечания + впервые выгруженные
     комплекты / уникальные листы за выбранный период + листы
     со статусами B и A за период.
  3. «Выполнение плана» — бублики против Ведомости комплектов
     (plan_drawings / plan_kits): комплекты, листы + блок
     «Замечания за период» в стиле Сводки (патч 18): бублик
     «Объём замечаний» — числитель «Выдано за период», сегменты
     Выдано / Отработано; справа список с маркерами — Выполнено и
     Закрыто заказчиком за период, «Отработано всего» = Закрыто +
     Аннулировано + Без статуса по всем замечаниям с фильтрами
     без ограничения периодом (_load_remark_scope).
     «Выполнено» — уникальные листы и комплекты в Витро по
     sheets.unique_vitro_sheets — ЕДИНОМУ стандарту «Плана
     выдачи», поэтому цифры совпадают (11 312 / 424).

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

Патч 17 (сходимость со Сводкой, цвета, листы A/B, чистка):
  1. «Выполнено» считается только по листам, которые НЕ
     утверждены и НЕ аннулированы (JOIN documents,
     COALESCE(UPPER(TRIM(d.status)), '') NOT IN ('A',
     'АННУЛИРОВАНО')) — так же, как в Сводке, где doc_status
     нормализуется .strip().upper(). Раньше часть «Выполнено»
     сидела на листах A и аннулированных и завышала цифру
     (в Динамике 17 166, в Сводке меньше).
  2. Цвета как в Сводке: «Выполнено» — зелёный #2ca02c,
     «Закрыто заказчиком» — серый #9E9E9E (бублики замечаний
     и линия накопительного графика). Прогресс-бары «доля
     недели от общего объёма» и подписи «N% от X всего» убраны
     — 7 плиток в один ряд. Новые плитки «Листов B» (🟡) и
     «Листов A» (🟢) за последние 7 дней; те же метрики добавлены
     в «Общий объём за период» (уникальные листы с последней
     ревизией в периоде и статусом B/A; дедуп
     sheets.unique_vitro_sheets — как у «Выгружено листов»).
  3. Удалены блок «Соблюдение сроков» (SLA), график «Доля
     ответов по скорости закрытия», спойлеры «топ-5 проблемных
     комплектов» и «Детали: по дисциплинам» — вместе с мёртвым
     кодом: _render_sla_compliance, _load_closed_with_sla,
     _closing_bucket, _filter_sla_frame, _shift_month,
     _sla_on_time_share, _sla_monthly_pct, _render_by_discipline,
     загрузчики _load_*_by_discipline, _load_totals_all (её
     единственным потребителем были убранные прогресс-бары)
     и неиспользуемый BLOCK_OPTIONS.
"""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro._cache import versioned_cache
from vitro.sheets import is_valid_kit_code, unique_vitro_sheets
from vitro.sheets import _norm_complex as _norm_kit_code
from vitro.ui._filters import render_standard_filters


# ---------------------------------------------------------------------------
#  Расположение (4-й блок шифра)
# ---------------------------------------------------------------------------
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
                    «Выполнено» на листах, которые не A и не
                    аннулированы; заказчик ещё не закрыл);
      cust_closed — закрыто заказчиком (comments.fix_date, «Закрыто»);
      kits        — комплекты, ПОЯВИВШИЕСЯ в Витро ВПЕРВЫЕ за период;
      sheets      — уникальные листы, чья последняя ревизия
                    датирована периодом;
      sheets_b    — из них листы со статусом B (к сдаче);
      sheets_a    — из них листы со статусом A (утверждён).

    Патч 17: our_answers исключает замечания к листам A и
    аннулированным (как в Сводке); sheets_b/sheets_a — тот же
    дедуп unique_vitro_sheets, что у sheets, с нормализацией
    статуса .strip().upper().

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
                                   AND COALESCE(UPPER(TRIM(d.status)), '')
                                       NOT IN ('A', 'АННУЛИРОВАНО')
                                  THEN 1 ELSE 0 END), 0) AS our_answers,
                COALESCE(SUM(CASE WHEN c.fix_date >= ? AND c.fix_date < ?
                                   AND c.status = 'Закрыто'
                                  THEN 1 ELSE 0 END), 0) AS cust_closed
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
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
    sheets = sheets_b = sheets_a = 0
    for r in unique_vitro_sheets(df.to_dict("records")):
        day = str(r.get("status_date") or "")[:10]
        if not (day and lo <= day < to_exclusive):
            continue
        sheets += 1
        # Патч 17: разбивка по статусам — нормализация как в Сводке
        sheet_status = str(r.get("status") or "").strip().upper()
        if sheet_status == "B":
            sheets_b += 1
        elif sheet_status == "A":
            sheets_a += 1

    return {
        "issued":      int(row["issued"] or 0),
        "our_answers": int(row["our_answers"] or 0),
        "cust_closed": int(row["cust_closed"] or 0),
        "kits":        kits,
        "sheets":      sheets,
        "sheets_b":    sheets_b,
        "sheets_a":    sheets_a,
    }


def _render_weekly_progress():
    """Блок «Прогресс за прошедшую неделю» — в самом верху вкладки.

    Патч 17: 7 плиток в один ряд; прогресс-бары «доля недели от
    общего объёма за всё время» и подписи «N% от X всего» убраны.
    Добавлены плитки «Листов B» и «Листов A» (последняя ревизия
    листа датирована последними 7 днями).
    """
    st.markdown("### 🗓 Прогресс за прошедшую неделю")
    st.caption("Статистика по всему проекту за последние 7 дней.")

    today = date.today()
    stats = _load_weekly_stats(today - timedelta(days=7), today)

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric(
        "📤 Выдано замечаний", _fmt(stats["issued"]),
        help="Замечания заказчика, созданные за последние 7 дней.",
    )
    c2.metric(
        "🟢 Выполнено", _fmt(stats["our_answers"]),
        help="Замечания, которые АТП ТЛП выполнил и направил "
             "заказчику на согласование (статус «Выполнено») "
             "за последние 7 дней. Заказчик их ещё не закрыл. "
             "Замечания к утверждённым (A) и аннулированным листам "
             "не считаются.",
    )
    c3.metric(
        "⚪ Закрыто заказчиком", _fmt(stats["cust_closed"]),
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
    c6.metric(
        "🟡 Листов B", _fmt(stats["sheets_b"]),
        help="Из «Выгружено листов»: листы, чья последняя ревизия "
             "за последние 7 дней имеет статус B (к сдаче).",
    )
    c7.metric(
        "🟢 Листов A", _fmt(stats["sheets_a"]),
        help="Из «Выгружено листов»: листы, чья последняя ревизия "
             "за последние 7 дней имеет статус A (утверждён).",
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
    """Помесячно «Выполнено» (fix_date) с учётом фильтров.

    Патч 17: исключены замечания к листам A и аннулированным —
    как в Сводке, где doc_status нормализуется .strip().upper()
    (COALESCE(UPPER(TRIM(d.status)), '') NOT IN ('A',
    'АННУЛИРОВАНО')). «Выполнено» в KPI сходится с «со статусом
    Выполнено» в Сводке («Замечания на стороне Заказчика»).
    """
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        date_from, date_to, "c.fix_date", marka=marka)
    q = f"""
        SELECT substr(c.fix_date, 1, 7) AS ym, COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.fix_date IS NOT NULL AND c.fix_date <> ''
          AND c.status = 'Выполнено'
          AND COALESCE(UPPER(TRIM(d.status)), '')
              NOT IN ('A', 'АННУЛИРОВАНО')
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

    Патч 17: добавлены sheets_b / sheets_a — из них листы со
    статусом B (к сдаче) и A (утверждён): уникальные листы,
    чья последняя ревизия датирована периодом (тот же дедуп
    unique_vitro_sheets, нормализация статуса .strip().upper()).
    """
    df = _load_document_rows()
    df = _filter_doc_frame(df, disciplines, sections, kits, block_codes)
    if df.empty:
        return {"kits": 0, "sheets": 0, "sheets_b": 0, "sheets_a": 0}

    lo = date_from.isoformat() if date_from else ""
    hi = date_to.isoformat() if date_to else ""

    # --- листы: уникальные (последняя ревизия), датированные периодом
    sheets = sheets_b = sheets_a = 0
    for r in unique_vitro_sheets(df.to_dict("records")):
        day = str(r.get("status_date") or "")[:10]
        if not day:
            continue
        if lo and day < lo:
            continue
        if hi and day > hi:
            continue
        sheets += 1
        # Патч 17: разбивка по статусам — нормализация как в Сводке
        sheet_status = str(r.get("status") or "").strip().upper()
        if sheet_status == "B":
            sheets_b += 1
        elif sheet_status == "A":
            sheets_a += 1

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

    return {"kits": kits, "sheets": sheets,
            "sheets_b": sheets_b, "sheets_a": sheets_a}


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

    Патч 17: добавлены «Листов со статусом B» и «Листов со статусом
    A» за выбранный период (doc_volume: sheets_b / sheets_a).
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
        "📤 Выдано замечаний", _fmt(total_issue),
        help="Сколько замечаний поступило от заказчика за выбранный период.",
    )
    c2.metric(
        "🟢 Выполнено", _fmt(total_our),
        help="Замечания, которые АТП ТЛП выполнил и направил "
             "заказчику на согласование (статус «Выполнено»). "
             "Заказчик их ещё не закрыл.\n\n"
             "Вместе с «Закрыто заказчиком» показывает все снятые "
             "замечания.",
    )
    c3.metric(
        "⚪ Закрыто заказчиком", _fmt(total_cust),
        help="Сколько замечаний заказчик формально закрыл за период.",
    )

    k1, k2, k3, k4 = st.columns(4)
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
    k3.metric(
        "🟡 Листов со статусом B", _fmt(doc_volume["sheets_b"]),
        help="Из «Выгружено листов»: уникальные листы, чья последняя "
             "ревизия датирована выбранным периодом и имеет статус "
             "B (к сдаче).",
    )
    k4.metric(
        "🟢 Листов со статусом A", _fmt(doc_volume["sheets_a"]),
        help="Из «Выгружено листов»: уникальные листы, чья последняя "
             "ревизия датирована выбранным периодом и имеет статус "
             "A (утверждён).",
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


@versioned_cache(ttl=3600)
def _load_remark_scope(disciplines: tuple, sections: tuple,
                       kits: tuple, block_codes: tuple,
                       marka: str = "") -> dict:
    """«Отработано» по всем замечаниям с учётом фильтров (патч 18).

    Формула (согласована с координатором, В1): Отработано =
    Замечания со статусом «Закрыто» + «Аннулировано» + Без статуса —
    по ВСЕМ замечаниям с учётом фильтров вкладки, БЕЗ ограничения
    выбранным периодом. Числители «Выдано / Выполнено / Закрыто
    заказчиком» считаются отдельно (_load_issue_stats /
    _load_our_answers_stats / _load_customer_closed_stats) и строго
    за выбранный период.

    Фильтры — те же, что во всех запросах вкладки
    (_build_filter_sql без дат, включая марку комплекта).
    """
    where, params = _build_filter_sql(
        disciplines, sections, kits, block_codes,
        None, None, "c.created", marka=marka)
    q = f"""
        SELECT
            COALESCE(SUM(CASE WHEN c.status = 'Закрыто'
                              THEN 1 ELSE 0 END), 0) AS closed,
            COALESCE(SUM(CASE WHEN c.status = 'Аннулировано'
                              THEN 1 ELSE 0 END), 0) AS annulled,
            COALESCE(SUM(CASE WHEN c.status IS NULL OR c.status = ''
                              THEN 1 ELSE 0 END), 0) AS no_status
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE 1=1 {where}
    """
    with get_conn() as conn:
        row = conn.execute(q, tuple(params)).fetchone()
    closed = int(row["closed"] or 0)
    annulled = int(row["annulled"] or 0)
    no_status = int(row["no_status"] or 0)
    return {"closed": closed, "annulled": annulled,
            "no_status": no_status,
            "worked": closed + annulled + no_status}


def _render_remark_donuts(remark_totals: dict | None,
                          remark_scope: dict | None = None):
    """Блок «Замечания за период: выполнение и закрытие» — стиль
    Сводки (патч 18).

    Слева бублик «Объём замечаний» (В2): числитель — «Выдано за
    период», сегменты = Выдано за период / Отработано. «Отработано»
    (В1) — Закрыто + Аннулировано + Без статуса по всем замечаниям
    с учётом фильтров, без ограничения периодом (_load_remark_scope).
    Справа — список с маркерами, как в Сводке: «Выполнено» и
    «Закрыто заказчиком» — строго за выбранный период, «Отработано
    всего» — за всё время по фильтрам; под списком — строка
    арифметики «Отработано».

    Патч 17: цвета как в Сводке — «Выполнено» зелёный #2ca02c,
    «Закрыто заказчиком» серый #9E9E9E.
    """
    issued_total = int((remark_totals or {}).get("issued") or 0)
    worked = int((remark_scope or {}).get("worked") or 0)
    if issued_total <= 0 and worked <= 0:
        return

    st.markdown("#### Замечания за период: выполнение и закрытие")
    st.caption(
        "Бублик: «Выдано за период» — замечания заказчика, созданные "
        "за выбранный период (с учётом фильтров); «Отработано» — "
        "закрытые, аннулированные и без статуса по всем замечаниям "
        "с учётом фильтров (без ограничения периодом). «Выполнено» "
        "и «Закрыто заказчиком» — строго за выбранный период."
    )

    left, right = st.columns([2, 3])

    our_total = int((remark_totals or {}).get("our_answers") or 0)
    cust_total = int((remark_totals or {}).get("cust_closed") or 0)
    remaining = max(0, issued_total - cust_total - our_total)

    with left:
        st.caption(
            f"Выдано за период: **{_fmt(issued_total)}** · "
            f"Закрыто заказчиком: **{_fmt(cust_total)}** · "
            f"Выполнено (ждёт закрытия): **{_fmt(our_total)}**"
        )
        if issued_total > 0:
            donut = pd.DataFrame([
                {"Категория": "Закрыто заказчиком",
                 "Значение": cust_total},
                {"Категория": "Выполнено (ждёт закрытия)",
                 "Значение": our_total},
                {"Категория": "Осталось (в работе)",
                 "Значение": remaining},
            ])
            donut = donut[donut["Значение"] > 0]
            fig = px.pie(
                donut,
                names="Категория",
                values="Значение",
                hole=0.45,
                color="Категория",
                color_discrete_map={
                    "Закрыто заказчиком": "#9E9E9E",
                    "Выполнено (ждёт закрытия)": "#2ca02c",
                    "Осталось (в работе)": "#FFA726",
                },
            )
            fig.update_traces(
                textposition="inside",
                textinfo="value",
                textfont_size=11,
            )
            fig.update_layout(
                showlegend=False,
                margin=dict(l=5, r=5, t=5, b=5),
                height=200,
            )
            st.plotly_chart(fig, width='stretch',
                            key="dyn_donut_remarks")
        else:
            st.caption("За выбранный период замечания не выдавались.")

    with right:
        st.metric(
            "📤 Выдано за период", _fmt(issued_total),
            help="Замечания заказчика, созданные за выбранный период "
                 "(с учётом фильтров).",
        )
        st.metric(
            "🟢 Выполнено за период",
            _fmt(int((remark_totals or {}).get("our_answers") or 0)),
            help="Замечания, которые АТП ТЛП выполнил и направил "
                 "заказчику на согласование (статус «Выполнено») "
                 "за выбранный период. Замечания к листам A и "
                 "аннулированным не считаются.",
        )
        st.metric(
            "⚪ Закрыто заказчиком за период",
            _fmt(int((remark_totals or {}).get("cust_closed") or 0)),
            help="Замечания, формально закрытые заказчиком "
                 "за выбранный период.",
        )
        st.metric(
            "⚫ Отработано всего", _fmt(worked),
            help="Закрыто + Аннулировано + Без статуса по всем "
                 "замечаниям с учётом фильтров — без ограничения "
                 "выбранным периодом.",
        )
        if worked > 0:
            closed = int((remark_scope or {}).get("closed") or 0)
            annulled = int((remark_scope or {}).get("annulled") or 0)
            no_status = int((remark_scope or {}).get("no_status") or 0)
            st.caption(
                f"Арифметика: Отработано {_fmt(worked)} = "
                f"Закрыто {_fmt(closed)} + Аннулировано "
                f"{_fmt(annulled)} + Без статуса {_fmt(no_status)}."
            )


def _render_progress_donuts(disciplines: tuple, sections: tuple,
                            kits: tuple, block_codes: tuple,
                            remark_totals: dict | None = None,
                            remark_scope: dict | None = None):
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
        _render_remark_donuts(remark_totals, remark_scope)
        return

    plan_kits_total = sum(v["kits"] for v in plan.values())
    plan_sheets_total = sum(v["sheets"] for v in plan.values())

    if plan_kits_total == 0 and plan_sheets_total == 0:
        st.info(
            "В Ведомости нет плановых значений для выбранной "
            "комбинации фильтров."
        )
        _render_remark_donuts(remark_totals, remark_scope)
        return

    totals = _load_doc_totals(disciplines, sections, kits, block_codes)

    col1, col2 = st.columns(2)

    with col1:
        fig, pct, status_text, is_over = _build_donut_figure(
            totals["kits"], plan_kits_total)
        st.plotly_chart(fig, width='stretch', key="dyn_donut_kits")
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
        st.plotly_chart(fig, width='stretch', key="dyn_donut_sheets")
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

    # ---- Бублики по замечаниям за период (патчи 14, 18) ----
    _render_remark_donuts(remark_totals, remark_scope)


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
            mode="lines", name="🟢 Выполнено",
            line=dict(color="#2ca02c", width=3),
        ))

    if "Закрыто заказчиком" in merged.columns:
        fig.add_trace(go.Scatter(
            x=merged["ym"], y=merged["Закрыто заказчиком"],
            mode="lines", name="⚪ Закрыто заказчиком",
            line=dict(color="#9E9E9E", width=3),
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
    st.plotly_chart(fig, width='stretch')


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
        "Объём выдачи, выполнение плана и потоки замечаний."
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
        remark_scope = _load_remark_scope(
            disc_t, sect_t, kit_t, block_codes, marka=marka_t)

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
                            remark_totals=remark_totals,
                            remark_scope=remark_scope)

    st.divider()

    _render_cumulative(issue, our_answers, cust_closed)
