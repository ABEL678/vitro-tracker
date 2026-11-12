# scripts/test_patch19.py
"""Тесты патча 19 «РД-коды любых организаций + дробные шифры + диагностика».

Блок 1: looks_like_rd_code — светлая проверка формы РД-кода ЛЮБОЙ
        организации (замена startswith("АТ-РД-") в sharepoint.py);
        parse_path находит папки ГИС (если requests_ntlm установлен).
Блок 2: is_valid_kit_code — 6-й блок без точки (приложения-комплекты,
        «ГИС-РД-СБ-ООС-С-00») и дробные шифры
        «ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1» с лимитами
        (цифр/точек ≤ 20, суффиксов ≤ 2).
Блок 3: _kit_drop_reason — новая причина 'fractional';
        сводка parse_kits_workbook упоминает дробные шифры.
Плюс: unique_vitro_sheets пропускает листы ГИС-комплектов.

Запуск: python scripts/test_patch19.py
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl

from vitro.sheets import is_valid_kit_code, looks_like_rd_code
from vitro.plan_xlsx import parse_kits_workbook, _kit_drop_reason

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------------------
# 1. Блок 2: is_valid_kit_code — 6-й блок и дробные шифры
# ---------------------------------------------------------------------------
MUST_PASS = [
    # 6-й блок без точки (согласованное правило)
    "ГИС-РД-СБ-ООС-С-00",       # комплект ГИС из Витро
    "АТ-РД-АР1-С-00-СО",        # приложение-комплект
    "АТ-РД-КЖ1-1-00-ВР",        # приложение-комплект
    "АТ-РД-ОВ1-С-00-10",        # было MUST_FAIL в патче 18
    # классика патчей 12/18 — не сломана
    "АТ-РД-КЖ1-1-00",
    "АТ-РД-ЛВС.АИС-С-00",
    "АТ-РД-КМ2-1-02.1",
    "АТ-РД-АР3-1-08Т",
    # дробные шифры
    "ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1",   # целевой кейс из ТЗ
    "ГИС-РД-СБ-ООС-С-00/10511.25.01",        # без суффиксов
    "АТ-РД-КЖ1-1-00/10511.25.01-ООС1",       # дробь на 5-блочной базе
    # границы лимитов: 20 цифр/точек и 2 суффикса — допустимо
    "АТ-РД-КЖ1-1-00/" + "1" * 20,
    "АТ-РД-КЖ1-1-00/10.5.2-ООС1-КМ2",
]
MUST_FAIL = [
    # полные шифры листов — как и раньше
    "АТ-РД-АР1-С-00-1.1",       # точка в 6-м блоке
    "АТ-РД-ОВ1-С-00-10.01",     # точка в 6-м блоке
    "ГИС-РД-СБ-ООС-С-00-1.1",   # 7 блоков (лист 6-блочной базы)
    # запятая — по-прежнему гарантированный отказ
    "АТ-РД-КЖ1,2-С-П2.3.ВР",
    "АТ-РД-КЖ1,2-С-00",
    # дробная ветка за лимитами
    "ГИС-РД-СБ-ООС-С-00/" + "1" * 21,        # 21 цифра
    "ГИС-РД-СБ-ООС-С-00/10.5.2-ООС1-КМ2-ХХ", # 3 суффикса
    "ГИС-РД-СБ-ООС-С-00/АБВ-ООС1",           # числовая часть с буквы
    "ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1-1.1",  # шифр ЛИСТА дробного к-та
    "ГИС-РД-СБ-ООС-С-00/10511.25.01/",       # пустой хвост после «/»
    "/10511.25.01",                          # пустая основная часть
    "АТ-РД-КЖ1-1-00/10511.25.01/10512",      # второй слэш
    "АТ-РД-КЖ1-С-",                          # хвост на дефисе
    "АТ-РД-КЖ1-С-00.",                       # хвост на точке
    "ИТОГО", "Всего", "Подземная часть", "12.05.2026", "",
]
for code in MUST_PASS:
    check(f"is_valid_kit_code('{code}') == True", is_valid_kit_code(code))
for code in MUST_FAIL:
    check(f"is_valid_kit_code('{code}') == False", not is_valid_kit_code(code))

# ---------------------------------------------------------------------------
# 2. Блок 1: looks_like_rd_code — форма РД-кода любой организации
# ---------------------------------------------------------------------------
RD_MUST_PASS = [
    "АТ-РД-КЖ1-1-00",
    "ГИС-РД-СБ-ООС-С-00",                    # организация ГИС
    "ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1",   # дробный код папки/файла
    "АТ-РД-КЖ1-1-00-1.1",                    # имя листа (файла)
    "АТ-РД-СП-0-00.PDF",                     # лист с расширением
    " ат-рд-кж1-1-00 ",                      # регистр и пробелы не важны
]
RD_MUST_FAIL = [
    "", None,
    "АТ-РД-",                 # нет блоков после РД
    "ИТОГО", "Всего",
    "Подземная часть",        # строка-раздел
    "02_КР", "03_ОВ",         # папки-дисциплины
    "12.05.2026",             # дата
    "Схема проезда",          # произвольное имя
    "ОТ", "РД",               # огрызки
]
for name in RD_MUST_PASS:
    check(f"looks_like_rd_code('{name}') == True", looks_like_rd_code(name))
for name in RD_MUST_FAIL:
    check(f"looks_like_rd_code({name!r}) == False", not looks_like_rd_code(name))

# ---------------------------------------------------------------------------
# 3. Блок 1: parse_path находит комплекты ГИС (нужен requests_ntlm)
# ---------------------------------------------------------------------------
try:
    from vitro.sharepoint import parse_path  # noqa: E402
    HAVE_SP = True
except Exception as e:  # ImportError и прочее (нет .env/зависимостей)
    HAVE_SP = False
    print(f"[SKIP] parse_path: не импортировался vitro.sharepoint ({e})")

if HAVE_SP:
    WORK = "/okhta/DocProjectLib/01_РД/02_АТП/01_В процессе"
    disc, sec, comp = parse_path(f"{WORK}/03_ГС/ГИС-РД-СБ-ООС-С-00")
    check("parse_path: папка ГИС найдена как комплект",
          (disc, sec, comp) == ("03_ГС", "", "ГИС-РД-СБ-ООС-С-00"),
          f"{disc!r}, {sec!r}, {comp!r}")
    disc, sec, comp = parse_path(f"{WORK}/02_КР/Секции/АТ-РД-КЖ1-1-00")
    check("parse_path: АТ-РД по-прежнему работает",
          (disc, sec, comp) == ("02_КР", "Секции", "АТ-РД-КЖ1-1-00"),
          f"{disc!r}, {sec!r}, {comp!r}")
    disc, sec, comp = parse_path(f"{WORK}/01_АР/Надземная часть")
    check("parse_path: папка-раздел без комплекта → comp=''",
          comp == "", f"{disc!r}, {sec!r}, {comp!r}")

# ---------------------------------------------------------------------------
# 4. Блок 3: _kit_drop_reason — новая причина 'fractional'
# ---------------------------------------------------------------------------
check("drop_reason: запятая → 'comma'",
      _kit_drop_reason("АТ-РД-КЖ1,2-С-П2.3.ВР") == "comma",
      _kit_drop_reason("АТ-РД-КЖ1,2-С-П2.3.ВР"))
check("drop_reason: полный шифр листа → 'sheet_code'",
      _kit_drop_reason("АТ-РД-АР1-С-00-1.1") == "sheet_code",
      _kit_drop_reason("АТ-РД-АР1-С-00-1.1"))
check("drop_reason: 7 блоков → 'sheet_code'",
      _kit_drop_reason("ГИС-РД-СБ-ООС-С-00-1.1") == "sheet_code",
      _kit_drop_reason("ГИС-РД-СБ-ООС-С-00-1.1"))
check("drop_reason: дробь за лимитами → 'fractional'",
      _kit_drop_reason("ГИС-РД-СБ-ООС-С-00/" + "1" * 21) == "fractional",
      _kit_drop_reason("ГИС-РД-СБ-ООС-С-00/" + "1" * 21))
check("drop_reason: лист дробного комплекта → 'fractional'",
      _kit_drop_reason("ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1-1.1")
      == "fractional",
      _kit_drop_reason("ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1-1.1"))
check("drop_reason: служебная строка → 'other'",
      _kit_drop_reason("Подземная часть") == "other",
      _kit_drop_reason("Подземная часть"))

# ---------------------------------------------------------------------------
# 5. Блок 3: сводка parse_kits_workbook упоминает дробные шифры
# ---------------------------------------------------------------------------
def build_kits_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Комплекты"
    ws.append(["Шифр комплекта", "Наименование комплекта"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


rows_data = [
    ["ГИС-РД-СБ-ООС-С-00", "Сборка ООС — теперь распознаётся (патч 19)"],
    ["АТ-РД-КЖ1-1-00", "КР секция 00"],
    ["ГИС-РД-СБ-ООС-С-00/" + "1" * 21, "дробь за лимитом"],
    ["АТ-РД-АР1-С-00-1.1", "полный шифр листа"],
    ["Всего", "итог"],
]
k_rows, k_warnings = parse_kits_workbook(
    build_kits_xlsx(rows_data), "Разрез_Комплект.xlsx")
check("распознано 2 комплекта (ГИС + АТ)", len(k_rows) == 2,
      f"распознано {len(k_rows)}: {[r['code'] for r in k_rows]}")
summary = [w for w in k_warnings if "всего строк — 5" in w]
check("сводка присутствует", bool(summary), f"warnings={k_warnings}")
if summary:
    s = summary[0]
    check("в сводке: дробный шифр за лимитами",
          "дробный шифр" in s, s)
    check("в сводке: полный шифр листа",
          "полный шифр листа" in s, s)
    check("в сводке: отброшено — 3", "отброшено — 3" in s, s)

# ---------------------------------------------------------------------------
# 6. Единый стандарт: unique_vitro_sheets считает листы ГИС-комплектов
# ---------------------------------------------------------------------------
from vitro.sheets import unique_vitro_sheets  # noqa: E402

docs = [
    {"id": 1, "complex": "ГИС-РД-СБ-ООС-С-00",
     "sheet_number": "10", "status": "A", "status_date": "2026-01-01"},
    {"id": 2, "complex": "ГИС-РД-СБ-ООС-С-00",
     "sheet_number": "11", "status": "B", "status_date": "2026-01-02"},
    {"id": 3, "complex": "АТ-РД-КЖ1-1-00",
     "sheet_number": "10.01", "status": "C", "status_date": "2026-01-03"},
    {"id": 4, "complex": "ГИС-РД-СБ-ООС-С-00/10511.25.01-ООС1",
     "sheet_number": "1", "status": "B", "status_date": "2026-01-04"},
    {"id": 5, "complex": "Подземная часть",
     "sheet_number": "1", "status": "A", "status_date": "2026-01-05"},
    {"id": 6, "complex": "ГИС-РД-СБ-ООС-С-00-1.1",
     "sheet_number": "1", "status": "A", "status_date": "2026-01-06"},
]
kept = unique_vitro_sheets(docs)
kept_ids = sorted(r["id"] for r in kept)
check("unique_vitro_sheets: ГИС-комплекты считаются, мусор отсечён",
      kept_ids == [1, 2, 3, 4], f"остались id {kept_ids}")

# ---------------------------------------------------------------------------
print()
if FAILED:
    print(f"ПРОВАЛЕНО тестов: {len(FAILED)}: {FAILED}")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ПАТЧА 19 ПРОЙДЕНЫ")
