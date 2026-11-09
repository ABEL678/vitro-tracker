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
      build_plan_export()       — 2 листа: «Комплекты» + «Чертежи (план)»

Распознавание колонок — по ключевым словам в заголовках (первые 15 строк
каждого видимого листа), поэтому формат «Ексель» может отличаться
в порядке и названиях колонок. Минимально нужны:
  - для ведомости комплектов: шифр комплекта;
  - для ведомости чертежей:   шифр комплекта + наименование.
"""

import io
import re
from datetime import date, datetime

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
                    "наименование", "обозначение", "название листа")
_QTY_KEYS = ("кол-во", "количество", "кол во")
_PLANNED_DATE_KEYS = ("плановая дата", "дата выдачи", "дата")

# Шифр комплекта — строка вида АТ-РД-XXX-N-MM (буквы-цифры через дефисы).
# Используется для фильтрации строк-заголовков разделов.
_KIT_CODE_PATTERN = re.compile(r"^[А-ZА-ЯЁ]{2,4}-[А-ZА-ЯЁ]{1,3}-[А-ZА-ЯЁ0-9]+-\d+-\d+")


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


def _find_header_row(ws, required: tuple) -> tuple[int, dict] | None:
    """
    Ищет строку заголовков (первые 15 строк листа), содержащую все
    обязательные ключевые слова. Возвращает (индекс, {ключ: колонка}).

    Пропускает строки 0-1 (проектная шапка: «ШИФР», «Тип чертежа» и т. п.),
    которые могут содержать одинокие слова, совпадающие с ключами.
    """
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > 15:
            break
        headers = [_norm_header(c) for c in row]
        if not any(headers):
            continue
        cols: dict[str, int] = {}
        for key_group, name in required:
            for j, h in enumerate(headers):
                if h and name not in cols and _match_header(h, key_group):
                    cols[name] = j
        if all(name in cols for _, name in required):
            # Если найдено < 2 колонок — это не таблица, пропускаем.
            # Строки 0-1 часто содержат «ШИФР» / «Название» в контексте
            # проекта, а не как заголовки колонок.
            if len(cols) < 2:
                continue
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
    """Нормализация шифра: строка, trim, верхний регистр, без пробелов."""
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip().upper()


def _clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()


def _looks_like_section_header(title: str) -> bool:
    """Проверяет, является ли строка заголовком раздела (а не чертежа)."""
    if not title:
        return True
    t = title.lower()
    section_markers = (
        "надземная часть", "подземная часть", "корпус ", "секция ",
        "секция", "этаж", "уровень", "блок",
    )
    return any(t.startswith(m) or t == m.strip() for m in section_markers)


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
        ["Шифр комплекта", "Наименование чертежа", "Дисциплина",
         "Раздел", "Кол-во чертежей", "Плановая дата"],
        [
            ["АТ-РД-КЖ1-1-00", "Фундаментная плита, узел А", "02_КР",
             "Секция 00", 3, "2026-12-15"],
            ["АТ-РД-КЖ1-1-00", "Схема армирования перекрытия", "02_КР",
             "Секция 00", 2, "2026-12-15"],
        ],
        [
            "Заполнение: одна строка — одна позиция чертежей комплекта.",
            "Шифр комплекта в каждой строке (или одинаковый по всему листу).",
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

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.sheet_state != "visible":
            continue

        found = _find_header_row(ws, required=(
            (_KIT_CODE_KEYS, "code"),
            (_KIT_NAME_KEYS, "name"),
        ))
        if not found:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: не найдены колонки "
                f"«Шифр комплекта» и/или «Наименование» — лист пропущен.")
            continue

        header_idx, cols = found
        headers = [_norm_header(c) for c in
                   next(ws.iter_rows(min_row=header_idx + 1,
                                     max_row=header_idx + 1,
                                     values_only=True))]
        extra: dict[str, int | None] = {}
        for name, keys in (
            ("discipline", _DISC_KEYS),
            ("section", _SECTION_KEYS),
            ("plan_status", _KIT_STATUS_KEYS),
            ("due_date", _DUE_DATE_KEYS),
        ):
            extra[name] = None
            for j, h in enumerate(headers):
                if h and _match_header(h, keys):
                    extra[name] = j
                    break

        sheet_rows = 0
        for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
            if row is None or all(
                    v is None or str(v).strip() == "" for v in row):
                if sheet_rows:
                    break  # пустая строка после данных — конец таблицы
                continue
            if len(row) <= cols["code"]:
                continue
            code = _norm_complex_code(row[cols["code"]])
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
            }
            for name in ("discipline", "section", "plan_status"):
                j = extra.get(name)
                if j is not None and len(row) > j:
                    row_data[name] = _clean_str(row[j])
            j = extra.get("due_date")
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
) -> tuple[list[dict], list[str]]:
    """
    Разбирает «Ведомость чертежей комплекта(ов)».

    default_kit — шифр комплекта, если в файле нет колонки с шифром
    (файл по одному комплекту). Может быть пустым, тогда строки без
    шифра пропускаются с предупреждением.

    Возвращает (rows, warnings):
      rows — [{'kit_code','title','discipline','section','qty',
               'planned_date','source_file'}]
    """
    warnings: list[str] = []
    rows: list[dict] = []
    default_kit = _norm_complex_code(default_kit)

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:
        return [], [f"Не удалось открыть файл {filename}: {e}"]

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.sheet_state != "visible":
            continue

        found = _find_header_row(ws, required=(
            (_DRAW_TITLE_KEYS, "title"),
        ))
        kit_cols: dict[str, int] = {}
        if found:
            header_idx, cols = found
            # Опциональная колонка шифра
            headers = [_norm_header(c) for c in
                       next(ws.iter_rows(min_row=header_idx + 1,
                                         max_row=header_idx + 1,
                                         values_only=True))]
            for j, h in enumerate(headers):
                if h and _match_header(h, _KIT_CODE_KEYS) \
                        and "kit_code" not in kit_cols \
                        and not _match_header(h, _DRAW_TITLE_KEYS):
                    kit_cols["kit_code"] = j
            extra: dict[str, int | None] = {}
            for name, keys in (
                ("discipline", _DISC_KEYS),
                ("section", _SECTION_KEYS),
                ("qty", _QTY_KEYS),
                ("planned_date", _PLANNED_DATE_KEYS),
            ):
                extra[name] = None
                for j, h in enumerate(headers):
                    if h and _match_header(h, keys) \
                            and extra[name] is None:
                        extra[name] = j
        else:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: не найдена колонка "
                f"«Наименование чертежа» — лист пропущен.")
            continue

        sheet_rows = 0
        for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
            if row is None or all(
                    v is None or str(v).strip() == "" for v in row):
                if sheet_rows:
                    break  # пустая строка после данных — конец таблицы
                continue
            title = _clean_str(row[cols["title"]]) \
                if len(row) > cols["title"] else ""
            if not title:
                continue

            # Пропускаем строки-заголовки разделов ("Надземная часть Корпус 1",
            # "Подземная часть" и т. п.) — они не являются чертежами.
            if _looks_like_section_header(title):
                continue

            kit_code = default_kit
            if "kit_code" in kit_cols and len(row) > kit_cols["kit_code"]:
                kit_code = _norm_complex_code(row[kit_cols["kit_code"]]) \
                    or default_kit
            if not kit_code:
                warnings.append(
                    f"{filename} · лист «{sheet_name}»: строка без шифра "
                    f"комплекта пропущена (задайте шифр при загрузке).")
                continue

            row_data = {
                "kit_code": kit_code,
                "title": title,
                "qty": 1,
                "planned_date": None,
            }
            for name in ("discipline", "section"):
                j = extra.get(name)
                if j is not None and len(row) > j:
                    row_data[name] = _clean_str(row[j])
            j = extra.get("qty")
            if j is not None and len(row) > j:
                try:
                    q = int(float(str(row[j]).replace(",", ".")))
                    if q > 0:
                        row_data["qty"] = q
                except (TypeError, ValueError):
                    pass
            j = extra.get("planned_date")
            if j is not None and len(row) > j:
                row_data["planned_date"] = _parse_date_value(row[j])
            row_data["source_file"] = filename
            rows.append(row_data)
            sheet_rows += 1

        if sheet_rows == 0:
            warnings.append(
                f"{filename} · лист «{sheet_name}»: строки чертежей "
                f"не найдены.")
    wb.close()

    if not rows and not warnings:
        warnings.append(f"{filename}: не найдено ни одной строки чертежей.")
    return rows, warnings


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


def build_plan_export(kits_df, drawings_df, period_label: str = "") -> bytes:
    """
    Excel-файл плана выдачи: лист «Комплекты» + лист «Чертежи (план)».
    Подаются ОТОБРАННЫЕ (по фильтрам и периоду) DataFrame.
    """
    wb = openpyxl.Workbook()

    # ---- Лист 1: Комплекты ----
    ws = wb.active
    ws.title = "Комплекты"
    kit_headers = ["Шифр комплекта", "Наименование комплекта",
                   "Дисциплина", "Раздел", "Общий статус",
                   "Листов в Витро", "Листов (план)",
                   "Срок готовности", "Статус (ведомость)",
                   "Обновлён", "Кем"]
    kit_rows = []
    for _, r in kits_df.iterrows():
        kit_rows.append([
            r.get("code", ""),
            r.get("name", "") or "",
            r.get("discipline", "") or "",
            r.get("section", "") or "",
            r.get("status", "") or "",
            int(r.get("sheets_vitro", 0) or 0),
            int(r.get("sheets_plan", 0) or 0),
            _iso_to_display(r.get("due_date")),
            r.get("plan_status", "") or "",
            r.get("updated_at", "") or "",
            r.get("updated_by", "") or "",
        ])
    _style_table(ws, kit_headers, kit_rows)

    # ---- Лист 2: Чертежи (план) ----
    ws2 = wb.create_sheet("Чертежи (план)")
    dr_headers = ["Дисциплина", "Раздел", "Комплект(ы)", "Наименование",
                  "Количество чертежей (план)", "Плановая дата",
                  "Общий статус комплекта"]
    dr_rows = []
    for _, r in drawings_df.iterrows():
        dr_rows.append([
            r.get("discipline", "") or "",
            r.get("section", "") or "",
            r.get("kit_code", "") or "",
            r.get("title", "") or "",
            int(r.get("qty", 1) or 1),
            _iso_to_display(r.get("planned_date")),
            r.get("status", "") or "",
        ])
    _style_table(ws2, dr_headers, dr_rows)

    if period_label:
        ws3 = wb.create_sheet("Параметры выгрузки")
        _style_table(ws3, ["Параметр", "Значение"], [
            ["Период", period_label],
            ["Дата формирования", datetime.now().strftime("%d.%m.%Y %H:%M")],
            ["Комплектов в выгрузке", len(kit_rows)],
            ["Позиций чертежей в выгрузке", len(dr_rows)],
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _iso_to_display(iso) -> str:
    """'YYYY-MM-DD' → 'ДД.ММ.ГГГГ' для Excel-выгрузки."""
    if not iso:
        return ""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)
