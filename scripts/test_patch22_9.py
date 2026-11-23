"""Тесты патча 22.9 — WAL-checkpoint при синхронизации.

Проблема: база работает в WAL-режиме, свежие записи (включая log)
остаются в vitro.db-wal и не попадают в основной vitro.db.
При переносе базы на сервер копированием только vitro.db — запись
в журнале остаётся старой, и полоса «База актуальна на …»
показывает устаревшую дату.

Исправление:
  - sync_runner.py: после log_event → _checkpoint_db() (PRAGMA
    wal_checkpoint(TRUNCATE)).
  - vitro/sync.py: в конце refresh_data() — тот же checkpoint.

Блоки проверок:
  0. БД открывается в WAL-режиме.
  1. Запись в log попадает в -wal до checkpoint.
  2. После checkpoint запись читается из основного файла (без -wal).
  3. refresh_data() с моками выполняет checkpoint (файл -wal пуст).
  4. sync_runner.main() с моками выполняет checkpoint.
  5. Регресс: checkpoint не ломается при уже пустом WAL.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_9.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_9_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import sqlite3  # noqa: E402
import shutil  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sync import refresh_data  # noqa: E402

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


def _clear_log() -> None:
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM log")


def _db_path() -> str:
    """Путь к основному файлу БД."""
    return str(sqlite_db.DB_FULL_PATH)


def _wal_path() -> str:
    """Путь к WAL-файлу."""
    return _db_path() + "-wal"


def _wal_size() -> int:
    """Размер WAL-файла (0 если не существует)."""
    p = _wal_path()
    if os.path.exists(p):
        return os.path.getsize(p)
    return 0


def _read_log_from_copy() -> list:
    """Прочитать log из копии основного файла (без WAL)."""
    # Копируем только основной файл (как при переносе на сервер).
    tmp_copy = os.path.join(_TMPDIR, "copy_for_read.db")
    if os.path.exists(_db_path()):
        shutil.copy2(_db_path(), tmp_copy)
    else:
        # Файла ещё нет — создаём пустую копию
        sqlite3.connect(tmp_copy).close()
        shutil.copy2(tmp_copy, tmp_copy)
    # Открываем копию с PRAGMA wal_checkpoint=PASSIVE — это сбрасывает
    # любые оставшиеся страницы из WAL в основной файл перед чтением.
    conn = sqlite3.connect(tmp_copy, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA wal_checkpoint=PASSIVE")
    rows = conn.execute("SELECT timestamp, event, message, rows, status FROM log").fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
#  БЛОК 0 — БД в WAL-режиме
# ---------------------------------------------------------------------------
with sqlite_db.get_conn() as conn:
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

check("0: journal_mode = WAL",
      mode.upper() == "WAL", f"получено «{mode}»")


# ---------------------------------------------------------------------------
#  БЛОК 1 — запись в log попадает в WAL до checkpoint
# ---------------------------------------------------------------------------
_clear_log()

# Открываем отдельное соединение, записываем и НЕ закрываем его
# (чтобы WAL не сбросился автоматически).
conn1 = sqlite3.connect(_db_path(), timeout=30)
conn1.row_factory = sqlite3.Row
conn1.execute("INSERT INTO log (timestamp, event, message, rows, status) "
              "VALUES (?, 'refresh', 'Тест WAL до checkpoint', 5, 'OK')",
              ("2026-11-23 12:00:00",))
# НЕ коммитим — проверяем, что WAL существует до checkpoint.
# Но для WAL нужно хотя бы сделать commit, иначе данные не попадут
# даже в WAL. Сделаем commit, но НЕ checkpoint.
conn1.commit()

wal_before = _wal_size()
check("1: после log_event WAL существует",
      os.path.exists(_wal_path()), f"WAL size={wal_before}")
check("1: после log_event WAL не пуст",
      wal_before > 0, f"WAL size={wal_before}")

# Прочитаем из копии — данных там нет (они ещё в WAL).
rows_no_wal = _read_log_from_copy()
check("1: без checkpoint log пуст в основном файле",
      len(rows_no_wal) == 0, f"строк={len(rows_no_wal)}")

conn1.close()


# ---------------------------------------------------------------------------
#  БЛОК 2 — после checkpoint запись читается из основного файла
# ---------------------------------------------------------------------------
# Открываем соединение, делаем checkpoint, проверяем.
conn2 = sqlite3.connect(_db_path(), timeout=30)
conn2.row_factory = sqlite3.Row
conn2.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn2.close()

wal_after = _wal_size()
check("2: после checkpoint WAL size = 0 (или отсутствует)",
      wal_after == 0, f"WAL size={wal_after}")

# Теперь прочитаем из копии — данные должны быть.
rows_after = _read_log_from_copy()
check("2: после checkpoint log читается из основного файла",
      len(rows_after) == 1, f"строк={len(rows_after)}")
if rows_after:
    check("2: после checkpoint event = refresh",
          rows_after[0]["event"] == "refresh",
          f"event={rows_after[0]['event']}")
    check("2: после checkpoint message сохранён",
          rows_after[0]["message"] == "Тест WAL до checkpoint",
          f"message={rows_after[0]['message']}")


# ---------------------------------------------------------------------------
#  БЛОК 3 — refresh_data() с моками выполняет checkpoint
# ---------------------------------------------------------------------------
MOCK_DOCS = [
    {"id": 1, "leaf": "АТ-РД-КЖ1-1-00-1.PDF", "discipline": "КЖ",
     "section": "Раздел 1", "complex": "АТ-РД-КЖ1-1-00",
     "status": "A", "revision": "1.0", "vitro_guid": "guid-1",
     "vitro_version": 1},
]
MOCK_COMMENTS = [
    {"Id": 1, "VitroBaseLibraryItemId": 1,
     "VitroBaseCommentNote": "Тест",
     "VitroBaseCommentStatus": "Новое",
     "VitroBaseCommentAuthor": "Иванов",
     "Created": "2026-11-20T10:00:00",
     "VitroBaseCommentFixDate": "",
     },
]
MOCK_STATUSES = {"Новое": "Новое"}
MOCK_AUTHORS = {"Иванов": "Иванов"}
MOCK_COMPLEXES = [{"code": "АТ-РД-КЖ1-1-00", "name": "КЖ", "discipline": "КЖ"}]


def mock_docs_fn(*a, **k):
    return MOCK_DOCS


def mock_comments_fn(*a, **k):
    return MOCK_COMMENTS


def mock_statuses_fn(*a, **k):
    return MOCK_STATUSES


def mock_authors_fn(*a, **k):
    return MOCK_AUTHORS


def mock_complexes_fn(*a, **k):
    return MOCK_COMPLEXES


def mock_summary_fn(*a, **k):
    return [{"Комплект": "АТ-РД-КЖ1-1-00", "Всего замечаний": 1,
             "Закрыто": 0, "Аннулировано": 0, "Активные": 1, "% выполнения": 0}]


_clear_log()

with patch("vitro.sync.fetch_atp_documents", mock_docs_fn), \
     patch("vitro.sync.fetch_atp_comments", mock_comments_fn), \
     patch("vitro.sync.fetch_comment_statuses", mock_statuses_fn), \
     patch("vitro.sync.fetch_authors", mock_authors_fn), \
     patch("vitro.sync.fetch_complex_names", mock_complexes_fn), \
     patch("vitro.sync.build_comments_summary", mock_summary_fn):

    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass

    result = refresh_data(verbose=False)

check("3: refresh_data() выполнилась успешно",
      isinstance(result, dict) and result.get("comments") == 1,
      f"comments={result.get('comments')}")

wal_sync = _wal_size()
check("3: после refresh_data() WAL size = 0",
      wal_sync == 0, f"WAL size={wal_sync}")

# Проверим, что log читается из основного файла.
rows_sync = _read_log_from_copy()
check("3: после refresh_data() log читается из основного файла",
      len(rows_sync) == 1, f"строк={len(rows_sync)}")
if rows_sync:
    check("3: refresh_data() пишет event=refresh",
          rows_sync[0]["event"] == "refresh",
          f"event={rows_sync[0]['event']}")


# ---------------------------------------------------------------------------
#  БЛОК 4 — sync_runner.main() с моками выполняет checkpoint
# ---------------------------------------------------------------------------
_clear_log()

with patch("vitro.sync.fetch_atp_documents", mock_docs_fn), \
     patch("vitro.sync.fetch_atp_comments", mock_comments_fn), \
     patch("vitro.sync.fetch_comment_statuses", mock_statuses_fn), \
     patch("vitro.sync.fetch_authors", mock_authors_fn), \
     patch("vitro.sync.fetch_complex_names", mock_complexes_fn), \
     patch("vitro.sync.build_comments_summary", mock_summary_fn):

    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass

    from sync_runner import main as sync_main  # noqa: E402
    exit_code = sync_main()

check("4: sync_runner.main() возвращает 0",
      exit_code == 0, f"exit_code={exit_code}")

wal_runner = _wal_size()
check("4: после sync_runner.main() WAL size = 0",
      wal_runner == 0, f"WAL size={wal_runner}")

rows_runner = _read_log_from_copy()
check("4: после sync_runner.main() log читается из основного файла",
      len(rows_runner) >= 1, f"строк={len(rows_runner)}")
refresh_rows = [r for r in rows_runner if r["event"] == "refresh"]
check("4: sync_runner пишет event=refresh",
      len(refresh_rows) >= 1, f"refresh записей={len(refresh_rows)}")


# ---------------------------------------------------------------------------
#  БЛОК 5 — регресс: checkpoint не ломается при уже пустом WAL
# ---------------------------------------------------------------------------
# Повторный checkpoint на уже проверпунной БД.
with sqlite_db.get_conn() as conn:
    res = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]

check("5: повторный checkpoint не падает",
      res is not None)

# Проверим, что данные не повреждены (должно быть >= 1).
rows_after2 = _read_log_from_copy()
check("5: после повторного checkpoint данные не потеряны",
      len(rows_after2) >= 1, f"строк={len(rows_after2)}")


# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.9 пройдены.")
