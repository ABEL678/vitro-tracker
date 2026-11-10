import os
from dotenv import load_dotenv

load_dotenv()

VITRO_USER = os.getenv("VITRO_USER", "")
VITRO_PASSWORD = os.getenv("VITRO_PASSWORD", "")
VITRO_BASE_URL = os.getenv("VITRO_BASE_URL", "https://pdm-lakhta.ru")
VITRO_OKHTA_URL = VITRO_BASE_URL + "/okhta"

# Проверка TLS-сертификата сервера Витро.
# По умолчанию — ВКЛЮЧЕНА (безопасное значение).
# Отключать ("false") можно только временно и только для внутреннего
# контура с самоподписанным сертификатом — тогда NTLM-креденшалы
# уязвимы к перехвату (MITM).
VITRO_VERIFY_SSL = os.getenv("VITRO_VERIFY_SSL", "true").lower() == "true"

HEADERS = {"Accept": "application/json;odata=verbose"}

# Версия приложения
APP_VERSION = "1.4 от 09.11.2026"