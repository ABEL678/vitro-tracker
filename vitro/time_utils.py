# vitro/time_utils.py
"""
Утилиты для работы со временем.

Все timestamp в БД (log.timestamp, users_activity.timestamp) хранятся
в UTC. При показе пользователю переводим в МСК (UTC+3).
"""

from datetime import datetime, timedelta

MSK_OFFSET = timedelta(hours=3)


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
    """UTC datetime → МСК datetime."""
    if dt is None:
        return None
    return dt + MSK_OFFSET


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

    dt = parse_utc(timestamp_str)
    if dt is None:
        return str(timestamp_str)

    dt_msk = dt + MSK_OFFSET
    base = dt_msk.strftime("%d.%m.%Y, %H:%M")

    if with_msk_label:
        return f"{base} (МСК)"
    return base
