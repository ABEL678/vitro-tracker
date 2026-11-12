# test_patch09.py — функциональные тесты финальной чистки вкладки «План выдачи»
"""
Проверяет (PATCH-09):
  1. Таблица А удалена: нет _build_kit_plan_table / _render_kit_plan_table /
     KIT_PLAN_COLUMNS; осталась только таблица Б (по листам).
  2. Период «с/по» применяется к сводной таблице — фильтр по
     «Сроку готовности» (_apply_period_to_overview); комплекты без срока
     при активном периоде скрываются.
  3. Бублики (_plan_totals): «Листов всего» = max(план, Витро) —
     регрессия на кейс пользователя 12 880 / 9 142 / 3 738; комплекты
     «Только в Витро» не теряются.
  4. Таблица Б: колонка «Статус выдачи» («Выдан» / «Планируется»),
     период по «Плановой дате» (fallback — срок комплекта).
  5. build_plan_export: ОДИН лист данных «Листы» + «Параметры выгрузки»;
     заголовки = SHEET_PLAN_COLUMNS, даты ДД.ММ.ГГГГ.
Запуск: python3 scripts/test_patch09.py
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
import io  # noqa: E402

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
# Фикстура: 4 комплекта в плане + 1 «Только в Витро», листы, документы
# ------------------------------------------------------------------
kits = [
    # code, name, discipline, section, due_date
    ("АТ-РД-АР1-С-00", "Архитектура, стилобат", "01_АР", "Подземная часть", "2026-05-01"),
    ("АТ-РД-АР1-1-00", "Архитектура, корпус 1", "01_АР", "Корпус 1", None),
    ("АТ-РД-КЖ1-С-00", "Конструкции, стилобат", "02_КР", "Подземная часть", "2026-06-15"),
    ("АТ-РД-ОВ1-С-00", "Отопление, стилобат", "03_ОВ", "Подземная часть", "2026-07-01"),
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
    # complex, sheet_number, status_date
    ("АТ-РД-АР1-С-00", "1", "2026-04-20"),
    ("АТ-РД-АР1-С-00", "1", "2026-04-25"),   # ревизия позже — MAX
    ("АТ-РД-КЖ1-С-00", "99.99", "2026-01-01"),  # номер не совпадает
    # «Только в Витро»: 3 листа комплекта, которого нет в ведомостях
    ("АТ-РД-ГП1-С-00", "1", "2026-03-01"),
    ("АТ-РД-ГП1-С-00", "2", "2026-03-02"),
    ("АТ-РД-ГП1-С-00", "3", "2026-03-03"),
]
with db.get_conn() as conn:
    conn.executemany("""
        INSERT INTO documents (leaf, complex, sheet_number, status_date)
        VALUES ('л', ?, ?, ?)
    """, docs)

flt_empty = {"disc": (), "section": (), "kits": (), "block_codes": (),
             "marka": "", "extra": {"kit_status": (), "only_dated": False}}

overview = plan._build_overview(include_vitro_only=True)
check("overview_incl_vitro_only", len(overview) == 5,
      f"n={len(overview)} (4 план + 1 «Только в Витро»)")


# ------------------------------------------------------------------
# 1. Таблица А удалена
# ------------------------------------------------------------------
check("no_kit_plan_builder", not hasattr(plan, "_build_kit_plan_table"))
check("no_kit_plan_renderer", not hasattr(plan, "_render_kit_plan_table"))
check("no_kit_plan_columns", not hasattr(plan, "KIT_PLAN_COLUMNS"))
check("sheet_cols_has_status",
      plan.SHEET_PLAN_COLUMNS[-1] == "Статус выдачи",
      f"cols={plan.SHEET_PLAN_COLUMNS}")
check("status_constants",
      plan.STATUS_ISSUED == "Выдан" and plan.STATUS_PLANNED == "Планируется")


# ------------------------------------------------------------------
# 2. Период → сводная таблица (по «Сроку готовности»)
# ------------------------------------------------------------------
ovp0 = plan._apply_period_to_overview(overview, None, None)
check("period_none_all_rows", len(ovp0) == 5, f"n={len(ovp0)}")

d_from = date(2026, 5, 1)
d_to = date(2026, 6, 30)
ovp = plan._apply_period_to_overview(overview, d_from, d_to)
check("period_both_codes",
      set(ovp["code"]) == {"АТ-РД-АР1-С-00", "АТ-РД-КЖ1-С-00"},
      f"kits={sorted(ovp['code'])} (без срока — скрыт)")

ovp_from = plan._apply_period_to_overview(overview, d_from, None)
check("period_from_codes",
      set(ovp_from["code"]) ==
      {"АТ-РД-АР1-С-00", "АТ-РД-КЖ1-С-00", "АТ-РД-ОВ1-С-00"},
      f"kits={sorted(ovp_from['code'])}")

# границы включительно
ovp_edge = plan._apply_period_to_overview(
    overview, date(2026, 5, 1), date(2026, 5, 1))
check("period_inclusive_bounds",
      set(ovp_edge["code"]) == {"АТ-РД-АР1-С-00"},
      f"kits={sorted(ovp_edge['code'])}")

# сводная по периоду: ИТОГО только по комплектам периода.
# Патч 11: лист «1» АР имеет 2 ревизии → считается ОДИН уникальный лист.
# ИТОГО «Листов в Витро» = 1 (АР) + 1 (КЖ) = 2.
disp, valid = plan._overview_to_grouped_display(ovp)
total_row = disp[disp["Шифр комплекта"] == "ИТОГО"].iloc[0]
check("period_overview_total_vitro",
      int(total_row["Листов в Витро"]) == 1 + 1,
      f"vitro={total_row['Листов в Витро']} (АР: 1 уникальный лист "
      "из 2 ревизий + КЖ: 1)")
check("period_overview_total_plan",
      int(total_row["Листов (план)"]) == 2 + 1,
      f"plan={total_row['Листов (план)']}")
check("period_overview_kits",
      total_row["Наименование комплекта"] == "всего комплектов: 2",
      f"name={total_row['Наименование комплекта']}")
check("period_overview_no_none_due",
      "АТ-РД-АР1-1-00" not in set(disp["Шифр комплекта"]))


# ------------------------------------------------------------------
# 3. Бублики: «Листов всего» = max(план, Витро) — патч 09
# ------------------------------------------------------------------
# 3а. Регрессия на числа пользователя (синтетический отбор):
#     в Витро 12 880, в плане 9 142 → всего 12 880, сверх плана 3 738.
n_kits = 479
df_user = pd.DataFrame({
    "code": [f"K{i:03d}" for i in range(n_kits)],
    "in_vitro": [True] * n_kits,
    "sheets_vitro": [12880 // n_kits] * (n_kits - 1) + [12880 - 12880 // n_kits * (n_kits - 1)],
    "sheets_plan": [9142 // n_kits] * (n_kits - 1) + [9142 - 9142 // n_kits * (n_kits - 1)],
})
t = plan._plan_totals(df_user)
check("user_case_total", t["sheets_total"] == 12880, f"total={t['sheets_total']}")
check("user_case_vitro", t["sheets_vitro"] == 12880, f"vitro={t['sheets_vitro']}")
check("user_case_plan", t["sheets_plan"] == 9142, f"plan={t['sheets_plan']}")
check("user_case_excess", t["sheets_excess"] == 3738, f"excess={t['sheets_excess']}")
check("user_case_remaining", t["sheets_remaining"] == 0,
      f"remaining={t['sheets_remaining']}")
check("user_case_done", t["sheets_done"] == 12880, f"done={t['sheets_done']}")
check("user_case_kits", t["kits_total"] == 479 and t["kits_loaded"] == 479)

# 3б. План больше Витро: всего = план, «осталось по плану».
df_pl = pd.DataFrame({
    "code": ["A", "B"],
    "in_vitro": [True, False],
    "sheets_vitro": [40, 0],
    "sheets_plan": [60, 40],
})
t2 = plan._plan_totals(df_pl)
check("plan_case_total", t2["sheets_total"] == 100, f"total={t2['sheets_total']}")
check("plan_case_remaining", t2["sheets_remaining"] == 60,
      f"remaining={t2['sheets_remaining']}")
check("plan_case_done", t2["sheets_done"] == 40, f"done={t2['sheets_done']}")
check("plan_case_excess", t2["sheets_excess"] == 0, f"excess={t2['sheets_excess']}")

# 3в. Реальная фикстура: «Только в Витро» (3 листа) не теряется.
#     Патч 11: листы = УНИКАЛЬНЫЕ sheet_number (ревизия «1» АР — одна).
#     Витро: АР 1 + КЖ 1 + ГП 3 = 5; план: 4 листа. Всего = 5, сверх 1.
t3 = plan._plan_totals(overview)
check("fixture_kits_total", t3["kits_total"] == 5, f"kits={t3['kits_total']}")
check("fixture_kits_loaded", t3["kits_loaded"] == 3, f"loaded={t3['kits_loaded']}")
check("fixture_sheets_vitro", t3["sheets_vitro"] == 5, f"vitro={t3['sheets_vitro']}")
check("fixture_sheets_plan", t3["sheets_plan"] == 4, f"plan={t3['sheets_plan']}")
check("fixture_sheets_total", t3["sheets_total"] == 5, f"total={t3['sheets_total']}")
check("fixture_sheets_excess", t3["sheets_excess"] == 1, f"excess={t3['sheets_excess']}")

# 3г. Пустой отбор — нули.
t4 = plan._plan_totals(pd.DataFrame(columns=["code", "in_vitro",
                                             "sheets_vitro", "sheets_plan"]))
check("empty_totals", all(v == 0 for v in t4.values()), f"totals={t4}")


# ------------------------------------------------------------------
# 4. Таблица Б: «Статус выдачи» + период по «Плановой дате»
# ------------------------------------------------------------------
tb = plan._build_sheet_plan_table(overview, flt_empty, None, None)
check("tb_columns", list(tb.columns) == plan.SHEET_PLAN_COLUMNS,
      f"cols={list(tb.columns)}")
check("tb_rows_all", len(tb) == 4, f"n={len(tb)}")

row1 = tb[(tb["Комплект(ы)"] == "АТ-РД-АР1-С-00") & (tb["Номер листа"] == "1")].iloc[0]
check("tb_fact_issued", row1["Фактическая дата"] == date(2026, 4, 25)
      and row1["Статус выдачи"] == "Выдан",
      f"fact={row1['Фактическая дата']}, status={row1['Статус выдачи']}")

row2 = tb[(tb["Комплект(ы)"] == "АТ-РД-АР1-С-00") & (tb["Номер листа"] == "2.1")].iloc[0]
check("tb_no_fact_planned", pd.isna(row2["Фактическая дата"])
      and row2["Статус выдачи"] == "Планируется",
      f"fact={row2['Фактическая дата']}, status={row2['Статус выдачи']}")

# лист КЖ «99.99» в Витро есть, но номер не совпал → «Планируется»
row3 = tb[(tb["Комплект(ы)"] == "АТ-РД-КЖ1-С-00")].iloc[0]
check("tb_no_match_planned", row3["Статус выдачи"] == "Планируется")

# лист без плановой даты: fallback — срок комплекта (2026-05-01)
check("tb_planned_fallback", row2["Плановая дата"] == date(2026, 5, 1),
      f"planned={row2['Плановая дата']}")

# период по «Плановой дате»: 01.06–30.06 → только КЖ 10.01
tb_p = plan._build_sheet_plan_table(overview, flt_empty,
                                    date(2026, 6, 1), date(2026, 6, 30))
check("tb_period_june",
      list(tb_p["Номер листа"]) == ["10.01"],
      f"sheets={list(tb_p['Номер листа'])}")

# период 01.05–31.05 → лист 2.1 попадает по сроку комплекта
tb_may = plan._build_sheet_plan_table(overview, flt_empty,
                                      date(2026, 5, 1), date(2026, 5, 31))
check("tb_period_may_fallback",
      list(tb_may["Номер листа"]) == ["2.1"],
      f"sheets={list(tb_may['Номер листа'])}")

# «Только в Витро» комплект (без ведомостей) в таблицу Б не попадает
check("tb_no_vitro_only_kit",
      "АТ-РД-ГП1-С-00" not in set(tb["Комплект(ы)"]))


# ------------------------------------------------------------------
# 5. Excel-выгрузка: один лист данных «Листы»
# ------------------------------------------------------------------
period_label = "01.06.2026 — 30.06.2026"
data = plan.build_plan_export(tb_p, period_label=period_label)
wb = openpyxl.load_workbook(io.BytesIO(data))
check("export_sheets", wb.sheetnames == ["Листы", "Параметры выгрузки"],
      f"sheets={wb.sheetnames}")

ws = wb["Листы"]
headers = [ws.cell(row=1, column=j).value for j in range(1, ws.max_column + 1)]
check("export_headers", headers == plan.SHEET_PLAN_COLUMNS, f"headers={headers}")
check("export_rows", ws.max_row - 1 == len(tb_p),
      f"rows={ws.max_row - 1}, expected {len(tb_p)}")

# дата в формате ДД.ММ.ГГГГ
cell_date = ws.cell(row=2, column=headers.index("Плановая дата") + 1).value
check("export_date_fmt", cell_date == "01.06.2026", f"date={cell_date}")
cell_status = ws.cell(row=2, column=headers.index("Статус выдачи") + 1).value
# единственная строка июньского периода — КЖ 10.01: в Витро был только
# лист «99.99» (номер не совпал) → «Планируется»
check("export_status_cell", cell_status == "Планируется",
      f"status={cell_status}")

ws2 = wb["Параметры выгрузки"]
params = {ws2.cell(row=i, column=1).value: ws2.cell(row=i, column=2).value
          for i in range(2, ws2.max_row + 1)}
check("export_params_period", params.get("Период") == period_label,
      f"period={params.get('Период')}")
check("export_params_rows", params.get("Позиций листов в выгрузке") == len(tb_p))
check("export_params_status_doc",
      "Выдан" in str(params.get("Статус выдачи", "")))

# старая сигнатура (два DataFrame) больше не поддерживается:
# второй DataFrame попадает в period_label → ValueError внутри
try:
    plan.build_plan_export(tb_p, tb_p)  # type: ignore[misc]
    check("export_new_signature", False, "приняла 2 DataFrame — старая сигнатура")
except (TypeError, ValueError):
    check("export_new_signature", True)

# ------------------------------------------------------------------
print()
if FAILURES:
    print(f"❌ ПРОВАЛЕНО: {len(FAILURES)}: {', '.join(FAILURES)}")
    sys.exit(1)
print("ИТОГ: все проверки патча 09 пройдены")
