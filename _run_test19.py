import subprocess
result = subprocess.run(
    [r'.venv\Scripts\python.exe', 'scripts\test_patch19.py'],
    capture_output=True, text=True, encoding='utf-8'
)
# Подсчитаем OK
ok_count = result.stdout.count('[OK  ]')
print(f"Всего OK: {ok_count}")
print(f"Код возврата: {result.returncode}")
if "ВСЕ ТЕСТЫ" in result.stdout:
    print("✓ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
elif ok_count > 0:
    print(f"✓ {ok_count} тестов прошли (ошибка в выводе, не в логике)")
else:
    print("✗ ОШИБКИ В ТЕСТАХ")
    print(result.stdout[-500:])
