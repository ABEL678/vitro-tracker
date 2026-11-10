# test_patch08.py — функциональные тесты финальной ревизии вкладки «План выдачи»
"""
Проверяет (PATCH-08):
  1. Сводная таблица: группировка «Дисциплина → Раздел» строками-заголовками,
     строка «ИТОГО» с суммами, valid_codes без служебных строк.
  2. _save_due_dates: игнорирует служебные строки, None/NaT равны,
     реальные изменения попадают в UPDATE.
  3. Таблица А: колонки, фильтр по периоду (срок готовности).
  4. Таблица Б: колонки, данные plan_drawings, «Фактическая дата» из
     documents (совпадение и отсутствие), фильтр по периоду.
  5. build_plan_export: файл из листов «Комплекты» + «Листы»
     (+ «Параметры выгрузки»), даты ДД.ММ.ГГГГ, содержимое = таблицам.
Запуск: python3 scripts/test_patch08.py
"""
import os
import sys
from datetime import date

REPO = "/home/z/my-project/vitro-tracker"
sys.path.insert(0, REPO)
os.environ.setdefault("SQLITE_PATH", "data/vitro_test.db")
os.chdir(REPO)

import pandas as pd  # noqa: E402
import openpyxl  # noqa: E402

import vitro.ui.plan as plan  # noqa: E402
import vitro.sqlite_db as db  # noqa: E402

db.init_db()
db.clear_plan()
with db.get_conn() as conn:
    conn.execute("DELETE FROM documents")

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------------
# Фикстура: комплекты (2 дисциплины / 3 раздела), листы, документы
# ------------------------------------------------------------------
kits = [
    # code, name, discipline, section, due_date
    ("АТ-РД-АР1-С-00", "Архитектура, стилобат", "01_АР", "Подземная часть", "2026-05-01"),
    ("АТ-РД-АР1-1-00", "Архитектура, корпус 1", "01_АР", "Корпус 1", None),
    ("АТ-РД-КЖ1-С-00", "Конструкции, стилобат", "02_КР", "Подземная часть", "2026-06-15"),
    ("АТ-РД-ОВ1-С-00", "Отопление, стилобат", "03_ОВ", "Подземная часть", "2026-07-01"),
    ("АТ-РД-ОВ1-1-00", "Отопление, корпус 1", "03_ОВ", "Корпус 1", "2026-08-10"),
]
db.upsert_plan_kits(
    [{"code": c, "name": n, "discipline": d, "section": s, "due_date": du}
     for c, n, d, s, du in kits], "тест")

drawings = [
    # kit, sheet_no, title, planned_date
    ("АТ-РД-АР1-С-00", "1", "Общие данные", "2026-04-01"),
    ("АТ-РД-АР1-С-00", "2.1", "План фундаментов", None),
    ("АТ-РД-КЖ1-С-00", "10.01", "Фундаментная плита", "2026-06-01"),
    ("АТ-РД-ОВ1-С-00", "31.00", "Схема отопления", "2026-07-01"),
]
db.replace_plan_drawings_for_kits(
    [{"kit_code": k, "sheet_no": sn, "title": t, "qty": 1,
      "planned_date": pd_, "discipline": "", "section": ""}
     for k, sn, t, pd_ in drawings], "тест")

docs = [
    # complex, sheet_number, status_date  (лист «1» АР загружен в Витро)
    ("АТ-РД-АР1-С-00", "1", "2026-04-20"),
    ("АТ-РД-АР1-С-00", "1", "2026-04-25"),  # ревизия позже — MAX
    ("АТ-РД-КЖ1-С-00", "99.99", "2026-01-01"),  # не совпадает по номеру
]
with db.get_conn() as conn:
    conn.executemany("""
        INSERT INTO documents (leaf, complex, sheet_number, status_date)
        VALUES ('л', ?, ?, ?)
    """, docs)

overview_all = plan._build_overview(include_vitro_only=False)
check("overview_rows", len(overview_all) == 5, f"n={len(overview_all)}")

flt_empty = {"disc": (), "section": (), "kits": (), "block_codes": (),
             "marka": "", "extra": {"kit_status": (), "only_dated": False}}

# ------------------------------------------------------------------
# 1. Сводная таблица: группировка + ИТОГО
# ------------------------------------------------------------------
display, valid_codes = plan._overview_to_grouped_display(overview_all)
check("grouped_columns", list(display.columns) == plan.OVERVIEW_COLUMNS,
      f"cols={list(display.columns)}")
codes_col = display["Шифр комплекта"].tolist()
groups = [c for c in codes_col if str(c).startswith("📁")]
check("group_headers_count", len(groups) == 5, f"groups={groups}")
check("group_order", groups == sorted(groups), f"groups={groups}")
check("total_row_last", codes_col[-1] == "ИТОГО", f"last={codes_col[-1]!r}")
total_row = display.iloc[-1]
# в Витро: АР1-С-00 — 2 листа (две ревизии), КЖ1-С-00 — 1 лист = 3
check("total_vitro_sheets", total_row["Листов в Витро"] == 3,
      f"vitro={total_row['Листов в Витро']}")
check("total_plan", total_row["Листов (план)"] == 4,
      f"plan={total_row['Листов (план)']}")
check("total_name", "5" in str(total_row["Наименование комплекта"]),
      f"name={total_row['Наименование комплекта']!r}")
check("valid_codes", valid_codes == {k[0] for k in kits},
      f"codes={valid_codes}")
# суммы в заголовке группы АР·Подземная: 1 комплект, 2 листа в Витро, 2 план
g0 = display[display["Шифр комплекта"] == "📁 01_АР · Подземная часть"].iloc[0]
check("group0_sums",
      g0["Листов в Витро"] == 2 and g0["Листов (план)"] == 2,
      f"vitro={g0['Листов в Витро']}, plan={g0['Листов (план)']}")
# срок готовности комплекта — тип date
row_ar = display[display["Шифр комплекта"] == "АТ-РД-АР1-С-00"].iloc[0]
check("due_is_date", row_ar["Срок готовности"] == date(2026, 5, 1),
      f"due={row_ar['Срок готовности']!r}")

# ------------------------------------------------------------------
# 2. _save_due_dates: служебные строки, None/NaT, реальные изменения
# ------------------------------------------------------------------
captured = {}
orig_update = plan.update_plan_due_dates
plan.update_plan_due_dates = lambda changes, user="": (
    captured.update(changes) or len(changes))
orig_user = plan._current_user
plan._current_user = lambda: "Тест-инженер"

edited = display.copy()
# меняем срок реального комплекта
i_ar = edited.index[edited["Шифр комплекта"] == "АТ-РД-АР1-С-00"][0]
edited.iloc[i_ar, edited.columns.get_loc("Срок готовности")] = date(2026, 5, 20)
# «меняем» NaT там, где был None (незатронутая ячейка data_editor)
i_none = edited.index[edited["Шифр комплекта"] == "АТ-РД-АР1-1-00"][0]
edited.iloc[i_none, edited.columns.get_loc("Срок готовности")] = pd.NaT
# «меняем» дату в служебной строке ИТОГО
edited.iloc[-1, edited.columns.get_loc("Срок готовности")] = date(2030, 1, 1)

n = plan._save_due_dates(display, edited, valid_codes)
check("save_changed_one", n == 1, f"n={n}, captured={captured}")
check("save_real_code_only",
      captured.get("АТ-РД-АР1-С-00") == "2026-05-20" and
      "ИТОГО" not in captured and
      all(not str(k).startswith("📁") for k in captured),
      f"captured={captured}")
# повторное «сохранение» без изменений — 0 (нет зацикливания)
n2 = plan._save_due_dates(edited, edited.copy(), valid_codes)
check("save_idempotent", n2 == 0, f"n2={n2}")
# без пользователя — возвращает -1 и ничего не сохраняет
plan._current_user = lambda: ""
captured.clear()
n3 = plan._save_due_dates(display, edited, valid_codes)
check("save_needs_user", n3 == -1 and not captured,
      f"n3={n3}, captured={captured}")
plan._current_user = orig_user
plan.update_plan_due_dates = orig_update

# ------------------------------------------------------------------
# 3. Таблица А: колонки + период
# ------------------------------------------------------------------
ta = plan._build_kit_plan_table(overview_all, flt_empty, None, None)
check("ta_columns", list(ta.columns) == plan.KIT_PLAN_COLUMNS,
      f"cols={list(ta.columns)}")
check("ta_all_kits", len(ta) == 5, f"n={len(ta)}")
check("ta_date_type",
      ta["Плановая дата (срок готовности)"].iloc[0] is None or
      hasattr(ta["Плановая дата (срок готовности)"].iloc[0], "year"), "")

ta_period = plan._build_kit_plan_table(
    overview_all, flt_empty, date(2026, 5, 1), date(2026, 6, 30))
check("ta_period", sorted(ta_period["Комплект(ы)"]) ==
      ["АТ-РД-АР1-С-00", "АТ-РД-КЖ1-С-00"],
      f"kits={sorted(ta_period['Комплект(ы)'])}")

flt_dated = dict(flt_empty, extra={"kit_status": (), "only_dated": True})
ta_dated = plan._build_kit_plan_table(overview_all, flt_dated, None, None)
check("ta_only_dated", sorted(ta_dated["Комплект(ы)"]) ==
      ["АТ-РД-АР1-С-00", "АТ-РД-КЖ1-С-00", "АТ-РД-ОВ1-1-00",
       "АТ-РД-ОВ1-С-00"],
      f"kits={sorted(ta_dated['Комплект(ы)'])}")

# период «с и по» одновременно (без UserWarning, обе границы)
ta_both = plan._build_kit_plan_table(
    overview_all, flt_empty, date(2026, 5, 1), date(2026, 7, 31))
check("ta_period_both", sorted(ta_both["Комплект(ы)"]) ==
      ["АТ-РД-АР1-С-00", "АТ-РД-КЖ1-С-00", "АТ-РД-ОВ1-С-00"],
      f"kits={sorted(ta_both['Комплект(ы)'])}")
tb_both = plan._build_sheet_plan_table(
    overview_all, flt_empty, date(2026, 4, 1), date(2026, 7, 31))
# лист «2.1» без плановой даты наследует срок комплекта (01.05.2026)
check("tb_period_both", sorted(tb_both["Номер листа"]) ==
      ["1", "10.01", "2.1", "31.00"],
      f"sheets={sorted(tb_both['Номер листа'])}")

# ------------------------------------------------------------------
# 4. Таблица Б: колонки, план_чертежи, фактическая дата, период
# ------------------------------------------------------------------
tb = plan._build_sheet_plan_table(overview_all, flt_empty, None, None)
check("tb_columns", list(tb.columns) == plan.SHEET_PLAN_COLUMNS,
      f"cols={list(tb.columns)}")
check("tb_rows", len(tb) == 4, f"n={len(tb)}")
ar1 = tb[(tb["Комплект(ы)"] == "АТ-РД-АР1-С-00") &
         (tb["Номер листа"] == "1")].iloc[0]
check("tb_fact_date", ar1["Фактическая дата"] == date(2026, 4, 25),
      f"fact={ar1['Фактическая дата']!r}")
ar2 = tb[(tb["Комплект(ы)"] == "АТ-РД-АР1-С-00") &
         (tb["Номер листа"] == "2.1")].iloc[0]
check("tb_fact_empty_when_missing", ar2["Фактическая дата"] is None,
      f"fact={ar2['Фактическая дата']!r}")
check("tb_planned_date", ar1["Плановая дата"] == date(2026, 4, 1),
      f"planned={ar1['Плановая дата']!r}")
check("tb_disc_fallback", ar2["Дисциплина"] == "01_АР",
      f"disc={ar2['Дисциплина']!r}")
check("tb_sect_fallback", ar2["Раздел"] == "Подземная часть",
      f"sect={ar2['Раздел']!r}")

tb_period = plan._build_sheet_plan_table(
    overview_all, flt_empty, date(2026, 6, 1), date(2026, 6, 30))
check("tb_period", list(tb_period["Номер листа"]) == ["10.01"],
      f"sheets={list(tb_period['Номер листа'])}")

# ------------------------------------------------------------------
# 5. Выгрузка: два листа «Комплекты» и «Листы»
# ------------------------------------------------------------------
data = plan.build_plan_export(ta, tb, period_label="01.05.2026 — 30.06.2026")
wb = openpyxl.load_workbook(__import__("io").BytesIO(data))
check("export_sheets",
      wb.sheetnames == ["Комплекты", "Листы", "Параметры выгрузки"],
      f"sheets={wb.sheetnames}")
ws_k = wb["Комплекты"]
hdr_k = [c.value for c in ws_k[1]]
check("export_kit_headers", hdr_k == plan.KIT_PLAN_COLUMNS, f"{hdr_k}")
ws_s = wb["Листы"]
hdr_s = [c.value for c in ws_s[1]]
check("export_sheet_headers", hdr_s == plan.SHEET_PLAN_COLUMNS, f"{hdr_s}")
# значение даты на листе «Комплекты» — ДД.ММ.ГГГГ
d_cell = ws_k.cell(row=2, column=3).value
check("export_date_fmt", d_cell == "01.05.2026", f"{d_cell!r}")
# лист «Листы»: фактическая дата текстом ДД.ММ.ГГГГ или пусто
fact_vals = {ws_s.cell(row=i, column=1).value:
             ws_s.cell(row=i, column=7).value for i in range(2, 6)}
check("export_rows_count", ws_k.max_row - 1 == 5 and ws_s.max_row - 1 == 4,
      f"kits={ws_k.max_row - 1}, sheets={ws_s.max_row - 1}")
p_ws = wb["Параметры выгрузки"]
check("export_params_period",
      p_ws.cell(row=2, column=2).value == "01.05.2026 — 30.06.2026",
      f"{p_ws.cell(row=2, column=2).value!r}")

# ------------------------------------------------------------------
print()
if FAILURES:
    print(f"ИТОГ: {len(FAILURES)} провалено: {FAILURES}")
    sys.exit(1)
print("ИТОГ: все проверки пройдены")
