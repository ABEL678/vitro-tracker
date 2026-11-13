# scripts/test_patch21.py
"""Тесты патча 21 «Статус комплекта во внешнем Excel (Экспорт)».

Блок 1: get_kit_status / get_kit_statuses — статус комплекта по
        последним ревизиям листов (unique_vitro_sheets):
        правила 2, 4, 5, 6, 7, 8 ТЗ; дедуп ревизий; «липкое»
        аннулирование; латинские двойники шифра; мусорные шифры;
        эквивалентность одиночного и батчевого вызова.
Блок 2: чтение внешнего Excel и добавление колонки
        «Статус комплекта»: _find_kit_code_column (варианты
        заголовков + промах + приоритет), _apply_kit_statuses
        (колонка в конце таблицы, значения, пустой шифр).

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db (путь
читается при импорте модуля).

Запуск: python scripts/test_patch21.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* (СТОП-ЛИСТ патча 21) ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch21_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sheets import (  # noqa: E402
    KIT_STATUS_NOT_LOADED,
    get_kit_status,
    get_kit_statuses,
    normalize_kit_code,
)
from vitro.ui.export import (  # noqa: E402
    KIT_STATUS_COL,
    _apply_kit_statuses,
    _find_kit_code_column,
)

# Схема временной БД (таблица documents)
sqlite_db.init_db()

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------------------
#  Подготовка: временная БД documents
# ---------------------------------------------------------------------------
def seed_documents(rows) -> None:
    """rows: (id, leaf, complex, status, sheet_number, status_date)."""
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM documents")
        conn.executemany(
            "INSERT INTO documents (id, leaf, complex, status, "
            "sheet_number, status_date) VALUES (?, ?, ?, ?, ?, ?)",
            list(rows),
        )


KIT = "АТ-РД-КЖ1-1-00"        # 5-блочный шифр (кириллица)
KIT2 = "АТ-РД-ОВ2-С-00"       # для латинских двойников
KIT3 = "АТ-РД-КМ2-1-02"       # для батчевых проверок
KIT_ABSENT = "АТ-РД-ХХ9-9-99"  # заведомо отсутствует в БД


def kit_rows(complex_code, statuses, sheet_numbers=None, start_id=1):
    """(complex, [статусы]) → строки documents по одному листу на статус."""
    sn = sheet_numbers or [str(i + 1) for i in range(len(statuses))]
    return [
        (start_id + i, f"{complex_code}-{sn[i]}.PDF", complex_code,
         statuses[i], sn[i], f"2025-06-{i + 1:02d}")
        for i in range(len(statuses))
    ]


# ---------------------------------------------------------------------------
#  БЛОК 1.1 — правила статусов 6/7/8 и игнор (правила 4/5)
# ---------------------------------------------------------------------------
RULES = [
    # (статусы листов комплекта, ожидаемый статус комплекта)
    (["A", "A", "A"], "A"),                                   # правило 6
    (["A", "B"], "B"),                                        # правило 7
    (["B"], "B"),                                             # только B
    (["A", "C"], "C"),                                        # правило 8
    (["C"], "C"),
    (["A", "ДЛЯ СОГЛАСОВАНИЯ"], "C"),
    (["B", "НА РАССМОТРЕНИИ"], "C"),
    (["A", "НА КОРРЕКТИРОВКЕ"], "C"),
    (["B", "РАЗМЕЩЕНО"], "C"),
    # правило 4: «И» и «АННУЛИРОВАНО» игнорируются
    (["И"], KIT_STATUS_NOT_LOADED),
    (["АННУЛИРОВАНО"], KIT_STATUS_NOT_LOADED),
    (["И", "АННУЛИРОВАНО"], KIT_STATUS_NOT_LOADED),
    (["A", "И"], "A"),
    (["B", "Аннулировано"], "B"),
    # правило 3: нормализация (регистр, пробелы)
    ([" a ", "b"], "B"),
    # согласованное дополнение: неизвестные статусы → «C»
    (["ОТМЕНА"], "C"),
    (["A", "ОТМЕНА"], "C"),
]
for sts, expected in RULES:
    seed_documents(kit_rows(KIT, sts))
    got = get_kit_status(KIT)
    check(f"правило: листы {sts} → «{expected}»",
          got == expected, f"получено «{got}»")

# ---------------------------------------------------------------------------
#  БЛОК 1.2 — правило 2: листов нет
# ---------------------------------------------------------------------------
seed_documents([])
check("пустая БД → «План (не загружен)»",
      get_kit_status(KIT) == KIT_STATUS_NOT_LOADED)

# мусорные шифры отсекаются unique_vitro_sheets — комплект не находится
seed_documents(kit_rows("ИТОГО", ["A"], sheet_numbers=["1"]))
check("мусорные шифры в БД не влияют на поиск комплекта",
      get_kit_status(KIT) == KIT_STATUS_NOT_LOADED)

# пустой шифр на входе
check("пустой шифр → «План (не загружен)»",
      get_kit_status("") == KIT_STATUS_NOT_LOADED)
check("None на входе → «План (не загружен)»",
      get_kit_status(None) == KIT_STATUS_NOT_LOADED)

# ---------------------------------------------------------------------------
#  БЛОК 1.3 — дедуп ревизий и «липкое» аннулирование
# ---------------------------------------------------------------------------
# последняя ревизия листа побеждает: старая C, свежая A
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "C", "1", "2025-01-01"),
    (2, f"{KIT}-1.PDF", KIT, "A", "1", "2025-06-01"),
    (3, f"{KIT}-2.PDF", KIT, "A", "2", "2025-06-01"),
])
check("дедуп ревизий: свежая ревизия листа побеждает → «A»",
      get_kit_status(KIT) == "A")

# равные даты → побеждает больший id
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "C", "1", "2025-06-01"),
    (2, f"{KIT}-1.PDF", KIT, "A", "1", "2025-06-01"),
])
check("равные даты ревизий: больший id побеждает → «A»",
      get_kit_status(KIT) == "A")

# «липкое» аннулирование: аннулированная ревизия листа игнорируется
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "Аннулировано", "1", "2025-01-01"),
    (2, f"{KIT}-1.PDF", KIT, "A", "1", "2025-06-01"),
    (3, f"{KIT}-2.PDF", KIT, "B", "2", "2025-06-01"),
])
check("«липкое» аннулирование: аннулированный лист игнорируется → «B»",
      get_kit_status(KIT) == "B")

# листы без sheet_number: каждый файл — самостоятельный лист
seed_documents([
    (1, f"{KIT}-a.PDF", KIT, "A", None, "2025-06-01"),
    (2, f"{KIT}-b.PDF", KIT, "B", None, "2025-06-02"),
])
check("пустой sheet_number: каждый файл — лист → «B»",
      get_kit_status(KIT) == "B")

# ---------------------------------------------------------------------------
#  БЛОК 1.4 — латинские двойники шифра
# ---------------------------------------------------------------------------
seed_documents(kit_rows(KIT2, ["A", "B"]))
latin_mix = "AT-PД-OB2-C-00"  # латиница: A, T, P, O, B, C; кириллица: Д
check("латинские двойники шифра нормализуются",
      normalize_kit_code(latin_mix) == KIT2,
      f"получено «{normalize_kit_code(latin_mix)}»")
check("статус по латинской записи шифра совпадает",
      get_kit_status(latin_mix)
      == get_kit_status(KIT2) == "B")

# ---------------------------------------------------------------------------
#  БЛОК 1.5 — батчевый вызов get_kit_statuses
# ---------------------------------------------------------------------------
seed_documents(kit_rows(KIT, ["A"]) + kit_rows(KIT2, ["A", "B"], start_id=10)
               + kit_rows(KIT3, ["C"], start_id=20))

single = {c: get_kit_status(c) for c in (KIT, KIT2, KIT3)}
batch = get_kit_statuses([KIT, KIT2, KIT3, KIT_ABSENT])
check("батчевый вызов ≡ одиночному для каждого шифра",
      all(batch.get(c) == s for c, s in single.items())
      and batch.get(KIT_ABSENT) == KIT_STATUS_NOT_LOADED,
      f"batch={batch}, single={single}")
check("ключи батчевого вызова — канонические шифры",
      set(batch) == {KIT, KIT2, KIT3, KIT_ABSENT})

all_map = get_kit_statuses()
check("codes=None → все комплекты базы",
      all_map.get(KIT) == "A" and all_map.get(KIT2) == "B"
      and all_map.get(KIT3) == "C" and KIT_ABSENT not in all_map)

check("строка вместо списка трактуется как один шифр",
      get_kit_statuses(KIT3) == {KIT3: "C"})

check("пустой шифр в батче пропускается",
      get_kit_statuses(["", "   "]) == {})

# один проход БД не мутирует базу (строк: 1 + 2 + 1)
with sqlite_db.get_conn() as conn:
    n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
check("чтение статусов не меняет documents", n_docs == 4,
      f"строк в documents: {n_docs}")

# ---------------------------------------------------------------------------
#  БЛОК 2.1 — _find_kit_code_column: варианты заголовков
# ---------------------------------------------------------------------------
HEADERS_OK = [
    ["Шифр РД"],
    ["Шифр комплекта"],
    ["Комплект"],
    ["шифр  РД "],          # регистр + двойной пробел
    ["Шифр\u00a0РД"],       # неразрывный пробел
    ["шифр комплекта"],     # нижний регистр
]
for headers in HEADERS_OK:
    df = pd.DataFrame({h: ["АТ-РД-КЖ1-1-00"] for h in headers})
    check(f"заголовок {headers!r} находится",
          _find_kit_code_column(df) == headers[0])

# промах — колонки с шифрами нет
df_miss = pd.DataFrame({"Наименование": ["x"], "Кол-во": [1]})
check("без колонки шифров → None", _find_kit_code_column(df_miss) is None)

# фолбэк: первый заголовок, содержащий «ШИФР»
df_fallback = pd.DataFrame({"Шифр узла": ["x"], "Наименование": ["y"]})
check("фолбэк по подстроке «ШИФР»",
      _find_kit_code_column(df_fallback) == "Шифр узла")

# приоритет: «Шифр РД» сильнее «Комплект»
df_priority = pd.DataFrame({
    "Комплект": ["АТ-РД-ОВ2-С-00"],
    "Шифр РД": ["АТ-РД-КЖ1-1-00"],
})
check("приоритет заголовков: «Шифр РД» выбран первым",
      _find_kit_code_column(df_priority) == "Шифр РД")

# ---------------------------------------------------------------------------
#  БЛОК 2.2 — чтение синтетического Excel и добавление колонки
# ---------------------------------------------------------------------------
def build_xlsx(headers, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Комплекты"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


xlsx = build_xlsx(
    ["Наименование", "Шифр РД", "Примечание"],
    [
        ["Комплект КЖ1", KIT, ""],
        ["Комплект ОВ2 (латиница)", "AT-PД-OB2-C-00", "двойники"],
        ["Нет в Витро", KIT_ABSENT, ""],
        ["Пустой шифр", "", ""],
    ],
)
df_x = pd.read_excel(io.BytesIO(xlsx))
col = _find_kit_code_column(df_x)
check("колонка шифров найдена в синтетическом Excel", col == "Шифр РД")

# статусы — как вернул бы get_kit_status (в тесте подставляем напрямую)
statuses = {
    KIT: "A",
    KIT2: "B",
    KIT_ABSENT: KIT_STATUS_NOT_LOADED,
}
result = _apply_kit_statuses(df_x, col, statuses)

check("колонка «Статус комплекта» добавлена последней",
      list(result.columns)
      == ["Наименование", "Шифр РД", "Примечание", KIT_STATUS_COL])
check("исходная таблица не мутируется",
      list(df_x.columns)
      == ["Наименование", "Шифр РД", "Примечание"])

expected_col = ["A", "B", KIT_STATUS_NOT_LOADED, KIT_STATUS_NOT_LOADED]
check("значения статусов корректны (в т.ч. латиница и пустой шифр)",
      list(result[KIT_STATUS_COL]) == expected_col,
      f"получено {list(result[KIT_STATUS_COL])}")

# чтение файла и статусы из БД — сквозной сценарий
db_statuses = get_kit_statuses(df_x[col].tolist())
result_db = _apply_kit_statuses(df_x, col, db_statuses)
check("сквозной сценарий: Excel → БД → колонка совпадает",
      list(result_db[KIT_STATUS_COL]) == expected_col,
      f"получено {list(result_db[KIT_STATUS_COL])}")

# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО проверок: {len(FAILED)}")
    sys.exit(1)
print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
sys.exit(0)
