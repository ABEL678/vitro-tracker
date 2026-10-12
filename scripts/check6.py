import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitro.db"

conn = sqlite3.connect(str(DB))

print("=== Все сырые статусы замечаний (comments.status) ===\n")

rows = conn.execute("""
    SELECT
        CASE
            WHEN status IS NULL OR TRIM(status) = '' THEN '(ПУСТО)'
            ELSE status
        END AS s,
        COUNT(*) AS n
    FROM comments
    GROUP BY s
    ORDER BY n DESC
""").fetchall()

total = sum(r[1] for r in rows)
print(f"{'Статус':<30} {'Кол-во':>10} {'%':>8}")
print("-" * 52)
for s, n in rows:
    pct = n / total * 100
    print(f"{repr(s):<30} {n:>10,} {pct:>7.1f}%")
print("-" * 52)
print(f"{'ИТОГО':<30} {total:>10,}")

conn.close()