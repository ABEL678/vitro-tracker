import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(str(ROOT / "data" / "vitro.db"))

# Все таблицы
tables = conn.execute("""
    SELECT name FROM sqlite_master WHERE type='table'
""").fetchall()
print("Таблицы:")
for t in tables:
    print(f"  - {t[0]}")

# Схемы интересных таблиц
for table in ["log", "history_summary", "users_activity"]:
    print(f"\n=== Схема {table} ===")
    try:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for c in cols:
            # c = (cid, name, type, notnull, dflt_value, pk)
            print(f"  {c[1]:<20} {c[2]}")
    except Exception as e:
        print(f"  Ошибка: {e}")

# Данные из log
print("\n=== Данные из log (5 последних) ===")
try:
    rows = conn.execute("SELECT * FROM log ORDER BY rowid DESC LIMIT 5").fetchall()
    for r in rows:
        print(r)
except Exception as e:
    print(f"Ошибка: {e}")

# Данные из history_summary
print("\n=== Данные из history_summary (5 последних) ===")
try:
    rows = conn.execute("SELECT * FROM history_summary ORDER BY rowid DESC LIMIT 5").fetchall()
    for r in rows:
        print(r)
except Exception as e:
    print(f"Ошибка: {e}")

conn.close()