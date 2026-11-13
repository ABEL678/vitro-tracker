# vitro/ui/plan.py
"""
📅 План выдачи комплектов — планирование выдачи РД Заказчику.

Формирует план выдачи ВСЕХ комплектов (загруженных в Витро и планируемых
к загрузке) на основании вводных данных:
  - «Ведомость основных комплектов» (Excel) — шифр, наименование, статус,
    срок готовности.
  - «Ведомости чертежей каждого комплекта» (Excel) — номер листа,
    наименование, плановая дата. Поддерживаются как шаблон, так и реальные
    регистры «Комплекты чертежей» (ШИФР / Тип чертежа / Номер листа /
    Название листа / Шифр листа, шапки комплектов, приложения без шифра,
    латинские двойники в шифрах).

Структура вкладки (сверху вниз):
  1. Фильтры — единый стандарт (как в «Сводке по проекту»).
  2. 📆 Период «с / по» — сразу под фильтрами; применяется к сводной
     таблице (по «Сроку готовности» комплекта) и к детальному плану
     по листам (по «Плановой дате» листа).
  3. 🎯 Бублики — сразу после фильтров и периода, ДО таблиц
     (патч 10): «Комплектов всего/план» и «Листов всего/план» по
     всему отфильтрованному отбору (без периода). Патч 17: стиль
     Сводки — слева бублик без легенды, справа список с маркерами
     и числами; загруженные листы разбиты по статусам A/B/C/Другие.
     «Листов всего» = max(листов по плану, УНИКАЛЬНЫХ листов в
     Витро) — листы комплектов без ведомостей не теряются; при
     превышении показывается «сверх плана».
  4. 📋 «Сводный план готовности комплектов и количества выданных
     листов» (патч 10 — переименована): группировка
     «Дисциплина → Раздел» строками-заголовками (как в Сводке),
     внутри групп — комплекты; колонки: шифр, наименование,
     дисциплина, раздел (патч 17), общий статус, листов в Витро,
     листов (план), срок готовности (редактируемый); внизу — строка
     «ИТОГО» с суммами.
  5. 🗓 Детальный план по листам комплекта — позиции plan_drawings:
     дисциплина, раздел, комплект, номер и ПОЛНЫЙ ШИФР листа (патч 12:
     «Шифр листа» = шифр комплекта + номер, например
     АТ-РД-СМИК-0-00-10), наименование, плановая дата, фактическая
     дата (из Витро) и статус выдачи («Выдан» — есть фактическая
     дата, «Планируется» — нет).
  6. 📥 Выгрузка в Excel — кнопка: лист «Листы»
     (таблица выше) + «Параметры выгрузки», с учётом фильтров
     и выбранного периода.
  7. 📥 Вводные данные — импорт ведомостей Excel (свёрнут),
     «⚠️ Замечания последней загрузки» и «📜 Журнал загрузок» —
     в самом низу вкладки (патч 17), чтобы не отодвигали рабочие
     бублики и таблицы.

Импорт (с патча 12): шифры комплектов перед записью в plan_kits
нормализуются и валидируются (_kit_code_of); чекбокс «🔄 Заменить
план» удаляет комплекты (и их чертежи), которых нет в загружаемой
ведомости, — старые записи не смешиваются с новыми (бублик
«Комплектов всего» больше не раздувается 541 вместо 471).
Бублик «Листов» считает «в Витро» тем же кодом дедупликации, что
и Сводка (с патча 13 — sheets.dedup_document_rows): файлы с пустым
sheet_number — отдельные листы (пример СМИК: 44, а не 33).

Патч 13:
  1. МУСОРНЫЕ КОМПЛЕКТЫ отсекаются и со стороны Витро: _build_overview
     пропускает шифры, не проходящие is_valid_kit_code («АТ-РД-КЖ1,2-С-П2.3.ВР»
     с запятыми/точками больше не попадают в таблицу и бублик как
     «Только в Витро»); _prune_plan_kits удаляет такой исторический
     мусор из plan_kits даже без «Заменить план».
  2. СЧЁТ ЛИСТОВ ОДИН НА ВСЮ ВКЛАДКУ: _vitro_sheet_counts считает
     через sheets.dedup_document_rows (тот же код, что и Сводка) —
     варианты написания шифра из Витро (латиница/пробелы) склеиваются,
     СМИК = 44, а не 33; _vitro_sheet_dates нормализует ключи.
  3. ЗАМЕЧАНИЯ ПАРСЕРА не пропадают: список хранится в
     st.session_state (блок «⚠️ Замечания последней загрузки» над
     вкладкой) и пишется в журнал (log) при сохранении.
  4. ШАПКИ ТХ/ЭС/АК: см. plan_xlsx.parse_drawings_workbook.
  5. «📜 ЖУРНАЛ ЗАГРУЗОК»: последние события plan_import/plan_clear
     из таблицы log (дата, строки, сообщение) — переживает F5.

Патч 17 (компоновка и бублики как в Сводке):
  1. «📥 Вводные данные», «⚠️ Замечания последней загрузки» и
     «📜 Журнал загрузок» перенесены в самый низ вкладки.
  2. Бублики «Комплектов и листов»: слева бублик, справа список
     с маркерами и числами (как в Сводке). Загруженные листы
     разбиты по статусам A/B/C/Другие — новый загрузчик
     _vitro_sheet_status_counts на том же дедупе
     sheets.unique_vitro_sheets, что и «Листов в Витро»:
     A + B + C + Другие = «Листов в Витро» по отбору.
  3. В «Сводный план готовности» добавлены колонки «Дисциплина»
     и «Раздел» — значения видны в каждой строке; группировка
     «Дисциплина → Раздел» строками-заголовками сохранена.

Данные плана хранятся в SQLite: plan_kits, plan_drawings (sqlite_db.py).
"""

from datetime import date, datetime

import io
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import (
    get_conn,
    load_plan_kits,
    load_plan_drawings,
    upsert_plan_kits,
    replace_plan_drawings_for_kits,
    update_plan_due_dates,
    clear_plan,
    log_event,
)
from vitro.sheets import dedup_document_rows, unique_vitro_sheets
from vitro._cache import versioned_cache
from vitro.ui._auth import ensure_user
from vitro.ui._filters import (
    _extract_2nd_block,
    _extract_4th_block,
    load_kit_options as _load_kit_options,
    render_standard_filters,
)
from vitro.plan_xlsx import (
    MIME_XLSX,
    build_plan_export,
    is_valid_kit_code,
    parse_drawings_workbook,
    parse_kits_workbook,
    _kit_code_of,
    _norm_complex_code,
)

# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
KIT_STATUS_OPTIONS = [
    "Загружен в Витро",
    "Загружается (частично)",
    "План (не загружен)",
    "Только в Витро",
]

KIT_STATUS_COLORS = {
    "Загружен в Витро":        "#2ca02c",
    "Загружается (частично)":  "#ffdd57",
    "План (не загружен)":      "#9467bd",
    "Только в Витро":          "#1f77b4",
}

# Цвета бублика «Листы» (патч 17; уточнены патчем 18 по В3):
# статусы A/B/C — как в Сводке (summary.py SHEET_COLORS);
# «Другие статусы» — фиолетовый #9467bd (как «Готовится к загрузке»
# в Сводке), «Осталось по плану» — серый #B0BEC5 (⚪). Сверх плана —
# не категория бублика: уже входит в A/B/C/Другие, показывается
# справочной плашкой ℹ️ под бубликом.
SHEET_STATUS_COLORS = {
    "Статус A — утверждён": "#2ca02c",
    "Статус B — к сдаче":   "#ffdd57",
    "Статус C — в работе":  "#ff7f0e",
    "Другие статусы":       "#9467bd",
    "Осталось по плану":    "#B0BEC5",
}

# Колонки сводной таблицы. Патч 17: «Дисциплина» и «Раздел» возвращены
# как обычные колонки (значение в каждой строке) — при этом остаются
# и заголовками групп.
OVERVIEW_COLUMNS = [
    "Дисциплина", "Раздел", "Шифр комплекта", "Наименование комплекта",
    "Общий статус", "Листов в Витро", "Листов (план)", "Срок готовности",
]

# Детальный план по листам комплекта — единственная детальная таблица
# (таблица А по комплектам удалена в патче 09: дублировала сводную).
# «Шифр листа» (патч 12): полный шифр = шифр комплекта + номер листа
# (например АТ-РД-СМИК-0-00 + 10 → АТ-РД-СМИК-0-00-10).
SHEET_PLAN_COLUMNS = [
    "Дисциплина", "Раздел", "Комплект(ы)", "Номер листа", "Шифр листа",
    "Наименование листа", "Плановая дата", "Фактическая дата",
    "Статус выдачи",
]

# Значения колонки «Статус выдачи» (таблица Б).
STATUS_ISSUED = "Выдан"          # фактическая дата в Витро есть
STATUS_PLANNED = "Планируется"   # фактической даты нет

# Служебные префиксы строк сводной таблицы (не комплекты — не сохраняются).
_GROUP_MARK = "📁"
_TOTAL_MARK = "ИТОГО"

MAX_DETAIL_ROWS = 2000  # строки детальных таблиц сверх лимита — в Excel


# ---------------------------------------------------------------------------
#  Данные Витро
# ---------------------------------------------------------------------------
@versioned_cache(600)
def _vitro_sheet_counts() -> dict[str, int]:
    """{шифр комплекта: количество УНИКАЛЬНЫХ листов в Витро}.

    Патч 14: считается через sheets.unique_vitro_sheets — единый
    стандарт всех вкладок (Сводка, Динамика, Экспорт, Управление):
      * ревизии/повторы листа с номером считаются ОДИН раз
        (ключ complex + sheet_number, последняя ревизия);
      * файлы с ПУСТЫМ sheet_number — самостоятельные листы
        (fallback по id);
      * complex НОРМАЛИЗУЕТСЯ (trim, регистр, латинские двойники):
        синк пишет complex как есть из папок SharePoint, поэтому
        один комплект мог храниться под несколькими написаниями —
        группировка делила его листы (пример СМИК: 33 вместо 44,
        фантомный дубль в «Только в Витро» раздувал бублик);
      * мусорные шифры (is_valid_kit_code) исключаются — как и раньше
        в _build_overview, теперь отсечение живёт в sheets.py.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, complex, sheet_number, status, status_date
            FROM documents
            WHERE complex IS NOT NULL
        """).fetchall()
    counts: dict[str, int] = {}
    for r in unique_vitro_sheets([dict(x) for x in rows]):
        code = str(r.get("complex") or "")
        counts[code] = counts.get(code, 0) + 1
    return counts


@versioned_cache(600)
def _vitro_sheet_status_counts() -> dict[str, dict[str, int]]:
    """{шифр комплекта: {"A": n, "B": n, "C": n, "other": n}} —
    разбивка УНИКАЛЬНЫХ листов в Витро по статусам (патч 17).

    Тот же дедуп sheets.unique_vitro_sheets, что и в
    _vitro_sheet_counts, поэтому A + B + C + other по комплектам
    отбора сходится с колонкой «Листов в Витро». Статус
    нормализуется .strip().upper() — как в Сводке; «other» — все
    не-A/B/C (И, на согласовании, готовится к загрузке,
    аннулированные, прочие).
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, complex, sheet_number, status, status_date
            FROM documents
            WHERE complex IS NOT NULL
        """).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for r in unique_vitro_sheets([dict(x) for x in rows]):
        code = str(r.get("complex") or "")
        status_raw = str(r.get("status") or "").strip().upper()
        bucket = status_raw if status_raw in ("A", "B", "C") else "other"
        per = counts.setdefault(code, {"A": 0, "B": 0, "C": 0, "other": 0})
        per[bucket] += 1
    return counts


@versioned_cache(600)
def _vitro_kit_meta() -> dict[str, dict]:
    """{шифр: {name, discipline, section}} — справочник комплектов Витро."""
    meta: dict[str, dict] = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT code, name, discipline FROM complexes"
        ).fetchall():
            meta[r["code"]] = {
                "name": r["name"] or "",
                "discipline": r["discipline"] or "",
                "section": "",
            }
        for r in conn.execute("""
            SELECT complex, discipline, section
            FROM documents WHERE complex IS NOT NULL
        """).fetchall():
            code = r["complex"]
            if code not in meta:
                meta[code] = {
                    "name": "",
                    "discipline": r["discipline"] or "",
                    "section": r["section"] or "",
                }
            else:
                if not meta[code]["discipline"]:
                    meta[code]["discipline"] = r["discipline"] or ""
                if not meta[code]["section"]:
                    meta[code]["section"] = r["section"] or ""
    return meta


@versioned_cache(600)
def _vitro_sheet_dates() -> dict[tuple[str, str], str]:
    """
    {(шифр комплекта, номер листа): дата статуса в Витро} — фактическая
    дата загрузки листа (MAX по ревизиям). Для колонки «Фактическая дата»
    таблицы Б.
    Патч 13: ключ шифра НОРМАЛИЗУЕТСЯ — план_drawings хранит уже
    нормализованные шифры, а documents.complex мог быть с пробелами/
    латиницей; без нормализации «Фактическая дата» не находилась и
    лист ошибочно считался «Планируется».
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT complex, sheet_number, MAX(status_date) AS d
            FROM documents
            WHERE complex IS NOT NULL
              AND TRIM(COALESCE(sheet_number, '')) != ''
              AND status_date IS NOT NULL
              AND TRIM(COALESCE(status_date, '')) != ''
            GROUP BY complex, sheet_number
        """).fetchall()
    return {
        (_norm_complex_code(r["complex"]),
         str(r["sheet_number"]).strip()): str(r["d"])
        for r in rows if r["d"]
    }


def _iso_to_date(v) -> date | None:
    """'YYYY-MM-DD[...]' / datetime.date → date; иначе None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        if v is not None and pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
#  Общая (сводная) таблица
# ---------------------------------------------------------------------------
def _build_overview(include_vitro_only: bool) -> pd.DataFrame:
    """
    Одна строка = один комплект: план (ведомости) + Витро.
    Общий статус вычисляется: Загружен в Витро / Загружается (частично) /
    План (не загружен) / Только в Витро.

    Патч 13: комплекты с шифрами, не проходящими is_valid_kit_code
    (мусор с запятыми/точками вида «АТ-РД-КЖ1,2-С-П2.3.ВР» и прочие
    не-шифры), НЕ попадают в таблицу и бублик, даже если пришли
    из Витро (статус «Только в Витро») или лежат в plan_kits с
    старых загрузок. Без фильтра «Комплектов всего» оставалось 541
    вместо реальных 421.
    """
    plan = load_plan_kits()
    drawings = load_plan_drawings()
    vitro_counts = _vitro_sheet_counts()
    plan_qty = {}
    if not drawings.empty:
        plan_qty = drawings.groupby("kit_code")["qty"].sum().to_dict()

    rows = []
    if not plan.empty:
        for _, r in plan.iterrows():
            code = r["code"]
            if not is_valid_kit_code(code):
                continue  # исторический мусор в plan_kits (патч 13)
            in_vitro = code in vitro_counts
            sheets_vitro = vitro_counts.get(code, 0)
            sheets_plan = int(plan_qty.get(code, 0))
            if in_vitro and sheets_plan > 0 and sheets_vitro < sheets_plan:
                status = "Загружается (частично)"
            elif in_vitro:
                status = "Загружен в Витро"
            else:
                status = "План (не загружен)"
            rows.append({
                "code": code,
                "name": r["name"] or "",
                "discipline": r["discipline"] or "",
                "section": r["section"] or "",
                "plan_status": r["plan_status"] or "",
                "due_date": r["due_date"] or None,
                "sheets_vitro": sheets_vitro,
                "sheets_plan": sheets_plan,
                "status": status,
                "in_vitro": in_vitro,
                "updated_at": r["updated_at"] or "",
                "updated_by": r["updated_by"] or "",
            })

    if include_vitro_only:
        meta = _vitro_kit_meta()
        known = set(r["code"] for r in rows)
        for code in sorted(vitro_counts):
            if code in known:
                continue
            if not is_valid_kit_code(code):
                continue  # мусорный шифр из папок Витро (патч 13)
            info = meta.get(code, {})
            rows.append({
                "code": code,
                "name": info.get("name", ""),
                "discipline": info.get("discipline", ""),
                "section": info.get("section", ""),
                "plan_status": "",
                "due_date": None,
                "sheets_vitro": vitro_counts[code],
                "sheets_plan": 0,
                "status": "Только в Витро",
                "in_vitro": True,
                "updated_at": "",
                "updated_by": "",
            })

    if not rows:
        return pd.DataFrame(columns=["code"])

    df = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    return df


def _overview_to_grouped_display(df: pd.DataFrame
                                 ) -> tuple[pd.DataFrame, set]:
    """
    Внутренний DataFrame → сводная таблица с группировкой
    «Дисциплина → Раздел» (как в Сводке по проекту):

        📁 01_АР · Подземная часть        (заголовок группы + суммы)
        АТ-РД-АР1-С-00   Архитектура …    (комплекты группы)
        …
        ИТОГО                             (суммы по всему отбору)

    Колонки — OVERVIEW_COLUMNS (с патча 17 включают «Дисциплину» и
    «Раздел»: они и заголовки групп, и значения в строках).
    Срок готовности — тип date (для DateColumn).

    Возвращает (display_df, valid_codes) — valid_codes нужен
    _save_due_dates, чтобы служебные строки (📁… / ИТОГО) не попадали
    в UPDATE.
    """
    if df.empty:
        return pd.DataFrame(columns=OVERVIEW_COLUMNS), set()

    def _sort_key(val: str) -> tuple[int, str]:
        v = str(val or "")
        return (1, "") if v == "" else (0, v)

    groups: list[tuple[str, str, pd.DataFrame]] = []
    for (disc, sect), sub in df.groupby(
            ["discipline", "section"], dropna=False, sort=True):
        groups.append((str(disc or ""), str(sect or ""), sub))
    groups.sort(key=lambda g: (_sort_key(g[0]), _sort_key(g[1])))

    out_rows = []
    valid_codes: set = set()
    sum_vitro = sum_plan = 0

    for disc, sect, sub in groups:
        disc_label = disc if disc else "— без дисциплины"
        sect_label = sect if sect else "— без раздела"
        out_rows.append({
            "Шифр комплекта":
                f"{_GROUP_MARK} {disc_label} · {sect_label}",
            "Наименование комплекта": f"комплектов: {len(sub)}",
            "Дисциплина": disc_label,
            "Раздел": sect_label,
            "Общий статус": "",
            "Листов в Витро": int(sub["sheets_vitro"].astype(int).sum()),
            "Листов (план)": int(sub["sheets_plan"].astype(int).sum()),
            "Срок готовности": None,
        })
        for _, r in sub.iterrows():
            valid_codes.add(r["code"])
            sum_vitro += int(r["sheets_vitro"])
            sum_plan += int(r["sheets_plan"])
            out_rows.append({
                "Шифр комплекта": r["code"],
                "Наименование комплекта": r["name"] or "",
                "Дисциплина": str(r["discipline"] or ""),
                "Раздел": str(r["section"] or ""),
                "Общий статус": r["status"] or "",
                "Листов в Витро": int(r["sheets_vitro"]),
                "Листов (план)": int(r["sheets_plan"]),
                "Срок готовности": _iso_to_date(r["due_date"]),
            })

    out_rows.append({
        "Шифр комплекта": _TOTAL_MARK,
        "Наименование комплекта": f"всего комплектов: {len(valid_codes)}",
        "Дисциплина": "",
        "Раздел": "",
        "Общий статус": "",
        "Листов в Витро": sum_vitro,
        "Листов (план)": sum_plan,
        "Срок готовности": None,
    })

    return pd.DataFrame(out_rows, columns=OVERVIEW_COLUMNS), valid_codes


def _norm_editor_date(v):
    """Нормализация даты из data_editor для сравнения:
    None / NaT / NaN → None, datetime → date."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date()
    return v


def _current_user() -> str:
    """Имя текущего пользователя (отдельная функция — для тестов)."""
    return st.session_state.get("user", "")


def _save_due_dates(display_before: pd.DataFrame,
                    display_after: pd.DataFrame,
                    valid_codes: set) -> int:
    """Разница сроков готовности → UPDATE в БД. Строки-заголовки групп
    (📁…) и «ИТОГО» игнорируются. None/NaT считаются одинаковыми —
    иначе незатронутые пустые ячейки дают ложные отличия и вкладка
    зацикливается на сохранении (см. патч 07). Возвращает число строк
    (или -1, если требуется имя пользователя)."""
    changes: dict[str, str] = {}
    for i in range(len(display_after)):
        code = display_after.iloc[i]["Шифр комплекта"]
        if code not in valid_codes:
            continue  # служебная строка (группа / ИТОГО) — не комплект
        old = _norm_editor_date(display_before.iloc[i]["Срок готовности"])
        new = _norm_editor_date(display_after.iloc[i]["Срок готовности"])
        if old != new:
            changes[code] = None if new is None else new.isoformat()
    if not changes:
        return 0
    user = _current_user()
    if not user or user == "инженер":
        return -1  # требуется имя пользователя
    return update_plan_due_dates(changes, user)


# ---------------------------------------------------------------------------
#  Экспорт сводного плана в Excel
# ---------------------------------------------------------------------------
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
_TOTAL_FILL = PatternFill("solid", fgColor="D9D9D9")
_TOTAL_FONT = Font(bold=True, size=11)
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP = Alignment(wrap_text=True, vertical="top")


@st.cache_data(show_spinner=False)
def _export_overview_excel(df: pd.DataFrame) -> bytes:
    """Сводный план готовности → Excel с форматированием."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Сводный план"

    headers = list(df.columns)
    n_cols = len(headers)

    # Заголовки
    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=j, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    # Данные
    for i, row in enumerate(df.itertuples(index=False), start=2):
        for j, v in enumerate(row, start=1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.border = _BORDER
            cell.alignment = _WRAP
            # Подсветка служебных строк (группы и ИТОГО)
            first_val = str(v).strip()
            if first_val.startswith(_GROUP_MARK) or first_val == _TOTAL_MARK:
                cell.font = _TOTAL_FONT
                cell.fill = _TOTAL_FILL

    # Ширина колонок: под содержимое (макс 60)
    for j, h in enumerate(headers, start=1):
        col_letter = get_column_letter(j)
        max_w = max(
            len(str(h)) + 2,
            max(
                (len(str(ws.cell(row=i, column=j).value or ""))
                 for i in range(2, ws.max_row + 1)),
                default=10,
            ),
        )
        ws.column_dimensions[col_letter].width = min(max_w, 60)

    # Заморозка заголовка и автофильтр
    ws.freeze_panes = "A2"
    if ws.max_row >= 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{ws.max_row}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_overview_table(overview_filtered: pd.DataFrame,
                           date_from=None, date_to=None) -> None:
    """Секция 4. «Сводный план готовности комплектов и количества
    выданных листов»: группировка по «Дисциплина → Раздел», строка
    «ИТОГО», редактируемый срок. Период «с/по» фильтрует комплекты
    по «Сроку готовности» (патчи 09–10)."""
    st.markdown(
        "##### 📋 Сводный план готовности комплектов "
        "и количества выданных листов")

    overview_period = _apply_period_to_overview(
        overview_filtered, date_from, date_to)

    if overview_period.empty:
        if overview_filtered.empty:
            st.info(
                "Нет комплектов по выбранным фильтрам. Разверните блок "
                "«📥 Вводные данные» и загрузите ведомости Excel."
            )
        else:
            st.info(
                "Нет комплектов со сроком готовности в выбранном периоде "
                f"({_period_label(date_from, date_to)}). Комплекты без "
                "срока при активном периоде скрываются."
            )
        return

    display_before, valid_codes = _overview_to_grouped_display(
        overview_period)

    edited = st.data_editor(
        display_before,
        width='stretch',
        hide_index=True,
        num_rows="fixed",
        height=min(max(len(display_before) * 42 + 40, 300), 700),
        column_config={
            "Шифр комплекта": st.column_config.TextColumn(width="medium"),
            "Наименование комплекта": st.column_config.TextColumn(
                width="large"),
            "Дисциплина": st.column_config.TextColumn(width="small"),
            "Раздел": st.column_config.TextColumn(width="small"),
            "Общий статус": st.column_config.TextColumn(width="small"),
            "Листов в Витро": st.column_config.NumberColumn(width="small"),
            "Листов (план)": st.column_config.NumberColumn(width="small"),
            "Срок готовности": st.column_config.DateColumn(
                "Срок готовности",
                format="DD.MM.YYYY",
                width="small",
                help="Редактируется в строках комплектов — изменения "
                     "сохраняются автоматически.",
            ),
        },
        disabled=[
            "Шифр комплекта", "Наименование комплекта",
            "Дисциплина", "Раздел", "Общий статус",
            "Листов в Витро", "Листов (план)",
        ],
        key="plan_overview_editor",
    )

    n = _save_due_dates(display_before, edited, valid_codes)
    if n > 0:
        # БЕЗ st.rerun(): повторный реран сразу после сохранения вызывал
        # зацикливание вкладки (см. патч 07). Значения уже отображаются
        # в редакторе, БД обновлена — остальное подтянется при следующем
        # естественном реране.
        st.toast(f"Срок готовности обновлён: {n} компл.")
    elif n == -1:
        st.warning(
            "✏️ Изменены сроки готовности. Введите имя, чтобы сохранить: "
            "нажмите кнопку ниже."
        )
        if st.button("💾 Сохранить сроки готовности", type="primary",
                     key="plan_save_due_btn"):
            user = ensure_user()
            if user:
                changed = _save_due_dates(display_before, edited, valid_codes)
                if changed > 0:
                    st.toast(f"Срок готовности сохранён: {changed} компл.")
                st.rerun()

    st.caption(
        f"Комплектов: **{len(overview_period)}** · "
        f"листов в Витро: **{int(overview_period['sheets_vitro'].sum())}** · "
        f"листов (план): **{int(overview_period['sheets_plan'].sum())}**. "
        "Группировка: Дисциплина → Раздел (строки «📁…» — заголовки групп, "
        "«ИТОГО» — суммы по отбору). Срок готовности редактируется "
        "в строках комплектов."
    )
    if date_from or date_to:
        st.caption(
            f"Учтён период: **{_period_label(date_from, date_to)}** "
            "(по «Сроку готовности»)."
        )

    # ---- Кнопка выгрузки в Excel ----
    excel_bytes = _export_overview_excel(display_before)
    st.download_button(
        "📥 Выгрузить в Excel: Сводный план",
        data=excel_bytes,
        file_name=f"Сводный_план_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width='content',
        key="plan_overview_export",
        help="Выгрузка сводного плана готовности в Excel с форматированием.",
    )


# ---------------------------------------------------------------------------
#  Фильтры (единый стандарт + статус комплекта)
# ---------------------------------------------------------------------------
def _plan_kit_options_fn(disc, sect, block_codes) -> dict[str, str]:
    """
    Комплекты для каскада: справочник Витро + комплекты из плана
    (их ещё нет в Витро, но они есть в ведомостях).
    """
    base = _load_kit_options(disc, sect, block_codes)
    plan = load_plan_kits()
    if plan.empty:
        return base
    for _, r in plan.iterrows():
        code = r["code"]
        if disc and (r["discipline"] or "") not in disc:
            continue
        if sect and (r["section"] or "") not in sect:
            continue
        if block_codes and _extract_4th_block(code) not in block_codes:
            continue
        if code not in base:
            base[code] = f"{code} — {r['name']}" if r["name"] else code
    return dict(sorted(base.items()))


def _render_filters() -> dict:
    """
    Единый стандарт фильтров (как в Сводке) + фильтр «Статус комплекта»
    и «Только с датами» в 3-м ряду.
    """
    def _plan_extra_row():
        c1, c2 = st.columns(2)
        with c1:
            kit_status = st.multiselect(
                "Статус комплекта",
                options=KIT_STATUS_OPTIONS,
                placeholder="Все",
                key="plan_kit_status",
                help="«План (не загружен)» — комплект ещё не загружен "
                     "в Витро. «Только в Витро» — нет в ведомостях.",
            )
        with c2:
            only_dated = st.checkbox(
                "Только позиции со сроками",
                value=False,
                key="plan_only_dated",
                help="Скрыть позиции без срока готовности и без плановой даты.",
            )
        return {
            "kit_status": tuple(kit_status),
            "only_dated": bool(only_dated),
        }

    return render_standard_filters(
        key_prefix="plan_",
        expander_label="Фильтры",
        expanded=True,
        show_remark_status=False,
        show_sheet_status=False,
        kit_options_fn=_plan_kit_options_fn,
        extra_row=_plan_extra_row,
    )


def _render_period_block() -> tuple[date | None, date | None]:
    """
    Секция 2. Период «с / по» — сразу под блоком фильтров, до общей
    таблицы. Применяется к сводной таблице (по «Сроку готовности»)
    и к детальному плану по листам (по «Плановой дате»).
    """
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        date_from = st.date_input(
            "Период: с",
            value=None,
            min_value=date(2020, 1, 1),
            max_value=date(2100, 12, 31),
            key="plan_date_from",
            help="Период применяется к сводной таблице и к плану по "
                 "листам: у комплекта — срок готовности, у листа — "
                 "плановая дата (или срок комплекта, если у листа даты "
                 "нет). Бублики от периода не зависят.",
        )
    with c2:
        date_to = st.date_input(
            "по",
            value=None,
            min_value=date(2020, 1, 1),
            max_value=date(2100, 12, 31),
            key="plan_date_to",
        )
    with c3:
        st.caption(
            "Применяется к сводной таблице (по «Сроку готовности» "
            "комплекта) и к плану по листам (по «Плановой дате» листа). "
            "Пустые даты при активном периоде скрываются. Бублики "
            "от периода не зависят."
        )
    return date_from, date_to


def _apply_overview_filters(overview: pd.DataFrame, flt: dict) -> pd.DataFrame:
    """Стандартные фильтры + статус комплекта → к общей таблице."""
    if overview.empty:
        return overview
    out = overview
    if flt["disc"]:
        out = out[out["discipline"].isin(flt["disc"])]
    if flt["section"]:
        out = out[out["section"].isin(flt["section"])]
    if flt["kits"]:
        out = out[out["code"].isin(flt["kits"])]
    if flt["block_codes"]:
        blocks = out["code"].apply(_extract_4th_block)
        out = out[blocks.isin(flt["block_codes"])]
    if flt["marka"]:
        m = flt["marka"].strip().upper()
        if m:
            out = out[out["code"].apply(_extract_2nd_block) == m]
    kit_status = flt["extra"].get("kit_status", ())
    if kit_status:
        out = out[out["status"].isin(kit_status)]
    return out


def _apply_period_to_overview(df: pd.DataFrame,
                              date_from, date_to) -> pd.DataFrame:
    """Период «с/по» → фильтр сводной таблицы по «Сроку готовности».

    При активном периоде остаются только комплекты, чей срок попадает
    в диапазон (границы включительно); комплекты без срока скрываются —
    иначе «ИТОГО» считал бы позиции вне периода (патч 09).
    """
    if df.empty or not (date_from or date_to):
        return df
    out = df[df["due_date"].notna()]
    if out.empty:
        return out
    iso = out["due_date"].astype(str).str.slice(0, 10)
    if date_from:
        out = out[iso >= date_from.isoformat()]
    if date_to:
        out = out[iso <= date_to.isoformat()]
    return out


def _plan_totals(overview_filtered: pd.DataFrame) -> dict:
    """Сводные числа для бубликов — по всему отфильтрованному отбору
    (без учёта периода).

    Листов всего = max(сумма листов по плану, сумма листов в Витро):
    листы комплектов, которых нет в ведомостях чертежей («Только в
    Витро»), и листы сверх плана (повторы, извещения) не теряются —
    иначе бублик показывал меньше, чем Сводка по проекту (патч 09:
    было 9 142 вместо 12 880).

    Возвращает словарь:
      kits_total / kits_loaded / kits_plan — комплектов всего, в Витро,
                                             план к загрузке;
      sheets_total / sheets_vitro / sheets_plan — листов всего (max),
                                             в Витро, по плану;
      sheets_excess — листов сверх плана (Витро − план, ≥ 0);
      sheets_remaining — осталось по плану (план − Витро, ≥ 0);
      sheets_done — выдано (total − remaining; при преимуществе плана
                    совпадает с «в Витро»)."""
    if overview_filtered.empty:
        return {k: 0 for k in (
            "kits_total", "kits_loaded", "kits_plan", "sheets_total",
            "sheets_vitro", "sheets_plan", "sheets_excess",
            "sheets_remaining", "sheets_done")}
    n_total = len(overview_filtered)
    n_loaded = int(overview_filtered["in_vitro"].astype(bool).sum())
    sheets_vitro = int(overview_filtered["sheets_vitro"].astype(int).sum())
    sheets_plan = int(overview_filtered["sheets_plan"].astype(int).sum())
    sheets_total = max(sheets_vitro, sheets_plan)
    sheets_excess = max(0, sheets_vitro - sheets_plan)
    sheets_remaining = max(0, sheets_plan - sheets_vitro)
    return {
        "kits_total": n_total,
        "kits_loaded": n_loaded,
        "kits_plan": n_total - n_loaded,
        "sheets_total": sheets_total,
        "sheets_vitro": sheets_vitro,
        "sheets_plan": sheets_plan,
        "sheets_excess": sheets_excess,
        "sheets_remaining": sheets_remaining,
        "sheets_done": sheets_total - sheets_remaining,
    }


# ---------------------------------------------------------------------------
#  Бублики
# ---------------------------------------------------------------------------
def _donut(fig_data: pd.DataFrame, names_col: str, values_col: str,
           key: str, center_total: str,
           color_map: dict | None = None) -> None:
    """Бублик в стиле Сводки: без легенды, проценты внутри.

    Патч 17: легенда убрана — подписи сегментов даёт список
    справа от бублика; цветовая карта задаётся параметром
    color_map (по умолчанию — статусы комплектов).
    """
    if fig_data.empty or int(fig_data[values_col].sum()) == 0:
        st.caption("Нет данных для диаграммы.")
        return
    fig = px.pie(
        fig_data,
        names=names_col,
        values=values_col,
        hole=0.45,
        color=names_col,
        color_discrete_map=color_map or KIT_STATUS_COLORS,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent",
        textfont_size=11,
    )
    fig.update_layout(
        showlegend=False,
        margin=dict(l=5, r=5, t=5, b=5),
        height=200,
        annotations=[dict(
            text=center_total,
            showarrow=False,
            font=dict(size=16, color="#1F4E78"),
            x=0.5, y=0.5,
        )],
    )
    st.plotly_chart(fig, width='stretch', key=key)


def _fmt_num(n) -> str:
    """Число с пробелами-разделителями тысяч: 12 880."""
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _render_donuts(overview_filtered: pd.DataFrame) -> tuple[int, int]:
    """Секция 3. Бублики «Комплектов всего/план» и «Листов всего/план»
    — сразу после фильтров и периода, до таблиц (патч 10).

    Патч 17: стиль Сводки — слева бублик без легенды, справа список
    с маркерами и числами. Загруженные листы разбиты по статусам
    A/B/C/Другие (_vitro_sheet_status_counts — тот же дедуп
    sheets.unique_vitro_sheets, что и у «Листов в Витро», поэтому
    A + B + C + Другие = «Листов в Витро» по отбору). Числа — из
    _plan_totals: по всему отфильтрованному отбору (без периода),
    включая комплекты «Только в Витро». «Листов всего» = max(план,
    УНИКАЛЬНЫХ листов в Витро) — см. патчи 09–10.

    Патч 18 (В3): «Другие статусы» — фиолетовый #9467bd (🟣),
    «Осталось по плану» — серый #B0BEC5 (⚪). «Сверх плана» не
    категория бублика и не метрика списка: это часть A/B/C/Других,
    выводится только справочной плашкой ℹ️ под бубликом.
    """
    st.markdown("##### 🎯 Комплектов и листов: всего / план")

    totals = _plan_totals(overview_filtered)
    if totals["kits_total"] == 0:
        st.info("Нет комплектов по выбранным фильтрам.")
        return (0, 0)

    # ---- КОМПЛЕКТЫ: все отфильтрованные, включая «Только в Витро» ----
    n_total = totals["kits_total"]
    n_loaded = totals["kits_loaded"]
    n_plan = totals["kits_plan"]

    kits_data = pd.DataFrame([
        {"Группа": "Загружено в Витро", "Количество": n_loaded},
        {"Группа": "План к загрузке", "Количество": n_plan},
    ])
    kits_data = kits_data[kits_data["Количество"] > 0]

    st.markdown("###### Комплекты")
    k_left, k_right = st.columns([2, 3])
    with k_left:
        st.caption(f"Комплектов всего: **{n_total}**")
        _donut(kits_data, "Группа", "Количество", "plan_pie_kits",
               center_total=f"{n_total}")
    with k_right:
        st.metric(
            "🟢 Загружено в Витро", _fmt_num(n_loaded),
            help="Комплекты, у которых есть хотя бы один лист в Витро "
                 "(включая «Только в Витро» — отсутствующие в "
                 "ведомостях).",
        )
        st.metric(
            "🟣 План к загрузке", _fmt_num(n_plan),
            help="Комплекты из ведомостей, которых ещё нет в Витро.",
        )
        st.caption(
            f"Арифметика: {_fmt_num(n_loaded)} + {_fmt_num(n_plan)} = "
            f"**{_fmt_num(n_total)}**."
        )

    # ---- ЛИСТЫ: разбивка загруженных по статусам A/B/C/Другие ----
    # «всего» = max(план ведомостей, фактически в Витро): листы
    # комплектов без ведомостей и превышения плана (повторы,
    # извещения) не теряются — при преимуществе Витро показываем
    # «сверх плана», при преимуществе плана — «осталось по плану».
    n_a = n_b = n_c = n_other = 0
    if not overview_filtered.empty:
        status_counts = _vitro_sheet_status_counts()
        for code in overview_filtered["code"]:
            per = status_counts.get(code)
            if per:
                n_a += per["A"]
                n_b += per["B"]
                n_c += per["C"]
                n_other += per["other"]

    sheets_total = totals["sheets_total"]
    sheets_vitro = totals["sheets_vitro"]
    sheets_plan = totals["sheets_plan"]
    sheets_remaining = totals["sheets_remaining"]
    sheets_excess = totals["sheets_excess"]

    sheets_data = pd.DataFrame([
        {"Группа": "Статус A — утверждён", "Количество": n_a},
        {"Группа": "Статус B — к сдаче",   "Количество": n_b},
        {"Группа": "Статус C — в работе",  "Количество": n_c},
        {"Группа": "Другие статусы",       "Количество": n_other},
        {"Группа": "Осталось по плану",    "Количество": sheets_remaining},
    ])
    sheets_data = sheets_data[sheets_data["Количество"] > 0]

    st.markdown("###### Листы")
    s_left, s_right = st.columns([2, 3])
    with s_left:
        cap = (f"Листов всего: **{sheets_total}** · "
               f"в Витро: **{sheets_vitro}** · "
               f"в плане: **{sheets_plan}**")
        if sheets_excess:
            cap += f" · сверх плана: **{sheets_excess}**"
        st.caption(cap)
        _donut(sheets_data, "Группа", "Количество", "plan_pie_sheets",
               center_total=f"{sheets_total}",
               color_map=SHEET_STATUS_COLORS)
    with s_right:
        st.metric(
            "🟢 Статус A — утверждён", _fmt_num(n_a),
            help="Уникальные листы в Витро со статусом A "
                 "(последняя ревизия, дедуп как в Сводке).",
        )
        st.metric(
            "🟡 Статус B — к сдаче", _fmt_num(n_b),
            help="Уникальные листы в Витро со статусом B.",
        )
        st.metric(
            "🟠 Статус C — в работе", _fmt_num(n_c),
            help="Уникальные листы в Витро со статусом C.",
        )
        st.metric(
            "🟣 Другие статусы", _fmt_num(n_other),
            help="Статус И, «на согласовании Заказчика», «готовится "
                 "к загрузке», аннулированные и прочие листы в Витро.",
        )
        if sheets_remaining > 0:
            st.metric(
                "⚪ Осталось по плану", _fmt_num(sheets_remaining),
                help="План ведомостей минус листы в Витро.",
            )

    if sheets_excess:
        st.caption(
            f"ℹ️ В Витро на **{sheets_excess}** лист(ов) больше планового "
            "количества (комплекты без ведомостей чертежей, повторы, "
            "извещения, приложения) — они включены в «Листов всего», "
            "чтобы сумма совпадала с Сводкой по проекту."
        )
    return (n_total, sheets_total)


# ---------------------------------------------------------------------------
#  Таблица Б. Детальный план по листам комплекта
# ---------------------------------------------------------------------------
def _full_sheet_codes(df: pd.DataFrame) -> pd.Series:
    """Колонка «Шифр листа» (патч 12): полный шифр листа = шифр
    комплекта + номер листа: «АТ-РД-СМИК-0-00» + «10» →
    «АТ-РД-СМИК-0-00-10» (номер берётся из полного шифра в реальных
    регистрах, поэтому склейка восстанавливает его точно). Если у
    позиции номера нет — показывается шифр комплекта."""
    kit = df["Комплект(ы)"].fillna("").astype(str).str.strip()
    no = df["sheet_no"].fillna("").astype(str).str.strip()
    codes = kit.where(no == "", kit + "-" + no)
    return codes


def _build_sheet_plan_table(overview_filtered: pd.DataFrame,
                            flt: dict,
                            date_from, date_to) -> pd.DataFrame:
    """
    Таблица Б: позиции листов из Ведомости (plan_drawings) + из Витро
    (documents). Объединяет:
      - Листы из Ведомости: плановая дата, наименование из ведомости;
      - Листы из Витро (без Ведомости): статус «Выдан», наименование
        из Витро, пометка «Нет в ведомости».
    С патча 12 есть колонка «Шифр листа» — полный шифр листа
    (шифр комплекта + номер листа).
    """
    if overview_filtered.empty:
        return pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

    drawings = load_plan_drawings()
    ov = overview_filtered[[
        "code", "status", "due_date", "discipline", "section",
    ]].rename(columns={"discipline": "kit_discipline",
                       "section": "kit_section"})

    # ---- 1. Листы из Ведомости (plan_drawings) ----
    if not drawings.empty:
        df_dwg = drawings.merge(
            ov, left_on="kit_code", right_on="code", how="inner",
        )
        df_dwg = df_dwg.rename(columns={
            "kit_code": "Комплект(ы)",
            "title": "Наименование листа",
            "discipline": "dwg_discipline",
            "section": "dwg_section",
        })
        # Добавляем колонку name для единообразия с df_vitro
        df_dwg["name"] = df_dwg["Наименование листа"]
        df_dwg["sheet_number"] = df_dwg.get("sheet_no", "")
        df_dwg["_source"] = "dwg"
        df_dwg = df_dwg.drop(columns=["code"])
    else:
        df_dwg = pd.DataFrame()

    # ---- 2. Листы из Витро (documents), которых нет в Ведомости ----
    if not df_dwg.empty:
        dwg_codes = set(df_dwg["Комплект(ы)"])
    else:
        dwg_codes = set()

    # Загружаем уникальные листы из Витро (dedup по complex + sheet_number)
    with get_conn() as conn:
        doc_rows = conn.execute("""
            SELECT complex, sheet_number, status_date, name
            FROM documents
            WHERE complex IS NOT NULL
              AND TRIM(COALESCE(sheet_number, '')) != ''
        """).fetchall()

    vitro_sheets = dedup_document_rows([dict(r) for r in doc_rows])
    if vitro_sheets:
        df_vitro = pd.DataFrame(vitro_sheets)
        df_vitro["Комплект(ы)"] = df_vitro["complex"].apply(_norm_complex_code)
        df_vitro["_source"] = "vitro"
        df_vitro["dwg_discipline"] = ""
        df_vitro["dwg_section"] = ""
        # Отфильтровываем комплекты, которых нет в overview_filtered
        if not df_vitro.empty:
            valid_codes = set(overview_filtered["code"])
            df_vitro = df_vitro[df_vitro["Комплект(ы)"].isin(valid_codes)]
            # Отфильтровываем те, что уже есть в Ведомости
            df_vitro = df_vitro[~df_vitro["Комплект(ы)"].isin(dwg_codes)]
    else:
        df_vitro = pd.DataFrame()

    # ---- 3. Приводим обе таблицы к общему набору колонок ----
    common_cols = [
        "Комплект(ы)", "Наименование листа", "dwg_discipline",
        "dwg_section", "_source", "name", "sheet_number",
        "sheet_no", "planned_date", "discipline", "section",
    ]
    for col in common_cols:
        if col not in df_dwg.columns:
            df_dwg[col] = "" if col != "planned_date" else None
        if col not in df_vitro.columns:
            df_vitro[col] = "" if col != "planned_date" else None

    # ---- 4. Объединяем Ведомость + Витро ----
    if df_dwg.empty and df_vitro.empty:
        return pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

    if df_dwg.empty:
        df = df_vitro
    elif df_vitro.empty:
        df = df_dwg
    else:
        df = pd.concat([df_dwg, df_vitro], ignore_index=True)

    # Для листов из Витро (без Ведомости) — заполняем поля
    if "_source" in df.columns:
        is_vitro = df["_source"] == "vitro"
        # Безопасный доступ: используем .get() чтобы не падать, если колонки нет
        df.loc[is_vitro, "Наименование листа"] = (
            df.loc[is_vitro, "name"].fillna(""))
        # sheet_number может отсутствовать если df_dwg пустой
        if "sheet_number" in df.columns:
            df.loc[is_vitro, "sheet_no"] = (
                df.loc[is_vitro, "sheet_number"].fillna(""))
        else:
            df.loc[is_vitro, "sheet_no"] = ""
        df.loc[is_vitro, "planned_date"] = None
        df.loc[is_vitro, "discipline"] = ""
        df.loc[is_vitro, "section"] = ""
        # Добавляем отсутствующие колонки (из Ведомости)
        for col in ["sheet_no", "planned_date", "discipline", "section"]:
            if col not in df.columns:
                df[col] = ""

    # Дисциплина/раздел: значение строки листа, а если пусто — комплекта.
    row_disc = df["dwg_discipline"].fillna("")
    df["Дисциплина"] = row_disc.where(row_disc != "", df["kit_discipline"]
                                      .fillna(""))
    row_sect = df["dwg_section"].fillna("")
    df["Раздел"] = row_sect.where(row_sect != "", df["kit_section"].fillna(""))

    # ---- Стандартные фильтры ----
    if flt["disc"]:
        df = df[df["Дисциплина"].isin(flt["disc"])]
    if flt["section"]:
        df = df[df["Раздел"].isin(flt["section"])]
    if flt["kits"]:
        df = df[df["Комплект(ы)"].isin(flt["kits"])]
    if flt["block_codes"]:
        blocks = df["Комплект(ы)"].apply(_extract_4th_block)
        df = df[blocks.isin(flt["block_codes"])]
    if flt["marka"]:
        m = flt["marka"].strip().upper()
        if m:
            df = df[df["Комплект(ы)"].apply(_extract_2nd_block) == m]
    kit_status = flt["extra"].get("kit_status", ())
    if kit_status:
        df = df[df["status"].isin(kit_status)]

    # ---- Дата позиции: плановая дата листа или срок комплекта ----
    df["_date"] = df["planned_date"].where(
        df["planned_date"].notna() & (df["planned_date"] != ""),
        df["due_date"],
    )

    only_dated = flt["extra"].get("only_dated", False)
    if only_dated:
        df = df[df["_date"].notna()]

    # ---- Период ----
    if date_from or date_to:
        df = df[df["_date"].notna()]
        if date_from:
            iso = df["_date"].astype(str).str.slice(0, 10)
            df = df[iso >= date_from.isoformat()]
        if date_to:
            iso = df["_date"].astype(str).str.slice(0, 10)
            df = df[iso <= date_to.isoformat()]

    if df.empty:
        return pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

    # ---- Фактическая дата из Витро ----
    vitro_dates = _vitro_sheet_dates()

    def _fact_date(row) -> date | None:
        if pd.isna(row["sheet_no"]) or not str(row["sheet_no"]).strip():
            return None
        d = vitro_dates.get(
            (row["Комплект(ы)"], str(row["sheet_no"]).strip()))
        return _iso_to_date(d) if d else None

    fact = df.apply(_fact_date, axis=1)

    def _issue_status(d) -> str:
        """«Выдан», если лист загружен в Витро (есть фактическая дата),
        иначе «Планируется» (патч 09)."""
        if d is None:
            return STATUS_PLANNED
        try:
            if pd.isna(d):
                return STATUS_PLANNED
        except (TypeError, ValueError):
            pass
        return STATUS_ISSUED

    out = pd.DataFrame({
        "Дисциплина": df["Дисциплина"],
        "Раздел": df["Раздел"],
        "Комплект(ы)": df["Комплект(ы)"],
        "Номер листа": df["sheet_no"].fillna(""),
        "Шифр листа": _full_sheet_codes(df),
        "Наименование листа": df["Наименование листа"].fillna(""),
        "Плановая дата": df["_date"].apply(_iso_to_date),
        "Фактическая дата": fact,
        "Статус выдачи": fact.map(_issue_status),
    })
    out = out.sort_values(
        ["Дисциплина", "Раздел", "Комплект(ы)", "Номер листа"],
    ).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
#  Секции 5–6: детальные таблицы + выгрузка в Excel
# ---------------------------------------------------------------------------
def _period_label(date_from, date_to) -> str:
    """Человекочитаемая подпись периода для выгрузки и подписей."""
    if date_from or date_to:
        return (f"{date_from.strftime('%d.%m.%Y') if date_from else '…'} — "
                f"{date_to.strftime('%d.%m.%Y') if date_to else '…'}")
    return "все даты"


def _render_export_button(sheets_table: pd.DataFrame,
                          period_label: str) -> None:
    """Плашка выгрузки: Excel с листом «Листы» (таблица Б) и служебным
    листом «Параметры выгрузки», с учётом фильтров и периода
    (патч 09: лист «Комплекты» удалён вместе с таблицей А)."""
    export_bytes = build_plan_export(
        sheets_table, period_label=period_label)
    st.download_button(
        "📥 Выгрузить в Excel: лист «Листы»",
        data=export_bytes,
        file_name=f"План_выдачи_листы_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime=MIME_XLSX,
        width='content',
        key="plan_export_excel",
        help="Файл повторяет таблицу «Детальный план по листам "
             "комплекта» с учётом фильтров и периода; второй лист — "
             "параметры формирования.",
    )
    st.caption(f"Период выгрузки: **{period_label}**.")


def _render_sheet_plan_table(sheets_table: pd.DataFrame,
                             period_label: str) -> None:
    """Детальный план по листам комплекта (plan_drawings) —
    единственная детальная таблица (патч 09)."""
    st.markdown("##### 🗓 Детальный план по листам комплекта")

    if sheets_table.empty:
        st.info(
            "Нет позиций листов по выбранным фильтрам/периоду. "
            "Загрузите ведомости чертежей в блоке «📥 Вводные данные»."
        )
        return

    n = len(sheets_table)
    st.caption(f"Строк: **{n}** · период: **{period_label}**")
    if n > MAX_DETAIL_ROWS:
        st.caption(
            f"Показаны первые **{MAX_DETAIL_ROWS}** строк из **{n}** — "
            "полный объём в Excel-выгрузке."
        )

    shown = sheets_table.head(MAX_DETAIL_ROWS)
    st.dataframe(
        shown,
        width='stretch',
        hide_index=True,
        height=min(max(len(shown) * 36 + 40, 300), 700),
        column_config={
            "Дисциплина": st.column_config.TextColumn(width="small"),
            "Раздел": st.column_config.TextColumn(width="small"),
            "Комплект(ы)": st.column_config.TextColumn(width="medium"),
            "Номер листа": st.column_config.TextColumn(width="small"),
            "Шифр листа": st.column_config.TextColumn(
                width="medium",
                help="Полный шифр листа: шифр комплекта + номер листа.",
            ),
            "Наименование листа": st.column_config.TextColumn(width="large"),
            "Плановая дата": st.column_config.DateColumn(
                format="DD.MM.YYYY", width="small"),
            "Фактическая дата": st.column_config.DateColumn(
                "Фактическая дата",
                format="DD.MM.YYYY", width="small",
                help="Дата статуса листа в Витро (если лист загружен).",
            ),
            "Статус выдачи": st.column_config.TextColumn(
                width="small",
                help="«Выдан» — лист загружен в Витро (есть фактическая "
                     "дата); «Планируется» — ещё не загружен.",
            ),
        },
    )


# ---------------------------------------------------------------------------
#  Секция 0. Импорт ведомостей
# ---------------------------------------------------------------------------
def _prune_plan_kits(keep_codes: set[str]) -> int:
    """Удаляет из plan_kits комплекты, которых НЕТ в keep_codes,
    вместе с их чертежами (патч 12 — режим «Заменить план»).

    Зачем: upsert_plan_kits только добавляет/обновляет записи, поэтому
    комплекты из старых загрузок (в т. ч. мусорные шифры с запятыми/
    точками и дубль-написания латиницей) оставались в базе навсегда,
    и бублик «Комплектов всего» показывал 541 вместо 471.
    Сначала удаляются чертежи (FK plan_drawings.kit_code), затем сами
    комплекты. Возвращает число удалённых комплектов.

    Патч 13: шифры, не проходящие is_valid_kit_code, удаляются
    ВСЕГДА (даже если вдруг попали в keep) — исторический мусор
    («АТ-РД-КЖ1,2-С-П2.3.ВР») не должен переживать пересохранение.
    """
    keep = {c for c in (keep_codes or set())
            if c and is_valid_kit_code(c)}
    with get_conn() as conn:
        existing = [r["code"] for r in
                    conn.execute("SELECT code FROM plan_kits")]
        stale = [c for c in existing
                 if c not in keep or not is_valid_kit_code(c)]
        if not stale:
            return 0
        ph = ",".join("?" * len(stale))
        conn.execute(
            f"DELETE FROM plan_drawings WHERE kit_code IN ({ph})",
            tuple(stale))
        conn.execute(
            f"DELETE FROM plan_kits WHERE code IN ({ph})", tuple(stale))
    return len(stale)


def _norm_kit_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Нормализация и валидация шифров перед записью в plan_kits
    (патч 12): каждый шифр проходит _kit_code_of (верхний регистр,
    латинские двойники → кириллица, строгое регулярное выражение).
    Строки с невалидным шифром отбрасываются со счётчиком.
    Возвращает (строки, предупреждения)."""
    out: list[dict] = []
    dropped: list[str] = []
    for r in rows or []:
        code = _kit_code_of(r.get("code"))
        if not code:
            dropped.append(str(r.get("code") or ""))
            continue
        out.append(dict(r, code=code))
    warns: list[str] = []
    if dropped:
        examples = "; ".join(f"«{d[:40]}»" for d in dropped[:3])
        more = " …" if len(dropped) > 3 else ""
        warns.append(
            f"Отброшено строк с некорректным шифром комплекта: "
            f"{len(dropped)} ({examples}{more}).")
    return out, warns


def _render_import_block() -> None:
    with st.expander("📥 Вводные данные: ведомости (Excel)", expanded=False):
        st.caption(
            "Загрузите **Ведомость основных комплектов** и **ведомости "
            "чертежей** в формате Excel. Колонки распознаются по названиям "
            "(«Шифр комплекта», «Номер листа», «Название листа», «Шифр "
            "листа» и т. п.) — порядок и лишние колонки не мешают; подходят "
            "и реальные регистры «Комплекты чертежей» с шапками комплектов "
            "и приложениями (СО/ВР/П). Повторная загрузка ведомости "
            "комплектов обновляет шифры; повторная загрузка ведомости "
            "чертежей **заменяет** чертежи указанных в ней комплектов."
        )

        c1, c2 = st.columns(2)

        with c1:
            up_kits = st.file_uploader(
                "Ведомость основных комплектов (Excel)",
                type=["xlsx", "xlsm", "xltx"],
                accept_multiple_files=True,
                key="plan_up_kits",
            )

        with c2:
            up_drawings = st.file_uploader(
                "Ведомости чертежей комплектов (Excel)",
                type=["xlsx", "xlsm", "xltx"],
                accept_multiple_files=True,
                key="plan_up_drawings",
            )
            default_kit = st.text_input(
                "Шифр комплекта (если в файлах нет колонки «Шифр»)",
                placeholder="например: АТ-РД-КЖ1-1-00",
                key="plan_default_kit",
                help="Если ведомость чертежей сделана для одного комплекта "
                     "без колонки шифра — укажите его здесь. Можно вводить "
                     "несколько через запятую (лист = шифр по порядку).",
            )

        # ---- Разбор ведомости комплектов ----
        kits_rows: list[dict] = []
        kits_warnings: list[str] = []
        if up_kits:
            for f in up_kits:
                r, w = parse_kits_workbook(f.getvalue(), f.name)
                kits_rows += r
                kits_warnings += w
            if kits_rows:
                st.markdown(
                    f"**Ведомость комплектов:** распознано строк — "
                    f"**{len(kits_rows)}**")
                df_kits = pd.DataFrame(kits_rows).head(300)
                st.dataframe(
                    df_kits.rename(columns={
                        "code": "Шифр комплекта",
                        "name": "Наименование комплекта",
                        "discipline": "Дисциплина",
                        "section": "Раздел",
                        "plan_status": "Статус",
                        "due_date": "Срок готовности",
                        "due_comment": "Пояснение",
                        "updated_at": "Обновлён",
                        "updated_by": "Кем",
                    }),
                    width='stretch', hide_index=True, height=200)
                replace_plan = st.checkbox(
                    "🔄 Заменить план: удалить комплекты, которых "
                    "нет в загружаемой ведомости",
                    value=False, key="plan_replace_kits",
                    help="Перед сохранением из плана удаляются "
                         "комплекты (и их чертежи), отсутствующие в "
                         "загружаемой ведомости. Включайте при "
                         "повторной загрузке ПОЛНОЙ ведомости — "
                         "старые записи (в т. ч. мусорные шифры и "
                         "дубль-написания) не смешаются с новыми. "
                         "Для частичной ведомости оставьте "
                         "выключенным.",
                )
                if st.button("💾 Сохранить комплекты в план",
                             type="primary", key="plan_save_kits"):
                    user = ensure_user()
                    if user:
                        try:
                            norm_rows, norm_warns = _norm_kit_rows(kits_rows)
                            removed = 0
                            if replace_plan:
                                removed = _prune_plan_kits(
                                    {r["code"] for r in norm_rows})
                            n = upsert_plan_kits(norm_rows, user)
                            for w in norm_warns:
                                st.warning(w)
                            msg = f"✅ Сохранено комплектов: {n}"
                            if removed:
                                msg += f" · удалено устаревших: {removed}"
                            st.success(msg)
                            files_str = ", ".join(f.name for f in up_kits)
                            log_event("plan_import",
                                      f"Ведомость комплектов: {n} строк; "
                                      f"файлы: {files_str}", rows=n)
                            save_warns = kits_warnings + norm_warns
                            if save_warns:
                                log_event(
                                    "plan_import",
                                    "Замечания разбора (комплекты):\n• "
                                    + "\n• ".join(save_warns),
                                    rows=n, status="WARN")
                            st.rerun()
                        except Exception as e:
                            st.error(f"ОШИБКА БД при сохранении комплектов: {e}")
                            st.stop()
            else:
                st.info("Строки комплектов не распознаны.")

        # ---- Разбор ведомостей чертежей ----
        drawings_rows: list[dict] = []
        drawings_warnings: list[str] = []
        drawings_kits: dict[str, str] = {}
        if up_drawings:
            # Поддержка «один файл = один комплект»: если файлов несколько
            # и колонки шифра нет, применяется шифр по порядку файлов.
            # Патч 12: ручной шифр проходит ту же нормализацию/валидацию,
            # что и ведомости (_kit_code_of) — мусор в план не попадёт.
            default_codes = []
            for c in (default_kit or "").split(","):
                code = _kit_code_of(c)
                if code:
                    default_codes.append(code)
            for idx, f in enumerate(up_drawings):
                dk = default_codes[idx] if idx < len(default_codes) else \
                    (default_codes[0] if len(default_codes) == 1 else "")
                r, w, k = parse_drawings_workbook(f.getvalue(), f.name, dk)
                drawings_rows += r
                drawings_warnings += w
                for code, kname in k.items():
                    # название берём из шапки комплекта; пустое не затираем
                    if kname and not drawings_kits.get(code):
                        drawings_kits[code] = kname
                    drawings_kits.setdefault(code, drawings_kits.get(code, ""))
            if drawings_rows:
                n_drw = sum(int(r["qty"]) for r in drawings_rows)
                st.markdown(
                    f"**Ведомости чертежей:** распознано листов — "
                    f"**{len(drawings_rows)}** ({n_drw} чертежей), "
                    f"комплектов — **{len({r['kit_code'] for r in drawings_rows})}**")
                df_drw = pd.DataFrame(drawings_rows).head(300)
                if "qty" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"qty": "Кол-во"})
                if "sheet_no" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"sheet_no": "Номер листа"})
                if "title" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"title": "Наименование чертежа"})
                st.dataframe(
                    df_drw.rename(columns={
                        "kit_code": "Шифр комплекта",
                        "planned_date": "Плановая дата",
                        "discipline": "Дисциплина",
                        "section": "Раздел",
                        "source_file": "Файл",
                    }),
                    width='stretch', hide_index=True, height=200)
                if st.button("💾 Сохранить чертежи в план",
                             type="primary", key="plan_save_drawings"):
                    user = ensure_user()
                    if user:
                        try:
                            # Названия комплектов из шапок регистров —
                            # в план (пустые существующие поля заполняются).
                            # Патч 12: шифры проходят нормализацию/валидацию
                            # перед записью в plan_kits.
                            stub_rows, stub_warns = _norm_kit_rows(
                                [{"code": c, "name": n} for c, n
                                 in drawings_kits.items()])
                            for w in stub_warns:
                                st.warning(w)
                            if stub_rows:
                                upsert_plan_kits(stub_rows, user)
                            n = replace_plan_drawings_for_kits(drawings_rows, user)
                            n_kits = len({r["kit_code"] for r in drawings_rows})
                            st.success(f"✅ Сохранено листов: {n} "
                                       f"({n_kits} компл.)")
                            files_str = ", ".join(f.name for f in up_drawings)
                            log_event("plan_import",
                                      f"Ведомости чертежей: {n} листов; "
                                      f"файлы: {files_str}", rows=n)
                            save_warns = drawings_warnings + stub_warns
                            if save_warns:
                                log_event(
                                    "plan_import",
                                    "Замечания разбора (чертежи):\n• "
                                    + "\n• ".join(save_warns),
                                    rows=n, status="WARN")
                            st.rerun()
                        except Exception as e:
                            st.error(f"ОШИБКА БД при сохранении чертежей: {e}")
                            st.stop()
            else:
                st.info("Строки чертежей не распознаны.")

        # ---- Предупреждения разбора (патч 13) ----
        # Сохраняем в session_state: блок «⚠️ Замечания последней
        # загрузки» отображается СНАРУЖИ экспандера «Вводные данные»
        # и переживает любые рераны вкладки. Полная история — в журнале.
        all_warnings = kits_warnings + drawings_warnings
        if all_warnings:
            st.session_state["plan_import_warnings"] = {
                "time": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "items": all_warnings,
            }
            st.caption(
                f"⚠️ Замечаний разбора: {len(all_warnings)} — см. блок "
                "«⚠️ Замечания последней загрузки» под панелью вводных."
            )

        # ---- Опасная зона ----
        st.divider()
        confirm = st.text_input(
            "Очистить план целиком: введите DELETE",
            key="plan_confirm_clear",
            help="Удалит все комплекты и чертежи плана. Данные Витро "
                 "не затрагиваются.",
        )
        if st.button("🗑 Очистить план", key="plan_clear_btn",
                     disabled=confirm != "DELETE"):
            user = ensure_user()
            if user:
                clear_plan()
                log_event("plan_clear",
                          f"План выдачи очищен. Пользователь: {user}")
                st.success("План очищен.")
                st.rerun()


# ---------------------------------------------------------------------------
#  Патч 13: замечания последней загрузки + журнал загрузок
# ---------------------------------------------------------------------------
def _render_last_warnings() -> None:
    """Блок «⚠️ Замечания последней загрузки» (патч 13).

    Раньше st.warning от парсера жил только до следующего рерана,
    а при F5 исчезал вместе с загруженными файлами — пользователь
    не мог прочитать, ЧТО было отброшено. Список предупреждений
    теперь хранится в st.session_state (переживает рераны) и
    отображается отдельным блоком вне экспандера «Вводные данные».
    После F5 (новая сессия) историю можно посмотреть в «📜 Журнал
    загрузок» — предупреждения пишутся в таблицу log при сохранении.
    """
    w = st.session_state.get("plan_import_warnings") or {}
    items = w.get("items") or []
    if not items:
        return
    with st.expander(
            f"⚠️ Замечания последней загрузки ({len(items)})",
            expanded=True):
        st.caption(
            f"Загрузка от {w.get('time', '—')}. Блок остаётся до "
            "следующей загрузки; полная история — в «📜 Журнал загрузок»."
        )
        for x in items[:50]:
            st.write(f"- {x}")


def _render_import_log() -> None:
    """Блок «📜 Журнал загрузок» (патч 13): последние события
    plan_import / plan_clear из таблицы log.

    Показывает: дату (МСК), имя файла/сообщение, число распознанных
    строк и предупреждения (отдельные записи со статусом WARN).
    В отличие от st.warning и session_state журнал хранится в БД
    и переживает перезагрузку страницы (F5) и перезапуск приложения.
    """
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT timestamp, event, message, rows, status
                FROM log
                WHERE event IN ('plan_import', 'plan_clear')
                ORDER BY rowid DESC
                LIMIT 30
            """).fetchall()
    except Exception:
        return  # таблицы log ещё нет (старая БД без init_db) — молча
    data = [{
        "Дата (МСК)": r["timestamp"] or "",
        "Событие": ("Очистка плана" if r["event"] == "plan_clear"
                    else "Загрузка ведомости"),
        "Строк": r["rows"] if r["rows"] else "",
        "Статус": r["status"] or "OK",
        "Сообщение": str(r["message"] or "").replace("\n", " · "),
    } for r in rows]
    with st.expander(
            f"📜 Журнал загрузок (последние {len(data) or 0} записей)",
            expanded=False):
        if not data:
            st.caption("Записей ещё нет — загрузите ведомости.")
            return
        st.dataframe(
            pd.DataFrame(data),
            width='stretch', hide_index=True,
            height=min(max(len(data) * 36 + 40, 200), 420),
            column_config={
                "Дата (МСК)": st.column_config.TextColumn(width="medium"),
                "Событие": st.column_config.TextColumn(width="small"),
                "Строк": st.column_config.NumberColumn(width="small"),
                "Статус": st.column_config.TextColumn(width="small"),
                "Сообщение": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(
            "Записи создаются при сохранении ведомостей и очистке плана; "
            "предупреждения парсера пишутся отдельными строками "
            "(Статус = WARN). Переживает F5 и перезапуск приложения."
        )


# ===========================================================================
#  Точка входа
# ===========================================================================
def render():
    st.header("📅 План выдачи комплектов")
    st.caption(
        "План выдачи всех комплектов РД: загруженных в Витро и "
        "планируемых к загрузке — на основании ведомостей Excel. "
        "Данные плана общие для всех пользователей приложения."
    )

    # ---- 1. Фильтры (единый стандарт) ----
    flt = _render_filters()

    # ---- 2. Период «с / по» — сразу под фильтрами, до общей таблицы ----
    date_from, date_to = _render_period_block()

    include_vitro_only = st.checkbox(
        "Включить комплекты, загруженные в Витро, но отсутствующие "
        "в ведомостях",
        value=True, key="plan_include_vitro_only",
        help="«Только в Витро» — комплекты есть в базе Витро, но их нет "
             "в загруженных ведомостях. Срок готовности для них можно "
             "назначить в сводной таблице.",
    )

    overview_all = _build_overview(include_vitro_only)
    overview_filtered = _apply_overview_filters(overview_all, flt)

    st.divider()

    # ---- 3. Бублики — сразу после фильтров и периода, ДО таблиц ----
    _render_donuts(overview_filtered)

    st.divider()

    # ---- 4. Сводный план готовности комплектов и выданных листов ----
    # Период применяется и к сводной таблице (по «Сроку готовности»).
    _render_overview_table(overview_filtered, date_from, date_to)

    st.divider()

    # ---- 5. Детальный план по листам комплекта ----
    # (Таблица по комплектам удалена в патче 09 — дублировала сводную.)
    sheets_table = _build_sheet_plan_table(
        overview_filtered, flt, date_from, date_to)

    period_label = _period_label(date_from, date_to)
    _render_sheet_plan_table(sheets_table, period_label)

    st.divider()

    # ---- 6. Выгрузка в Excel ----
    _render_export_button(sheets_table, period_label)

    st.divider()

    # ---- 7. Вводные данные + журнал загрузок — в самом низу (патч 17):
    #         служебные блоки не отодвигают рабочие таблицы ----
    _render_import_block()

    # ---- Патч 13: замечания последней загрузки (переживают рераны) ----
    _render_last_warnings()

    # ---- Патч 13: журнал загрузок (переживает F5, данные из БД) ----
    _render_import_log()
