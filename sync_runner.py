# sync_runner.py
"""
Ручной запуск синхронизации (для отладки) или запуск по расписанию.

Пример запуска вручную:
    python sync_runner.py

Пример для Планировщика задач Windows (ежедневно в 06:00):
    Программа: C:\\Users\\abelo\\Vitro-tracker\\.venv\\Scripts\\python.exe
    Аргументы: C:\\Users\\abelo\\Vitro-tracker\\sync_runner.py
    Рабочая папка: C:\\Users\\abelo\\Vitro-tracker

Пример для cron (Linux, ежедневно в 06:00):
    0 6 * * * cd /path/to/vitro_app && .venv/bin/python sync_runner.py
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vitro.sync import refresh_data
from vitro.sqlite_db import log_event, get_conn


def _checkpoint_db() -> None:
    """WAL-checkpoint: сбрасывает все страницы из -wal в основной файл."""
    try:
        with get_conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass  # не критично, если база уже в режиме DELETE/NOMODE


def main() -> int:
    print(f"=== Запуск синхронизации: {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    try:
        result = refresh_data(verbose=True)
        n = result.get("comments", 0)
        log_event("refresh", f"Загружено {n} замечаний", rows=n)
        _checkpoint_db()
        print(f"\nИтог: {result}")
        return 0
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"\n❌ Ошибка: {err}", file=sys.stderr)
        traceback.print_exc()
        try:
            log_event("sync_error", err, status="ERROR")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())