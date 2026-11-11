import sqlite3
conn = sqlite3.connect('data/vitro.db')
cur = conn.cursor()

print("=== plan_kits ===")
cur.execute("PRAGMA table_info(plan_kits)")
for r in cur.fetchall():
    print(f"  {r}")

print("\n=== plan_drawings ===")
cur.execute("PRAGMA table_info(plan_drawings)")
for r in cur.fetchall():
    print(f"  {r}")

print("\n=== sample plan_drawings ===")
cur.execute("SELECT * FROM plan_drawings LIMIT 5")
for r in cur.fetchall():
    print(f"  {r}")

print(f"\nplan_drawings rows: {cur.execute('SELECT COUNT(*) FROM plan_drawings').fetchone()[0]}")
conn.close()
