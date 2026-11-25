# scripts/test_patch22_18.py
"""Тесты патча 22.18 «Экспорт Обновить статусы: КСТ2 и ГИС».

Следствие патчей 22.15 (единый хелпер сопоставления шифров) и 22.17
(приоритет статуса ГИС-комплектов с документами).

Блок (а): КСТ2 с текстовым хвостом в базе («АТ-РД-КСТ2-А-00
        КОНТАКТНАЯ СЕТЬ …») — статус «Загружено в Витро», ревизия и
        дата ревизии заполнены из базы.
Блок (б): загруженный ГИС (Excel-код с дробью, комплекс в базе без
        дробной части) — сопоставление хелпером 22.15 →
        «Загружено в Витро» с датой/ревизией.
Блок (в): ГИС вне Витро — ровно пометка «Передан (вне Витро)»,
        ревизия и дата пустые (ничего не выдумываем).
Блок (г): несопоставленный шифр — «План (не загружен)» и попадает
        в список расхождений отчёта; пустые ячейки — нет.
Блок (д): обычный АТ-комплект — без регресса: статус листов A/B/C
        (патч 21), ревизия и дата как раньше.
Блок (е): регресс построчно EXIT=0 (патчи 21–22.17 + gis_filters).
Блок (ж): реальная выгрузка Excel (openpyxl-режим
        _xlsx_add_kit_status) — структура прежняя, значения трёх
        колонок по правилам шага 2.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_18.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* (СТОП-ЛИСТ патчей 21/22) ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_18_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sheets import (  # noqa: E402
    KIT_STATUS_NOT_LOADED,
    get_kit_revision_info,
    get_kit_statuses,
)
from vitro.ui.export import (  # noqa: E402
    KIT_REV_DATE_COL,
    KIT_REVISION_COL,
    KIT_STATUS_COL,
    KIT_STATUS_LOADED,
    _apply_kit_statuses_resolved,
    _resolve_kit_codes,
    _xlsx_add_kit_status,
)
from vitro.ui.plan import KIT_STATUS_PASSED, _kit_matches_complex  # noqa: E402

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


# ---------------------------------------------------------------------------
#  Данные
# ---------------------------------------------------------------------------
# КСТ2: в Excel — чистый шифр, в базе — тот же шифр + текстовый хвост
# (папка SharePoint). is_valid_kit_code хвост не пропускает, поэтому
# раньше комплекс не попадал в unique_vitro_sheets → «План (не загружен)».
KST2 = "АТ-РД-КСТ2-А-00"
KST2_DB = ("АТ-РД-КСТ2-А-00  КОНТАКТНАЯ СЕТЬ ТРОЛЛЕЙБУСА И ТРАМВАЯ "
           "ПО ВРЕМЕННОЙ СХЕМЕ")

# 5 загруженных ГИС: в Excel — код с дробью, в базе — база без дроби.
GIS_LOADED = [
    ("ГИС-РД-СБ-АСЗП-0-00/10511.25.01-АСЗП",
     "ГИС-РД-СБ-АСЗП-0-00"),
    ("ГИС-РД-СБ-ООС-С-00/10511.26.01-ООС1",
     "ГИС-РД-СБ-ООС-С-00"),
    ("ГИС-РД-СБ-ЭО-С-00/10511.27.01-ЭО",
     "ГИС-РД-СБ-ЭО-С-00"),
    ("ГИС-РД-СБ-ПБ-С-00/10511.28.01-ПБ",
     "ГИС-РД-СБ-ПБ-С-00"),
    ("ГИС-РД-СБ-ТХ-С-00/10511.29.01-ТХ",
     "ГИС-РД-СБ-ТХ-С-00"),
]

# 2 ГИС вне Витро: документов в базе нет, в плане — ГИС-комплекты.
GIS_PASSED = [
    "ГИС-РД-СБ-КЖ-С-00/10511.30.01-КЖ",
    "ГИС-РД-СБ-АР-С-00/10511.31.01-АР",
]

KIT = "АТ-РД-КЖ1-1-00"            # обычный АТ, листы A + B → статус «B»
KIT_ABSENT = "АТ-РД-ХХ9-9-99"     # заведомо отсутствует в базе


def seed_documents() -> None:
    """(id, leaf, complex, status, sheet_number, status_date, revision)."""
    rows = [
        # КСТ2: два листа, max ревизия 12.0, крайняя дата 15.03.2026
        (1, "КСТ2-1.PDF", KST2_DB, "A", "1", "2026-02-01", "12.0"),
        (2, "КСТ2-2.PDF", KST2_DB, "B", "2", "2026-03-15", "11.0"),
        # обычный АТ: A + B → «B», max ревизия 4.0, дата 20.01.2026
        (10, "КЖ1-1.PDF", KIT, "A", "1", "2026-01-10", "1.0"),
        (11, "КЖ1-1.PDF", KIT, "A", "1", "2026-01-20", "4.0"),
        (12, "КЖ1-2.PDF", KIT, "B", "2", "2025-06-15", "2.0"),
    ]
    next_id = 100
    for i, (_excel, base) in enumerate(GIS_LOADED):
        rows.append((next_id, f"{base}-1.PDF", base, "A", "1",
                     f"2026-0{i + 1}-05", f"{i + 5}.0"))
        next_id += 1
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM documents")
        conn.executemany(
            "INSERT INTO documents (id, leaf, complex, status, "
            "sheet_number, status_date, revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


seed_documents()

ALL_CODES = ([KST2] + [e for e, _b in GIS_LOADED] + GIS_PASSED
             + [KIT, KIT_ABSENT])


def metrics_of(df: pd.DataFrame, code: str, col: str = "Шифр РД") -> dict:
    row = df[df[col].astype(str) == code].iloc[0]
    return {
        "status": row[KIT_STATUS_COL],
        "revision": row[KIT_REVISION_COL],
        "date": row[KIT_REV_DATE_COL],
    }


# ===========================================================================
#  а–д. Резолвер: три kit-колонки по правилам шага 2
# ===========================================================================
def block_a_d():
    df = pd.DataFrame({"Шифр РД": ALL_CODES})
    out, unmatched = _apply_kit_statuses_resolved(df, "Шифр РД")

    # --- (а) КСТ2 с хвостом ---
    kst2 = metrics_of(out, KST2)
    check("а: КСТ2 — статус «Загружено в Витро»",
          kst2["status"] == KIT_STATUS_LOADED, f"получено {kst2['status']}")
    check("а: КСТ2 — ревизия из базы «12.0»",
          kst2["revision"] == "12.0", f"получено «{kst2['revision']}»")
    check("а: КСТ2 — дата загрузки из базы «15.03.2026»",
          kst2["date"] == "15.03.2026", f"получено «{kst2['date']}»")
    check("а: КСТ2 сопоставлен именно хелпером 22.15",
          _kit_matches_complex(KST2, KST2_DB))

    # --- (б) 5 загруженных ГИС (дробь в Excel, база без дроби) ---
    for excel_code, base in GIS_LOADED:
        m = metrics_of(out, excel_code)
        check(f"б: ГИС {base} — «Загружено в Витро»",
              m["status"] == KIT_STATUS_LOADED, f"получено {m['status']}")
        check(f"б: ГИС {base} — ревизия заполнена", bool(m["revision"]))
        check(f"б: ГИС {base} — дата заполнена",
              len(m["date"]) == 10 and m["date"].count(".") == 2,
              f"получено «{m['date']}»")

    # --- (в) ГИС вне Витро: только пометка, пустые ревизия/дата ---
    for code in GIS_PASSED:
        m = metrics_of(out, code)
        check(f"в: ГИС вне Витро {code} — «Передан (вне Витро)»",
              m["status"] == KIT_STATUS_PASSED, f"получено {m['status']}")
        check(f"в: ГИС вне Витро {code} — ревизия пустая",
              m["revision"] == "", f"получено «{m['revision']}»")
        check(f"в: ГИС вне Витро {code} — дата пустая",
              m["date"] == "", f"получено «{m['date']}»")

    # --- (г) несопоставленный шифр ---
    absent = metrics_of(out, KIT_ABSENT)
    check("г: несопоставленный шифр — «План (не загружен)»",
          absent["status"] == KIT_STATUS_NOT_LOADED,
          f"получено {absent['status']}")
    check("г: несопоставленный — ревизия/дата пустые",
          absent["revision"] == "" and absent["date"] == "")
    check("г: несопоставленный попадает в список расхождений",
          unmatched == [KIT_ABSENT], f"получено {unmatched}")
    check("г: ГИС вне Витро НЕ считается расхождением",
          all(c not in unmatched for c in GIS_PASSED))
    check("г: загруженные КСТ2/ГИС НЕ считаются расхождением",
          KST2 not in unmatched
          and all(e not in unmatched for e, _b in GIS_LOADED))

    # --- (д) обычный АТ-комплект — без регресса ---
    kit = metrics_of(out, KIT)
    check("д: обычный АТ — статус листов «B» (патч 21, не регресс)",
          kit["status"] == "B", f"получено {kit['status']}")
    check("д: обычный АТ — ревизия «4.0»",
          kit["revision"] == "4.0", f"получено «{kit['revision']}»")
    check("д: обычный АТ — дата «20.01.2026»",
          kit["date"] == "20.01.2026", f"получено «{kit['date']}»")
    check("д: get_kit_statuses не изменён (KIT → «B»)",
          get_kit_statuses([KIT])[KIT] == "B")

    # порядок колонок и отсутствие мутации исходного df
    check("колонки добавлены в конец: Статус → Ревизия → Дата",
          list(out.columns)[-3:] == [KIT_STATUS_COL, KIT_REVISION_COL,
                                     KIT_REV_DATE_COL])
    check("исходный df не мутирован",
          list(df.columns) == ["Шифр РД"])

    # пустой шифр: дефолт, без расхождения
    df_empty = pd.DataFrame({"Шифр РД": ["", None]})
    out_empty, unmatched_empty = _apply_kit_statuses_resolved(
        df_empty, "Шифр РД")
    check("пустой шифр → «План (не загружен)», не расхождение",
          list(out_empty[KIT_STATUS_COL]) == [KIT_STATUS_NOT_LOADED] * 2
          and unmatched_empty == [])


# ===========================================================================
#  Расширение sheets: include_invalid (хвостовые комплексы)
# ===========================================================================
def block_sheets():
    info_default = get_kit_revision_info(KST2)
    check("sheets: по умолчанию хвостовой комплекс не виден (патч 22)",
          info_default[KST2] == {"revision": "", "date": ""})

    resolved = _resolve_kit_codes((KST2,))
    check("резолвер: КСТ2 разрешён через хелпер (без равенства)",
          resolved[KST2]["status"] == KIT_STATUS_LOADED
          and resolved[KST2]["revision"] == "12.0",
          f"получено {resolved.get(KST2)}")

    info_all = get_kit_revision_info(include_invalid=True)
    check("sheets: include_invalid=True — КСТ2 с хвостом в индексе",
          KST2_DB in info_all,
          f"ключей: {len(info_all)}")
    check("sheets: include_invalid — ревизия/дата КСТ2",
          info_all[KST2_DB] == {"revision": "12.0", "date": "15.03.2026"},
          f"получено {info_all.get(KST2_DB)}")

    sts_default = get_kit_statuses()
    check("sheets: по умолчанию КСТ2 с хвостом не в статусах",
          KST2_DB not in sts_default and KST2 not in sts_default)
    check("sheets: обычный АТ в статусах по умолчанию",
          sts_default.get(KIT) == "B")


# ===========================================================================
#  ж. Реальная выгрузка Excel (openpyxl-режим) — значения трёх колонок
# ===========================================================================
def block_excel():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Комплекты"
    ws.append(["Наименование", "Шифр РД"])
    for i, code in enumerate(ALL_CODES, start=1):
        ws.append([f"Комплект {i}", code])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    raw = _xlsx_add_kit_status(
        buf, "Комплекты", None, None, KIT_STATUS_COL,
        sync_ts="2026-11-25T09:00:00",
    )
    out = openpyxl.load_workbook(io.BytesIO(raw))["Комплекты"]

    # Структура прежняя: полоса даты + шапка, три kit-колонки справа.
    check("Excel: шапка в строке 2, исходные колонки на месте",
          (out["A2"].value, out["B2"].value) == ("Наименование", "Шифр РД"))
    check("Excel: три kit-колонки C/D/E — структура не изменена",
          (out["C2"].value, out["D2"].value, out["E2"].value)
          == (KIT_STATUS_COL, KIT_REVISION_COL, KIT_REV_DATE_COL))
    check("Excel: полоса даты в C1 (над kit-колонками)",
          out["C1"].value == "База актуальна на 25.11.2026, 09:00 (МСК)",
          f"получено «{out['C1'].value}»")

    # Строки: 3 — КСТ2, 4–8 — 5 ГИС, 9–10 — ГИС вне Витро,
    # 11 — обычный АТ, 12 — несопоставленный.
    check("Excel: КСТ2 — «Загружено в Витро» + ревизия + дата",
          (out["C3"].value, out["D3"].value, out["E3"].value)
          == (KIT_STATUS_LOADED, "12.0", "15.03.2026"),
          f"получено {(out['C3'].value, out['D3'].value, out['E3'].value)}")
    check("Excel: загруженный ГИС (строка 4) — «Загружено в Витро»",
          out["C4"].value == KIT_STATUS_LOADED
          and out["D4"].value not in ("", None)
          and out["E4"].value not in ("", None),
          f"получено {(out['C4'].value, out['D4'].value, out['E4'].value)}")
    check("Excel: ГИС вне Витро (строка 9) — только пометка",
          out["C9"].value == KIT_STATUS_PASSED
          and (out["D9"].value or "") == ""
          and (out["E9"].value or "") == "",
          f"получено {(out['C9'].value, out['D9'].value, out['E9'].value)}")
    check("Excel: обычный АТ (строка 11) — «B» + ревизия + дата",
          (out["C11"].value, out["D11"].value, out["E11"].value)
          == ("B", "4.0", "20.01.2026"),
          f"получено {(out['C11'].value, out['D11'].value, out['E11'].value)}")
    check("Excel: несопоставленный (строка 12) — «План (не загружен)»",
          out["C12"].value == KIT_STATUS_NOT_LOADED
          and (out["D12"].value or "") == ""
          and (out["E12"].value or "") == "",
          f"получено {(out['C12'].value, out['D12'].value, out['E12'].value)}")
    check("Excel: выгрузка перезагружается openpyxl", True)


# ===========================================================================
#  е. Регресс построчно EXIT=0
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
    "test_patch22_17.py",
    "test_patch_gis_filters.py",
]


def block_e():
    # Защита от взаимной рекурсии: вложенный запуск блока (е) не
    # повторяется — тесты регресса сами гоняют друг друга.
    if os.environ.get("VITRO_REGRESSION_NESTED") == "1":
        print("[SKIP] блок (е): вложенный запуск")
        return
    import subprocess
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
        check(f"е: {name} EXIT=0", r.returncode == 0,
              f"EXIT={r.returncode}; хвост вывода: "
              f"{r.stdout.decode('utf-8', 'replace')[-400:]}")


if __name__ == "__main__":
    block_a_d()
    block_sheets()
    block_excel()
    block_e()
    if FAILED:
        print(f"\nПРОВАЛЕНО: {len(FAILED)}")
        for n in FAILED:
            print("  -", n.encode("ascii", "replace").decode("ascii")
                  if not n.isascii() else n)
        sys.exit(1)
    print("\nВсе проверки патча 22.18 пройдены.")
    sys.exit(0)
