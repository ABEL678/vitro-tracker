"""Тесты патча 22.17 — приоритет статуса ГИС-комплектов.

Проблема (следствие диагностики 22.11, раунд 2): патч 22.11 присваивал
«Передан (вне Витро)» ВСЕМ ГИС-комплектам плана безусловно, хотя часть
из них фактически загружена в Витро — в папках GIS_WORK лежат PDF,
документы есть в documents. По данным пользователя: из 31 ГИС-комплекта
плана 5 загружены, 26 переданы вне Витро.

Правки (vitro/ui/plan.py):
  - _is_pdf_document_leaf(leaf): PDF — имя с расширением .pdf (регистр
    не важен) либо реестровый «Р-NNN» (патч 22.16 хранит его без
    расширения);
  - _vitro_pdf_complex_codes(): шифры комплексов, у которых есть хотя
    бы один PDF-документ в documents;
  - _build_overview(): у ГИС-комплекта с PDF приоритет «Загружен в
    Витро»; «Передан (вне Витро)» — только для ГИС без документов.
    Сопоставление plan_kits ↔ documents — тем же хелпером 22.15
    (_kit_matches_complex → _vitro_claim_index), хелпер не тронут.

Блоки:
  а. ГИС-комплект с PDF в базе → «Загружен в Витро» (в т.ч. зачёт
     через правила 22.15: ГИС-дробь плана ↔ базовый комплекс Витро,
     нормализация регистра/пробелов/латиницы, реестровый Р-NNN).
  б. ГИС-комплект без документов → «Передан (вне Витро)»; документ
     не-PDF (например .dwg) статус не меняет.
  в. АТ-комплекты не затронуты: с документами — «Загружен в Витро»,
     частично — «Загружается (частично)», без документов — «План
     (не загружен)».
  г. Сумма статусов всегда равна числу комплектов плана; арифметика
     итогов (Загружено + Передан + План = Всего).
  д. Регресс построчно EXIT=0: test_patch21, test_patch22, _3, _5, _6,
     _8, _9, _10, _11, _12, _13, _15, _16, test_patch_gis_filters.

Тестовая БД — временная (SQLITE_PATH до импорта vitro.*); боевая
data/vitro.db не открывается и не меняется.
Запуск: .venv\\Scripts\\python.exe scripts\\test_patch22_17.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_17_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
import vitro.ui.plan as plan  # noqa: E402

sqlite_db.init_db()

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
    if not cond:
        FAILED.append(name)


GIS_A = "ГИС-РД-СБ-ТСО-1-00"
GIS_B = "ГИС-РД-СБ-ТСО-2-00"
GIS_FRAC = "ГИС-РД-СБ-АСЗП-0-00/10511.25.01-АСЗП"
GIS_BASE = "ГИС-РД-СБ-АСЗП-0-00"
AT_LOADED = "АТ-РД-КЖ1-1-00"
AT_PLAN = "АТ-РД-КЖ2-1-00"
AT_PARTIAL = "АТ-РД-ОВ1-1-00"
AT_VITRO_ONLY = "АТ-РД-СМИК-0-00"


def reset() -> None:
    with sqlite_db.get_conn() as conn:
        for t in ("documents", "comments", "plan_drawings", "plan_kits",
                  "complexes"):
            conn.execute(f"DELETE FROM {t}")
    for fn in (plan._vitro_sheet_counts, plan._vitro_sheet_status_counts,
               plan._vitro_kit_meta, plan._vitro_sheet_dates,
               plan._vitro_pdf_complex_codes):
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


def add_drawing(kit_code, sheet_no="1", qty=1):
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO plan_drawings (kit_code, sheet_no, title, "
            "discipline, section, qty, planned_date, source, source_file, "
            "uploaded_at, uploaded_by) "
            "VALUES (?, ?, '', '', '', ?, '', 'excel', '', '', '')",
            (kit_code, sheet_no, qty))


def add_doc(doc_id, complex_code, sheet="", leaf=None, status="A"):
    if leaf is None:
        leaf = f"{complex_code}-{sheet}.pdf" if sheet else "doc.pdf"
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, leaf, complex, status, sheet_number, "
            "discipline) VALUES (?, ?, ?, ?, ?, '')",
            (doc_id, leaf, complex_code, status, sheet))


def overview(include_vitro_only=False):
    for fn in (plan._vitro_sheet_counts, plan._vitro_sheet_status_counts,
               plan._vitro_pdf_complex_codes):
        fn.clear()
    return plan._build_overview(include_vitro_only)


def row_of(df, code):
    r = df[df["code"] == code]
    return r.iloc[0] if len(r) else None


def status_of(df, code):
    r = row_of(df, code)
    return None if r is None else r["status"]


# ===========================================================================
#  а. ГИС-комплект с PDF в базе → «Загружен в Витро»
# ===========================================================================
def block_a():
    reset()
    add_plan_kit(GIS_A, "Техсредства охраны. Подземная часть")
    add_doc(1, GIS_A, "1", leaf=f"{GIS_A}-1.pdf")
    ov = overview()
    r = row_of(ov, GIS_A)
    check("а: ГИС с PDF-документом → «Загружен в Витро»",
          r is not None and r["status"] == "Загружен в Витро",
          f"status={status_of(ov, GIS_A)}")
    check("а: ГИС с PDF засчитан выполненным (in_vitro=True)",
          r is not None and bool(r["in_vitro"]))
    check("а: лист учтён (sheets_vitro=1)",
          r is not None and int(r["sheets_vitro"]) == 1)
    totals = plan._plan_totals(ov)
    check("а: итоги — всего 1, Загружено 1, Передан 0, План 0",
          totals["kits_total"] == 1 and totals["kits_loaded"] == 1
          and totals["kits_passed"] == 0 and totals["kits_plan"] == 0,
          f"total={totals['kits_total']}, loaded={totals['kits_loaded']}, "
          f"passed={totals['kits_passed']}, plan={totals['kits_plan']}")

    # Зачёт через правило 22.15 (б): ГИС-план-код с дробью ↔ базовый
    # комплекс в documents. Хелпер 22.15 не тронут.
    reset()
    add_plan_kit(GIS_FRAC, "АСУЗП (дробный шифр)")
    add_doc(1, GIS_BASE, "10", leaf=f"{GIS_BASE}-10.pdf")
    ov = overview()
    check("а: ГИС-дробь плана + базовый комплекс с PDF → «Загружен в Витро»",
          status_of(ov, GIS_FRAC) == "Загружен в Витро",
          f"status={status_of(ov, GIS_FRAC)}")
    check("а: хелпер 22.15 по-прежнему сопоставляет дробь и базу",
          plan._kit_matches_complex(GIS_FRAC, GIS_BASE))

    # Нормализация: регистр, пробелы, латинский двойник в complex и
    # расширение .PDF в верхнем регистре.
    reset()
    add_plan_kit(GIS_B, "ТСО Корпус 2")
    dirty_complex = "  " + GIS_B.replace("С", "C") + " "
    add_doc(1, dirty_complex, "1", leaf=f"{GIS_B}-1.PDF")
    ov = overview()
    check("а: «грязный» complex + .PDF (верхний регистр) → «Загружен»",
          status_of(ov, GIS_B) == "Загружен в Витро",
          f"status={status_of(ov, GIS_B)}")

    # Реестровый PDF «Р-NNN» хранится без расширения (патч 22.16).
    reset()
    add_plan_kit(GIS_A)
    add_doc(1, GIS_A, "", leaf="Р-381")
    ov = overview()
    check("а: реестровый PDF «Р-381» (без расширения) → «Загружен в Витро»",
          status_of(ov, GIS_A) == "Загружен в Витро",
          f"status={status_of(ov, GIS_A)}")


# ===========================================================================
#  б. ГИС-комплект без документов → «Передан (вне Витро)»
# ===========================================================================
def block_b():
    reset()
    add_plan_kit(GIS_A)
    ov = overview()
    r = row_of(ov, GIS_A)
    check("б: ГИС без документов → «Передан (вне Витро)»",
          r is not None and r["status"] == plan.KIT_STATUS_PASSED,
          f"status={status_of(ov, GIS_A)}")
    check("б: «Передан» засчитан выполненным (in_vitro=True)",
          r is not None and bool(r["in_vitro"]))
    check("б: листов в Витро 0",
          r is not None and int(r["sheets_vitro"]) == 0)
    totals = plan._plan_totals(ov)
    check("б: итоги — Передан 1, Загружено 0, План 0",
          totals["kits_passed"] == 1 and totals["kits_loaded"] == 0
          and totals["kits_plan"] == 0,
          f"passed={totals['kits_passed']}, loaded={totals['kits_loaded']}, "
          f"plan={totals['kits_plan']}")

    # Документ не-PDF (.dwg) — не основание для «Загружено».
    reset()
    add_plan_kit(GIS_A)
    add_doc(1, GIS_A, "1", leaf=f"{GIS_A}-1.dwg")
    ov = overview()
    check("б: ГИС с не-PDF документом (.dwg) → «Передан (вне Витро)»",
          status_of(ov, GIS_A) == plan.KIT_STATUS_PASSED,
          f"status={status_of(ov, GIS_A)}")

    # Документы соседнего ГИС-комплекта не зачитываются.
    reset()
    add_plan_kit(GIS_A)
    add_plan_kit(GIS_B)
    add_doc(1, GIS_B, "1", leaf=f"{GIS_B}-1.pdf")
    ov = overview()
    check("б: сосед без документов остался «Передан»",
          status_of(ov, GIS_A) == plan.KIT_STATUS_PASSED,
          f"status={status_of(ov, GIS_A)}")
    check("б: сосед с PDF — «Загружен в Витро»",
          status_of(ov, GIS_B) == "Загружен в Витро",
          f"status={status_of(ov, GIS_B)}")


# ===========================================================================
#  в. АТ-комплекты не затронуты
# ===========================================================================
def block_c():
    reset()
    add_plan_kit(AT_LOADED, disc="01_АР", section="")
    add_plan_kit(AT_PLAN, disc="01_АР", section="")
    add_plan_kit(AT_PARTIAL, disc="01_АР", section="")
    add_drawing(AT_PARTIAL, "1", 1)
    add_drawing(AT_PARTIAL, "2", 1)
    add_drawing(AT_PARTIAL, "3", 1)
    add_drawing(AT_PARTIAL, "4", 1)
    add_drawing(AT_PARTIAL, "5", 1)
    add_doc(1, AT_LOADED, "1", leaf=f"{AT_LOADED}-1.pdf")
    add_doc(2, AT_PARTIAL, "1", leaf=f"{AT_PARTIAL}-1.pdf")
    add_doc(3, AT_PARTIAL, "2", leaf=f"{AT_PARTIAL}-2.pdf")
    ov = overview()
    check("в: АТ с документами → «Загружен в Витро» (как было)",
          status_of(ov, AT_LOADED) == "Загружен в Витро",
          f"status={status_of(ov, AT_LOADED)}")
    check("в: АТ без документов → «План (не загружен)» (как было)",
          status_of(ov, AT_PLAN) == "План (не загружен)",
          f"status={status_of(ov, AT_PLAN)}")
    check("в: АТ частично (2 из 5 листов) → «Загружается (частично)»",
          status_of(ov, AT_PARTIAL) == "Загружается (частично)",
          f"status={status_of(ov, AT_PARTIAL)}")
    check("в: среди АТ нет «Передан (вне Витро)»",
          not (ov[ov["code"].str.startswith("АТ-РД-")]
               ["status"] == plan.KIT_STATUS_PASSED).any())
    check("в: _is_gis_kit не считает АТ-комплекты ГИС",
          not plan._is_gis_kit(AT_LOADED)
          and plan._is_gis_kit(GIS_A))


# ===========================================================================
#  г. Сумма статусов = число комплектов плана; арифметика итогов
# ===========================================================================
def block_d():
    reset()
    # 3 ГИС с PDF, 3 ГИС без, 2 АТ загружены, 2 АТ в плане,
    # 1 комплект «Только в Витро» (вне плана).
    gis_with = [f"ГИС-РД-СБ-ТСО-{i}-00" for i in (1, 2, 3)]
    gis_without = [f"ГИС-РД-СБ-ТСО-{i}-00" for i in (4, 5, 6)]
    at_loaded = [f"АТ-РД-КЖ{i}-1-00" for i in (1, 2)]
    at_plan = [f"АТ-РД-ОВ{i}-1-00" for i in (1, 2)]
    doc_id = 0
    for code in gis_with + at_loaded:
        add_plan_kit(code)
        doc_id += 1
        add_doc(doc_id, code, "1", leaf=f"{code}-1.pdf")
    for code in gis_without + at_plan:
        add_plan_kit(code)
    doc_id += 1
    add_doc(doc_id, AT_VITRO_ONLY, "1", leaf=f"{AT_VITRO_ONLY}-1.pdf")

    ov = overview(include_vitro_only=True)
    n_plan = len(gis_with) + len(gis_without) + len(at_loaded) + len(at_plan)
    ov_plan = ov[ov["code"].isin(
        gis_with + gis_without + at_loaded + at_plan)]
    statuses = ov_plan["status"].value_counts().to_dict()
    check("г: строк плана ровно столько, сколько комплектов в plan_kits",
          len(ov_plan) == n_plan, f"строк={len(ov_plan)}, план={n_plan}")
    check("г: сумма статусов = число комплектов плана",
          sum(statuses.values()) == n_plan,
          f"сумма={sum(statuses.values())}, статусы={statuses}")
    check("г: 3 ГИС с PDF + 2 АТ → «Загружен в Витро» (5)",
          statuses.get("Загружен в Витро", 0) == 5,
          f"загружено={statuses.get('Загружен в Витро', 0)}")
    check("г: 3 ГИС без документов → «Передан (вне Витро)» (3)",
          statuses.get(plan.KIT_STATUS_PASSED, 0) == 3,
          f"передано={statuses.get(plan.KIT_STATUS_PASSED, 0)}")
    check("г: 2 АТ без документов → «План (не загружен)» (2)",
          statuses.get("План (не загружен)", 0) == 2,
          f"план={statuses.get('План (не загружен)', 0)}")
    totals = plan._plan_totals(ov)
    check("г: Загружено + Передан + План = Всего",
          totals["kits_loaded"] + totals["kits_passed"]
          + totals["kits_plan"] == totals["kits_total"],
          f"{totals['kits_loaded']} + {totals['kits_passed']} + "
          f"{totals['kits_plan']} != {totals['kits_total']}")
    check("г: «Только в Витро» учтён в «Загружено», но не в плане",
          status_of(ov, AT_VITRO_ONLY) == "Только в Витро"
          and totals["kits_total"] == n_plan + 1,
          f"total={totals['kits_total']}, status="
          f"{status_of(ov, AT_VITRO_ONLY)}")

    # Пустые план и документы — не падает, суммы нулевые.
    reset()
    ov = overview(include_vitro_only=False)
    totals = plan._plan_totals(ov)
    check("г: пустые данные — итоги нулевые",
          totals["kits_total"] == 0 and totals["kits_loaded"] == 0
          and totals["kits_passed"] == 0 and totals["kits_plan"] == 0)


# ===========================================================================
#  д. Регресс построчно EXIT=0
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
    "test_patch22_15.py",
    "test_patch22_16.py",
    "test_patch_gis_filters.py",
]


def block_e():
    # Защита от взаимной рекурсии: 22.15/22.16 сами регрессят друг
    # друга — при вложенном запуске блок (д) не повторяется.
    if os.environ.get("VITRO_REGRESSION_NESTED") == "1":
        print("[SKIP] блок (д): вложенный запуск (регресс другого теста)")
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
        check(f"д: {name} EXIT=0", r.returncode == 0,
              f"EXIT={r.returncode}; хвост вывода: "
              f"{r.stdout.decode('utf-8', 'replace')[-400:]}")


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
    print("\nВсе проверки патча 22.17 пройдены.")
