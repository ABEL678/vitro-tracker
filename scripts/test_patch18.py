# scripts/test_patch18.py
"""Тесты патча 18.

Блок 5: валидация шифров (is_valid_kit_code) — примеры из ТЗ;
        сводка «всего / распознано / отброшено и почему» в парсерах;
        служебный лист «Легенда» пропускается молча.
Блок 6: колонки «Ревизия комплекта» и «Количество листов» в Сводке.

Запуск: python scripts/test_patch18.py
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl

from vitro.sheets import is_valid_kit_code
from vitro.plan_xlsx import parse_kits_workbook, parse_drawings_workbook

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------------------
# 1. is_valid_kit_code — примеры из ТЗ (Блок 5)
# ---------------------------------------------------------------------------
MUST_PASS = [
    "АТ-РД-ЛВС.АИС-С-00",   # точка в середине (марка)
    "АТ-РД-КМ2-1-02.1",     # точка с цифрой в конце (индекс)
    "АТ-РД-АР3-1-08Т",      # буква в конце
    "АТ-РД-КЖ1-1-00",       # базовый вид
    "АТ-РД-АР12.1-1-00",    # точка в марке (патч 12)
    "АТ-РД-СМИК-0-00",      # блок «0»
    "АТ-РД-ОВ2-С-00",       # латинские двойники нормализуются до проверки
    "АТ-РД-ОВ1-С-00-10",    # 6-й блок без точки: с патча 19 это
                            # приложение-комплект (было в MUST_FAIL)
]
MUST_FAIL = [
    "АТ-РД-КЖ1,2-С-П2.3.ВР",  # запятая — объединение двух комплектов
    "АТ-РД-КЖ1,2-С-00",       # запятая в марке
    "АТ-РД-АР1-С-00-1.1",     # полный шифр листа (точка в 6-м блоке)
    "АТ-РД-ОВ1-С-00-10.01",   # полный шифр листа (точка в 6-м блоке)
    "ИТОГО", "Всего", "Подземная часть", "Легенда",
    "СО", "ВР", "П.01", "12.05.2026", "",
    "АТ-РД-КЖ1-С-",           # хвост на дефисе
    "АТ-РД-КЖ1-С-00.",        # хвост на точке
]
for code in MUST_PASS:
    check(f"is_valid_kit_code('{code}') == True", is_valid_kit_code(code))
for code in MUST_FAIL:
    check(f"is_valid_kit_code('{code}') == False", not is_valid_kit_code(code))

# латинские двойники (как из CAD) — через нормализацию plan_xlsx
from vitro.plan_xlsx import _kit_code_of  # noqa: E402
check("_kit_code_of('AT-PД-KЖ1-1-00' с латиницей)",
      _kit_code_of("AT-РД-KM2-1-02.1") == "АТ-РД-КМ2-1-02.1",
      f"получено: {_kit_code_of('AT-РД-KM2-1-02.1')!r}")

# ---------------------------------------------------------------------------
# 2. parse_kits_workbook — сводка «всего/распознано/отброшено и почему»
# ---------------------------------------------------------------------------
def build_kits_xlsx(rows: list[list], sheet_name: str = "Комплекты") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(["Шифр комплекта", "Наименование комплекта",
               "Дисциплина", "Раздел", "Статус", "Срок готовности"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


kits_rows = [
    ["АТ-РД-ЛВС.АИС-С-00", "ЛВС и АИС", "01_ИС", "Секция 00", "", None],
    ["АТ-РД-КМ2-1-02.1", "КМ корпус 2", "03_ОВ", "Корпус 2", "", None],
    ["АТ-РД-АР3-1-08Т", "АР корпус 3", "01_АР", "Корпус 3", "", None],
    ["АТ-РД-КЖ1-1-00", "КР секция 00", "02_КР", "Секция 00", "", None],
    ["АТ-РД-АР12.1-1-00", "АР 12.1", "01_АР", "Секция 00", "", None],
    ["АТ-РД-КЖ1,2-С-П2.3.ВР", "мусор с запятой", "", "", "", None],
    ["АТ-РД-АР1-С-00-1.1", "полный шифр листа", "", "", "", None],
    ["ИТОГО", "итог", "", "", "", None],
]
data = build_kits_xlsx(kits_rows)
rows, warnings = parse_kits_workbook(data, "Разрез_Комплект.xlsx")
check("распознано 5 комплектов из 8 строк", len(rows) == 5,
      f"распознано {len(rows)}")
summary_lines = [w for w in warnings if "всего строк — 8" in w]
check("предупреждение-сводка присутствует", bool(summary_lines),
      f"warnings={warnings}")
if summary_lines:
    s = summary_lines[0]
    check("в сводке: распознано комплектов — 5",
          "распознано комплектов — 5" in s, s)
    check("в сводке: отброшено — 3", "отброшено — 3" in s, s)
    check("в сводке: причина «запятая»", "запятая" in s, s)
    check("в сводке: причина «полный шифр листа»",
          "полный шифр листа" in s, s)
    check("в сводке: причина «служебная строка»",
          "служебная строка" in s, s)
examples_lines = [w for w in warnings if "примеры отброшенных" in w]
check("примеры отброшенных шифров с запятой",
      bool(examples_lines) and "АТ-РД-КЖ1,2-С-П2.3.ВР" in examples_lines[0])

# служебный лист «Легенда» — молча, без предупреждений о колонках
wb = openpyxl.Workbook()
ws_leg = wb.active
ws_leg.title = "Легенда"
ws_leg.append(["Это служебный лист, описание колонок"])
ws_data = wb.create_sheet("Данные")
ws_data.append(["Шифр комплекта", "Наименование комплекта"])
ws_data.append(["АТ-РД-КЖ1-1-00", "КР секция 00"])
buf = io.BytesIO()
wb.save(buf)
rows2, warnings2 = parse_kits_workbook(buf.getvalue(), "с_легендой.xlsx")
check("данные с листа «Данные» распознаны", len(rows2) == 1)
check("лист «Легенда» пропущен молча (нет предупреждения о колонках)",
      not any("не найдены колонки" in w or "Легенда" in w
              for w in warnings2),
      f"warnings={warnings2}")

# ---------------------------------------------------------------------------
# 3. parse_drawings_workbook — сводка + «Легенда»
# ---------------------------------------------------------------------------
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Чертежи"
ws.append(["Шифр", "Название листа", "Шифр листа"])
ws.append(["АТ-РД-ОВ1-С-00 Система отопления", "Шапка комплекта", ""])
ws.append(["", "Отопление общие данные", "АТ-РД-ОВ1-С-00-10.01"])
ws.append(["", "Подземная часть", ""])          # строка-раздел
ws.append(["", "", ""])                          # пустая строка
ws_leg = wb.create_sheet("Легенда")
ws_leg.append(["служебный лист"])
buf = io.BytesIO()
wb.save(buf)
d_rows, d_warnings, d_kits = parse_drawings_workbook(
    buf.getvalue(), "реестр_ОВ.xlsx")
check("распознан 1 лист чертежей", len(d_rows) == 1,
      f"распознано {len(d_rows)}")
check("комплект из шапки распознан",
      "АТ-РД-ОВ1-С-00" in d_kits, f"kits={d_kits}")
d_summary = [w for w in d_warnings if "всего строк — 3" in w]
check("сводка по чертежам присутствует", bool(d_summary),
      f"warnings={d_warnings}")
if d_summary:
    s = d_summary[0]
    check("в сводке: шапок комплектов — 1", "шапок комплектов — 1" in s, s)
    check("в сводке: служебные строки/разделы — 1",
          "служебные строки/разделы — 1" in s, s)
check("«Легенда» в чертежах пропущена молча",
      not any("Легенда" in w for w in d_warnings))

# ---------------------------------------------------------------------------
# 4. Блок 6: kit_revision / docs_total в Сводке (без Streamlit-кэша)
# ---------------------------------------------------------------------------
import pandas as pd  # noqa: E402
import vitro.ui.summary as summary_mod  # noqa: E402

docs_df = pd.DataFrame([
    {"complex": "АТ-РД-КЖ1-1-00", "section": "Секция 00",
     "status": "A", "revision": "1.0"},
    {"complex": "АТ-РД-КЖ1-1-00", "section": "Секция 00",
     "status": "B", "revision": "2.0"},
    {"complex": "АТ-РД-КЖ1-1-00", "section": "Секция 00",
     "status": "C", "revision": "1.0"},
    {"complex": "АТ-РД-КМ2-1-02.1", "section": "Корпус 2",
     "status": "B", "revision": "10.0"},
])
res = summary_mod._build_summary_rows("complex", pd.DataFrame(), docs_df)
row1 = res[res["Комплект"] == "АТ-РД-КЖ1-1-00"].iloc[0]
row2 = res[res["Комплект"] == "АТ-РД-КМ2-1-02.1"].iloc[0]
check("docs_total комплекта = 3", int(row1["docs_total"]) == 3,
      str(row1["docs_total"]))
check("ревизия комплекта = «2.0» (макс. численно)",
      row1["kit_revision"] == "2.0", str(row1["kit_revision"]))
check("ревизия «10.0» больше «2.0» численно",
      row2["kit_revision"] == "10.0", str(row2["kit_revision"]))

res_sec = summary_mod._build_summary_rows("section", pd.DataFrame(), docs_df)
check("для разреза по разделам kit_revision пуст",
      bool(res_sec.empty) or str(res_sec.iloc[0]["kit_revision"]) == "")
# _render_summary_table — проверяем раскладку колонок без рендера:
# извлекаем логику переименований через прямую сборку таблицы.
view = res.copy()
view = view.rename(columns={"docs_total": "Количество листов",
                            "kit_revision": "Ревизия комплекта"})
cols = ["Комплект", "Ревизия комплекта", "Количество листов"]
check("колонки присутствуют после переименования",
      all(c in view.columns for c in cols), str(list(view.columns)))

# ---------------------------------------------------------------------------
print()
if FAILED:
    print(f"ПРОВАЛЕНО тестов: {len(FAILED)}: {FAILED}")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ПАТЧА 18 ПРОЙДЕНЫ")
