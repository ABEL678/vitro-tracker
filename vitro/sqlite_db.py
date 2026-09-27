# vitro/sqlite_db.py
"""
Работа с локальной SQLite-базой.
Ключевые особенности:
  - Оптимистичная блокировка категорий (category_version) для безопасного
    одновременного редактирования 10 специалистами.
  - Функция load_remarks_for_editor для загрузки ограниченного среза
    замечаний в st.data_editor (не тянем все 65k строк).
  - save_all атомарно перезаписывает documents/comments, сохраняя
    ранее введённые категории и их версии по ID замечания.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = os.environ.get("SQLITE_PATH", "data/vitro.db")
DB_FULL_PATH = ROOT / DB_PATH
DB_FULL_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  Подключение
# ---------------------------------------------------------------------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_FULL_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  Инициализация схемы
# ---------------------------------------------------------------------------
def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id            INTEGER PRIMARY KEY,
            leaf          TEXT NOT NULL,
            discipline    TEXT,
            section       TEXT,
            complex       TEXT,
            status        TEXT,
            revision      TEXT,
            dir_ref       TEXT,
            name          TEXT,
            status_date   TEXT,
            sheet_number  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_documents_complex    ON documents(complex);
        CREATE INDEX IF NOT EXISTS idx_documents_discipline ON documents(discipline);

        CREATE TABLE IF NOT EXISTS comments (
            id                     INTEGER PRIMARY KEY,
            doc_id                 INTEGER NOT NULL,
            comment                TEXT,
            status                 TEXT,
            author                 TEXT,
            created                TEXT,
            fix_date               TEXT,
            category               TEXT,
            category_date          TEXT,
            category_user          TEXT,
            category_version       INTEGER DEFAULT 0,
            category_updated_at    TEXT,
            FOREIGN KEY (doc_id) REFERENCES documents(id)
        );
        CREATE INDEX IF NOT EXISTS idx_comments_doc      ON comments(doc_id);
        CREATE INDEX IF NOT EXISTS idx_comments_status   ON comments(status);
        CREATE INDEX IF NOT EXISTS idx_comments_category ON comments(category);

        CREATE TABLE IF NOT EXISTS history_status (
            snapshot_date TEXT NOT NULL,
            comment_id    INTEGER NOT NULL,
            status        TEXT,
            complex       TEXT,
            PRIMARY KEY (snapshot_date, comment_id)
        );
        CREATE INDEX IF NOT EXISTS idx_history_status_complex ON history_status(complex);

        CREATE TABLE IF NOT EXISTS history_summary (
            snapshot_date TEXT NOT NULL,
            complex       TEXT NOT NULL,
            total         INTEGER,
            closed        INTEGER,
            annulled      INTEGER,
            active        INTEGER,
            percent       REAL,
            PRIMARY KEY (snapshot_date, complex)
        );

        CREATE TABLE IF NOT EXISTS complexes (
            code        TEXT PRIMARY KEY,
            name        TEXT,
            discipline  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_complexes_discipline ON complexes(discipline);

        CREATE TABLE IF NOT EXISTS log (
            timestamp TEXT,
            event     TEXT,
            message   TEXT,
            rows      INTEGER,
            status    TEXT
        );

        CREATE TABLE IF NOT EXISTS users_activity (
            timestamp  TEXT,
            user       TEXT,
            comment_id INTEGER,
            old_cat    TEXT,
            new_cat    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_users_activity_user ON users_activity(user);
        """)

        # Мягкая миграция: если БД была создана старой версией — добавим колонки
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(comments)")}
        for col, ddl in [
            ("category_version",    "INTEGER DEFAULT 0"),
            ("category_updated_at", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE comments ADD COLUMN {col} {ddl}")


# ---------------------------------------------------------------------------
#  Категоризация с оптимистичной блокировкой
# ---------------------------------------------------------------------------
def update_category_safe(comment_id: int, category: str, user: str,
                         expected_version: int) -> tuple[bool, str]:
    """
    Обновляет категорию только если версия в БД совпадает с ожидаемой.
    Возвращает (успех, сообщение).
    Записывает действие в users_activity.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        old_row = conn.execute(
            "SELECT category FROM comments WHERE id = ?", (comment_id,)
        ).fetchone()
        old_cat = old_row["category"] if old_row else None

        cur = conn.execute("""
            UPDATE comments
            SET category = ?, category_user = ?, category_date = ?,
                category_version = category_version + 1,
                category_updated_at = ?
            WHERE id = ? AND COALESCE(category_version, 0) = ?
        """, (category, user, now, now, comment_id, expected_version))

        if cur.rowcount == 0:
            row = conn.execute("""
                SELECT category, category_user, category_date
                FROM comments WHERE id = ?
            """, (comment_id,)).fetchone()
            if row:
                return False, (
                    f"Замечание уже изменил {row['category_user'] or 'другой пользователь'} "
                    f"({row['category_date'] or '—'}), новая категория: {row['category'] or '—'}"
                )
            return False, "Строка не найдена"

        conn.execute("""
            INSERT INTO users_activity (timestamp, user, comment_id, old_cat, new_cat)
            VALUES (?, ?, ?, ?, ?)
        """, (now, user, comment_id, old_cat, category))

    return True, "OK"


# ---------------------------------------------------------------------------
#  Загрузка среза замечаний для st.data_editor
# ---------------------------------------------------------------------------
def load_remarks_for_editor(disciplines=None, kits=None, sections=None,
                            statuses=None, only_uncategorized=False,
                            limit: int = 500):
    """
    Возвращает срез замечаний с учётом каскадных фильтров.

    Поля:
      id, discipline, section, complex, sheet (без .pdf), sheet_name,
      comment, api_status, author, created (дд.мм.гггг),
      category, category_user, category_date, category_version
    """
    import pandas as pd

    q = """
        SELECT c.id,
               d.discipline,
               d.section,
               d.complex,
               REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS sheet,
               d.name AS sheet_name,
               c.comment,
               c.status AS api_status,
               c.author,
               strftime('%d.%m.%Y', c.created) AS created,
               c.category,
               c.category_user,
               c.category_date,
               COALESCE(c.category_version, 0) AS category_version
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE 1=1
    """
    params: list = []

    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if sections:
        q += f" AND d.section IN ({','.join('?' * len(sections))})"
        params += list(sections)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if statuses:
        q += f" AND c.status IN ({','.join('?' * len(statuses))})"
        params += list(statuses)
    if only_uncategorized:
        q += " AND (c.category IS NULL OR c.category = '')"

    q += " ORDER BY d.discipline, d.section, d.complex, d.leaf, c.id LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


# ---------------------------------------------------------------------------
#  Атомарная запись документов и замечаний
# ---------------------------------------------------------------------------
def _pick(d, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def save_all(docs: list[dict], comments: list[dict], doc_ids: set) -> tuple:
    """
    Атомарно перезаписывает documents и comments.
    Сохраняет ранее введённые категории и их версии по ID замечания.
    Порядок:
      1. Читаем существующие категории и версии.
      2. DELETE FROM comments (освобождаем FK).
      3. DELETE FROM documents.
      4. INSERT документов.
      5. INSERT комментариев с перенесёнными категориями.
    """
    with get_conn() as conn:
        existing = {}
        for row in conn.execute("""
            SELECT id, category, category_date, category_user,
                   category_version, category_updated_at
            FROM comments
        """):
            existing[row["id"]] = (
                row["category"], row["category_date"], row["category_user"],
                row["category_version"] or 0, row["category_updated_at"],
            )

        conn.execute("DELETE FROM comments")
        conn.execute("DELETE FROM documents")

        if docs:
            conn.executemany("""
                INSERT INTO documents
                    (id, leaf, discipline, section, complex, status,
                     revision, dir_ref, name, status_date, sheet_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (d["id"], d["leaf"], d.get("discipline"), d.get("section"),
                 d.get("complex"), d.get("status"), d.get("revision"),
                 d.get("dir_ref"), d.get("name"), d.get("status_date"),
                 d.get("sheet_number"))
                for d in docs
            ])

        rows = []
        for c in comments:
            cid = _pick(c, "id", "Id")
            did = _pick(c, "doc_id", "VitroBaseLibraryItemId")
            if cid is None or did is None or did not in doc_ids:
                continue
            cat, cat_date, cat_user, cat_ver, cat_upd = existing.get(
                cid, (None, None, None, 0, None)
            )
            rows.append((
                cid, did,
                _pick(c, "comment", "VitroBaseCommentNote", default=""),
                _pick(c, "status", "VitroBaseCommentStatus", default=""),
                _pick(c, "author", "VitroBaseCommentAuthor", default=""),
                _pick(c, "created", "Created", default=""),
                _pick(c, "fix_date", "VitroBaseCommentFixDate", default=None),
                cat, cat_date, cat_user, cat_ver, cat_upd,
            ))

        if rows:
            conn.executemany("""
                INSERT INTO comments
                    (id, doc_id, comment, status, author, created, fix_date,
                     category, category_date, category_user,
                     category_version, category_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    return (len(docs), len(rows))


# ---------------------------------------------------------------------------
#  Комплекты
# ---------------------------------------------------------------------------
def save_complexes(rows: list[dict]) -> int:
    if not rows:
        return 0
    with get_conn() as conn:
        conn.execute("DELETE FROM complexes")
        conn.executemany(
            "INSERT INTO complexes (code, name, discipline) VALUES (?, ?, ?)",
            [(r["code"], r.get("name", ""), r.get("discipline", "")) for r in rows]
        )
    return len(rows)


# ---------------------------------------------------------------------------
#  История
# ---------------------------------------------------------------------------
def save_history_summary(snapshot_date: str, summary: list[dict]) -> int:
    if not summary:
        return 0
    with get_conn() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO history_summary
                (snapshot_date, complex, total, closed, annulled, active, percent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (snapshot_date, s["Комплект"], s["Всего замечаний"],
             s["Закрыто"], s["Аннулировано"], s["Активные"], s["% выполнения"])
            for s in summary
        ])
    return len(summary)


def save_history_status(snapshot_date: str, comments: list[dict],
                        doc_by_id: dict) -> int:
    if not comments:
        return 0
    with get_conn() as conn:
        rows = []
        for c in comments:
            cid = _pick(c, "id", "Id")
            did = _pick(c, "doc_id", "VitroBaseLibraryItemId")
            if cid is None:
                continue
            doc = doc_by_id.get(did)
            rows.append((
                snapshot_date, cid,
                _pick(c, "status", "VitroBaseCommentStatus", default=""),
                doc["complex"] if doc else "",
            ))
        conn.executemany("""
            INSERT OR REPLACE INTO history_status
                (snapshot_date, comment_id, status, complex)
            VALUES (?, ?, ?, ?)
        """, rows)
    return len(rows)


# ---------------------------------------------------------------------------
#  Логи
# ---------------------------------------------------------------------------
def log_event(event: str, message: str = "", rows: int = 0, status: str = "OK") -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO log (timestamp, event, message, rows, status)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(timespec="seconds"), event, message, rows, status))


# ---------------------------------------------------------------------------
#  Статистика и очистка
# ---------------------------------------------------------------------------
def db_stats() -> dict:
    with get_conn() as conn:
        return {
            "documents":         conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "comments":          conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
            "categorized":       conn.execute(
                                    "SELECT COUNT(*) FROM comments "
                                    "WHERE category IS NOT NULL AND category <> ''"
                                 ).fetchone()[0],
            "history_snapshots": conn.execute(
                                    "SELECT COUNT(DISTINCT snapshot_date) FROM history_summary"
                                 ).fetchone()[0],
            "complexes":         conn.execute("SELECT COUNT(*) FROM complexes").fetchone()[0],
        }


def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM comments")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM complexes")
        conn.execute("DELETE FROM history_status")
        conn.execute("DELETE FROM history_summary")
        conn.execute("DELETE FROM log")
        conn.execute("DELETE FROM users_activity")