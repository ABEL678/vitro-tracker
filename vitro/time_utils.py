# vitro/time_utils.py
"""
Утилиты для работы со временем.

Все timestamp в БД (log.timestamp, users_activity.timestamp) хранятся
в МСК (Europe/Moscow) — их пишут `msk_now_str()` / `now_msk()`,
поэтому результат не зависит от часового пояса сервера.
При показе пользователю дополнительный сдвиг не нужен.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

# МСК = UTC+3 круглый год (летнее время отменено в 2014).
MSK = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    """Текущее время в МСК независимо от часового пояса сервера."""
    return datetime.now(MSK)


def msk_now_str() -> str:
    """Время МСК в формате БД: 'YYYY-MM-DDTHH:MM:SS' (без offset)."""
    return now_msk().strftime("%Y-%m-%dT%H:%M:%S")


def parse_utc(timestamp_str) -> datetime | None:
    """
    Парсит строку из БД формата 'YYYY-MM-DDTHH:MM:SS' → datetime (UTC).
    Возвращает None, если строка пустая или не парсится.
    """
    if not timestamp_str:
        return None
    try:
        return datetime.strptime(str(timestamp_str)[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def to_msk(dt: datetime | None) -> datetime | None:
    """Приводит datetime к МСК. Naive считается уже московским."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def parse_msk(timestamp_str) -> datetime | None:
    """Парсит строку из БД как время МСК (в этом формате его пишет msk_now_str)."""
    if not timestamp_str:
        return None
    try:
        return datetime.strptime(
            str(timestamp_str)[:19], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=MSK)
    except (ValueError, TypeError):
        return None


def fmt_msk(timestamp_str, with_msk_label: bool = False) -> str:
    """
    Парсит timestamp из БД (UTC) и возвращает строку в МСК.

    Аргументы:
      timestamp_str    — строка из БД.
      with_msk_label   — если True, добавляет «(МСК)» в конец.
                         Используется в хедерах (app.py, deadlines.py).

    Возвращает:
      - '—', если timestamp пустой.
      - Исходную строку, если парсинг не удался.
      - Строку в МСК иначе.
    """
    if not timestamp_str:
        return "—"

    dt = parse_msk(timestamp_str)
    if dt is None:
        return str(timestamp_str)

    base = dt.strftime("%d.%m.%Y, %H:%M")

    if with_msk_label:
        return f"{base} (МСК)"
    return base
