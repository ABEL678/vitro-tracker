import os
from dotenv import load_dotenv

load_dotenv()

VITRO_USER = os.getenv("VITRO_USER", "")
VITRO_PASSWORD = os.getenv("VITRO_PASSWORD", "")
VITRO_BASE_URL = os.getenv("VITRO_BASE_URL", "https://pdm-lakhta.ru")
VITRO_OKHTA_URL = VITRO_BASE_URL + "/okhta"
VITRO_VERIFY_SSL = os.getenv("VITRO_VERIFY_SSL", "false").lower() == "true"

HEADERS = {"Accept": "application/json;odata=verbose"}