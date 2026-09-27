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
    log_event,
)


def refresh_data(verbose: bool = True) -> dict:
    """
    Полный цикл синхронизации:
      1. Документы (листы) из SharePoint
      2. Замечания + справочники статусов и авторов
      3. Комплекты (коды + названия)
      4. Атомарная запись в SQLite с сохранением категорий
      5. Снимок истории за текущую дату
    """
    t0 = time.time()

    def log(msg):
        if verbose:
            print(msg)

    # 1. Документы
    log("📄 Загрузка документов из SharePoint...")
    docs = fetch_atp_documents(max_items=0)
    doc_ids = {d["id"] for d in docs}
    log(f"   Листов: {len(docs)}")

    # 2. Замечания
    log("💬 Загрузка замечаний...")
    raw_comments = fetch_atp_comments(doc_ids=doc_ids, max_items=0)
    log(f"   Замечаний: {len(raw_comments)}")

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
    log("🏗 Загрузка комплектов...")
    complexes = fetch_complex_names()
    log(f"   Комплектов: {len(complexes)}")

    # 4. Запись в БД
    init_db()
    log("💾 Запись в SQLite...")
    n_docs, n_comments = save_all(docs, comments, doc_ids)
    n_complexes = save_complexes(complexes)
    log(f"   Документов: {n_docs}, замечаний: {n_comments}, комплектов: {n_complexes}")

    # 5. Снимок истории
    log("📈 Построение снимка истории...")
    summary = build_comments_summary(docs, comments)
    today = date.today().isoformat()
    save_history_summary(today, summary)
    save_history_status(today, comments, {d["id"]: d for d in docs})
    log(f"   Снимок за {today}: {len(summary)} комплектов")

    elapsed = round(time.time() - t0, 1)
    log_event("refresh", f"Загружено {len(comments)} замечаний", rows=len(comments))
    log(f"\n✅ Готово за {elapsed} сек")

    return {
        "documents": n_docs,
        "comments":  n_comments,
        "complexes": n_complexes,
        "elapsed":   elapsed,
    }


if __name__ == "__main__":
    # Позволяет запускать файл напрямую: python -m vitro.sync
    refresh_data(verbose=True)