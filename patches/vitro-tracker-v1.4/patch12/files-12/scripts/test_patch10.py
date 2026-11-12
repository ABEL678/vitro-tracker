# test_patch10.py — тесты генерального аудита вкладки «План выдачи»
"""
Проверяет (PATCH-10):
  1. Строгая валидация шифров: _kit_code_of отбрасывает разделы, итоги,
     полные шифры листов, приложения, даты; реальные комплекты
     (включая «АТ-РД-АР3-1-08Т», «АТ-РД-ТМ-С-П1») проходят.
  2. parse_kits_workbook: из ведомости с типовым мусором извлекаются
     РОВНО реальные комплекты; предупреждение «отброшено N строк».
  3. Реальные регистры (upload/ АР/ВК/ОВ): 0 мусорных комплектов,
     все 69 шифров проходят строгую регулярку.
  4. «Листов в Витро» = УНИКАЛЬНЫЕ листы (_vitro_sheet_counts):
     2 PDF одной ревизии = 1 лист; fallback по файлам для комплектов
     без номеров листов.
  5. Механика чисел (_plan_totals): нет дублей и потерь.
  6. Порядок блоков: Фильтры → Период → Бублики → «Сводный план
     готовности комплектов и количества выданных листов» →
     Детальный план по листам → Выгрузка (внизу).
  7. Регрессия 09: период фильтрует сводную (срок готовности) и листы
     (плановая дата).
Запуск: python3 scripts/test_patch10.py
"""
import io
import os
import sys
from datetime import date

REPO = "/home/z/my-project/vitro-tracker"
sys.path.insert(0, REPO)
os.environ.setdefault("SQLITE_PATH", "data/vitro_test.db")
os.chdir(REPO)

import pandas as pd  # noqa: E402
import openpyxl  # noqa: E402

import vitro.plan_xlsx as px  # noqa: E402
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
# 1. _kit_code_of: строгая валидация
# ------------------------------------------------------------------
good = ["АТ-РД-АР1-С-00", "АТ-РД-КЖ1-1-00", "АТ-РД-АР-2-00",
        "АТ-РД-АР3-1-08Т", "АТ-РД-ТМ-С-П1", "АТ-РД-АР12.1-0-00",
        "АТ-РД-КЖ2.1-1-00",  # патч 12: ТОЧКИ В МАРКЕ ДОПУСТИМЫ
        "АТ-РД-АР1-00", "АТ-РД-ОВ2-C-00"]  # латинский C → кириллица
for c in good:
    check(f"kit_ok_{c}", px._kit_code_of(c) == c.upper().translate(px._HOMOGLYPHS),
          f"->{px._kit_code_of(c)}")

junk = {
    "полный шифр листа": "АТ-РД-АР1-С-00-1.1",
    "шифр листа 2.1.0": "АТ-РД-АР1-С-00-2.1.0",
    "шифр листа 10.01": "АТ-РД-ОВ1-С-00-10.01",
    "шифр листа-число": "АТ-РД-АР1-С-00-05",
    "строка-раздел": "Подземная часть",
    "строка-раздел 2": "Надземная часть Корпус 1",
    "итого": "Всего",
    "итого 2": "ИТОГО:",
    "приложение": "Приложение СО",
    "приложение-метка": "П.01",
    "приложение-метка 2": "СО",
    "дата": "01.02.2026",
    "марка": "АР",
    "пусто": "",
    # ПАТЧ 12: запятая в марке и точки в хвосте запрещены
    # (точки В МАРКЕ допустимы — «АР12.1», «КЖ2.1» в good выше)
    "запятая в марке": "АТ-РД-КЖ1,2-С-00",
    "запятая и точки (баг 12)": "АТ-РД-КЖ1,2-С-П2.3.ВР",
    "точки в хвосте": "АТ-РД-КЖ1-С-П2.3.ВР",
}
for label, v in junk.items():
    check(f"junk_{label}", px._kit_code_of(v) == "", f"«{v}»")


# ------------------------------------------------------------------
# 2. parse_kits_workbook: фантомов больше нет
# ------------------------------------------------------------------
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["Шифр комплекта", "Наименование комплекта", "Раздел",
           "Срок готовности"])
real_kits = [
    ("АТ-РД-АР1-С-00", "Архитектура, подземная часть", "Подземная часть"),
    ("АТ-РД-АР1-1-00", "Архитектура, корпус 1", "Корпус 1"),
    ("АТ-РД-КЖ1-С-00", "Конструкции, подземная часть", "Подземная часть"),
    ("АТ-РД-ОВ1-С-00", "Отопление, подземная часть", "Подземная часть"),
]
junk_rows = [
    "Подземная часть", "Надземная часть Корпус 1",
    "АТ-РД-АР1-С-00-1.1", "АТ-РД-АР1-С-00-2.1.0",
    "Всего", "ИТОГО:", "Приложение СО", "П.01", "01.02.2026", "АР",
]
for c, n, s in real_kits:
    ws.append([c, n, s, ""])
for v in junk_rows:
    ws.append([v, "", "", ""])
buf = io.BytesIO()
wb.save(buf)
rows, warnings = px.parse_kits_workbook(buf.getvalue(), "Ведомость.xlsx")
codes = [r["code"] for r in rows]
check("kits_exact_real", codes == [c for c, _, _ in real_kits],
      f"{len(codes)} kits: {codes} (было бы 14 с мусором)")
check("kits_warning_total",
      any("всего отброшено 10 строк" in w for w in warnings),
      f"warnings={len(warnings)}")
check("kits_warning_examples",
      any("«Подземная часть»" in w and "«АТ-РД-АР1-С-00-1.1»" in w
          for w in warnings))


# ------------------------------------------------------------------
# 3. Реальные регистры: 0 мусора, все шифры валидны
# ------------------------------------------------------------------
UP = "/home/z/my-project/upload"
real_files = [f for f in (
    "АР_Комплекты чертежей.xlsx", "ВК_Комплекты чертежей.xlsx",
    "ОВ_Комплекты чертежей.xlsx") if os.path.exists(os.path.join(UP, f))]
if real_files:
    all_real_kits = {}
    all_rows = 0
    for fn in real_files:
        with open(os.path.join(UP, fn), "rb") as f:
            data = f.read()
        drw_rows, drw_warn, kits = px.parse_drawings_workbook(data, fn)
        all_rows += len(drw_rows)
        all_real_kits.update(kits)
        bad = [k for k in kits if not px._is_valid_kit_code(k)]
        check(f"register_{fn.split('_')[0]}_no_junk", not bad,
              f"комплектов: {len(kits)}, мусор: {bad[:5]}")
    check("register_kits_total", len(all_real_kits) >= 60,
          f"уникальных комплектов: {len(all_real_kits)}")
    check("register_rows_total", all_rows >= 1900,
          f"строк листов: {all_rows}")
else:
    print("[SKIP] реальные регистры недоступны")


# ------------------------------------------------------------------
# 4-7. Фикстура БД: дубли PDF, «Только в Витро», пустые номера листов
# ------------------------------------------------------------------
kits4 = [
    ("АТ-РД-АР1-С-00", "Архитектура, стилобат", "01_АР", "Подземная часть", "2026-05-01"),
    ("АТ-РД-АР1-1-00", "Архитектура, корпус 1", "01_АР", "Корпус 1", None),
    ("АТ-РД-КЖ1-С-00", "Конструкции, стилобат", "02_КР", "Подземная часть", "2026-06-15"),
    ("АТ-РД-ОВ1-С-00", "Отопление, стилобат", "03_ОВ", "Подземная часть", "2026-07-01"),
]
db.upsert_plan_kits(
    [{"code": c, "name": n, "discipline": d, "section": s, "due_date": du}
     for c, n, d, s, du in kits4], "тест")

drawings = [
    ("АТ-РД-АР1-С-00", "1", "Общие данные", "2026-04-01"),
    ("АТ-РД-АР1-С-00", "2.1", "План фундаментов", None),
    ("АТ-РД-КЖ1-С-00", "10.01", "Фундаментная плита", "2026-06-01"),
    ("АТ-РД-ОВ1-С-00", "31.00", "Схема отопления", "2026-07-01"),
]
db.replace_plan_drawings_for_kits(
    [{"kit_code": k, "sheet_no": sn, "title": t, "qty": 1,
      "planned_date": pd_, "discipline": "", "section": ""}
     for k, sn, t, pd_ in drawings], "тест")

# documents: дубль PDF листа «1» (2 ревизии) — должен считаться 1 раз
docs = [
    ("АТ-РД-АР1-С-00", "1", "2026-04-20"),
    ("АТ-РД-АР1-С-00", "1", "2026-04-25"),      # дубль того же листа
    ("АТ-РД-КЖ1-С-00", "99.99", "2026-01-01"),
    ("АТ-РД-ГП1-С-00", "1", "2026-03-01"),      # «Только в Витро»
    ("АТ-РД-ГП1-С-00", "2", "2026-03-02"),
    ("АТ-РД-ГП1-С-00", "3", "2026-03-03"),
    ("АТ-РД-Т2-С-00", None, "2026-03-05"),      # все без номера листа
    ("АТ-РД-Т2-С-00", None, "2026-03-06"),
    ("АТ-РД-Т2-С-00", None, "2026-03-07"),
]
with db.get_conn() as conn:
    conn.executemany("""
        INSERT INTO documents (leaf, complex, sheet_number, status_date)
        VALUES ('л', ?, ?, ?)
    """, docs)

counts = plan._vitro_sheet_counts()
check("distinct_sheets_dup_pdf",
      counts.get("АТ-РД-АР1-С-00") == 1,
      f"АР1-С-00: {counts.get('АТ-РД-АР1-С-00')} (2 PDF одного листа = 1)")
check("distinct_sheets_kit", counts.get("АТ-РД-КЖ1-С-00") == 1)
check("distinct_sheets_vitro_only", counts.get("АТ-РД-ГП1-С-00") == 3)
check("fallback_empty_sheet_numbers",
      counts.get("АТ-РД-Т2-С-00") == 3,
      f"Т2-С-00: {counts.get('АТ-РД-Т2-С-00')} (без номеров — по файлам)")

flt_empty = {"disc": (), "section": (), "kits": (), "block_codes": (),
             "marka": "", "extra": {"kit_status": (), "only_dated": False}}
overview = plan._build_overview(include_vitro_only=True)
check("overview_rows", len(overview) == 6,
      f"n={len(overview)} (4 план + ГП1 + Т2)")

row_ap = overview[overview["code"] == "АТ-РД-АР1-С-00"].iloc[0]
check("overview_dedup_status",
      int(row_ap["sheets_vitro"]) == 1
      and row_ap["status"] == "Загружается (частично)",
      f"листов в Витро={row_ap['sheets_vitro']}, "
      f"статус={row_ap['status']} (план 2 > витро 1)")

t = plan._plan_totals(overview)
check("totals_sheets_vitro", t["sheets_vitro"] == 8,
      f"витро={t['sheets_vitro']} (1+1+0+0+3+3, дубль схлопнут)")
check("totals_sheets_plan", t["sheets_plan"] == 4)
check("totals_max", t["sheets_total"] == 8, f"всего={t['sheets_total']}")
check("totals_excess", t["sheets_excess"] == 4, f"сверх={t['sheets_excess']}")
check("totals_no_loss_no_dup",
      t["sheets_done"] + t["sheets_remaining"] == t["sheets_total"],
      f"выдано {t['sheets_done']} + осталось {t['sheets_remaining']} "
      f"= всего {t['sheets_total']}")
check("totals_kits", t["kits_total"] == 6 and t["kits_loaded"] == 4,
      f"комплектов {t['kits_total']}, в Витро {t['kits_loaded']}, "
      f"план {t['kits_plan']}")


# ------------------------------------------------------------------
# 6. Порядок блоков и название сводной таблицы
# ------------------------------------------------------------------
src = open("vitro/ui/plan.py", encoding="utf-8").read()
render_src = src[src.index("def render():"):]
pos = {
    "donuts": render_src.find("_render_donuts(overview_filtered)"),
    "overview": render_src.find("_render_overview_table(overview_filtered"),
    "sheets": render_src.find("_render_sheet_plan_table(sheets_table"),
    "export": render_src.find("_render_export_button(sheets_table"),
}
check("order_donuts_first", 0 < pos["donuts"] < pos["overview"],
      f"бублики={pos['donuts']} < сводная={pos['overview']}")
check("order_overview_before_sheets", pos["overview"] < pos["sheets"])
check("order_export_last", pos["sheets"] < pos["export"],
      f"листы={pos['sheets']} < выгрузка={pos['export']}")
check("overview_new_title",
      "Сводный план готовности комплектов" in src
      and "и количества выданных листов" in src)


# ------------------------------------------------------------------
# 7. Регрессия 09: период для сводной и листов
# ------------------------------------------------------------------
ovp = plan._apply_period_to_overview(
    overview, date(2026, 5, 1), date(2026, 6, 30))
check("reg_period_overview",
      set(ovp["code"]) == {"АТ-РД-АР1-С-00", "АТ-РД-КЖ1-С-00"},
      f"kits={sorted(ovp['code'])}")

tb = plan._build_sheet_plan_table(overview, flt_empty, None, None)
check("reg_tb_columns", list(tb.columns) == plan.SHEET_PLAN_COLUMNS,
      f"cols={list(tb.columns)}")
row1 = tb[(tb["Комплект(ы)"] == "АТ-РД-АР1-С-00")
          & (tb["Номер листа"] == "1")].iloc[0]
check("reg_tb_issued", row1["Статус выдачи"] == "Выдан"
      and row1["Фактическая дата"] == date(2026, 4, 25))
tb_p = plan._build_sheet_plan_table(overview, flt_empty,
                                    date(2026, 6, 1), date(2026, 6, 30))
check("reg_tb_period", list(tb_p["Номер листа"]) == ["10.01"],
      f"sheets={list(tb_p['Номер листа'])}")

data = plan.build_plan_export(tb_p, period_label="01.06.2026 — 30.06.2026")
wbx = openpyxl.load_workbook(io.BytesIO(data))
check("reg_export_sheets",
      wbx.sheetnames == ["Листы", "Параметры выгрузки"],
      f"sheets={wbx.sheetnames}")

# ------------------------------------------------------------------
print()
if FAILURES:
    print(f"❌ ПРОВАЛЕНО: {len(FAILURES)}: {', '.join(FAILURES)}")
    sys.exit(1)
print("ИТОГ: все проверки патча 10 пройдены")
