import sqlite3

conn = sqlite3.connect('data/vitro.db')
cursor = conn.cursor()

# Шаг 1: Проверка организаций
print("=== ОРГАНИЗАЦИИ (префиксы до -РД-) ===")
cursor.execute("""
    SELECT DISTINCT substr(complex, 1, instr(complex, '-РД-') - 1) 
    FROM documents 
    WHERE complex LIKE '%-РД-%' 
    LIMIT 50
""")
result = cursor.fetchall()
for r in result:
    print(r[0])

# Шаг 2: Проверка латиницы
print("\n=== ЛАТИНИЦА (AT-РД) ===")
cursor.execute("""
    SELECT COUNT(*) FROM documents 
    WHERE complex LIKE 'AT-%' OR complex LIKE 'AТ-%'
""")
count = cursor.fetchone()[0]
print(f"Найдено: {count}")

conn.close()
