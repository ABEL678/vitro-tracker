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
from pathlib import Path

from dotenv import load_dotenv

from vitro.time_utils import msk_now_str

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
            id             INTEGER PRIMARY KEY,
            leaf           TEXT NOT NULL,
            discipline     TEXT,
            section        TEXT,
            complex        TEXT,
            status         TEXT,
            revision       TEXT,
            dir_ref        TEXT,
            name           TEXT,
            status_date    TEXT,
            sheet_number   TEXT,
            vitro_guid     TEXT,
            vitro_version  INTEGER
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

        -- План выдачи комплектов (вкладка «План выдачи»)
        -- Ведомость основных комплектов: одна строка = один комплект.
        CREATE TABLE IF NOT EXISTS plan_kits (
            code         TEXT PRIMARY KEY,   -- шифр комплекта
            name         TEXT,               -- наименование
            discipline   TEXT,
            section      TEXT,
            plan_status  TEXT,               -- ручной статус из ведомости (опц.)
            due_date     TEXT,               -- срок готовности, ISO (редактируется в UI)
            due_comment  TEXT,               -- пояснение (опц.)
            updated_at   TEXT,
            updated_by   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_plan_kits_disc ON plan_kits(discipline);

        -- Ведомости чертежей: одна строка = один лист (позиция).
        CREATE TABLE IF NOT EXISTS plan_drawings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_code     TEXT NOT NULL,      -- шифр комплекта (FK → plan_kits.code)
            sheet_no     TEXT,               -- номер листа («10.01», «СО»…)
            title        TEXT,               -- наименование чертежа
            discipline   TEXT,
            section      TEXT,
            qty          INTEGER DEFAULT 1,  -- количество чертежей (план)
            planned_date TEXT,               -- плановая дата выдачи, ISO (опц.)
            source       TEXT DEFAULT 'excel',
            source_file  TEXT,
            uploaded_at  TEXT,
            uploaded_by  TEXT,
            FOREIGN KEY (kit_code) REFERENCES plan_kits(code)
        );
        CREATE INDEX IF NOT EXISTS idx_plan_drawings_kit ON plan_drawings(kit_code);
        """)

        # Мягкая миграция: если БД была создана старой версией — добавим колонки
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(comments)")}
        for col, ddl in [
            ("category_version",    "INTEGER DEFAULT 0"),
            ("category_updated_at", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE comments ADD COLUMN {col} {ddl}")

        # Мягкая миграция для documents — новые поля для ссылок на Витро
        doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
        for col, ddl in [
            ("vitro_guid",    "TEXT"),
            ("vitro_version", "INTEGER"),
        ]:
            if col not in doc_cols:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {ddl}")

        # Мягкая миграция plan_drawings — колонка «номер листа»
        # (добавлена в v1.4.1 для реальных регистров «Комплекты чертежей»)
        dr_cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan_drawings)")}
        if "sheet_no" not in dr_cols:
            conn.execute("ALTER TABLE plan_drawings ADD COLUMN sheet_no TEXT")


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
    now = msk_now_str()
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


def update_category_force(comment_id: int, category: str,
                          user: str) -> tuple[bool, str]:
    """
    Принудительно обновляет категорию, игнорируя версию.
    Используется при импорте с флагом «Перезаписать».
    """
    now = msk_now_str()
    with get_conn() as conn:
        old_row = conn.execute(
            "SELECT category FROM comments WHERE id = ?", (comment_id,)
        ).fetchone()
        old_cat = old_row["category"] if old_row else None

        cur = conn.execute("""
            UPDATE comments
            SET category = ?, category_user = ?, category_date = ?,
                category_version = COALESCE(category_version, 0) + 1,
                category_updated_at = ?
            WHERE id = ?
        """, (category, user, now, now, comment_id))

        if cur.rowcount == 0:
            return False, "Строка не найдена"

        conn.execute("""
            INSERT INTO users_activity (timestamp, user, comment_id,
                                        old_cat, new_cat)
            VALUES (?, ?, ?, ?, ?)
        """, (now, user, comment_id, old_cat, category))

    return True, "OK"


# ---------------------------------------------------------------------------
#  Загрузка среза замечаний для st.data_editor
# ---------------------------------------------------------------------------
def load_remarks_for_editor(disciplines=None, kits=None, sections=None,
                            statuses=None, only_uncategorized=False,
                            show_waiting=False, limit: int = 500):
    """
    Возвращает срез замечаний для редактора.

    Параметры:
      disciplines, kits, sections — фильтры по иерархии.
      statuses — список статусов.
      only_uncategorized — только без категории.
      show_waiting — если False, статус «Выполнено» исключается.
      limit — максимум строк.

    НИКОГДА не возвращает: Закрыто, Аннулировано.
    """
    import pandas as pd

    q = """
        SELECT c.id,
               d.discipline,
               d.section,
               d.complex,
               REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS sheet,
               d.name AS sheet_name,
               d.status AS doc_status,
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

    # Жёсткое ограничение: НИКОГДА не показываем Закрыто / Аннулировано
    q += " AND c.status NOT IN ('Закрыто', 'Аннулировано')"

    # Статусы: если show_waiting=False — исключаем «Выполнено»
    if show_waiting:
        allowed_statuses = [
            "Новое", "Принято в работу", "Не принято",
            "К обсуждению", "Выполнено",
        ]
    else:
        allowed_statuses = [
            "Новое", "Принято в работу", "Не принято", "К обсуждению",
        ]

    if statuses:
        effective = [s for s in statuses if s in allowed_statuses]
        if effective:
            q += f" AND c.status IN ({','.join('?' * len(effective))})"
            params += effective
        else:
            q += " AND 1=0"
    else:
        q += f" AND c.status IN ({','.join('?' * len(allowed_statuses))})"
        params += allowed_statuses

    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if sections:
        q += f" AND d.section IN ({','.join('?' * len(sections))})"
        params += list(sections)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if only_uncategorized:
        q += " AND (c.category IS NULL OR c.category = '')"

    q += " ORDER BY d.complex, d.leaf, c.id LIMIT ?"
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
                     revision, dir_ref, name, status_date, sheet_number,
                     vitro_guid, vitro_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (d["id"], d["leaf"], d.get("discipline"), d.get("section"),
                 d.get("complex"), d.get("status"), d.get("revision"),
                 d.get("dir_ref"), d.get("name"), d.get("status_date"),
                 d.get("sheet_number"),
                 d.get("vitro_guid"), d.get("vitro_version"))
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
        """, (msk_now_str(), event, message, rows, status))


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


# ===========================================================================
#  План выдачи комплектов (вкладка «План выдачи»)
# ===========================================================================
def upsert_plan_kits(rows: list[dict], user: str = "") -> int:
    """
    Добавляет/обновляет комплекты плана по шифру (INSERT OR REPLACE).
    Редактированные в UI сроки готовности (due_date) при повторной
    загрузке ведомости НЕ перезаписываются пустым значением:
    если в новой строке дата не заполнена — остаётся прежняя.
    Возвращает число обработанных строк.
    """
    if not rows:
        return 0
    now = msk_now_str()
    with get_conn() as conn:
        for r in rows:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            new_due = r.get("due_date") or None
            conn.execute("""
                INSERT INTO plan_kits
                    (code, name, discipline, section, plan_status,
                     due_date, due_comment, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    -- Пустая строка в ведомости НЕ затирает ранее
                    -- сохранённое значение (NULLIF '' → NULL → COALESCE):
                    name        = COALESCE(NULLIF(excluded.name, ''),
                                           plan_kits.name, ''),
                    discipline  = COALESCE(NULLIF(excluded.discipline, ''),
                                           plan_kits.discipline, ''),
                    section     = COALESCE(NULLIF(excluded.section, ''),
                                           plan_kits.section, ''),
                    plan_status = COALESCE(NULLIF(excluded.plan_status, ''),
                                           plan_kits.plan_status, ''),
                    due_date    = COALESCE(excluded.due_date,
                                           plan_kits.due_date),
                    due_comment = COALESCE(NULLIF(excluded.due_comment, ''),
                                           plan_kits.due_comment, ''),
                    updated_at  = excluded.updated_at,
                    updated_by  = excluded.updated_by
            """, (
                code,
                r.get("name") or "",
                r.get("discipline") or "",
                r.get("section") or "",
                r.get("plan_status") or "",
                new_due,
                r.get("due_comment") or "",
                now,
                user,
            ))
    return len(rows)


def replace_plan_drawings_for_kits(rows: list[dict], user: str = "") -> int:
    """
    Заменяет чертежи тех комплектов, которые присутствуют в загруженных
    строках (DELETE по затронутым шифрам + INSERT). Комплекты, которых
    нет в plan_kits, создаются минимальной записью (один шифр).
    Возвращает число вставленных строк.
    """
    if not rows:
        return 0
    now = msk_now_str()
    kit_codes = sorted({str(r["kit_code"]).strip() for r in rows
                        if r.get("kit_code")})
    if not kit_codes:
        return 0
    with get_conn() as conn:
        # Комплекты, отсутствующие в плане, добавим заглушками,
        # чтобы сработал внешний ключ и комплект был виден в общей таблице.
        for code in kit_codes:
            conn.execute("""
                INSERT OR IGNORE INTO plan_kits (code, updated_at, updated_by)
                VALUES (?, ?, ?)
            """, (code, now, user))
        ph = ",".join("?" * len(kit_codes))
        conn.execute(
            f"DELETE FROM plan_drawings WHERE kit_code IN ({ph})",
            tuple(kit_codes))
        conn.executemany("""
            INSERT INTO plan_drawings
                (kit_code, sheet_no, title, discipline, section, qty,
                 planned_date, source, source_file, uploaded_at, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'excel', ?, ?, ?)
        """, [(
            str(r["kit_code"]).strip(),
            r.get("sheet_no") or "",
            r.get("title") or "",
            r.get("discipline") or "",
            r.get("section") or "",
            int(r.get("qty") or 1),
            r.get("planned_date"),
            r.get("source_file") or "",
            now,
            user,
        ) for r in rows])
    return len(rows)


def update_plan_due_dates(changes: dict[str, str], user: str = "") -> int:
    """
    Обновляет сроки готовности (due_date) комплектов плана.
    changes — {шифр: ISO-дата или None}. Возвращает число обновлённых строк.
    """
    if not changes:
        return 0
    now = msk_now_str()
    with get_conn() as conn:
        n = 0
        for code, iso in changes.items():
            cur = conn.execute("""
                UPDATE plan_kits
                SET due_date = ?, updated_at = ?, updated_by = ?
                WHERE code = ?
            """, (iso, now, user, code))
            n += cur.rowcount
    return n


def load_plan_kits():
    """Все комплекты плана (без кэша — данные меняются кнопками вкладки)."""
    import pandas as pd

    with get_conn() as conn:
        return pd.read_sql("""
            SELECT code, name, discipline, section, plan_status,
                   due_date, due_comment, updated_at, updated_by
            FROM plan_kits
            ORDER BY code
        """, conn)


def load_plan_drawings():
    """Все позиции чертежей плана (без кэша)."""
    import pandas as pd

    with get_conn() as conn:
        return pd.read_sql("""
            SELECT id, kit_code, sheet_no, title, discipline, section, qty,
                   planned_date, source, source_file,
                   uploaded_at, uploaded_by
            FROM plan_drawings
            ORDER BY kit_code, id
        """, conn)


def clear_plan() -> None:
    """Полностью очищает план выдачи (комплекты + чертежи)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM plan_drawings")
        conn.execute("DELETE FROM plan_kits")