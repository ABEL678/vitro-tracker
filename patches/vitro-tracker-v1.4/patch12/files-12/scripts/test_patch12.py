# test_patch12.py — тесты исправлений после дедупликации (PATCH-12)
"""
Проверяет (PATCH-12) пять исправлений:

  1. Аннулирование «липкое» (vitro/sheets.py + vitro/ui/summary.py):
     лист, у которого ХОТЯ БЫ ОДНА ревизия «Аннулировано», учитывается
     как аннулированный — даже если последняя ревизия живая (A/B/C)
     или у аннулированной ревизии пустая дата; строки без номера
     считаются по id и не теряются. Счётчик «Аннулировано» больше не
     падает в 0.
  2. Бублик «Листов» в Плане выдачи (vitro/ui/plan.py):
     _vitro_sheet_counts считает тем же выражением, что и Сводка
     (sheets.sql_count_sheets): файлы с пустым sheet_number —
     самостоятельные листы (пример СМИК: 44, а не 33).
  3. Режим «Заменить план» (vitro/ui/plan.py): _prune_plan_kits
     удаляет комплекты, которых нет в новой ведомости, вместе с их
     чертежами (541 → 471); _norm_kit_rows нормализует шифры
     (латиница → кириллица, мусор отбрасывается).
  4. Регулярка _KIT_CODE_PATTERN (vitro/plan_xlsx.py): запятые
     запрещены везде; точки допустимы в марке (АР12.1, КЖ2.1),
     но запрещены в хвостовых блоках (мусор «…-С-П2.3.ВР»);
     реальные комплекты проходят.
  5. Колонка «Шифр листа» в Детальном плане (vitro/ui/plan.py):
     полный шифр = шифр комплекта + номер листа.

Запуск: python3 scripts/test_patch12.py
"""
import os
import sys

REPO = "/home/z/my-project/vitro-tracker"
sys.path.insert(0, REPO)
os.environ["SQLITE_PATH"] = "data/vitro_test12.db"
os.chdir(REPO)

import pandas as pd  # noqa: E402

import vitro.sqlite_db as db  # noqa: E402
from vitro.sheets import (  # noqa: E402
    dedup_document_rows,
    dedup_documents,
)

DB_FILE = os.path.join(REPO, "data", "vitro_test12.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
db.init_db()

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------------
# 1. Липкое аннулирование (sheets.py)
# ------------------------------------------------------------------
def doc(i, cx, sn, status, sd):
    return dict(
        id=i, leaf=f"FILE_{i}.pdf", discipline="СМИК", section="Секция 00",
        complex=cx, status=status, revision="1", dir_ref="",
        name=f"Лист {sn or i}", status_date=sd, sheet_number=sn,
        vitro_guid=f"g{i}", vitro_version=1,
    )


K = "АТ-РД-СМИК-0-00"

# Сценарий из бага: аннулированная ревизия проигрывает живой по дате
rows = [
    doc(1, K, "10.01", "A", "2025-03-01"),            # живая, позже
    doc(2, K, "10.01", "АННУЛИРОВАНО", "2025-01-01"), # аннулирована раньше
    doc(3, K, "10.02", "АННУЛИРОВАНО", "2025-02-01"), # последняя и есть аннулирована
    doc(4, K, "10.03", "A", "2025-03-01"),            # без аннулирования
    doc(5, K, "10.03", "B", "2025-04-01"),
    # аннулированная ревизия с ПУСТОЙ датой статуса проигрывает по (date, id)
    doc(6, K, "10.04", "A", "2025-05-01"),
    doc(7, K, "10.04", "АННУЛИРОВАНО", ""),
    # аннулированные файлы БЕЗ номера листа — самостоятельные листы (по id)
    doc(8, K, "", "АННУЛИРОВАНО", "2025-06-01"),
    doc(9, K, None, "Аннулировано", "2025-06-02"),  # в другом регистре
]
kept = dedup_document_rows(rows)
by_sn = {}
for r in kept:
    key = r.get("sheet_number") or f"id:{r['id']}"
    by_sn.setdefault(str(key), []).append(r)

check("dedup_keeps_one_per_sheet",
      len(by_sn.get("10.01", [])) == 1, "10.01: одна строка")
check("sticky_annul_later_live_rev",
      by_sn["10.01"][0]["status"].upper() == "АННУЛИРОВАНО",
      "10.01: живая ревизия позже — лист всё равно аннулирован")
check("annulled_latest",
      by_sn["10.02"][0]["status"].upper() == "АННУЛИРОВАНО",
      "10.02: последняя ревизия аннулирована")
check("no_annul_stays_b",
      by_sn["10.03"][0]["status"] == "B" and len(by_sn["10.03"]) == 1,
      "10.03: без аннулирования — остаётся B (регресс патча 11)")
check("sticky_annul_empty_date",
      by_sn["10.04"][0]["status"].upper() == "АННУЛИРОВАНО",
      "10.04: аннулирование с пустой датой не теряется")
check("empty_sn_annulled_by_id",
      len(by_sn.get("id:8", [])) == 1 and len(by_sn.get("id:9", [])) == 1,
      "аннулированные без номера — два отдельных листа (fallback по id)")
check("annulled_count",
      sum(1 for r in kept
          if str(r.get("status", "")).upper() == "АННУЛИРОВАНО") == 5,
      f"аннулированных листов = "
      f"{sum(1 for r in kept if str(r.get('status','')).upper() == 'АННУЛИРОВАНО')} "
      "(10.01, 10.02, 10.04, id:8, id:9), ожидалось 5")

# pandas-обёртка сохраняет колонки и липкость
df_kept = dedup_documents(pd.DataFrame(rows))
check("df_wrapper_annulled",
      int(df_kept["status"].fillna("").astype(str).str.strip()
          .str.upper().eq("АННУЛИРОВАНО").sum()) == 5)

# ------------------------------------------------------------------
# 2. Бублик «Листов» в Плане выдачи (_vitro_sheet_counts = Сводка)
# ------------------------------------------------------------------
docs = []
i = 1
for sn in [f"{n:02d}" for n in range(1, 34)]:       # 33 файла с номерами
    docs.append(doc(i, K, sn, "A", "2025-01-01")); i += 1
for _ in range(11):                                  # 11 файлов без номера
    docs.append(doc(i, K, "", "C", "2025-01-02")); i += 1

n_docs, _ = db.save_all(docs, [], {d["id"] for d in docs})
check("fixture_saved", n_docs == 44, f"docs={n_docs}")
# комплект должен быть в плане, чтобы попасть в общую таблицу
db.upsert_plan_kits([{"code": K, "name": "СМИК"}], user="Тест")

import vitro.ui.plan as plan_mod  # noqa: E402

counts = plan_mod._vitro_sheet_counts()
check("vitro_counts_44", counts.get(K) == 44,
      f"СМИК в Витро = {counts.get(K)} (было 33, реально 44)")

# эталон Сводки — то же число
import vitro.ui.summary as summary  # noqa: E402

sm = summary._load_documents()
sm_k = sm[sm["complex"] == K]
check("summary_matches_plan", len(sm_k) == 44,
      f"Сводка: уникальных листов у СМИК = {len(sm_k)}")

# через _plan_totals: sheets_vitro строго = уникальным листам
overview = plan_mod._build_overview(include_vitro_only=False)
row_k = overview[overview["code"] == K]
check("overview_row_exists", len(row_k) == 1, "комплект СМИК в общей таблице")
totals = plan_mod._plan_totals(row_k)
check("totals_sheets_vitro_44", totals["sheets_vitro"] == 44,
      f"sheets_vitro={totals['sheets_vitro']}")

# ------------------------------------------------------------------
# 3. «Заменить план»: _prune_plan_kits + _norm_kit_rows
# ------------------------------------------------------------------
K2, K3 = "АТ-РД-КЖ1-1-00", "АТ-РД-КЖ1-2-00"
db.upsert_plan_kits([
    {"code": K, "name": "СМИК"},
    {"code": K2, "name": "КЖ секция 1"},
    {"code": K3, "name": "КЖ секция 2 (устарел)"},
    {"code": "АТ-РД-КЖ1,2-С-П2.3.ВР", "name": "мусор из старой загрузки"},
], user="Тест")
db.replace_plan_drawings_for_kits([
    {"kit_code": K2, "sheet_no": "10.01", "title": "Общие данные",
     "qty": 1},
    {"kit_code": K3, "sheet_no": "20.01", "title": "Устаревший лист",
     "qty": 1},
], user="Тест")

removed = plan_mod._prune_plan_kits({K, K2})
check("prune_removed", removed == 2,
      f"удалено комплектов = {removed} (КЖ1-2-00 и мусор)")
plan_now = db.load_plan_kits()
check("prune_left", set(plan_now["code"]) == {K, K2},
      f"в плане остались: {sorted(set(plan_now['code']))}")
draw_now = db.load_plan_drawings()
check("prune_drawings",
      set(draw_now["kit_code"]) == {K2} and len(draw_now) == 1,
      "чертежи устаревших комплектов удалены вместе с ними")

norm_rows, norm_warns = plan_mod._norm_kit_rows([
    {"code": " ат-рд-ов2-c-00 ", "name": "латиница и пробелы"},
    {"code": "АТ-РД-КЖ1,2-С-П2.3.ВР", "name": "мусор"},
])
check("norm_homoglyph", len(norm_rows) == 1
      and norm_rows[0]["code"] == "АТ-РД-ОВ2-С-00",
      f"латиница/пробелы → {norm_rows[0]['code'] if norm_rows else '?'}")
check("norm_drops_garbage", len(norm_warns) == 1 and "1" in norm_warns[0],
      "мусорный шифр отброшен с предупреждением")

# ------------------------------------------------------------------
# 4. Строгая регулярка _KIT_CODE_PATTERN (plan_xlsx.py)
# ------------------------------------------------------------------
from vitro.plan_xlsx import _is_valid_kit_code, _kit_code_of  # noqa: E402

valid = ["АТ-РД-КЖ1-1-00", "АТ-РД-АР-2-00", "АТ-РД-ОВ1-С-00",
         "АТ-РД-ОВ10-1-08Т", "АТ-РД-СМИК-0-00", "АТ-РД-КЖ1-С-П1",
         # патч 12: точки в марке ДОПУСТИМЫ (класс реальных комплектов)
         "АТ-РД-АР12.1-0-00", "АТ-РД-КЖ2.1-1-00"]
garbage = ["АТ-РД-КЖ1,2-С-П2.3.ВР",   # запятая в марке + точки в хвосте (баг)
           "АТ-РД-КЖ1,2-С-00",        # запятая в марке
           "АТ-РД-КЖ1-С-П2.3.ВР",     # точки в хвостовом блоке
           "АТ-РД-ОВ1-С-00-10.01",    # полный шифр листа (6 блоков)
           "СО", "П.01", "ВР", "Всего", "ИТОГО",
           "Подземная часть", ""]
for c in valid:
    check(f"valid[{c}]", _is_valid_kit_code(c), "проходит регулярку")
for c in garbage:
    check(f"garbage[{c}]", not _is_valid_kit_code(c), "отброшен")
check("kit_code_of_norm", _kit_code_of(" ат-рд-ов2-c-00 ")
      == "АТ-РД-ОВ2-С-00", "нормализация: регистр + латиница → кириллица")

# ------------------------------------------------------------------
# 5. Колонка «Шифр листа» в Детальном плане
# ------------------------------------------------------------------
db.upsert_plan_kits([{"code": K, "name": "СМИК"}], user="Тест")
db.replace_plan_drawings_for_kits([
    {"kit_code": K, "sheet_no": "10", "title": "Общие данные", "qty": 1},
    {"kit_code": K, "sheet_no": "10.01", "title": "План", "qty": 1},
    {"kit_code": K, "sheet_no": "", "title": "Без номера", "qty": 1},
], user="Тест")

overview_f = overview[overview["code"].isin([K])].copy()
flt = {"disc": (), "section": (), "kits": (), "block_codes": (),
       "marka": "", "extra": {"kit_status": (), "only_dated": False}}
table = plan_mod._build_sheet_plan_table(
    overview_f, flt, date_from=None, date_to=None)
check("sheet_plan_has_column", "Шифр листа" in table.columns,
      f"колонки: {list(table.columns)}")
if "Шифр листа" in table.columns:
    codes = dict(zip(table["Номер листа"], table["Шифр листа"]))
    check("full_code_composed", codes.get("10") == f"{K}-10",
          f"10 → {codes.get('10')}")
    check("full_code_fractional", codes.get("10.01") == f"{K}-10.01",
          f"10.01 → {codes.get('10.01')}")
    check("full_code_empty_no", codes.get("") == K,
          "без номера → шифр комплекта")
    check("column_after_number",
          list(table.columns).index("Шифр листа")
          == list(table.columns).index("Номер листа") + 1,
          "колонка стоит сразу после «Номер листа»")

# выгрузка в Excel содержит новую колонку
from vitro.plan_xlsx import build_plan_export  # noqa: E402

import io  # noqa: E402
import openpyxl  # noqa: E402

xlsx = build_plan_export(table, period_label="все даты")
ws = openpyxl.load_workbook(io.BytesIO(xlsx))["Листы"]
headers = [c.value for c in ws[1]]
check("export_has_full_code", "Шифр листа" in headers,
      f"заголовки выгрузки: {headers}")

# ------------------------------------------------------------------
# Итог
# ------------------------------------------------------------------
print()
if FAILURES:
    print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
