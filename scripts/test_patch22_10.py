"""Тесты патча 22.10 — комплект «АТ-РД-СП-0-00» не выгружается.

Проблема: файлы из папки АТ-РД-СП-0-00 («Ведомость комплектов»)
отбрасывались списком _EXCLUDED_LEAFS в sharepoint.py, поэтому
в таблице documents строк по этому комплекту не было.

Исправление:
  - Убрано из _EXCLUDED_LEAFS.
  - fetch_atp_documents: для complex == «АТ-РД-СП-0-00» discipline
    переопределяется на «АТ-РД-СП-0-00» (а не «01_РД» из пути).
  - fetch_complex_names: тот же override.
  - DISCIPLINE_NAMES: добавлена запись «АТ-РД-СП-0-00» →
    «Ведомость комплектов».

Блоки проверок:
  0. _EXCLUDED_LEAFS больше не содержит АТ-РД-СП-0-00.PDF/.DWG.
  1. _is_excluded проходит для файлов АТ-РД-СП-0-00.
  2. parse_path для пути Ведомости комплектов возвращает
     discipline="01_РД", section="Комплекты/01_РД/Основной вид",
     comp="АТ-РД-СП-0-00" (до override).
  3. fetch_atp_documents: mock-файл из папки АТ-РД-СП-0-00
     попадает в docs с discipline="АТ-РД-СП-0-00".
  4. fetch_complex_names: комплект «АТ-РД-СП-0-00» появляется
     в результате с discipline="АТ-РД-СП-0-00".
  5. DISCIPLINE_NAMES содержит «АТ-РД-СП-0-00» → «Ведомость комплектов».
  6. discipline_name() возвращает корректное название.
  7. Регресс: обычные комплекты (КЖ1, АР1) не затронуты.

СТОП-ЛИСТ: real data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_10.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_10_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import sqlite3  # noqa: E402
import shutil  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sharepoint import (  # noqa: E402
    _EXCLUDED_LEAFS,
    _is_excluded,
    parse_path,
    fetch_atp_documents,
    fetch_complex_names,
    ATP_WORK,
)
from vitro.disciplines import DISCIPLINE_NAMES, discipline_name  # noqa: E402

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


# ===========================================================================
#  БЛОК 0 — _EXCLUDED_LEAFS больше не содержит АТ-РД-СП-0-00
# ===========================================================================
check("0: АТ-РД-СП-0-00.PDF не в _EXCLUDED_LEAFS",
      "АТ-РД-СП-0-00.PDF" not in _EXCLUDED_LEAFS,
      f"содержит: {_EXCLUDED_LEAFS}")
check("0: АТ-РД-СП-0-00.DWG не в _EXCLUDED_LEAFS",
      "АТ-РД-СП-0-00.DWG" not in _EXCLUDED_LEAFS,
      f"содержит: {_EXCLUDED_LEAFS}")


# ===========================================================================
#  БЛОК 1 — _is_excluded проходит для файлов АТ-РД-СП-0-00
# ===========================================================================
test_dir = f"{ATP_WORK}/01_РД/Комплекты/01_РД/Основной вид/АТ-РД-СП-0-00"

check("1: _is_excluded('.../АТ-РД-СП-0-00.PDF') = False",
      not _is_excluded(test_dir, "АТ-РД-СП-0-00.PDF"))
check("1: _is_excluded('.../АТ-РД-СП-0-00.DWG') = False",
      not _is_excluded(test_dir, "АТ-РД-СП-0-00.DWG"))

# Обычные маркеры всё ещё работают
check("1: _is_excluded с TECT = True",
      _is_excluded(test_dir, "TECT-test.PDF"))
check("1: _is_excluded с АННУЛИРОВАН = True",
      _is_excluded(test_dir, "АННУЛИРОВАН.PDF"))


# ===========================================================================
#  БЛОК 2 — parse_path для пути Ведомости комплектов
# ===========================================================================
# Путь: /okhta/.../01_В процессе/01_РД/Комплекты/01_РД/Основной вид/АТ-РД-СП-0-00/
rel_path = "01_РД/Комплекты/01_РД/Основной вид/АТ-РД-СП-0-00"
full_path = f"{ATP_WORK}/{rel_path}"

disc, sec, comp = parse_path(full_path)
check("2: parse_path discipline = '01_РД' (до override)",
      disc == "01_РД", f"получено «{disc}»")
check("2: parse_path section содержит 'Комплекты'",
      "Комплекты" in sec, f"получено «{sec}»")
check("2: parse_path comp = 'АТ-РД-СП-0-00'",
      comp == "АТ-РД-СП-0-00", f"получено «{comp}»")


# ===========================================================================
#  БЛОК 3 — fetch_atp_documents: discipline override
# ===========================================================================
MOCK_DOCS_WITH_SP = [
    {
        "Id": 9999,
        "UniqueId": "guid-at-sp-0-00-pdf",
        "OData__UIVersionString": "2.0",
        "FileLeafRef": "АТ-РД-СП-0-00.PDF",
        "FileDirRef": full_path,
        "VitroBaseStatus": None,
        "VitroBaseDocumentRevision": "1.0",
        "VitroBaseFullName": "Ведомость комплектов",
        "VitroBaseStatusDate": "2026-11-20",
        "VitroBaseDocSheetNumber": "",
    },
    # Обычный документ для регресса
    {
        "Id": 9998,
        "UniqueId": "guid-at-kzh1",
        "OData__UIVersionString": "1.0",
        "FileLeafRef": "АТ-РД-КЖ1-1-00-1.PDF",
        "FileDirRef": f"{ATP_WORK}/02_КР/Раздел 1/АТ-РД-КЖ1-1-00",
        "VitroBaseStatus": None,
        "VitroBaseDocumentRevision": "1.0",
        "VitroBaseFullName": "КЖ",
        "VitroBaseStatusDate": "2026-11-20",
        "VitroBaseDocSheetNumber": "1",
    },
]

MOCK_STATUSES = {}  # doc statuses not critical for this test
MOCK_COMPLEXES = []  # not used in fetch_atp_documents


def mock_fetch_all(*args, **kwargs):
    return MOCK_DOCS_WITH_SP


def mock_fetch_doc_statuses(*a, **k):
    return MOCK_STATUSES


def mock_fetch_complex_names(*a, **k):
    return MOCK_COMPLEXES


with patch("vitro.sharepoint._fetch_all", mock_fetch_all), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):

    docs = fetch_atp_documents()

# Проверяем, что документ с АТ-РД-СП-0-00 попал в результаты
sp_docs = [d for d in docs if d["complex"] == "АТ-РД-СП-0-00"]
check("3: fetch_atp_documents: комплект АТ-РД-СП-0-00 в результатах",
      len(sp_docs) == 1, f"найдено {len(sp_docs)} docs")
if sp_docs:
    check("3: discipline = 'АТ-РД-СП-0-00' (override сработал)",
          sp_docs[0]["discipline"] == "АТ-РД-СП-0-00",
          f"получено «{sp_docs[0]['discipline']}»")
    check("3: section = '' (override очистил section)",
          sp_docs[0]["section"] == "",
          f"получено «{sp_docs[0]['section']}»")
    check("3: complex = 'АТ-РД-СП-0-00'",
          sp_docs[0]["complex"] == "АТ-РД-СП-0-00",
          f"получено «{sp_docs[0]['complex']}»")

# Регресс: обычный комплект не затронут
kzh_docs = [d for d in docs if d["complex"] == "АТ-РД-КЖ1-1-00"]
check("3: регресс — обычный комплект (КЖ1) не затронут",
      len(kzh_docs) == 1, f"найдено {len(kzh_docs)} docs")
if kzh_docs:
    check("3: регресс — discipline обычного комплекта = '02_КР'",
          kzh_docs[0]["discipline"] == "02_КР",
          f"получено «{kzh_docs[0]['discipline']}»")


# ===========================================================================
#  БЛОК 4 — fetch_complex_names: комплект появляется
# ===========================================================================
# Mock: один PDF-файл + одна папка с названием
MOCK_COMPLEX_NAMES_RAW = [
    {
        "Id": 100,
        "FileLeafRef": "АТ-РД-СП-0-00.PDF",
        "FileDirRef": full_path,
        "VitroBaseFullName": "",
    },
    {
        "Id": 101,
        "FileLeafRef": "АТ-РД-СП-0-00",
        "FileDirRef": full_path,
        "VitroBaseFullName": "Ведомость комплектов",
    },
    {
        "Id": 102,
        "FileLeafRef": "АТ-РД-КЖ1-1-00.PDF",
        "FileDirRef": f"{ATP_WORK}/02_КР/Раздел 1/АТ-РД-КЖ1-1-00",
        "VitroBaseFullName": "",
    },
]


def mock_fetch_all_complex_names(*args, **kwargs):
    return MOCK_COMPLEX_NAMES_RAW


with patch("vitro.sharepoint._fetch_all", mock_fetch_all_complex_names), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):

    complexes = fetch_complex_names()

sp_in_names = [c for c in complexes if c["code"] == "АТ-РД-СП-0-00"]
check("4: fetch_complex_names: АТ-РД-СП-0-00 в результатах",
      len(sp_in_names) == 1, f"найдено {len(sp_in_names)} комплектов")
if sp_in_names:
    check("4: discipline = 'АТ-РД-СП-0-00'",
          sp_in_names[0]["discipline"] == "АТ-РД-СП-0-00",
          f"получено «{sp_in_names[0]['discipline']}»")
    check("4: name = 'Ведомость комплектов'",
          sp_in_names[0]["name"] == "Ведомость комплектов",
          f"получено «{sp_in_names[0]['name']}»")

# Регресс: КЖ1
kzh_in_names = [c for c in complexes if c["code"] == "АТ-РД-КЖ1-1-00"]
check("4: регресс — КЖ1 в fetch_complex_names",
      len(kzh_in_names) == 1, f"найдено {len(kzh_in_names)} комплектов")


# ===========================================================================
#  БЛОК 5 — DISCIPLINE_NAMES содержит новую запись
# ===========================================================================
check("5: DISCIPLINE_NAMES содержит 'АТ-РД-СП-0-00'",
      "АТ-РД-СП-0-00" in DISCIPLINE_NAMES,
      f"ключи: {list(DISCIPLINE_NAMES.keys())}")
check("5: DISCIPLINE_NAMES['АТ-РД-СП-0-00'] = 'Ведомость комплектов'",
      DISCIPLINE_NAMES.get("АТ-РД-СП-0-00") == "Ведомость комплектов",
      f"получено «{DISCIPLINE_NAMES.get('АТ-РД-СП-0-00')}»")


# ===========================================================================
#  БЛОК 6 — discipline_name() возвращает корректное название
# ===========================================================================
check("6: discipline_name('АТ-РД-СП-0-00') = 'Ведомость комплектов'",
      discipline_name("АТ-РД-СП-0-00") == "Ведомость комплектов",
      f"получено «{discipline_name('АТ-РД-СП-0-00')}»")
check("6: discipline_name('01_АР') = 'Архитектурные решения'",
      discipline_name("01_АР") == "Архитектурные решения",
      f"получено «{discipline_name('01_АР')}»")
# Fallback для неизвестного кода
check("6: discipline_name('UNKNOWN') = 'UNKNOWN' (fallback)",
      discipline_name("UNKNOWN") == "UNKNOWN",
      f"получено «{discipline_name('UNKNOWN')}»")


# ===========================================================================
#  БЛОК 7 — регресс: обычные комплекты не затронуты
# ===========================================================================
# Проверяем, что все прежние дисциплины на месте
expected_disciplines = {
    "01_АР", "01_АС", "02_КР", "03_ЭС", "04_МЕХ",
    "05_ТХ", "06_ВТ", "06_ГК", "06_ДГУ", "06_ХЦ",
    "07_ГП,АД",
}
missing = expected_disciplines - set(DISCIPLINE_NAMES.keys())
check("7: регресс — все прежние дисциплины на месте",
      len(missing) == 0, f"отсутствуют: {missing}")

# Проверяем, что normalize_complex не ломает АТ-РД-СП-0-00
from vitro.sharepoint import normalize_complex  # noqa: E402
check("7: normalize_complex('АТ-РД-СП-0-00') = 'АТ-РД-СП-0-00'",
      normalize_complex("АТ-РД-СП-0-00") == "АТ-РД-СП-0-00",
      f"получено «{normalize_complex('АТ-РД-СП-0-00')}»")


# ===========================================================================
#  Итог
# ===========================================================================
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.10 пройдены.")
