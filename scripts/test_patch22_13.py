"""Тесты патча 22.13 — дисциплина «ГИС»: единое значение и пути GIS_WORK.

Блоки:
  а. parse_path: путь внутри GIS_WORK → discipline = "ГИС" (в т.ч.
     без комплекта); имя комплекта/шифр в дисциплину не попадает.
  б. Путь без GIS_WORK → без изменений (пустой результат или прежняя
     дисциплина АТП).
  в. Миграция идемпотентна: первый запуск меняет только «грязные»
     ГИС-строки, повторный — 0 строк, дублей нет.
  г. АТП-пути не затронуты ни parse_path, ни миграцией.
  д. В значениях фильтра «Дисциплина» для ГИС ровно один пункт «ГИС»
     без по-комплектных значений; каскад комплектов/разделов работает.
  е. Регресс построчно EXIT=0: test_patch21, test_patch22, _3, _5, _6,
     _8, _9, _10, _11, _12, _16, test_patch_gis_filters.

Тестовая БД — временная (SQLITE_PATH до импорта vitro.*), боевая
data/vitro.db не открывается и не меняется.

Запуск: .venv\\Scripts\\python.exe scripts\\test_patch22_13.py
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_13_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sharepoint import ATP_WORK, GIS_WORK, parse_path  # noqa: E402
import vitro.ui._filters as flt  # noqa: E402
import migrate_patch22_13 as mig  # noqa: E402

sqlite_db.init_db()

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    print(msg)
    if not cond:
        FAILED.append(name)


def reset() -> None:
    with sqlite_db.get_conn() as conn:
        for t in ("documents", "complexes", "plan_kits"):
            conn.execute(f"DELETE FROM {t}")
    for fn in (flt.load_discipline_options, flt.load_section_options,
               flt.load_kit_options):
        try:
            fn.clear()
        except Exception:
            pass


def add_doc(doc_id, complex_code, dir_ref, discipline):
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, leaf, complex, dir_ref, discipline) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, f"{doc_id}.pdf", complex_code, dir_ref, discipline))


def disc_of(doc_id):
    with sqlite_db.get_conn() as conn:
        r = conn.execute(
            "SELECT discipline FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    return None if r is None else r["discipline"]


GIS_A = "ГИС-РД-СБ-ТСО-1-00"
GIS_B = "ГИС-РД-СБ-ТСО-2-00"
ATP = "АТ-РД-КЖ1-1-00"

# ===========================================================================
#  а. parse_path: ГИС-пути → единая дисциплина "ГИС"
# ===========================================================================
disc, sec, comp = parse_path(f"{GIS_WORK}/{GIS_A}")
check("а: ГИС-комплект сразу под GIS_WORK → discipline='ГИС'",
      (disc, sec, comp) == ("ГИС", "", GIS_A),
      f"{disc!r}, {sec!r}, {comp!r}")

disc, sec, comp = parse_path(f"{GIS_WORK}/03_ЭС/{GIS_B}")
check("а: ГИС-комплект в подпапке → discipline='ГИС', шифр в discipline не тянется",
      (disc, sec, comp) == ("ГИС", "", GIS_B),
      f"{disc!r}, {sec!r}, {comp!r}")

disc, sec, comp = parse_path(f"{GIS_WORK}/03_ЭС/КИТСО")
check("а: ГИС-путь без комплекта → discipline='ГИС'",
      (disc, sec, comp) == ("ГИС", "КИТСО", ""),
      f"{disc!r}, {sec!r}, {comp!r}")

# ===========================================================================
#  б. Пути без GIS_WORK — без изменений
# ===========================================================================
disc, sec, comp = parse_path(f"{ATP_WORK}/02_КР/Секции/{ATP}")
check("б: АТП-путь → прежняя дисциплина 02_КР",
      (disc, sec, comp) == ("02_КР", "Секции", ATP),
      f"{disc!r}, {sec!r}, {comp!r}")

disc, sec, comp = parse_path("/okhta/DocProjectLib/01_РД/02_ГИС/Архив")
check("б: путь вне 01_В процессе → пустой результат",
      (disc, sec, comp) == ("", "", ""),
      f"{disc!r}, {sec!r}, {comp!r}")

# ===========================================================================
#  в. Миграция идемпотентна; г. АТП не затронуты
# ===========================================================================
reset()
add_doc(1, GIS_A, f"{GIS_WORK}/{GIS_A}", GIS_A)          # грязная
add_doc(2, GIS_B, f"{GIS_WORK}/{GIS_B}", "ГИС")          # уже корректная
add_doc(3, ATP, f"{ATP_WORK}/02_КР/Секции/{ATP}", "02_КР")  # АТП
add_doc(4, GIS_A, "/okhta/DocProjectLib/01_РД/02_ГИС/Архив", GIS_A)  # без GIS_WORK
add_doc(5, GIS_B, f"{GIS_WORK}/03_ЭС/{GIS_B}", None)     # NULL

with sqlite_db.get_conn() as conn:
    first = mig.migrate_conn(conn)
    skipped = mig.collect_skipped(conn)
with sqlite_db.get_conn() as conn:
    second = mig.migrate_conn(conn)

check("в: первый запуск обновил 2 строки (1 и 5)", first == 2,
      f"обновлено={first}")
check("в: повторный запуск обновил 0 строк", second == 0,
      f"обновлено={second}")
with sqlite_db.get_conn() as conn:
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
check("в: количество строк documents не изменилось (5)", n == 5, f"n={n}")
check("в: строка с уже корректной «ГИС» не перезаписывалась",
      disc_of(2) == "ГИС", f"discipline={disc_of(2)!r}")

check("в: ГИС-строка без GIS_WORK не изменена (id=4)",
      disc_of(4) == GIS_A, f"discipline={disc_of(4)!r}")
check("в: ГИС-строка без GIS_WORK попала в список",
      [r["id"] for r in skipped] == [4],
      f"skipped={[r['id'] for r in skipped]}")

check("г: АТП-строка не изменена", disc_of(3) == "02_КР",
      f"discipline={disc_of(3)!r}")

# ===========================================================================
#  д. Фильтр «Дисциплина»: ровно один «ГИС», без по-комплектных значений
# ===========================================================================
reset()
add_doc(10, GIS_A, f"{GIS_WORK}/{GIS_A}", GIS_A)
add_doc(11, GIS_B, f"{GIS_WORK}/{GIS_B}", GIS_B)
add_doc(12, ATP, f"{ATP_WORK}/02_КР/Секции/{ATP}", "02_КР")

with sqlite_db.get_conn() as conn:
    for code in (GIS_A, GIS_B):
        conn.execute(
            "INSERT INTO complexes (code, name, discipline) VALUES (?, ?, ?)",
            (code, "", code))  # старое по-комплектное значение до синка
    conn.execute(
        "INSERT INTO complexes (code, name, discipline) VALUES (?, ?, ?)",
        (ATP, "", "02_КР"))
    conn.execute(
        "INSERT INTO plan_kits (code, name, discipline, section, "
        "updated_at, updated_by) VALUES (?, ?, ?, ?, '', '')",
        (GIS_A, "", "03_ЭС", "КИТСО"))
    mig.migrate_conn(conn)

flt.load_discipline_options.clear()
flt.load_section_options.clear()
flt.load_kit_options.clear()

opts = flt.load_discipline_options()
gis_keys = [k for k in opts if "ГИС" in str(k).upper()]
check("д: в «Дисциплине» ровно один пункт «ГИС»",
      gis_keys == ["ГИС"], f"найдено={gis_keys}")
check("д: подпись пункта — «ГИС»", opts.get("ГИС") == "ГИС",
      f"подпись={opts.get('ГИС')!r}")
check("д: по-комплектных ГИС-значений в фильтре нет",
      not any(k.startswith("ГИС-РД-") for k in opts),
      f"ключи={sorted(opts)}")

sections = flt.load_section_options(("ГИС",))
check("д: для «ГИС» в разделах есть КИТСО из plan_kits",
      "КИТСО" in sections, f"разделы={sorted(sections)}")

kits = flt.load_kit_options(("ГИС",))
check("д: для «ГИС» каскад комплектов показывает ГИС-комплекты",
      GIS_A in kits and GIS_B in kits,
      f"ключи={sorted(kits)}")
check("д: АТП-комплект при фильтре «ГИС» не показывается",
      ATP not in kits, f"ключи={sorted(kits)}")

# ===========================================================================
#  е. Регресс построчно (защита от рекурсии при вложенном запуске)
# ===========================================================================
REGRESSION = [
    "test_patch21.py",
    "test_patch22.py",
    "test_patch22_3.py",
    "test_patch22_5.py",
    "test_patch22_6.py",
    "test_patch22_8.py",
    "test_patch22_9.py",
    "test_patch22_10.py",
    "test_patch22_11.py",
    "test_patch22_12.py",
    "test_patch22_16.py",
    "test_patch_gis_filters.py",
]


def block_e() -> None:
    if os.environ.get("VITRO_REGRESSION_NESTED") == "1":
        print("[SKIP] блок (е): вложенный запуск (регресс другого теста)")
        return
    env = {k: v for k, v in os.environ.items() if k != "SQLITE_PATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env["VITRO_REGRESSION_NESTED"] = "1"
    for name in REGRESSION:
        script = ROOT / "scripts" / name
        if not script.exists():
            print(f"[SKIP] {name} — файла нет в scripts/")
            continue
        r = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                           capture_output=True, env=env)
        tail = r.stdout.decode("utf-8", "replace")[-300:]
        check(f"е: {name} EXIT=0", r.returncode == 0,
              f"EXIT={r.returncode}; хвост: {tail}")


if __name__ == "__main__":
    block_e()
    if FAILED:
        print(f"\nПРОВАЛЕНО: {len(FAILED)}")
        for n in FAILED:
            print("  -", n)
        sys.exit(1)
    print("\nВсе блоки пройдены.")
