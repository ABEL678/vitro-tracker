"""Тесты патча 22.15 — сопоставление шифров комплекта
(plan_kits.code ↔ documents.complex).

Проблема (следствие диагностики 22.11, раунд 2): сверка шла только
на равенство после _norm_complex_code, поэтому не зачитывались:
  1) documents.complex с текстовым хвостом — «АТ-РД-КСТ2-А-00
     КОНТАКТНАЯ СЕТЬ ТРОЛЛЕЙБУСА И ТРАМВАЯ ПО ВРЕМЕННОЙ СХЕМЕ»;
  2) ГИС-план-коды с дробной частью — «ГИС-РД-СБ-АСЗП-0-00/
     10511.25.01-АСЗП» при комплексе в базе «ГИС-РД-СБ-АСЗП-0-00».

Правки (vitro/ui/plan.py, рядом с _is_gis_kit):
  - _kit_matches_complex(kit, complex) — единый хелпер: 1) точное/
    нормализованное совпадение; (а) «шифр плана + пробел(ы) +
    не-шифровый хвост»; (б) ГИС — база до «/», дробь ровно с одной
    стороны и валидная; (в) близнецы (оба написания невалидны,
    различие только в точках);
  - _vitro_claim_index — индекс зачёта: каждый комплекс Витро
    зачитывается НЕ БОЛЕЕ чем одному комплекту плана (точные
    совпадения сильнее) — двойного счёта листов нет;
  - _vitro_sheet_counts / _vitro_sheet_status_counts — дедуп БЕЗ
    отсечения невалидных написаний (иначе хвостовые комплексы не
    доходили до сопоставления); мусор отсекается где и раньше —
    is_valid_kit_code в «Только в Витро» и валидация план-кодов.

Блоки:
  а. complex с текстовым хвостом зачитывается за план-код (КСТ2):
     листы в таблице, статус «Загружен в Витро», без фантомной
     строки «Только в Витро».
  б. ГИС план-код с дробью совпадает с базовым комплексом; без
     двойного счёта, когда в плане есть и базовый код, и дробный;
     разные дроби не зачитываются.
  в. Ложные совпадения не возникают: АТ-Р-КИ.ВК-2-00 не матчится на
     АТ-РД-КИ.ВК-С-00 и …ВК-1-00 (в хелпере и в таблице).
  г. Близнецы АТ-РД-КЖ1,2-С-П2.3.ВР / АТ-РД-КЖ1,2-С-П2.3ВР — один
     код (_kit_matches_complex в обе стороны); патч 13 не сломан —
     в таблицу плана близнецы по-прежнему не попадают.
  д. Регресс построчно EXIT=0: test_patch21, test_patch22, _3, _5,
     _6, _8, _9, _10, _11, _12, _13, _16, test_patch_gis_filters.

Тестовая БД — временная (SQLITE_PATH до импорта vitro.*).
Запуск: .venv\\Scripts\\python.exe scripts\\test_patch22_15.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_15_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
import vitro.ui.plan as plan  # noqa: E402

sqlite_db.init_db()

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


KST2 = "АТ-РД-КСТ2-А-00"
KST2_TAIL = ("АТ-РД-КСТ2-А-00  КОНТАКТНАЯ СЕТЬ ТРОЛЛЕЙБУСА "
             "И ТРАМВАЯ ПО ВРЕМЕННОЙ СХЕМЕ")
GIS_BASE = "ГИС-РД-СБ-АСЗП-0-00"
GIS_FRAC = "ГИС-РД-СБ-АСЗП-0-00/10511.25.01-АСЗП"
TWIN1 = "АТ-РД-КЖ1,2-С-П2.3.ВР"
TWIN2 = "АТ-РД-КЖ1,2-С-П2.3ВР"


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


def add_plan_kit(code, name="", disc="01_АР", section=""):
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


def overview(include_vitro_only=True):
    for fn in (plan._vitro_sheet_counts, plan._vitro_sheet_status_counts):
        fn.clear()
    return plan._build_overview(include_vitro_only)


def row_of(df, code):
    r = df[df["code"] == code]
    return r.iloc[0] if len(r) else None


# ===========================================================================
#  а. complex с текстовым хвостом зачитывается за план-код (КСТ2)
# ===========================================================================
def block_a():
    reset()
    add_plan_kit(KST2, "Контактная сеть троллейбуса и трамвая "
                       "по временной схеме")
    for i in range(6):
        add_doc(i + 1, KST2_TAIL, str(10 + i), "A")
    ov = overview(include_vitro_only=True)
    r = row_of(ov, KST2)
    check("а: КСТ2 зачтён — строка комплекта в таблице", r is not None)
    check("а: sheets_vitro = 6 (листы хвостового комплекса)",
          r is not None and int(r["sheets_vitro"]) == 6,
          f"sheets_vitro={None if r is None else r['sheets_vitro']}")
    check("а: статус «Загружен в Витро» (ушёл из «Плана»)",
          r is not None and r["status"] == "Загружен в Витро",
          f"status={None if r is None else r['status']}")
    check("а: хвостовой комплекс НЕ стал строкой «Только в Витро»",
          len(ov[ov["code"] == KST2_TAIL]) == 0
          and len(ov[ov["code"] == plan._norm_complex_code(KST2_TAIL)]) == 0)
    totals = plan._plan_totals(ov)
    check("а: итоги — всего 1, Загружено 1, План 0",
          totals["kits_total"] == 1 and totals["kits_loaded"] == 1
          and totals["kits_plan"] == 0,
          f"total={totals['kits_total']}, loaded={totals['kits_loaded']}, "
          f"plan={totals['kits_plan']}")
    check("а: бублик A/B/C видит хвостовые листы (A=6)",
          plan._vitro_sheet_status_counts().get(KST2_TAIL, {}).get("A") == 6)
    # Хелпер напрямую + регистр/пробелы по краям
    check("а: _kit_matches_complex(КСТ2, комплекс с хвостом) = True",
          plan._kit_matches_complex(KST2, KST2_TAIL))
    check("а: нормализация краёв не ломает зачёт",
          plan._kit_matches_complex(f"  {KST2.lower()} ", f" {KST2_TAIL} "))


# ===========================================================================
#  б. ГИС план-код с дробью ↔ базовый комплекс; без двойного счёта
# ===========================================================================
def block_b():
    reset()
    add_plan_kit(GIS_FRAC, "АСУЗП (дробный шифр)")
    add_doc(1, GIS_BASE, "10", "A")
    add_doc(2, GIS_BASE, "31.01", "B")
    add_doc(3, GIS_BASE, "31.02", "C")
    ov = overview()
    r = row_of(ov, GIS_FRAC)
    check("б: дробный ГИС-план зачтён — строка есть", r is not None)
    check("б: sheets_vitro = 3 (листы базового комплекса)",
          r is not None and int(r["sheets_vitro"]) == 3,
          f"sheets_vitro={None if r is None else r['sheets_vitro']}")
    check("б: статус «Передан (вне Витро)» (22.11 не сломан)",
          r is not None and r["status"] == "Передан (вне Витро)",
          f"status={None if r is None else r['status']}")
    check("б: in_vitro=True — засчитан выполненным",
          r is not None and bool(r["in_vitro"]))
    check("б: хелпер: дробь против базы = True",
          plan._kit_matches_complex(GIS_FRAC, GIS_BASE))
    check("б: хелпер: база против дроби = True (симметрично)",
          plan._kit_matches_complex(GIS_BASE, GIS_FRAC))

    # Двойной счёт: в плане и база, и дробь → листы одной базе
    reset()
    add_plan_kit(GIS_BASE, "АСУЗП (база)")
    add_plan_kit(GIS_FRAC, "АСУЗП (дробь)")
    add_doc(1, GIS_BASE, "10", "A")
    ov = overview()
    check("б: в плане две строки (база + дробь)", len(ov) == 2,
          f"строк={len(ov)}")
    rb, rf = row_of(ov, GIS_BASE), row_of(ov, GIS_FRAC)
    check("б: листы у базового — 1 (точное совпадение сильнее)",
          rb is not None and int(rb["sheets_vitro"]) == 1)
    check("б: у дробного — 0, двойного счёта нет",
          rf is not None and int(rf["sheets_vitro"]) == 0)
    totals = plan._plan_totals(ov)
    check("б: sheets_vitro в итоге = 1 (не 2)", totals["sheets_vitro"] == 1,
          f"sheets_vitro={totals['sheets_vitro']}")

    # Разные дроби / шифр листа с дробью — не зачитываются
    check("б: разные дроби не матчатся",
          not plan._kit_matches_complex(
              GIS_FRAC, "ГИС-РД-СБ-АСЗП-0-00/99999.99-ХХХ"))
    check("б: дробь с обеих сторон (равная) — это правило 1, не (б)",
          plan._kit_matches_complex(GIS_FRAC, GIS_FRAC))
    check("б: шифр листа «…/1.1» не зачитывается дробному комплекту",
          not plan._kit_matches_complex(GIS_FRAC, "ГИС-РД-СБ-АСЗП-0-00/1.1"))


# ===========================================================================
#  в. Ложные совпадения не возникают (КИ.ВК)
# ===========================================================================
def block_c():
    check("в: АТ-Р-КИ.ВК-2-00 не матчится на АТ-РД-КИ.ВК-С-00",
          not plan._kit_matches_complex("АТ-Р-КИ.ВК-2-00", "АТ-РД-КИ.ВК-С-00"))
    check("в: АТ-Р-КИ.ВК-2-00 не матчится на АТ-РД-КИ.ВК-1-00",
          not plan._kit_matches_complex("АТ-Р-КИ.ВК-2-00", "АТ-РД-КИ.ВК-1-00"))
    check("в: обратные пары тоже не матчатся",
          not plan._kit_matches_complex("АТ-РД-КИ.ВК-С-00", "АТ-Р-КИ.ВК-2-00")
          and not plan._kit_matches_complex("АТ-РД-КИ.ВК-1-00",
                                            "АТ-Р-КИ.ВК-2-00"))
    check("в: полный шифр листа не матчится на комплект",
          not plan._kit_matches_complex("АТ-РД-КИ.ВК-С-00",
                                        "АТ-РД-КИ.ВК-С-00-1.1"))
    check("в: хвост-«второй шифр» не матчится (две папки в одной)",
          not plan._kit_matches_complex(
              "АТ-РД-КИ.ВК-С-00", "АТ-РД-КИ.ВК-С-00 АТ-РД-КИ.ВК-1-00"))

    # И в таблице: листы ВК-С-00 не уходят комплекту АТ-Р-КИ.ВК-2-00
    reset()
    add_plan_kit("АТ-Р-КИ.ВК-2-00", "КИ.ВК ревизия 2")
    add_doc(1, "АТ-РД-КИ.ВК-С-00", "1", "A")
    add_doc(2, "АТ-РД-КИ.ВК-1-00", "2", "A")
    ov = overview(include_vitro_only=True)
    r = row_of(ov, "АТ-Р-КИ.ВК-2-00")
    check("в: в таблице листы ВК-С-00/ВК-1-00 не ушли АТ-Р-КИ.ВК-2-00",
          r is not None and int(r["sheets_vitro"]) == 0,
          f"sheets_vitro={None if r is None else r['sheets_vitro']}")
    check("в: статус АТ-Р-КИ.ВК-2-00 — «План (не загружен)»",
          r is not None and r["status"] == "План (не загружен)",
          f"status={None if r is None else r['status']}")
    # Листы ВК-С-00/ВК-1-00 — самостоятельные «Только в Витро»
    check("в: ВК-С-00 и ВК-1-00 остались «Только в Витро»",
          row_of(ov, "АТ-РД-КИ.ВК-С-00") is not None
          and row_of(ov, "АТ-РД-КИ.ВК-1-00") is not None)


# ===========================================================================
#  г. Близнецы КЖ1,2 — один код
# ===========================================================================
def block_d():
    check("г: близнецы матчатся в обе стороны (один код)",
          plan._kit_matches_complex(TWIN1, TWIN2)
          and plan._kit_matches_complex(TWIN2, TWIN1))
    check("г: патч 13 не сломан — оба написания невалидны как комплекты",
          not plan.is_valid_kit_code(TWIN1) and not plan.is_valid_kit_code(TWIN2))
    check("г: _norm_complex_code каноничен (регистр/пробелы/латиница — "
          "без стирания точек у валидных шифров)",
          plan._norm_complex_code(TWIN1) == TWIN1
          and plan._norm_complex_code(" ат-рд-ов2-c-00 ") == "АТ-РД-ОВ2-С-00")
    # В таблице плана близнецы не появляются — как и раньше (патч 13)
    reset()
    add_doc(1, TWIN1, "1", "A")
    add_doc(2, TWIN2, "2", "A")
    ov = overview(include_vitro_only=True)
    check("г: близнецы не попадают в таблицу плана (невалидные)",
          ov.empty, f"строк={len(ov)}")
    # Индекс зачёта считает пару близнецов ОДНОЙ позицией «вне плана»
    claims = plan._vitro_claim_index([TWIN1, TWIN2], [])
    check("г: без плана близнецы не зачтены никому", len(claims) == 0)
    groups: list[list[str]] = []
    for cx in (TWIN1, TWIN2):
        for g in groups:
            if plan._kit_matches_complex(g[0], cx):
                g.append(cx)
                break
        else:
            groups.append([cx])
    check("г: пара близнецов схлопывается в одну позицию",
          len(groups) == 1, f"позиций={len(groups)}")


# ===========================================================================
#  д. Регресс: полный список патчей построчно
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
    "test_patch22_13.py",
    "test_patch22_16.py",
    "test_patch_gis_filters.py",
]


def block_e():
    # Защита от взаимной рекурсии: test_patch22_16 сам регрессит
    # test_patch22_15 — при вложенном запуске (из его регресса) блок (д)
    # не запускается повторно.
    if os.environ.get("VITRO_REGRESSION_NESTED") == "1":
        print("[SKIP] блок (д): вложенный запуск (регресс другого теста) "
              "— защита от взаимной рекурсии с test_patch22_16")
        return
    env = {k: v for k, v in os.environ.items() if k != "SQLITE_PATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env["VITRO_REGRESSION_NESTED"] = "1"
    for name in REGRESSION:
        script = ROOT / "scripts" / name
        if not script.exists():
            print(f"[SKIP] {name} — файла нет в scripts/ (патч не в этой "
                  f"рабочей копии; см. HANDOFF патча 22.16)")
            continue
        r = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                           capture_output=True, env=env)
        check(f"д: {name} EXIT=0", r.returncode == 0,
              f"EXIT={r.returncode}; хвост вывода: "
              f"{r.stdout.decode('utf-8', 'replace')[-300:]}")


if __name__ == "__main__":
    block_a()
    block_b()
    block_c()
    block_d()
    block_e()
    if FAILED:
        print(f"\nПРОВАЛЕНО: {len(FAILED)}")
        for n in FAILED:
            print("  -", n.encode("ascii", "replace").decode("ascii")
                  if not n.isascii() else n)
        sys.exit(1)
    print("\nВсе проверки патча 22.15 пройдены.")
