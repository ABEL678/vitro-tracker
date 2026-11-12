# vitro/_cache.py
"""
Кэширование загрузчиков, зависящее от версии данных БД (PATCH-06).

Зачем: @st.cache_data(ttl=3600) живёт в процессе приложения, а данные
может обновить ДРУГОЙ процесс (sync_runner.py — SharePoint-синк) или
обычный UPDATE (правка категории замечания). После таких изменений
пользователи видели устаревшие цифры до истечения TTL.

Как: каждый versioned_cache-загрузчик получает первым аргументом
«отпечаток» данных — PRAGMA data_version, который растёт при любом
коммите из любого соединения/процесса (INSERT, UPDATE, DELETE, синк).
Отпечаток изменился → кэш автоматически пересчитывается. Сам отпечаток
кэшируется на 5 секунд, поэтому цена проверки ≈ 0.

Техническая деталь: PRAGMA data_version корректно отражает чужие
коммиты только на долгоживущем соединении — у нового соединения первое
значение может отставать (WAL). Поэтому держим одно read-соединение
на процесс (под локом, т.к. Streamlit многопоточный).
"""
import functools
import sqlite3
import threading

import streamlit as st

from vitro.sqlite_db import DB_FULL_PATH

_lock = threading.Lock()
_ver_conn: sqlite3.Connection | None = None
_REGISTRY: dict = {}   # fn_key → исходная функция-загрузчик


def _read_data_version() -> int:
    global _ver_conn
    with _lock:
        if _ver_conn is None:
            _ver_conn = sqlite3.connect(str(DB_FULL_PATH), timeout=30)
        try:
            row = _ver_conn.execute("PRAGMA data_version").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            # БД временно недоступна — не валидируем кэш, а не роняем вкладку
            return 0


@st.cache_data(ttl=5, show_spinner=False)
def data_version() -> int:
    """Отпечаток данных БД; пересчитывается не чаще раза в 5 секунд."""
    return _read_data_version()


def versioned_cache(ttl: int = 3600):
    """
    Замена @st.cache_data для загрузчиков, читающих БД:
    кэш автоматически сбрасывается при любом изменении данных,
    в том числе сделанном другим процессом (синк).

    Сохраняет .clear() для точечного сброса (используется в authors.py).

    Технически: версия и идентификатор функции передаются АРГУМЕНТАМИ
    кэшируемой функции-диспетчера — так они гарантированно входят в ключ
    кэша Streamlit (аргументы с «_» не хэшируются, а одинаковые вложенные
    функции без различающего аргумента конфликтуют за один ключ).
    """
    def deco(fn):
        fn_key = f"{fn.__module__}.{fn.__qualname__}#{id(fn)}"
        _REGISTRY[fn_key] = fn

        def _dispatch(fn_key: str, ver: int, args: tuple, kwargs: tuple):
            return _REGISTRY[fn_key](*args, **dict(kwargs))

        # уникальное имя — доп. защита от коллизий ключей
        _dispatch.__name__ = f"_versioned_{fn.__name__}"
        _dispatch.__qualname__ = f"_versioned_{fn.__qualname__}"
        cached = st.cache_data(ttl=ttl, show_spinner=False)(_dispatch)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            kw = tuple(sorted(kwargs.items()))
            return cached(fn_key, data_version(), args, kw)

        wrapper.clear = cached.clear
        wrapper._versioned = True
        return wrapper

    return deco
