import sqlite3
conn = sqlite3.connect('data/vitro.db')
cur = conn.cursor()

print("=== plan_drawings columns ===")
cur.execute("PRAGMA table_info(plan_drawings)")
for r in cur.fetchall():
    print(f"  {r}")

print("\n=== sample plan_drawings ===")
cur.execute("SELECT kit_code, discipline, section, qty FROM plan_drawings LIMIT 10")
for r in cur.fetchall():
    print(f"  kit_code={r[0]}, discipline={r[1]}, section={r[2]}, qty={r[3]}")

print("\n=== unique disciplines in plan_drawings ===")
cur.execute("SELECT DISTINCT discipline FROM plan_drawings WHERE discipline IS NOT NULL AND discipline != '' LIMIT 20")
for r in cur.fetchall():
    print(f"  {r[0]}")

print("\n=== unique sections in plan_drawings ===")
cur.execute("SELECT DISTINCT section FROM plan_drawings WHERE section IS NOT NULL AND section != '' LIMIT 20")
for r in cur.fetchall():
    print(f"  {r[0]}")

conn.close()
