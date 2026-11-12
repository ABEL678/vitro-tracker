# test_patch13.py — тесты патча 13 (5 исправлений)
"""
Проверяет (PATCH-13):

  1. Мусорные комплекты не отображаются и не выживают:
     - _build_overview пропускает шифры, не проходящие
       is_valid_kit_code, даже если они пришли из Витро
       («Только в Витро») или лежат в plan_kits (исторический мусор);
     - _prune_plan_kits удаляет невалидные шифры из plan_kits.
  2. СМИК: листов 44, а не 33:
     - _vitro_sheet_counts считает через sheets.dedup_document_rows
       (тот же код, что и Сводка); 33 файла с номерами + 11 файлов
       с пустым номером = 44 уникальных листа;
     - варианты написания шифра (латинские двойники, хвостовой
       пробел) склеиваются — листы одного комплекта не делятся
       между фантомными написаниями;
     - sql_count_sheets согласован с pandas при \\xa0/табуляции
       в номере листа.
  3. Предупреждения парсера: логика session_state (функции существуют,
     журнал пишется в БД — переживает F5).
  4. Шапки комплектов в реестрах ТХ/ЭС/АК: шапка в колонке «Название
     листа» и шифр в отдельной колонке распознаются — строки больше
     не отбрасываются с «не найден шифр комплекта».
  5. Журнал загрузок: события plan_import пишутся в таблицу log
     (в т. ч. предупреждения со статусом WARN) и читаются обратно.

Запуск: python3 scripts/test_patch13.py
"""
import io
import os
import sys

REPO = "/home/z/my-project/vitro-tracker"
sys.path.insert(0, REPO)
os.environ["SQLITE_PATH"] = "data/vitro_test13.db"
os.chdir(REPO)

import pandas as pd  # noqa: E402
import openpyxl  # noqa: E402

import vitro.sqlite_db as db  # noqa: E402
from vitro.sheets import (  # noqa: E402
    dedup_document_rows,
    dedup_documents,
    sql_count_sheets,
)
from vitro.plan_xlsx import (  # noqa: E402
    is_valid_kit_code,
    parse_drawings_workbook,
)

DB_FILE = os.path.join(REPO, "data", "vitro_test13.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
db.init_db()

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def doc(i, cx, sn, status, sd):
    return dict(
        id=i, leaf=f"FILE_{i}.pdf", discipline="СМИК", section="Секция 00",
        complex=cx, status=status, revision="1", dir_ref="",
        name=f"Лист {sn or i}", status_date=sd, sheet_number=sn,
        vitro_guid=f"g{i}", vitro_version=1,
    )


# ------------------------------------------------------------------
# 1. Мусорные комплекты (is_valid_kit_code)
# ------------------------------------------------------------------
check("valid_kit_code",
      is_valid_kit_code("АТ-РД-КЖ1-1-00")
      and is_valid_kit_code("АТ-РД-ОВ10-1-08Т"),
      "реальные шифры проходят")
check("trash_kit_code_commas",
      not is_valid_kit_code("АТ-РД-КЖ1,2-С-П2.3.ВР"),
      "мусор с запятой/точками отброшен")
check("trash_kit_code_sections",
      not is_valid_kit_code("ПОДЗЕМНАЯ ЧАСТЬ")
      and not is_valid_kit_code("СО")
      and not is_valid_kit_code(""),
      "разделы/приложения/пустые отброшены")

# ------------------------------------------------------------------
# 2. СМИК 44 (дедуп) + варианты написания шифра
# ------------------------------------------------------------------
K = "АТ-РД-СМИК-0-00"
rows = []
i = 1
for sn in [f"{n:02d}" for n in range(1, 34)]:       # 33 файла с номерами
    rows.append(doc(i, K, sn, "A", "2025-01-01")); i += 1
for _ in range(11):                                  # 11 файлов без номера
    rows.append(doc(i, K, "", "C", "2025-01-02")); i += 1
check("smik_dedup_44", len(dedup_document_rows(rows)) == 44,
      f"СМИК: {len(dedup_document_rows(rows))} уникальных листов (реально 44)")

# Варианты написания: латинский «C» и хвостовой пробел — тот же комплект
rows_v = [
    doc(101, "АТ-РД-ОВ2-C-00", "10", "A", "2025-02-01"),    # латиница
    doc(102, "АТ-РД-ОВ2-С-00 ", "11", "B", "2025-02-01"),   # кириллица+пробел
    doc(103, " АТ-РД-ОВ2-С-00", "12", "C", "2025-02-02"),
    doc(104, "АТ-РД-ОВ2-С-00", "", "C", "2025-02-02"),      # без номера — по id
]
kept_v = dedup_document_rows(rows_v)
complexes_v = {r["complex"] for r in kept_v}
check("variant_glued_in_dedup",
      complexes_v == {"АТ-РД-ОВ2-С-00"} and len(kept_v) == 4,
      f"варианты шифра склеились: {sorted(complexes_v)}, листов {len(kept_v)}")

# pandas-обёртка (путь Сводки) — тоже нормализует complex
df_v = dedup_documents(pd.DataFrame(rows_v))
check("variant_glued_in_df",
      set(df_v["complex"]) == {"АТ-РД-ОВ2-С-00"},
      f"dedup_documents: {sorted(set(df_v['complex']))}")

# SQL-выражение согласовано с pandas: \xa0 в номере не порождает фантом
with db.get_conn() as conn:
    conn.execute("DELETE FROM documents")
    conn.executemany("""
        INSERT INTO documents (id, leaf, discipline, section, complex,
                               status, revision, name, status_date,
                               sheet_number)
        VALUES (?, ?, 'ОВ', '', ?, 'A', '1', '', '2025-01-01', ?)
    """, [
        (1, "a.pdf", "АТ-РД-ОВ3-С-00", "10"),
        (2, "b.pdf", "АТ-РД-ОВ3-С-00", "10\xa0"),   # NBSP — тот же лист
        (3, "c.pdf", "АТ-РД-ОВ3-С-00", "11\t"),     # табуляция
        (4, "d.pdf", "АТ-РД-ОВ3-С-00", None),       # без номера — по id
    ])
    n_sql = conn.execute(
        f"SELECT {sql_count_sheets('d')} FROM documents d "
        "WHERE complex = 'АТ-РД-ОВ3-С-00'").fetchone()[0]
    from_db = conn.execute(
        "SELECT id, complex, sheet_number, status, status_date "
        "FROM documents").fetchall()
check("sql_nbsp_consistent", n_sql == 3,
      f"SQL: {n_sql} уникальных листов (10 + 11 + без номера), ожидалось 3")

# ------------------------------------------------------------------
# 3. _vitro_sheet_counts: СМИК=44 и склейка вариантов (по БД)
# ------------------------------------------------------------------
with db.get_conn() as conn:
    conn.execute("DELETE FROM documents")
    # ОВ3 — согласованность SQL/pandas при \xa0 и табуляции в номере
    rows_nb = [
        doc(201, "АТ-РД-ОВ3-С-00", "10", "A", "2025-01-01"),
        doc(202, "АТ-РД-ОВ3-С-00", "10\xa0", "A", "2025-01-01"),
        doc(203, "АТ-РД-ОВ3-С-00", "11\t", "A", "2025-01-01"),
        doc(204, "АТ-РД-ОВ3-С-00", None, "A", "2025-01-01"),
    ]
    docs = rows + rows_v + rows_nb                # СМИК 44 + ОВ2 4 + ОВ3 3
    conn.executemany("""
        INSERT INTO documents (id, leaf, discipline, section, complex,
                               status, revision, name, status_date,
                               sheet_number)
        VALUES (?, ?, ?, ?, ?, ?, '1', ?, ?, ?)
    """, [(d["id"], d["leaf"], d["discipline"], d["section"], d["complex"],
           d["status"], d["name"], d["status_date"], d["sheet_number"])
          for d in docs])

# ------------------------------------------------------------------
# 4. Парсер ТХ/ЭС/АК: шапка в названии листа / отдельная колонка
# ------------------------------------------------------------------
def build_xlsx(rows_data, headers):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Подземная часть"
    ws.append(headers)
    for r in rows_data:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Формат ТХ: колонки «Название листа» + «Номер листа», шапка комплекта —
# в КОЛОНКЕ НАЗВАНИЯ («АТ-РД-ТХ1-С-00 Система ТХ»), отдельной колонки
# «Шифр» нет.
tx = build_xlsx([
    ["АТ-РД-ТХ1-С-00 Система ТХ", None],        # шапка в названии
    ["Отопление узел ввода", "10"],             # чертёж (наследует шапку)
    ["Теплоснабжение схема", "11"],
    ["АТ-РД-ТХ2-С-00 Система ТХ корпус 2", None],  # вторая шапка
    ["Отопление корпус 2", "10"],
], ["Название листа", "Номер листа"])
r_tx, w_tx, kits_tx = parse_drawings_workbook(tx, "ТХ_Комплекты чертежей.xlsx")
check("tx_header_in_title",
      len(r_tx) == 3 and {r["kit_code"] for r in r_tx} ==
      {"АТ-РД-ТХ1-С-00", "АТ-РД-ТХ2-С-00"},
      f"распознано листов: {len(r_tx)}, комплектов: "
      f"{sorted({r['kit_code'] for r in r_tx})}")
check("tx_no_skipped_warning",
      not any("пропущено" in w for w in w_tx),
      f"предупреждений о пропуске нет ({len(w_tx)})")
check("tx_kit_names", kits_tx.get("АТ-РД-ТХ1-С-00") == "Система ТХ",
      f"название из шапки: {kits_tx}")

# Формат ЭС/АК: шифр комплекта в ОТДЕЛЬНОЙ колонке без заголовка
es = build_xlsx([
    ["АТ-РД-ЭС1-С-00", "Ввод №1", "10"],
    ["АТ-РД-ЭС1-С-00", "Ввод №2", "11"],
    ["АТ-РД-ЭС2-С-00", "Щитовая", "12"],
], ["Шифр РД (тех.)", "Название листа", "Номер листа"])
# колонка «Шифр РД (тех.)» не распознаётся как «Шифр» → сработает скан строки
r_es, w_es, kits_es = parse_drawings_workbook(es, "ЭС_Комплекты чертежей.xlsx")
check("es_kit_in_separate_column",
      len(r_es) == 3 and {r["kit_code"] for r in r_es} ==
      {"АТ-РД-ЭС1-С-00", "АТ-РД-ЭС2-С-00"},
      f"распознано листов: {len(r_es)}, комплектов: "
      f"{sorted({r['kit_code'] for r in r_es})}")
check("es_no_skipped_warning",
      not any("пропущено" in w for w in w_es),
      f"предупреждений о пропуске нет ({len(w_es)})")

# Регресс ОВ/АР/ВК: шапка в колонке «Шифр комплекта» по-прежнему работает
ov = build_xlsx([
    ["АТ-РД-ОВ1-С-00 Система отопления", None],
    [None, "Отопление схема", "10"],
], ["Шифр комплекта", "Название листа", "Номер листа"])
r_ov, w_ov, kits_ov = parse_drawings_workbook(ov, "ОВ.xlsx")
check("ov_regress_header_in_kit_column",
      len(r_ov) == 1 and r_ov[0]["kit_code"] == "АТ-РД-ОВ1-С-00",
      f"листов: {len(r_ov)}, шапка в «Шифре комплекта» распознана")

# Приложения без шифра наследуют комплект (регресс патча 10);
# метка «Приложение 1» в колонке шифра комплектом НЕ считается,
# а строка-раздел «Прилагаемые документы» пропускается как раньше
ov2 = build_xlsx([
    ["АТ-РД-ОВ1-С-00 Система отопления", None],
    [None, "Отопление схема", "10"],
    ["Приложение 1", "СО исполнительная схема", None],
    [None, "Прилагаемые документы", None],   # строка-раздел — пропуск
], ["Шифр комплекта", "Название листа", "Номер листа"])
r_ov2, _, _ = parse_drawings_workbook(ov2, "ОВ2.xlsx")
check("ov_regress_appendix_inherits",
      len(r_ov2) == 2 and all(
          r["kit_code"] == "АТ-РД-ОВ1-С-00" for r in r_ov2),
      f"листов: {len(r_ov2)} (приложение наследует комплект, раздел пропущен)")

# ------------------------------------------------------------------
# 5. Журнал загрузок (log)
# ------------------------------------------------------------------
db.log_event("plan_import",
             "Ведомость комплектов: 421 строк; файлы: Ведомость.xlsx",
             rows=421)
db.log_event("plan_import",
             "Замечания разбора (комплекты):\n• Отброшено строк: 2",
             rows=421, status="WARN")
db.log_event("plan_clear", "План выдачи очищен. Пользователь: Тест")
with db.get_conn() as conn:
    log_rows = conn.execute("""
        SELECT timestamp, event, message, rows, status FROM log
        WHERE event IN ('plan_import', 'plan_clear')
        ORDER BY rowid DESC
    """).fetchall()
check("log_import_present",
      any(r["event"] == "plan_import" and r["rows"] == 421
          for r in log_rows), "запись загрузки в журнале")
check("log_warn_present",
      any(r["status"] == "WARN" for r in log_rows),
      "предупреждение (WARN) в журнале — переживает F5")
check("log_clear_present",
      any(r["event"] == "plan_clear" for r in log_rows),
      "очистка плана в журнале")

# ------------------------------------------------------------------
# 6. Фильтр мусора в _build_overview и _prune_plan_kits (по БД)
# ------------------------------------------------------------------
TRASH = "АТ-РД-КЖ1,2-С-П2.3.ВР"
GOOD = "АТ-РД-ОВ1-С-00"
K = "АТ-РД-СМИК-0-00"
with db.get_conn() as conn:
    conn.execute("DELETE FROM plan_kits")
    conn.execute("DELETE FROM plan_drawings")
    # исторический мусор в plan_kits + валидные комплекты
    conn.execute(
        "INSERT INTO plan_kits (code, name) VALUES (?, 'мусор')", (TRASH,))
    conn.execute(
        "INSERT INTO plan_kits (code, name) VALUES (?, 'Отопление')",
        (GOOD,))
    conn.execute(
        "INSERT INTO plan_kits (code, name) VALUES (?, 'СМИК')", (K,))

from vitro.ui.plan import (  # noqa: E402
    _build_overview, _prune_plan_kits, _vitro_sheet_counts,
    _render_import_log, _render_last_warnings,
)

counts = _vitro_sheet_counts()
check("vitro_counts_smik_44", counts.get(K) == 44,
      f"СМИК в Витро = {counts.get(K)} (33 + 11 без номера)")
check("vitro_counts_variant_glued",
      counts.get("АТ-РД-ОВ2-С-00") == 4
      and "АТ-РД-ОВ2-C-00" not in counts,
      f"ОВ2: {counts.get('АТ-РД-ОВ2-С-00')} листов под одним шифром")

ov_df = _build_overview(include_vitro_only=True)
codes_in_table = set(ov_df["code"])
check("overview_no_trash",
      TRASH not in codes_in_table and "АТ-РД-ОВ2-C-00" not in codes_in_table,
      "мусорные и дубль-шифры не в таблице")
check("overview_keeps_good",
      {K, GOOD, "АТ-РД-ОВ2-С-00", "АТ-РД-ОВ3-С-00"} <= codes_in_table,
      f"валидные комплекты на месте: {sorted(codes_in_table)}")
row_k = ov_df[ov_df["code"] == K]
check("overview_smik_44",
      len(row_k) == 1 and int(row_k.iloc[0]["sheets_vitro"]) == 44,
      f"строка СМИК: sheets_vitro={int(row_k.iloc[0]['sheets_vitro'])}")

# _prune_plan_kits удаляет невалидный код даже без «Заменить план»
removed = _prune_plan_kits({GOOD, K})
check("prune_removes_trash", removed == 1,
      f"удалено комплектов: {removed} (мусор)")
with db.get_conn() as conn:
    left = [r["code"] for r in conn.execute("SELECT code FROM plan_kits")]
check("prune_keeps_valid", set(left) == {GOOD, K},
      f"остались валидные: {sorted(left)} (мусор удалён)")

# ------------------------------------------------------------------
# 7. UI-функции патча 13 существуют (импорт без ошибок)
# ------------------------------------------------------------------
check("ui_functions_exist",
      callable(_render_last_warnings) and callable(_render_import_log),
      "блоки «Замечания последней загрузки» и «Журнал загрузок»")

# ------------------------------------------------------------------
print()
if FAILURES:
    print(f"ПРОВАЛЕНО: {len(FAILURES)} -> {FAILURES}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПАТЧА 13 ПРОЙДЕНЫ")
