# vitro/plan_xlsx.py
"""
Excel для вкладки «План выдачи комплектов».

Состав:
  - Шаблоны ведомостей (скачиваются из вкладки):
      build_kits_template()     — «Ведомость основных комплектов»
      build_drawings_template() — «Ведомость чертежей комплектов»
  - Парсеры загруженных файлов (гибкое распознавание заголовков):
      parse_kits_workbook()     — комплекты → upsert_plan_kits
      parse_drawings_workbook() — чертежи  → replace_plan_drawings_for_kits
  - Выгрузка результата фильтрации:
      build_plan_export()       — 2 листа: «Комплекты» (таблица А) +
                                  «Листы» (таблица Б); подаются
                                  ОТОБРАЖАЕМЫЕ DataFrame (русские
                                  заголовки), выгрузка повторяет
                                  таблицы на экране

Распознавание колонок — по ключевым словам в заголовках (первые 15 строк
каждого видимого листа), поэтому формат «Ексель» может отличаться
в порядке и названиях колонок. Минимально нужны:
  - для ведомости комплектов: шифр комплекта;
  - для ведомости чертежей:   шифр комплекта + наименование.
"""

import io
import math
import re
from datetime import date, datetime

import streamlit as st

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

MIME_XLSX = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")

# ---------------------------------------------------------------------------
#  Стили шаблонов
# ---------------------------------------------------------------------------
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_NOTE_FONT = Font(size=9, color="808080", italic=True)
_EXAMPLE_FILL = PatternFill("solid", fgColor="FFF2CC")
_WRAP = Alignment(wrap_text=True, vertical="top")

# ---------------------------------------------------------------------------
#  Ключевые слова для распознавания колонок (нижний регистр)
# ---------------------------------------------------------------------------
# Порядок важен: более специфичные ключи — первыми.
# «шифр рд» проверяется раньше «шифр», чтобы не спутать с другими колонками.
_KIT_CODE_KEYS = ("шифр рд", "шифр комплекта", "шифр", "код комплекта")
_KIT_NAME_KEYS = ("наименование основного комплекта",
                  "наименование комплекта", "название комплекта",
                  "наименование основного", "название основного")
_KIT_STATUS_KEYS = ("статус",)
_DUE_DATE_KEYS = ("срок готовности", "дата готовности", "срок", "до даты")
_DISC_KEYS = ("дисциплина",)
_SECTION_KEYS = ("раздел",)

# Для чертежей: «название листа» проверяется раньше «название»,
# чтобы не спутать с заголовками разделов типа «Надземная часть Корпус 1».
_DRAW_TITLE_KEYS = ("название листа", "наименование чертежа",
                    "наименование", "обозначение", "название")
# Номер листа («10.01», «СО», «П.01»…).
_SHEET_NO_KEYS = ("номер листа", "номер")
# Полный шифр листа/чертежа: «АТ-РД-ОВ1-С-00-10.01». Порядок важен:
# более длинные ключи — первыми, чтобы «полный шифр листа» не совпал как «шифр».
_FULL_CODE_KEYS = ("полный шифр листа", "шифр листа", "шифр чертежа")
_QTY_KEYS = ("кол-во", "количество", "кол во")
_PLANNED_DATE_KEYS = ("плановая дата", "дата выдачи", "дата")

# Шифр комплекта — строка вида АТ-РД-ОВ1-С-00: 4-5 блоков через дефис.
# Полный шифр ЛИСТА (АТ-РД-ОВ1-С-00-10.01) имеет 6+ блоков и НЕ должен
# совпадать, поэтому хвост ограничен двумя блоками.
# ВНИМАНИЕ: диапазоны указаны отдельно для латиницы [A-Z] и кириллицы [А-ЯЁ] —
# смешанный диапазон «А-Z» (кириллическая А + латинская Z) невалиден в re.
_KIT_CODE_PATTERN = re.compile(
    r"^[A-ZА-ЯЁ]{2,4}-[A-ZА-ЯЁ]{1,3}-[A-ZА-ЯЁ0-9.]+(?:-[A-ZА-ЯЁ0-9.]+){1,2}$"
)

# Латинские двойники кириллических букв (появляются при копировании
# шифров из CAD/Word): «АТ-РД-ОВ2-C-00» и «АТ-РД-ОВ2-С-00» — один комплект.
_HOMOGLYPHS = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х",
})


def _is_valid_kit_code(code: str) -> bool:
    """True, если строка похожа на шифр комплекта (АТ-РД-КЖ1-1-00)."""
    return bool(_KIT_CODE_PATTERN.match(code)) if code else False


def _norm_header(value) -> str:
    """Нормализация заголовка ячейки: нижний регистр, без лишних пробелов."""
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\xa0", " ").replace("\u200b", "")
    s = " ".join(s.split())
    return s.strip().lower()


def _match_header(header: str, keys: tuple) -> bool:
    """True, если заголовок начинается с одного из ключевых слов."""
    h = _norm_header(header)
    for k in keys:
        if h.startswith(k) or h == k:
            return True
    return False


def _find_header_row(ws, required: tuple, optional: tuple = ()) -> tuple[int, dict] | None:
    """
    Ищет строку заголовков (первые 15 строк листа). Возвращает
    (индекс, {ключ: колонка}) или None.

    Строка считается заголовком, если:
      1) найдены ВСЕ обязательные группы ключей (required);
      2) суммарно распознано >= 2 РАЗНЫХ колонок (required + optional).

    Пункт 2 отсекает проектные шапки (одинокие «ШИФР»/«Название» в строке 1),
    но НЕ должен считать только обязательные группы: у ведомости чертежей
    обязательная группа одна («Название листа»), а реальные заголовки
    содержат 3-5 именованных колонок (см. файлы ОВ/АР/ВК «Комплекты чертежей»).
    """
    all_groups = tuple(required) + tuple(optional)
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > 15:
            break
        headers = [_norm_header(c) for c in row]
        if not any(headers):
            continue
        cols: dict[str, int] = {}
        for key_group, name in all_groups:
            for j, h in enumerate(headers):
                if h and name not in cols and _match_header(h, key_group):
                    cols[name] = j
        if all(name in cols for _, name in required) and len(cols) >= 2:
            return i, cols
    return None


def _parse_date_value(value) -> str | None:
    """Дата из ячейки → ISO-строка 'YYYY-MM-DD' или None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return None
    # ГГГГ-ММ-ДД
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        pass
    # ДД.ММ.ГГГГ / ДД.ММ.ГГ
    for sep in (".", "/", "-"):
        parts = s.split(sep)
        if len(parts) == 3:
            try:
                d, m, y = (int(p) for p in parts)
                if y < 100:
                    y += 2000
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return date(y, m, d).isoformat()
            except ValueError:
                continue
    return None


def _norm_complex_code(value) -> str:
    """Нормализация шифра: строка, trim, верхний регистр, без пробелов.
    Латинские двойники приводятся к кириллице (см. _HOMOGLYPHS), иначе
    «АТ-РД-ОВ2-C-00» и «АТ-РД-ОВ2-С-00» создадут два разных комплекта."""
    if value is None:
        return ""
    s = str(value).replace("\xa0", " ").strip().upper()
    return s.translate(_HOMOGLYPHS)


def _fmt_sheet_no(value) -> str:
    """Номер листа в строку без порчи «10.01»/«2.1.0»: int → «10»,
    float 10.01 → «10.01», float 31.0 → «31», текст — как есть (trim)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return (f"{value:.6f}").rstrip("0").rstrip(".")
    s = str(value).replace("\xa0", " ").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s.upper().translate(_HOMOGLYPHS)


def _clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()


def _looks_like_section_header(title: str) -> bool:
    """Похоже ли НАЗВАНИЕ на строку-раздел, а не на чертёж
    («Подземная часть», «Надземная часть Корпус 1», «Прилагаемые документы»)."""
    if not title:
        return True
    t = _norm_header(title)
    section_markers = (
        "надземная часть", "подземная часть", "корпус ", "корпус",
        "секция", "этаж", "уровень", "блок",
        "прилагаемые документы", "приложение",
    )
    return any(t.startswith(m) for m in section_markers)


# ===========================================================================
#  Шаблоны
# ===========================================================================
def _write_template(ws, title: str, headers: list[str],
                    examples: list[list], notes: list[str]) -> None:
    n_cols = len(headers)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    c = ws.cell(row=1, column=1, value=title)
    c.font = Font(bold=True, size=13, color="1F4E78")

    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=j, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for i, row in enumerate(examples, start=3):
        for j, v in enumerate(row, start=1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.border = _BORDER
            cell.fill = _EXAMPLE_FILL

    note_row = 3 + len(examples) + 1
    for k, note in enumerate(notes):
        cell = ws.cell(row=note_row + k, column=1, value=note)
        cell.font = _NOTE_FONT

    for j in range(1, n_cols + 1):
        ws.column_dimensions[get_column_letter(j)].width = 28
    ws.column_dimensions["A"].width = 34


def build_kits_template() -> bytes:
    """Шаблон «Ведомость основных комплектов»."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Комплекты"

    _write_template(
        ws,
        "Ведомость основных комплектов — План выдачи (АТП ТЛП)",
        ["Шифр комплекта", "Наименование комплекта", "Дисциплина",
         "Раздел", "Статус", "Срок готовности"],
        [
            ["АТ-РД-КЖ1-1-00", "Конструкции корпуса 1, секция 00",
             "02_КР", "Секция 00", "В работе", "2026-12-15"],
            ["АТ-РД-АР-2-00", "Архитектура корпуса 2",
             "01_АР", "Корпус 2", "План", "2027-02-01"],
        ],
        [
            "Заполнение: одна строка — один комплект.",
            "Обязательные колонки: «Шифр комплекта». Остальные — по возможности.",
            "Срок готовности: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ (редактируется и во вкладке).",
            "При повторной загрузке существующие шифры обновляются, новые — добавляются.",
        ],
    )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_drawings_template() -> bytes:
    """Шаблон «Ведомость чертежей комплектов»."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Чертежи"

    _write_template(
        ws,
        "Ведомость чертежей комплекта — План выдачи (АТП ТЛП)",
        ["Шифр комплекта", "Номер листа", "Наименование чертежа",
         "Дисциплина", "Раздел", "Кол-во чертежей", "Плановая дата"],
        [
            ["АТ-РД-КЖ1-1-00", "10.01", "Общие данные", "02_КР",
             "Секция 00", 1, "2026-12-15"],
            ["АТ-РД-КЖ1-1-00", "31.00", "Фундаментная плита, узел А",
             "02_КР", "Секция 00", 1, "2026-12-15"],
        ],
        [
            "Заполнение: одна строка — один лист (позиция) комплекта.",
            "Обязательные колонки: «Шифр комплекта» и «Наименование чертежа».",
            "Также распознаются форматы реальных регистров «Комплекты чертежей».",
            "Шапки комплектов вида «АТ-РД-ОВ1-С-00 Система отопления» распознаются,",
            "строки без шифра (в т. ч. приложения СО, ВР, П.01…) наследуют комплект.",
            "Кол-во чертежей — план по позиции (по умолчанию 1).",
            "Плановая дата: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ (необязательно).",
            "Повторная загрузка по тому же шифру заменяет его чертежи целиком.",
        ],
    )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ===========================================================================
#  Парсеры ведомостей
# ===========================================================================
def parse_kits_workbook(data: bytes, filename: str = "") -> tuple[list[dict], list[str]]:
    """
    Разбирает «Ведомость основных комплектов».

    Возвращает (rows, warnings):
      rows — [{'code','name','discipline','section','plan_status',
               'due_date','due_comment'}]
      warnings — список сообщений о пропущенных строках/листах.
    """
    warnings: list[str] = []
    rows: list[dict] = []

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:
        return [], [f"Не удалось открыть файл {filename}: {e}"]

    optional_groups = (
        (_DISC_KEYS, "discipline"),
        (_SECTION_KEYS, "section"),
        (_KIT_STATUS_KEYS, "plan_status"),
        (_DUE_DATE_KEYS, "due_date"),
    )
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.sheet_state != "visible":
            continue

        found = _find_header_row(
            ws,
            required=(
                (_KIT_CODE_KEYS, "code"),
                (_KIT_NAME_KEYS, "name"),
            ),
            optional=optional_groups,
        )
        if not found:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: не найдены колонки "
                f"«Шифр комплекта» и/или «Наименование» — лист пропущен.")
            continue

        header_idx, cols = found
        sheet_rows = 0
        for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
            if row is None or all(
                    v is None or str(v).strip() == "" for v in row):
                if sheet_rows:
                    break  # пустая строка после данных — конец таблицы
                continue
            code = _norm_complex_code(
                row[cols["code"]] if len(row) > cols["code"] else None)
            if not code:
                continue
            name = _clean_str(row[cols["name"]]) \
                if cols.get("name") is not None and \
                len(row) > cols["name"] else ""
            row_data = {
                "code": code,
                "name": name,
                "plan_status": "",
                "due_date": None,
                "due_comment": "",
            }
            for name_key in ("discipline", "section", "plan_status"):
                j = cols.get(name_key)
                if j is not None and len(row) > j:
                    row_data[name_key] = _clean_str(row[j])
            j = cols.get("due_date")
            if j is not None and len(row) > j:
                row_data["due_date"] = _parse_date_value(row[j])
            rows.append(row_data)
            sheet_rows += 1

        if sheet_rows == 0:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: заголовок найден, "
                f"но строк данных нет.")
    wb.close()

    if not rows and not warnings:
        warnings.append(f"{filename}: не найдено ни одной строки комплектов.")
    return rows, warnings


def parse_drawings_workbook(
    data: bytes, filename: str = "", default_kit: str = "",
) -> tuple[list[dict], list[str], dict[str, str]]:
    """
    Разбирает ведомость чертежей комплектов — как шаблонную, так и
    реальные регистры «Комплекты чертежей» (ОВ/АР/ВК…).

    Поддерживаемые форматы колонок (распознаются по заголовкам, порядок
    и лишние колонки не мешают):
      - шифр комплекта:  «ШИФР», «Шифр комплекта», «Шифр РД»;
      - номер листа:     «Номер листа», «Номер» (может отсутствовать);
      - наименование:    «Название листа», «Наименование чертежа»;
      - полный шифр:     «Шифр листа», «Полный шифр листа», «Шифр чертежа».

    Структура строк реальных регистров:
      - строка-раздел («Подземная часть», «Прилагаемые документы») — пропуск;
      - строка-шапка комплекта «АТ-РД-ОВ1-С-00 Система отопления…» —
        запоминает текущий комплект и его НАЗВАНИЕ;
      - строка чертежа с шифром в колонке шифра — сменяет текущий комплект;
      - строка чертежа БЕЗ шифра (приложения СО/ВР/П.01 и т. п.) —
        наследует текущий комплект;
      - метки «Приложение 1» в колонке шифра комплектом НЕ считаются.

    Номер листа берётся из ПОЛНОГО шифра листа (последний блок после дефиса —
    в реальных регистрах он каноничен: «АТ-РД-АР1-С-00-2.1.0»), а если той
    колонки нет — из колонки «Номер листа».

    default_kit — шифр комплекта, если в файле нет колонки с шифром
    (файл по одному комплекту).

    Возвращает (rows, warnings, kits):
      rows      — [{'kit_code','sheet_no','title','discipline','section',
                    'qty','planned_date','source_file'}]
      warnings  — сообщения о пропущенных строках/листах;
      kits      — {шифр: наименование} — все комплекты, встретившиеся
                  в файле (для заполнения plan_kits названиями).
    """
    warnings: list[str] = []
    rows: list[dict] = []
    kits: dict[str, str] = {}
    default_kit = _norm_complex_code(default_kit)

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:
        return [], [f"Не удалось открыть файл {filename}: {e}"], kits

    optional_groups = (
        (_KIT_CODE_KEYS, "kit"),
        (_SHEET_NO_KEYS, "sheet_no"),
        (_FULL_CODE_KEYS, "sheet_code"),
        (_DISC_KEYS, "discipline"),
        (_SECTION_KEYS, "section"),
        (_QTY_KEYS, "qty"),
        (_PLANNED_DATE_KEYS, "planned_date"),
    )
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.sheet_state != "visible":
            continue

        found = _find_header_row(
            ws,
            required=((_DRAW_TITLE_KEYS, "title"),),
            optional=optional_groups,
        )
        if not found:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: не найдена строка "
                f"заголовков (нет «Названия листа» вместе с другими "
                f"колонками) — лист пропущен.")
            continue

        header_idx, cols = found
        c_kit = cols.get("kit")
        c_no = cols.get("sheet_no")
        c_full = cols.get("sheet_code")

        def _cell(row, j, _c_kit=c_kit, _c_no=c_no, _c_full=c_full,
                  _cols=cols):
            if j is None or len(row) <= j:
                return None
            return row[j]

        # Состояние «текущий комплект» внутри листа (регистры идут
        # блоками: шапка комплекта → его чертежи → приложения).
        current_kit = ""
        current_kit_name = ""
        sheet_rows = 0
        no_kit = 0
        empty_run = 0  # подряд идущие пустые строки

        for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
            if row is None or all(
                    v is None or str(v).strip() == "" for v in row):
                # Пустая строка: в регистрах бывают одиночные
                # строки-разделители между блоками комплектов — НЕ конец
                # таблицы. Конец — длинная серия пустых строк.
                empty_run += 1
                if empty_run >= 8:
                    break
                continue
            empty_run = 0

            # ---- колонка шифра комплекта: контекст ----
            # Переводы строк внутри ячейки («АТ-РД-ОВ1-С-00\nСистема…»)
            # схлопываем в один пробел.
            raw_kit = " ".join(
                _clean_str(_cell(row, c_kit)).split()) \
                if c_kit is not None else ""
            code_part, _, name_part = raw_kit.partition(" ")
            kit_norm = _norm_complex_code(code_part)
            if kit_norm and _is_valid_kit_code(kit_norm):
                if name_part.strip():
                    # шапка комплекта: «АТ-РД-ОВ1-С-00 Система отопления…»
                    current_kit = kit_norm
                    current_kit_name = " ".join(name_part.split())
                    if not kits.get(current_kit):
                        kits[current_kit] = current_kit_name
                    continue  # это не чертёж
                # шифр без названия — смена комплекта внутри листа,
                # строка ниже всё ещё может быть чертежом
                current_kit = kit_norm
                kits.setdefault(current_kit, "")
            # иначе в колонке шифра метка («Приложение 1», «Подземная
            # часть», «Прилагаемые документы») — комплект НЕ сменяем.

            # ---- строка чертежа ----
            title = " ".join(_clean_str(_cell(row, cols["title"])).split())
            full = _norm_complex_code(_cell(row, c_full)) \
                if c_full is not None else ""
            sheet_no = ""
            if full and "-" in full:
                sheet_no = full.rsplit("-", 1)[-1]
            if not sheet_no and c_no is not None:
                sheet_no = _fmt_sheet_no(_cell(row, c_no))
            if not title and not sheet_no:
                continue  # служебная строка (раздел/метка) без данных
            if _looks_like_section_header(title) and not sheet_no:
                continue  # «Подземная часть» в колонке названия

            kit = current_kit or default_kit
            if not kit:
                no_kit += 1
                continue

            row_data = {
                "kit_code": kit,
                "sheet_no": sheet_no,
                "title": title,
                "qty": 1,
                "planned_date": None,
                "discipline": "",
                "section": "",
            }
            for name_key in ("discipline", "section"):
                j = cols.get(name_key)
                if j is not None and len(row) > j:
                    row_data[name_key] = _clean_str(row[j])
            j = cols.get("qty")
            if j is not None and len(row) > j:
                try:
                    q = int(float(str(row[j]).replace(",", ".")))
                    if q > 0:
                        row_data["qty"] = q
                except (TypeError, ValueError):
                    pass
            j = cols.get("planned_date")
            if j is not None and len(row) > j:
                row_data["planned_date"] = _parse_date_value(row[j])
            row_data["source_file"] = filename
            rows.append(row_data)
            sheet_rows += 1

        if no_kit:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: {no_kit} строк пропущено — "
                f"не найден шифр комплекта (нет шапки комплекта и не задан "
                f"шифр по умолчанию при загрузке).")
        if sheet_rows == 0 and not no_kit:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: строки чертежей "
                f"не найдены.")
    wb.close()

    if not rows and not warnings:
        warnings.append(f"{filename}: не найдено ни одной строки чертежей.")
    return rows, warnings, kits

# ===========================================================================
#  Выгрузка результата фильтрации
# ===========================================================================
def _style_table(ws, headers: list[str], rows: list[list]) -> None:
    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=j, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for i, row in enumerate(rows, start=2):
        for j, v in enumerate(row, start=1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.border = _BORDER
            cell.alignment = _WRAP
    for j in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(j)].width = 26
    ws.freeze_panes = "A2"


def _fmt_export_cell(col_name: str, value):
    """Значение ячейки отображаемого DataFrame → значение для Excel.
    Даты (date/datetime/ISO-строки в колонках с «дата») → ДД.ММ.ГГГГ,
    NaN → пусто, остальное — как есть."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return value
    s = str(value)
    if "дата" in str(col_name).lower():
        return _iso_to_display(s)  # ISO-строки → ДД.ММ.ГГГГ (иначе как есть)
    return s


def _write_display_df(ws, df) -> None:
    """Записывает отображаемый DataFrame (русские заголовки) на лист
    «как есть» — выгрузка повторяет таблицу на экране."""
    headers = [str(c) for c in df.columns]
    rows = []
    for _, r in df.iterrows():
        rows.append([_fmt_export_cell(col, r[col]) for col in df.columns])
    _style_table(ws, headers, rows)
    # Ширины колонок: базовые 26, наименованиям — пошире, шифрам — 30.
    for j, h in enumerate(headers, start=1):
        hl = h.lower()
        if "наименование" in hl:
            ws.column_dimensions[get_column_letter(j)].width = 46
        elif "шифр" in hl or "комплект" in hl:
            ws.column_dimensions[get_column_letter(j)].width = 30


@st.cache_data(show_spinner=False)   # строится раз на набор данных,
                                     # а не на каждом реране вкладки
def build_plan_export(kits_df, sheets_df, period_label: str = "") -> bytes:
    """
    Excel-файл плана выдачи из ДВУХ листов:
      «Комплекты» — таблица А (детальный план по комплектам);
      «Листы»     — таблица Б (детальный план по листам комплекта).
    Подаются ОТОБРАЖАЕМЫЕ DataFrame (русские заголовки, как на экране) —
    уже отобранные по фильтрам и периоду: выгрузка повторяет таблицы
    вкладки. Кэшируется по содержимому DataFrame — на 10 000 строк
    экономит ~1–2 секунды на каждом реране, пока данные не изменились.
    """
    wb = openpyxl.Workbook()

    # ---- Лист 1: Комплекты (таблица А) ----
    ws = wb.active
    ws.title = "Комплекты"
    _write_display_df(ws, kits_df)

    # ---- Лист 2: Листы (таблица Б) ----
    ws2 = wb.create_sheet("Листы")
    _write_display_df(ws2, sheets_df)

    # ---- Лист 3: Параметры выгрузки ----
    ws3 = wb.create_sheet("Параметры выгрузки")
    _style_table(ws3, ["Параметр", "Значение"], [
        ["Период", period_label or "все даты"],
        ["Дата формирования", datetime.now().strftime("%d.%m.%Y %H:%M")],
        ["Комплектов в выгрузке", len(kits_df)],
        ["Позиций листов в выгрузке", len(sheets_df)],
    ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _iso_to_display(iso) -> str:
    """'YYYY-MM-DD' / date / datetime → 'ДД.ММ.ГГГГ' для Excel-выгрузки."""
    if iso is None:
        return ""
    if isinstance(iso, datetime):
        return iso.date().strftime("%d.%m.%Y")
    if isinstance(iso, date):
        return iso.strftime("%d.%m.%Y")
    if not str(iso).strip():
        return ""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)
