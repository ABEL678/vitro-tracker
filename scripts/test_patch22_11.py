"""Тесты патча 22.11 — ГИС-комплекты: статус «Передан (вне Витро)».

Правки:
  1. fetch_complex_names() обходит GIS_WORK (комплекты ГИС → complexes).
  2. _build_overview(): ГИС-РД-* → статус «Передан (вне Витро)» безусловно;
     категорий ровно три: «Загружено в Витро», «Передан (вне Витро)»,
     «План к загрузке». В итогах и статистике «Передан» = выполненный.
  3. Сопоставление plan_kits ↔ documents.complex через _norm_complex_code.

Блоки:
  а. ГИС-комплект «Передан» — без листов в Витро и с листами в Витро.
  б. Нет двойного счёта: шифр и в plan_kits, и в documents → одна строка.
  в. Пустой plan_kits — не падает, таблица и итоги пустые/нулевые.
  г. Сопоставление после нормализации (регистр, пробелы, латиница).
  д. ГИС-комплект появляется в kit-опциях (_plan_kit_options_fn) и в
     complexes после синка (fetch_complex_names → save_complexes).

Тестовая БД — временная (SQLITE_PATH до импорта vitro.*).
Запуск: .venv\\Scripts\\python.exe scripts\\test_patch22_11.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_11_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
import vitro.sharepoint as sp  # noqa: E402
import vitro.ui.plan as plan  # noqa: E402

sqlite_db.init_db()

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    print(msg.encode("ascii", "replace").decode("ascii")
          if False else msg)
    if not cond:
        FAILED.append(name)


GIS_A = "ГИС-РД-СБ-ТСО-1-00"        # без листов в Витро
GIS_B = "ГИС-РД-СБ-ТСО-2-00"        # с листами в Витро
ATP = "АТ-РД-КЖ1-1-00"


def reset() -> None:
    with sqlite_db.get_conn() as conn:
        for t in ("documents", "comments", "plan_drawings", "plan_kits",
                  "complexes"):
            conn.execute(f"DELETE FROM {t}")
    for fn in (plan._vitro_sheet_counts, plan._vitro_sheet_status_counts,
               plan._vitro_kit_meta, plan._vitro_sheet_dates):
        try:
            fn.clear()
        except Exception:
            pass


def add_plan_kit(code, name="", disc="03_ЭС", section="КИТСО"):
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO plan_kits (code, name, discipline, section, "
            "updated_at, updated_by) VALUES (?, ?, ?, ?, '', '')",
            (code, name, disc, section))


def add_doc(doc_id, complex_code, sheet, status="A"):
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, leaf, complex, status, sheet_number, "
            "discipline) VALUES (?, ?, ?, ?, ?, '')",
            (doc_id, f"{complex_code}-{sheet}.pdf", complex_code, status,
             sheet))


def overview(include_vitro_only=False):
    for fn in (plan._vitro_sheet_counts,):
        fn.clear()
    return plan._build_overview(include_vitro_only)


def row_of(df, code):
    r = df[df["code"] == code]
    return r.iloc[0] if len(r) else None


# ===========================================================================
#  а. ГИС «Передан» — без листов и с листами
# ===========================================================================
def block_a():
    reset()
    add_plan_kit(GIS_A, "Техсредства охраны. Подземная часть")
    ov = overview()
    r = row_of(ov, GIS_A)
    check("а: ГИС без листов в Витро → «Передан (вне Витро)»",
          r is not None and r["status"] == "Передан (вне Витро)",
          f"status={None if r is None else r['status']}")
    check("а: ГИС «Передан» засчитан как выполненный (in_vitro)",
          r is not None and bool(r["in_vitro"]))

    reset()
    add_plan_kit(GIS_B, "Техсредства охраны. Корпус 2")
    add_doc(1, GIS_B, "1", "A")
    add_doc(2, GIS_B, "2", "B")
    ov = overview()
    r = row_of(ov, GIS_B)
    check("а: ГИС с листами в Витро → всё равно «Передан (вне Витро)»",
          r is not None and r["status"] == "Передан (вне Витро)",
          f"status={None if r is None else r['status']}")
    check("а: ГИС с листами — листы в Витро посчитаны (sheets_vitro=2)",
          r is not None and int(r["sheets_vitro"]) == 2)


# ===========================================================================
#  б. Нет двойного счёта: шифр и в plan_kits, и в documents
# ===========================================================================
def block_b():
    reset()
    add_plan_kit(GIS_B)
    add_doc(1, GIS_B, "1", "A")
    ov = overview()
    n = int((ov["code"] == GIS_B).sum())
    check("б: шифр в plan_kits + documents → одна строка", n == 1,
          f"строк={n}")

    # Итоги: «Комплектов всего» не растёт, «Загружено» не двоится
    totals = plan._plan_totals(ov)
    check("б: kits_total = 1 (без двойного счёта)",
          totals["kits_total"] == 1, f"kits_total={totals['kits_total']}")
    check("б: kits_passed = 1, kits_loaded = 0 (ГИС не в «Загружено»)",
          totals["kits_passed"] == 1 and totals["kits_loaded"] == 0,
          f"passed={totals['kits_passed']}, loaded={totals['kits_loaded']}")


# ===========================================================================
#  в. Пустой plan_kits — не падает
# ===========================================================================
def block_c():
    reset()
    try:
        ov = overview(include_vitro_only=False)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        ov = pd.DataFrame()
        print(f"   исключение: {e}")
    check("в: пустой plan_kits — _build_overview не падает", ok)
    check("в: пустой plan_kits — таблица пустая",
          ok and ov.empty or (ok and "code" in ov.columns and ov.empty))
    totals = plan._plan_totals(ov)
    check("в: пустой plan_kits — итоги нулевые",
          totals["kits_total"] == 0 and totals["kits_passed"] == 0
          and totals["kits_plan"] == 0)

    # Пустой plan_kits + ГИС в documents → «Только в Витро», не падает
    reset()
    add_doc(1, GIS_B, "1", "A")
    try:
        ov = overview(include_vitro_only=True)
        ok2 = True
    except Exception as e:  # noqa: BLE001
        ok2 = False
        ov = pd.DataFrame()
        print(f"   исключение: {e}")
    check("в: пустой plan_kits + ГИС в Витро — не падает", ok2)


# ===========================================================================
#  г. Сопоставление после нормализации шифра
# ===========================================================================
def block_d():
    reset()
    # В plan_kits — «грязный» шифр (регистр, пробелы, латиница «C» в
    # шифре вместо кириллической «С»). upsert_plan_kits пишет через
    # _kit_code_of → нормализованный код.
    dirty = "  гис-рд-сб-тсо-2-00 "
    norm = plan._norm_complex_code(dirty)
    check("г: _norm_complex_code приводит к каноническому виду",
          norm == GIS_B, f"norm={norm!r}")

    rows, _ = [], None
    from vitro.plan_xlsx import _kit_code_of  # noqa: E402
    kc = _kit_code_of(dirty)
    check("г: _kit_code_of принимает «грязный» шифр ГИС",
          kc == GIS_B, f"kc={kc!r}")

    # Документы с латинским двойником и пробелом в complex
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, leaf, complex, status, sheet_number, "
            "discipline) VALUES (1, 'x.pdf', ?, 'A', '1', '')",
            (" " + GIS_B.replace("С", "C") + " ",))
    if kc:
        add_plan_kit(kc)
    ov = overview()
    r = row_of(ov, GIS_B)
    check("г: после нормализации документ сопоставлен с комплектом",
          r is not None and int(r["sheets_vitro"]) == 1,
          f"sheets_vitro={None if r is None else r['sheets_vitro']}")


# ===========================================================================
#  д. ГИС-комплект в kit-опциях и в complexes после синка
# ===========================================================================
def block_e():
    reset()
    add_plan_kit(GIS_A, "Техсредства охраны. Подземная часть")
    opts = plan._plan_kit_options_fn((), (), ())
    check("д: ГИС-комплект из plan_kits — в kit-опциях",
          any(GIS_A in k for k in opts), f"ключей={len(opts)}")

    # Синк: fetch_complex_names обходит и ATP, и GIS
    calls = []

    def fake_fetch_all(base, guid, select, flt=None, page_size=5000,
                       max_items=0):
        calls.append(flt or "")
        if flt and sp.GIS_WORK in flt:
            # PDF-лист внутри папки комплекта (комплекты собираются из PDF)
            return [{"Id": 1, "FileLeafRef": "ГИС-РД-СБ-ТСО-2-00-1.pdf",
                     "FileDirRef": f"{sp.GIS_WORK}/03_ЭС/ГИС-РД-СБ-ТСО-2-00",
                     "VitroBaseFullName": "ТСО Корпус 2"}]
        return []

    with patch.object(sp, "_fetch_all", side_effect=fake_fetch_all):
        sp.fetch_complex_names()
    check("д: fetch_complex_names запрашивает папку ГИС (GIS_WORK)",
          any(sp.GIS_WORK in c for c in calls), f"фильтры={calls}")
    check("д: fetch_complex_names запрашивает и АТП (ATP_WORK)",
          any(sp.ATP_WORK in c for c in calls))

    # Регистрация в complexes (как save_complexes после синка)
    with patch.object(sp, "_fetch_all", side_effect=fake_fetch_all):
        names = sp.fetch_complex_names()
    sqlite_db.save_complexes(names)
    with sqlite_db.get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM complexes WHERE code LIKE 'ГИС-РД-%'"
        ).fetchone()[0]
    check("д: ГИС-комплект записан в complexes после синка", n >= 1,
          f"ГИС в complexes={n}")


if __name__ == "__main__":
    block_a()
    block_b()
    block_c()
    block_d()
    block_e()
    if FAILED:
        print(f"\nПРОВАЛЕНО: {len(FAILED)}")
        for n in FAILED:
            print("  -", n.encode("ascii", "replace").decode("ascii"))
        sys.exit(1)
    print("\nВсе проверки патча 22.11 пройдены.")
