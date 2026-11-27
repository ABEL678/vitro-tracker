# vitro/sync.py
"""
Синхронизация данных из SharePoint (Витрокад) в локальную SQLite.
Запускается отдельным скриптом sync_runner.py или вручную из вкладки «Управление».

ВАЖНО:
  - Существующие категории замечаний НЕ стираются. Они переносятся
    по ID замечания внутри sqlite_db.save_all().
  - При каждом запуске создаётся снимок истории (history_summary,
    history_status) за текущую дату.
"""

import time
from collections.abc import Callable
from datetime import date

from vitro.sharepoint import (
    fetch_atp_documents,
    fetch_atp_comments,
    fetch_comment_statuses,
    fetch_authors,
    build_comments_summary,
    fetch_complex_names,
)
from vitro.sqlite_db import (
    init_db,
    save_all,
    save_complexes,
    save_history_summary,
    save_history_status,
    upsert_documents,
    load_sovr_statuses,
    log_event,
    get_conn,
)


def refresh_sovr_statuses(docs: list[dict] | None = None,
                          verbose: bool = True,
                          user: str = "sync") -> dict:
    """Шаг синка: витрина статусов СО/ВР (патч 22.24b).

    Порядок:
      1. fetch_atp_documents (только если docs не переданы — в регулярном
         синке используются уже загруженные документы, без второго fetch);
      2. upsert_documents — обновляет документы, НЕ удаляя comments
         (логика 22.24, не дублируется);
      3. load_sovr_statuses — пересобирает documents_sovr по правилу
         «последний по процессу»; заодно обновляет updated_at строк,
         поэтому versioned_cache сбросится по PRAGMA data_version.

    Журнал событий ведёт вызывающая сторона (контракт «одно событие
    refresh в конце синка»):
      - регулярный синк складывает результат шага в сообщение события
        'refresh';
      - ручная кнопка «Обновить статусы СО и ВОР из Витро» пишет
        отдельное событие 'sovr_sync' со своей сводкой.

    Ошибка шага (в т.ч. недоступность Витро при самостоятельном fetch)
    НЕ выбрасывается наружу: остальные сущности синка продолжают работу.

    Возвращает {"ok", "documents", "sheets"} (+ "error" при сбое).
    """
    def log(msg):
        if verbose:
            try:
                print(msg)
            except UnicodeEncodeError:
                print(msg.encode("ascii", "replace").decode("ascii"))

    try:
        init_db()
        if docs is None:
            log("📑 Загрузка документов для витрины СО/ВР...")
            docs = fetch_atp_documents(max_items=0)
        n_docs = upsert_documents(docs)
        n_sheets = load_sovr_statuses(user=user)
        log(f"   Витрина СО/ВР: документов {n_docs}, листов {n_sheets}")
        return {"ok": True, "documents": n_docs, "sheets": n_sheets}
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        log(f"   ⚠️ Витрина СО/ВР не обновлена: {err}")
        return {"ok": False, "documents": 0, "sheets": 0, "error": err}


def refresh_data(verbose: bool = True,
                 progress_cb: Callable[[float, str], None] | None = None) -> dict:
    """
    Полный цикл синхронизации:
      1. Документы (листы) из SharePoint
      2. Замечания + справочники статусов и авторов
      3. Комплекты (коды + названия)
      4. Атомарная запись в SQLite с сохранением категорий
      5. Витрина статусов СО/ВР (documents_sovr)
      6. Снимок истории за текущую дату

    ``progress_cb`` — необязательный колбэк ``(процент, текст)`` для
    прогресс-бара Streamlit. Ошибка колбэка не прерывает синхронизацию.
    """
    t0 = time.time()

    def log(msg):
        if verbose:
            try:
                print(msg)
            except UnicodeEncodeError:
                # Windows cp1251 не поддерживает эмодзи — выводим без них
                print(msg.encode('ascii', 'replace').decode('ascii'))

    def progress(pct: float, msg: str) -> None:
        """Обновляет внешний прогресс-бар, не влияя на сам синк."""
        if progress_cb is None:
            return
        try:
            progress_cb(pct, msg)
        except Exception:
            pass  # сбой UI-колбэка не должен ломать синхронизацию

    # 1. Документы
    progress(5, "📄 Загрузка документов из SharePoint...")
    log("📄 Загрузка документов из SharePoint...")
    docs = fetch_atp_documents(max_items=0)
    doc_ids = {d["id"] for d in docs}
    log(f"   Листов: {len(docs)}")

    # 2. Замечания
    progress(20, "💬 Загрузка замечаний...")
    log("💬 Загрузка замечаний...")
    raw_comments = fetch_atp_comments(doc_ids=doc_ids, max_items=0)
    log(f"   Замечаний: {len(raw_comments)}")

    progress(40, "📚 Загрузка справочников (статусы, авторы)...")
    log("📚 Загрузка справочников (статусы, авторы)...")
    statuses = fetch_comment_statuses()
    authors  = fetch_authors()

    comments = [{
        "id":       c.get("Id"),
        "doc_id":   c.get("VitroBaseLibraryItemId"),
        "comment":  c.get("VitroBaseCommentNote"),
        "status":   statuses.get(c.get("VitroBaseCommentStatus"), ""),
        "author":   authors.get(c.get("VitroBaseCommentAuthor"), ""),
        "created":  c.get("Created"),
        "fix_date": c.get("VitroBaseCommentFixDate"),
    } for c in raw_comments]

    # 3. Комплекты
    progress(50, "🏗 Загрузка комплектов...")
    log("🏗 Загрузка комплектов...")
    complexes = fetch_complex_names()
    log(f"   Комплектов: {len(complexes)}")

    # 4. Запись в БД
    progress(60, "💾 Запись в SQLite...")
    init_db()
    log("💾 Запись в SQLite...")
    n_docs, n_comments = save_all(docs, comments, doc_ids)
    n_complexes = save_complexes(complexes)
    log(f"   Документов: {n_docs}, замечаний: {n_comments}, комплектов: {n_complexes}")

    # 4b. Витрина статусов СО/ВР (патч 22.24b).
    # Документы уже сохранены save_all; upsert обновляет свежие поля,
    # load_sovr_statuses пересобирает витрину. Шаг изолирован: его сбой
    # не прерывает остальные сущности синка. Отдельное событие журнала
    # здесь НЕ пишется: итог шага складывается в сообщение единственного
    # события 'refresh' в конце синка (контракт теста 22.9).
    progress(70, "📑 Обновление статусов СО и ВОР...")
    log("📑 Обновление витрины СО/ВР...")
    try:
        sovr = refresh_sovr_statuses(docs=docs, verbose=verbose)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        log(f"   ⚠️ Витрина СО/ВР не обновлена: {err}")
        sovr = {"ok": False, "documents": 0, "sheets": 0, "error": err}

    if sovr.get("ok"):
        sovr_summary = (f"Витрина СО/ВР: документов {sovr.get('documents', 0)}, "
                        f"листов {sovr.get('sheets', 0)}")
    else:
        sovr_summary = (f"Витрина СО/ВР: ошибка — "
                        f"{sovr.get('error') or 'неизвестная'}")

    # 5. Снимок истории
    progress(88, "📈 Построение снимка истории...")
    log("📈 Построение снимка истории...")
    summary = build_comments_summary(docs, comments)
    today = date.today().isoformat()
    save_history_summary(today, summary)
    save_history_status(today, comments, {d["id"]: d for d in docs})
    log(f"   Снимок за {today}: {len(summary)} комплектов")

    elapsed = round(time.time() - t0, 1)
    refresh_message = f"Загружено {len(comments)} замечаний; {sovr_summary}"
    log_event("refresh", refresh_message, rows=len(comments))
    log(f"\n✅ {refresh_message}")
    log(f"✅ Готово за {elapsed} сек")

    # WAL-checkpoint: сбросить -wal в основной файл, чтобы данные
    # при копировании vitro.db на сервер включали все записи.
    try:
        with get_conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass  # не критично, если база уже в режиме DELETE/NOMODE

    # PATCH-06: сброс кэшей Streamlit, если синк вызван из приложения
    # (кнопка в «Управлении») — данные на вкладках обновятся мгновенно.
    # Для синка из отдельного процесса (sync_runner.py) кэши приложения
    # сбрасывает версионный штамп vitro/_cache.py (см. versioned_cache).
    try:
        import streamlit as st
        st.cache_data.clear()
        log("   Кэши Streamlit сброшены")
    except Exception:
        pass  # bare-режим (запуск вне Streamlit) — не критично

    progress(100, "✅ Синхронизация завершена")

    return {
        "documents": n_docs,
        "comments":  n_comments,
        "complexes": n_complexes,
        "sovr_sheets": sovr.get("sheets", 0),
        "sovr_ok": bool(sovr.get("ok")),
        "sovr_error": sovr.get("error") or "",
        "elapsed":   elapsed,
    }


if __name__ == "__main__":
    # Позволяет запускать файл напрямую: python -m vitro.sync
    refresh_data(verbose=True)