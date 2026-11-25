"""Патч 22.13 — разовая идемпотентная миграция дисциплины ГИС.

Что делает:
  * у всех строк documents, чей dir_ref лежит внутри GIS_WORK
    («/01_РД/02_ГИС/01_В процессе/»), выставляет discipline = "ГИС";
  * строки с уже корректной дисциплиной не перезаписывает;
  * ГИС-строки, у которых путь НЕ содержит GIS_WORK, не меняет —
    выводит их списком (их нужно разобрать вручную);
  * АТП-строки (и любые не-ГИС пути) не трогает.

Идемпотентность: UPDATE идёт только по строкам, где discipline
отличается от "ГИС", поэтому повторный запуск меняет 0 строк и не
создаёт дублей.

Запуск:
  .venv\\Scripts\\python.exe scripts\\migrate_patch22_13.py

Для тестов функции migrate_conn()/collect_skipped() принимают
открытое соединение sqlite3 — боевая БД при этом не затрагивается.
"""

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# То же значение, что и vitro.disciplines.GIS_DISCIPLINE.
GIS_DISCIPLINE = "ГИС"

# Эквивалент GIS_WORK из vitro/sharepoint.py, но без импорта модуля
# SharePoint (миграции не нужны сеть, .env и requests_ntlm).
# GIS_WORK = GIS_ROOT + "/01_В процессе",
# GIS_ROOT = "/okhta/DocProjectLib/01_РД/02_ГИС".
GIS_WORK_MARKER = "/02_ГИС/01_В процессе/"


def is_gis_path(dir_ref: str | None,
                marker: str = GIS_WORK_MARKER) -> bool:
    """Путь лежит внутри GIS_WORK (или эквивалентный признак)."""
    return bool(dir_ref) and marker in str(dir_ref)


def migrate_conn(conn: sqlite3.Connection,
                 marker: str = GIS_WORK_MARKER) -> int:
    """Обновляет discipline у ГИС-путей. Возвращает число строк.

    UPDATE выполняется одним запросом с условием
    discipline <> 'ГИС' — уже корректные значения не перезаписываются,
    повторный запуск возвращает 0. instr() вместо LIKE: в именах папок
    есть «_», а LIKE трактует его как шаблон.
    """
    cur = conn.execute(
        "UPDATE documents SET discipline = ? "
        "WHERE instr(COALESCE(dir_ref, ''), ?) > 0 "
        "  AND (discipline IS NULL OR discipline <> ?)",
        (GIS_DISCIPLINE, marker, GIS_DISCIPLINE),
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def collect_skipped(conn: sqlite3.Connection,
                    marker: str = GIS_WORK_MARKER) -> list[dict]:
    """ГИС-строки без пути GIS_WORK, которые миграция НЕ меняет."""
    rows = conn.execute(
        "SELECT id, discipline, complex, dir_ref FROM documents "
        "WHERE instr(COALESCE(dir_ref, ''), ?) = 0 "
        "  AND (complex LIKE '%ГИС%' OR discipline LIKE '%ГИС%') "
        "  AND (discipline IS NULL OR discipline <> ?) "
        "ORDER BY id",
        (marker, GIS_DISCIPLINE),
    ).fetchall()
    return [dict(r) for r in rows]


def _default_db_path() -> Path:
    """Путь к БД как в vitro/sqlite_db.py (SQLITE_PATH → data/vitro.db)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    p = Path(os.environ.get("SQLITE_PATH", "data/vitro.db"))
    return p if p.is_absolute() else ROOT / p


def main() -> int:
    db_path = _default_db_path()
    if not db_path.exists():
        print(f"[FAIL] БД не найдена: {db_path}")
        return 1

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE instr(COALESCE(dir_ref, ''), ?) > 0",
            (GIS_WORK_MARKER,),
        ).fetchone()[0]
        updated = migrate_conn(conn)
        skipped = collect_skipped(conn)
        after = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE instr(COALESCE(dir_ref, ''), ?) > 0 "
            "  AND discipline = ?",
            (GIS_WORK_MARKER, GIS_DISCIPLINE),
        ).fetchone()[0]
    finally:
        conn.close()

    print(f"БД: {db_path}")
    print(f"Строк в GIS_WORK: {before}")
    print(f"Обновлено миграцией: {updated}")
    print(f"ГИС-строк в GIS_WORK с discipline='{GIS_DISCIPLINE}': {after}")
    if skipped:
        print(f"\nГИС-строки БЕЗ пути GIS_WORK (не изменены): {len(skipped)}")
        for r in skipped:
            print(f"  id={r['id']} discipline={r['discipline']!r} "
                  f"complex={r['complex']!r} dir_ref={r['dir_ref']!r}")
    else:
        print("\nГИС-строк без пути GIS_WORK: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
