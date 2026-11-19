# scripts/test_patch22_3.py
"""Регресс-тесты фиксов 22.2/22.3: исходный файл с таблицей Excel и
объединёнными ячейками при выгрузке статусов.

Два независимых источника «Ошибки в части содержимого» (Excel
предлагает восстановить файл):

1. Таблица Excel (ListObject, xl/tables/table1.xml). ws.insert_rows()
   НЕ сдвигает table.ref, добавленные kit-колонки остаются вне
   таблицы: старый диапазон пересекается с merge-полосой даты и не
   совпадает ни с шапкой, ни с числом колонок → Excel удаляет таблицу:
   «Удалено: автофильтр … /xl/tables/table1.xml». Фикс: таблицы
   целевого листа снимаются (del ws.tables[name]).

2. Пересечение merge-диапазонов. ws.insert_rows() НЕ сдвигает и
   merges исходника: старое A1:C1 остаётся на строке 1 и пересекается
   с новой полосой A1:…1 → Excel чинит файл. Фикс: merges снимаются
   перед вставкой и возвращаются со сдвигом на строку вниз.

Дополнительно закреплено:
- полоса даты НАД шапкой (одобренный дизайн), шапка в строке
  header+1, автофильтр шапка+данные без полосы;
- kit-значения в ФАКТИЧЕСКИХ строках (словарь {строка: шифр}) —
  пустая строка в середине данных не сдвигает значения вверх;
- пустая таблица (только шапка) → автофильтр не ставится;
- контент под таблицей (примечания) не затирается;
- таблица на ДРУГОМ листе сохраняется;
- широкий баннер, попадающий в зону kit-колонок, не возвращается.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_3.py
"""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* (СТОП-ЛИСТ патчей 21/22) ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_3_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import openpyxl  # noqa: E402
from openpyxl.worksheet.table import Table, TableStyleInfo  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.ui.export import (  # noqa: E402
    KIT_REV_DATE_COL,
    KIT_REVISION_COL,
    KIT_STATUS_COL,
    _styled_upload_to_xlsx,
    _xlsx_add_kit_status,
)

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


KIT = "АТ-РД-КЖ1-1-00"
KIT2 = "АТ-РД-ОВ2-С-00"
KIT3 = "АТ-РД-КЖ1-2-00"  # отсутствует в БД


def seed_db() -> None:
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM documents")
        conn.executemany(
            "INSERT INTO documents (id, leaf, complex, status, "
            "sheet_number, status_date, revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, f"{KIT}-1.PDF", KIT, "A", "1", "2025-01-10", "1.0"),
                (2, f"{KIT}-1.PDF", KIT, "A", "1", "2026-01-20", "4.0"),
                (3, f"{KIT}-2.PDF", KIT, "B", "2", "2025-06-15", "2.0"),
                (4, f"{KIT2}-1.PDF", KIT2, "C", "1", "2025-07-01", "11.0"),
            ],
        )


def build_source(with_table: bool = False, header_row: int = 1,
                 merged_title: bool = False, blank_mid_row: bool = False,
                 note_below: bool = False, empty_data: bool = False,
                 wide_banner: bool = False,
                 extra_sheet_table: bool = False) -> io.BytesIO:
    """Синтетический исходник «Шифр РД» с опциональными ловушками."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Комплекты"
    if wide_banner:
        ws.cell(row=1, column=1, value="БАННЕР ОТЧЁТА")
        ws.merge_cells(start_row=1, start_column=1,
                       end_row=1, end_column=8)
        header_row = 2
    if merged_title:
        ws.cell(row=header_row, column=1, value="График статусов")
        ws.merge_cells(start_row=header_row, start_column=1,
                       end_row=header_row, end_column=3)
        header_row += 1
    ws.cell(row=header_row, column=1, value="Шифр РД")
    ws.cell(row=header_row, column=2, value="Наименование")
    ws.cell(row=header_row, column=3, value="% готовности")
    data = [] if empty_data else [KIT, KIT2, KIT3]
    r = header_row
    for i, code in enumerate(data, start=1):
        r = header_row + i
        if blank_mid_row and i == 2:
            # пустая строка В середине данных: KIT2 пропускается,
            # KIT3 попадает на строку ниже обычной
            continue
        ws.cell(row=r, column=1, value=code)
        ws.cell(row=r, column=2, value=f"Работы {i}")
        ws.cell(row=r, column=3, value=50 + i)
    last = r
    if blank_mid_row and data:
        last = header_row + len(data)  # данные с дыркой
    if with_table and not empty_data:
        tbl = Table(displayName="Таблица1",
                    ref=f"A{header_row}:C{last}")
        tbl.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium9", showRowStripes=True)
        ws.add_table(tbl)
    if note_below and not empty_data:
        ws.cell(row=last + 2, column=1,
                value="Перевыпускаем 18.11. Выяснить что делаем с колодцами")
    if extra_sheet_table:
        ws2 = wb.create_sheet("Прочее")
        ws2.append(["Что-то", "Ещё"])
        ws2.append(["a", "b"])
        tbl2 = Table(displayName="ТаблицаДругогоЛиста", ref="A1:B2")
        ws2.add_table(tbl2)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export(source: io.BytesIO) -> bytes:
    return _xlsx_add_kit_status(
        source, "Комплекты", None, None, KIT_STATUS_COL, sync_ts=None,
    )


def table_parts(raw: bytes) -> list[str]:
    zf = zipfile.ZipFile(io.BytesIO(raw))
    return [n for n in zf.namelist() if "/tables/" in n]


def merges_overlap(ranges) -> bool:
    """Пересекаются ли merge-диапазоны между собой."""
    for i, a in enumerate(ranges):
        ac1, ar1, ac2, ar2 = a.bounds
        for b in ranges[i + 1:]:
            bc1, br1, bc2, br2 = b.bounds
            if not (ac2 < bc1 or bc2 < ac1 or ar2 < br1 or br2 < ar1):
                return True
    return False


def no_overlap_with_strip(ws, strip_ref: str = "D1:F1") -> bool:
    """Ни один merge не пересекает строку полосы (кроме самой полосы)."""
    from openpyxl.utils import range_boundaries
    _, s_row, _, _e = range_boundaries(strip_ref)
    for rng in ws.merged_cells.ranges:
        if str(rng) == strip_ref:
            continue
        if rng.min_row <= s_row <= rng.max_row:
            return False
    return True


# ---------------------------------------------------------------------------
#  1. Главный сценарий: исходник С таблицей Excel (ListObject)
# ---------------------------------------------------------------------------
seed_db()
out = export(build_source(with_table=True))
check("выгрузка непустая", len(out) > 0)
parts = table_parts(out)
check("1: в выгрузке нет xl/tables/*.xml (диалог «table1.xml»)",
      parts == [], f"остались: {parts}")

wb_out = openpyxl.load_workbook(io.BytesIO(out))
ws_out = wb_out["Комплекты"]
check("1: после перезагрузки у листа нет таблиц", len(ws_out.tables) == 0)
check("1: текст полосы объединён над kit-колонками (D1:F1)",
      "D1:F1" in [str(r) for r in ws_out.merged_cells.ranges])
check("1: D1 — «База актуальна на —» (sync_ts=None), над kit-шапкой",
      ws_out["D1"].value == "База актуальна на —",
      f"получено «{ws_out['D1'].value}»")
check("1: заливка полосы E8F0F8 во всю ширину строки 1 (A1..D1)",
      all(str(ws_out.cell(row=1, column=i).fill.start_color.rgb)[-6:]
          == "E8F0F8" for i in range(1, 5)))
check("1: шапка в строке 2: A2 = «Шифр РД»",
      ws_out["A2"].value == "Шифр РД")
check("1: шапка в строке 2: D2/E2/F2 — три kit-колонки",
      (ws_out["D2"].value, ws_out["E2"].value, ws_out["F2"].value)
      == (KIT_STATUS_COL, KIT_REVISION_COL, KIT_REV_DATE_COL))
check("1: автофильтр на шапке (A2:F5)",
      ws_out.auto_filter.ref == "A2:F5",
      f"получено «{ws_out.auto_filter.ref}»")
check("1: merge не пересекаются между собой",
      not merges_overlap(list(ws_out.merged_cells.ranges)))
check("1: ни один merge не лезет в строку полосы",
      no_overlap_with_strip(ws_out))
check("1: статус KIT = «B» (правило 21: худший из A/B по листам)",
      ws_out["D3"].value == "B", f"получено «{ws_out['D3'].value}»")
check("1: ревизия KIT = «4.0»", ws_out["E3"].value == "4.0")
check("1: дата KIT = «20.01.2026»", ws_out["F3"].value == "20.01.2026")
check("1: ревизия KIT2 = «11.0»", ws_out["E4"].value == "11.0")
check("1: отсутствующий комплект → «План (не загружен)»",
      ws_out["D5"].value == "План (не загружен)")
check("1: отсутствующий комплект → пустая ревизия",
      ws_out["E5"].value in ("", None))
wb_out.save(io.BytesIO())
check("1: выгрузка перезагружается и пересохраняется openpyxl", True)

# ---------------------------------------------------------------------------
#  2. Merge-заголовок над шапкой (диагноз правки 5d465a4)
# ---------------------------------------------------------------------------
seed_db()
out_m = export(build_source(merged_title=True))
wb_m = openpyxl.load_workbook(io.BytesIO(out_m))
ws_m = wb_m["Комплекты"]
mr = [str(r) for r in ws_m.merged_cells.ranges]
check("2: полоса D1:F1 и сдвинутый заголовок A2:C2",
      "D1:F1" in mr and "A2:C2" in mr, f"merges: {mr}")
check("2: заголовок не пересекается с полосой",
      not merges_overlap(list(ws_m.merged_cells.ranges)),
      f"merges: {mr}")
check("2: текст заголовка уехал на строку 2",
      ws_m["A2"].value == "График статусов",
      f"получено «{ws_m['A2'].value}»")
check("2: шапка в строке 3", ws_m["A3"].value == "Шифр РД",
      f"получено «{ws_m['A3'].value}»")
check("2: автофильтр A3:F6", ws_m.auto_filter.ref == "A3:F6",
      f"получено «{ws_m.auto_filter.ref}»")
check("2: kit-значения на фактических строках",
      (ws_m["D4"].value, ws_m["E4"].value, ws_m["F4"].value)
      == ("B", "4.0", "20.01.2026"))

# ---------------------------------------------------------------------------
#  3. Таблица + merge-заголовок ОДНОВРЕМЕННО (оба источника поломки)
# ---------------------------------------------------------------------------
seed_db()
out_both = export(build_source(with_table=True, merged_title=True))
check("3: в выгрузке нет xl/tables/*.xml", table_parts(out_both) == [])
wb_b = openpyxl.load_workbook(io.BytesIO(out_both))
ws_b = wb_b["Комплекты"]
check("3: нет пересекающихся merges",
      not merges_overlap(list(ws_b.merged_cells.ranges)),
      f"merges: {[str(r) for r in ws_b.merged_cells.ranges]}")
check("3: шапка в строке 3, kit-колонки на месте",
      (ws_b["A3"].value, ws_b["D3"].value, ws_b["E3"].value)
      == ("Шифр РД", KIT_STATUS_COL, KIT_REVISION_COL))

# ---------------------------------------------------------------------------
#  4. Пустая строка в середине данных: kit-значения в фактических строках
# ---------------------------------------------------------------------------
seed_db()
out_gap = export(build_source(blank_mid_row=True))
wb_g = openpyxl.load_workbook(io.BytesIO(out_gap))
ws_g = wb_g["Комплекты"]
# строки данных: 3 (KIT), 4 (пусто), 5 (KIT3)
check("4: KIT в строке 3 → статус в D3",
      ws_g["D3"].value == "B", f"получено «{ws_g['D3'].value}»")
check("4: пустая строка 4 → kit-ячейки пустые",
      ws_g["D4"].value in ("", None) and ws_g["E4"].value in ("", None),
      f"получено D4={ws_g['D4'].value!r}, E4={ws_g['E4'].value!r}")
check("4: KIT3 в строке 5 → «План (не загружен)» в D5, не в D4",
      ws_g["D5"].value == "План (не загружен)",
      f"получено «{ws_g['D5'].value}»")
check("4: шифр KIT3 остался в A5",
      ws_g["A5"].value == KIT3, f"получено «{ws_g['A5'].value}»")

# ---------------------------------------------------------------------------
#  5. Контент под таблицей не затирается (полоса сверху, строка 1)
# ---------------------------------------------------------------------------
seed_db()
out_note = export(build_source(note_below=True))
wb_n = openpyxl.load_workbook(io.BytesIO(out_note))
ws_n = wb_n["Комплекты"]
check("5: примечание под таблицей сохранено (A7)",
      ws_n["A7"].value == "Перевыпускаем 18.11. Выяснить что делаем с колодцами",
      f"получено «{ws_n['A7'].value}»")
check("5: полоса на месте (D1, над kit-колонками)",
      ws_n["D1"].value == "База актуальна на —")

# ---------------------------------------------------------------------------
#  6. Пустая таблица (только шапка, без данных)
# ---------------------------------------------------------------------------
seed_db()
out_empty = export(build_source(empty_data=True))
wb_e = openpyxl.load_workbook(io.BytesIO(out_empty))
ws_e = wb_e["Комплекты"]
check("6: полоса в строке 1 (D1)", ws_e["D1"].value == "База актуальна на —")
check("6: шапка в строке 2", ws_e["A2"].value == "Шифр РД")
check("6: автофильтр не ставится",
      ws_e.auto_filter.ref in (None, ""),
      f"получено «{ws_e.auto_filter.ref}»")
zf_e = zipfile.ZipFile(io.BytesIO(out_empty))
sheet_xml_e = zf_e.read("xl/worksheets/sheet1.xml").decode()
check("6: в XML нет элемента autoFilter",
      "<autoFilter" not in sheet_xml_e)

# ---------------------------------------------------------------------------
#  7. Широкий баннер над шапкой (merge шире таблицы данных)
# ---------------------------------------------------------------------------
seed_db()
out_banner = export(build_source(wide_banner=True))
wb_w = openpyxl.load_workbook(io.BytesIO(out_banner))
ws_w = wb_w["Комплекты"]
mr_w = [str(r) for r in ws_w.merged_cells.ranges]
check("7: нет пересекающихся merges",
      not merges_overlap(list(ws_w.merged_cells.ranges)), f"merges: {mr_w}")
check("7: баннер возвращён со сдвигом (A2:H2), полоса над kit-колонками I1:K1",
      "A2:H2" in mr_w and "I1:K1" in mr_w, f"merges: {mr_w}")
check("7: текст полосы в I1 — над шапкой kit-колонок",
      ws_w["I1"].value == "База актуальна на —",
      f"получено «{ws_w['I1'].value}»")
check("7: шапка в строке 3", ws_w["A3"].value == "Шифр РД",
      f"получено «{ws_w['A3'].value}»")
# баннер раздувает max_column до 8 → kit-колонки уходят на I/J/K
check("7: шапки kit-колонок в I3/J3/K3",
      (ws_w["I3"].value, ws_w["J3"].value, ws_w["K3"].value)
      == (KIT_STATUS_COL, KIT_REVISION_COL, KIT_REV_DATE_COL),
      f"получено {(ws_w['I3'].value, ws_w['J3'].value, ws_w['K3'].value)}")
check("7: kit-значения в фактических строках (I4/J4/K4)",
      (ws_w["I4"].value, ws_w["J4"].value, ws_w["K4"].value)
      == ("B", "4.0", "20.01.2026"),
      f"получено {(ws_w['I4'].value, ws_w['J4'].value, ws_w['K4'].value)}")
check("7: баннер не пересекается с полосой",
      not merges_overlap(list(ws_w.merged_cells.ranges)))

# ---------------------------------------------------------------------------
#  8. Таблица на другом листе — снимается только с целевого листа
# ---------------------------------------------------------------------------
seed_db()
out_two = export(build_source(extra_sheet_table=True))
wb_t = openpyxl.load_workbook(io.BytesIO(out_two))
check("8: таблица другого листа сохраняется",
      len(wb_t["Прочее"].tables) == 1,
      f"таблицы: {list(wb_t['Прочее'].tables)}")
check("8: на целевом листе таблиц нет",
      len(wb_t["Комплекты"].tables) == 0)

# ---------------------------------------------------------------------------
#  9. Регрессия: исходник БЕЗ ловушек — прежнее поведение
# ---------------------------------------------------------------------------
seed_db()
out_plain = export(build_source())
wb_p = openpyxl.load_workbook(io.BytesIO(out_plain))
ws_p = wb_p["Комплекты"]
check("9: без таблицы в выгрузке нет xl/tables/*.xml",
      table_parts(out_plain) == [])
check("9: шапка в строке 2", ws_p["A2"].value == "Шифр РД")
check("9: автофильтр A2:F5", ws_p.auto_filter.ref == "A2:F5")
check("9: статус KIT2 = «C»", ws_p["D4"].value == "C",
      f"получено «{ws_p['D4'].value}»")

# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки фиксов 22.2/22.3 пройдены.")
