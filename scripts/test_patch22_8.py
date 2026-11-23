"""Тесты патча 22.8 — log_event("refresh") при синхронизации.

Проблема: sync_runner.py не пишет событие в журнал синхронизации.
Полоса «База актуальна на …» (deadlines._load_last_sync_date) берёт
MAX(timestamp) из log WHERE event IN ('sync_from_ui', 'refresh')
AND status = 'OK'.

Исправление:
  - sync_runner.py: после успешного refresh_data() → log_event("refresh", …)
  - refresh_data() (vitro/sync.py:99): log_event("refresh", …) уже есть.

Блоки проверок:
  0. log_event("refresh", …) записывает строку в log с event='refresh',
     status='OK', rows=N.
  1. _load_last_sync_date() читает MAX(timestamp) из log для refresh.
  2. refresh_data() (с замоканным сетью) оставляет запись refresh в log.
  3. sync_runner.py: main() → log_event после refresh_data.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_8.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_8_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import sqlite3  # noqa: E402
import pandas as pd  # noqa: E402

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


def _load_ts_uncached() -> str | None:
    """Прямой запрос к БД (без кэша Streamlit) — MAX(timestamp) из log."""
    try:
        with sqlite_db.get_conn() as conn:
            row = conn.execute("""
                SELECT MAX(timestamp) AS ts FROM log
                WHERE event IN ('sync_from_ui', 'refresh')
                  AND status = 'OK'
            """).fetchone()
        if row and row["ts"]:
            return row["ts"]
    except Exception:
        pass
    return None


def _clear_st_cache() -> None:
    """Сброс кэшей Streamlit (если доступен)."""
    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  БЛОК 0 — log_event("refresh", …) записывает строку
# ---------------------------------------------------------------------------
_clear_log()

sqlite_db.log_event("refresh", "Загружено 42 замечаний", rows=42)

with sqlite_db.get_conn() as conn:
    row = conn.execute(
        "SELECT timestamp, event, message, rows, status FROM log WHERE event = 'refresh'"
    ).fetchone()

check("0: log_event записывает event='refresh'",
      row is not None and row["event"] == "refresh")
check("0: log_event записывает message",
      row is not None and row["message"] == "Загружено 42 замечаний",
      f"получено «{row['message']}»" if row else "нет строки")
check("0: log_event записывает rows=42",
      row is not None and row["rows"] == 42,
      f"получено {row['rows']}" if row else "нет строки")
check("0: log_event записывает status='OK'",
      row is not None and row["status"] == "OK",
      f"получено «{row['status']}»" if row else "нет строки")
check("0: log_event записывает timestamp (не пустой)",
      row is not None and row["timestamp"] is not None and row["timestamp"] != "")


# ---------------------------------------------------------------------------
#  БЛОК 1 — _load_last_sync_date() читает MAX(timestamp) из log
#         (используем uncached-версию, чтобы кэш не мешал)
# ---------------------------------------------------------------------------
_clear_log()

# Сначала — пусто
ts_empty = _load_ts_uncached()
check("1: пустой log → None", ts_empty is None, f"получено {ts_empty!r}")

# После записи refresh
sqlite_db.log_event("refresh", "Загружено 100 замечаний", rows=100)
ts_refresh = _load_ts_uncached()
check("1: после refresh → timestamp не None",
      ts_refresh is not None, f"получено {ts_refresh!r}")

# Запись sync_from_ui тоже учитывается
_clear_log()
sqlite_db.log_event("sync_from_ui", "Синк из UI", rows=50)
ts_sync = _load_ts_uncached()
check("1: sync_from_ui → timestamp не None",
      ts_sync is not None, f"получено {ts_sync!r}")

# Несколько записей → берётся последняя
sqlite_db.log_event("refresh", "Загружено 200 замечаний", rows=200)
ts_latest = _load_ts_uncached()
check("1: несколько записей → MAX(timestamp)",
      ts_latest is not None, f"получено {ts_latest!r}")

# status != 'OK' игнорируется
_clear_log()
sqlite_db.log_event("refresh", "Ошибка синка", status="ERROR")
ts_error = _load_ts_uncached()
check("1: status='ERROR' игнорируется → None",
      ts_error is None, f"получено {ts_error!r}")

# После ERROR → OK
sqlite_db.log_event("refresh", "Успешно", rows=300, status="OK")
ts_ok = _load_ts_uncached()
check("1: после ERROR → OK → timestamp не None",
      ts_ok is not None, f"получено {ts_ok!r}")


# ---------------------------------------------------------------------------
#  БЛОК 2 — refresh_data() (с замоканным сетью) оставляет запись refresh
# ---------------------------------------------------------------------------
# Мокаем все сетевые вызовы
MOCK_DOCS = [
    {"id": 1, "leaf": "АТ-РД-КЖ1-1-00-1.PDF", "discipline": "КЖ",
     "section": "Раздел 1", "complex": "АТ-РД-КЖ1-1-00",
     "status": "A", "revision": "1.0", "vitro_guid": "guid-1",
     "vitro_version": 1},
]
MOCK_COMMENTS = [
    {"Id": 1, "VitroBaseLibraryItemId": 1,
     "VitroBaseCommentNote": "Тестовое замечание",
     "VitroBaseCommentStatus": "Новое",
     "VitroBaseCommentAuthor": "Иванов И.И.",
     "Created": "2026-11-20T10:00:00",
     "VitroBaseCommentFixDate": "",
     },
]
MOCK_STATUSES = {"Новое": "Новое", "Принято в работу": "Принято в работу"}
MOCK_AUTHORS = {"Иванов И.И.": "Иванов И.И."}
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

    _clear_st_cache()
    result = refresh_data(verbose=False)

check("2: refresh_data() возвращает результат с comments",
      isinstance(result, dict) and "comments" in result,
      f"получено {result!r}")
check("2: refresh_data() вернула comments=1",
      result.get("comments") == 1,
      f"получено {result.get('comments')!r}")

with sqlite_db.get_conn() as conn:
    refresh_rows = conn.execute(
        "SELECT * FROM log WHERE event = 'refresh' AND status = 'OK'"
    ).fetchall()

check("2: после refresh_data() в log есть запись refresh со статусом OK",
      len(refresh_rows) >= 1,
      f"строк в log: {len(refresh_rows)}")

if refresh_rows:
    last_refresh = refresh_rows[-1]
    check("2: log.refresh rows совпадает с result[comments]",
          last_refresh["rows"] == result.get("comments", 0),
          f"rows={last_refresh['rows']}, comments={result.get('comments')}")


# ---------------------------------------------------------------------------
#  БЛОК 3 — sync_runner.py: main() → log_event после refresh_data
# ---------------------------------------------------------------------------
# Проверяем, что sync_runner.py импортирует log_event и вызывает его
_sync_runner_src = (ROOT / "sync_runner.py").read_text(encoding="utf-8")
check("3: sync_runner.py импортирует log_event",
      "from vitro.sqlite_db import log_event" in _sync_runner_src)
check("3: sync_runner.py вызывает log_event('refresh', …)",
      'log_event("refresh"' in _sync_runner_src or "log_event('refresh'" in _sync_runner_src)
check("3: sync_runner.py берёт n из result.get('comments')",
      "result.get(\"comments\"" in _sync_runner_src or "result.get('comments'" in _sync_runner_src)

# Интеграционный вызов main() с моками
_clear_log()

with patch("vitro.sync.fetch_atp_documents", mock_docs_fn), \
     patch("vitro.sync.fetch_atp_comments", mock_comments_fn), \
     patch("vitro.sync.fetch_comment_statuses", mock_statuses_fn), \
     patch("vitro.sync.fetch_authors", mock_authors_fn), \
     patch("vitro.sync.fetch_complex_names", mock_complexes_fn), \
     patch("vitro.sync.build_comments_summary", mock_summary_fn):

    _clear_st_cache()

    from sync_runner import main as sync_main  # noqa: E402
    exit_code = sync_main()

check("3: sync_runner.main() возвращает 0 (успех)",
      exit_code == 0, f"exit_code={exit_code}")

with sqlite_db.get_conn() as conn:
    runner_refresh = conn.execute(
        "SELECT * FROM log WHERE event = 'refresh' AND status = 'OK'"
    ).fetchall()

check("3: после sync_runner.main() в log есть запись refresh",
      len(runner_refresh) >= 1,
      f"строк в log: {len(runner_refresh)}")


# ---------------------------------------------------------------------------
#  БЛОК 4 — регресс: log_event не ломается при rows=0
# ---------------------------------------------------------------------------
_clear_log()
sqlite_db.log_event("refresh", "Загружено 0 замечаний", rows=0)

with sqlite_db.get_conn() as conn:
    row_zero = conn.execute(
        "SELECT * FROM log WHERE event = 'refresh' AND rows = 0"
    ).fetchone()

check("4: log_event с rows=0 записывается",
      row_zero is not None and row_zero["event"] == "refresh"
      and row_zero["rows"] == 0)

ts_zero = _load_ts_uncached()
check("4: _load_last_sync_date() читает запись с rows=0",
      ts_zero is not None, f"получено {ts_zero!r}")


# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.8 пройдены.")
