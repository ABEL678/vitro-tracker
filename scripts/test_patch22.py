# scripts/test_patch22.py
"""Тесты патча 22 «Ревизия комплекта и Дата ревизии во внешнем Excel».

Блок 1: get_kit_revision_info (_rev_key / _format_rev_date /
        _kit_revision_from_rows) — максимум ревизии (числовое
        сравнение «2.0» < «10.0»), формат даты ДД.ММ.ГГГГ, ревизия
        из ПОСЛЕДНЕЙ ревизии листа (дедуп), «И»/«АННУЛИРОВАНО»
        учитываются, латинские двойники шифра, отсутствующий
        комплект, одиночный и батчевый вызов, регрессия
        get_kit_statuses (патч 21 не сломан).
Блок 2: _apply_kit_statuses(..., rev_info) — три kit-колонки в
        порядке Статус → Ревизия → Дата, исходный df не мутируется,
        пустой шифр / отсутствующий комплект → «».
Блок 3: _xlsx_add_kit_status — объединённая ячейка «База актуальна
        на …(МСК)» в строке 1, шапка в строке 2, светло-голубая
        заливка D9E1F2 у шапок трёх kit-колонок, тонкие границы у
        всех ячеек шапки и данных, центрирование kit-колонок,
        автофильтр A2:…, чтение синтетического Excel «Шифр РД».

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db (путь
читается при импорте модуля).

Запуск: python scripts/test_patch22.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* (СТОП-ЛИСТ патчей 21/22) ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sheets import (  # noqa: E402
    KIT_STATUS_NOT_LOADED,
    _format_rev_date,
    _kit_revision_from_rows,
    _rev_key,
    get_kit_revision_info,
    get_kit_status,
    get_kit_statuses,
)
from vitro.ui.export import (  # noqa: E402
    KIT_REV_DATE_COL,
    KIT_REVISION_COL,
    KIT_STATUS_COL,
    _apply_kit_statuses,
    _find_kit_code_column,
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


# ---------------------------------------------------------------------------
#  Подготовка: временная БД documents
# ---------------------------------------------------------------------------
def seed_documents(rows) -> None:
    """rows: (id, leaf, complex, status, sheet_number, status_date,
    revision)."""
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM documents")
        conn.executemany(
            "INSERT INTO documents (id, leaf, complex, status, "
            "sheet_number, status_date, revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )


KIT = "АТ-РД-КЖ1-1-00"
KIT2 = "АТ-РД-ОВ2-С-00"        # латинские двойники
KIT_ABSENT = "АТ-РД-ХХ9-9-99"  # заведомо отсутствует в БД


# ---------------------------------------------------------------------------
#  БЛОК 1.1 — _rev_key: числовое сравнение ревизий
# ---------------------------------------------------------------------------
check("_rev_key: «2.0» < «10.0» (числовое сравнение)",
      _rev_key("2.0") < _rev_key("10.0"),
      f"{_rev_key('2.0')} vs {_rev_key('10.0')}")
check("_rev_key: «10.0» == 10.0", _rev_key("10.0") == 10.0)
check("_rev_key: пробелы игнорируются", _rev_key(" 3.0 ") == 3.0)
check("_rev_key: пустое → -1.0", _rev_key("") == -1.0)
check("_rev_key: None → -1.0", _rev_key(None) == -1.0)
check("_rev_key: «И» (нечисловое) → -1.0", _rev_key("И") == -1.0)

# ---------------------------------------------------------------------------
#  БЛОК 1.2 — _format_rev_date: ISO → ДД.ММ.ГГГГ
# ---------------------------------------------------------------------------
check("дата: ISO с временем → 19.11.2026",
      _format_rev_date("2026-11-19T11:46:00") == "19.11.2026")
check("дата: ISO без времени → 01.06.2025",
      _format_rev_date("2025-06-01") == "01.06.2025")
check("дата: пустая → ''", _format_rev_date("") == "")
check("дата: None → ''", _format_rev_date(None) == "")
check("дата: мусор → ''", _format_rev_date("не дата") == "")

# ---------------------------------------------------------------------------
#  БЛОК 1.3 — _kit_revision_from_rows: чистое ядро
# ---------------------------------------------------------------------------
rev, dt = _kit_revision_from_rows([
    {"revision": "2.0", "status_date": "2025-03-01T10:00:00"},
    {"revision": "10.0", "status_date": "2026-01-15T09:00:00"},
    {"revision": "1.0", "status_date": "2024-12-31"},
])
check("ядро: max ревизия «10.0», не «2.0» (числовое сравнение)",
      rev == "10.0", f"получено «{rev}»")
check("ядро: max дата 15.01.2026", dt == "15.01.2026", f"получено «{dt}»")

rev, dt = _kit_revision_from_rows([
    {"revision": "", "status_date": None},
    {"revision": "И", "status_date": "мусор"},
])
check("ядро: нет числовой ревизии → ''", rev == "")
check("ядро: нет валидной даты → ''", dt == "")

rev, dt = _kit_revision_from_rows([])
check("ядро: пустой список → ('', '')", (rev, dt) == ("", ""))

rev, dt = _kit_revision_from_rows([
    {"revision": "5.0", "status_date": "2025-05-05"},
])
check("ядро: одна строка → («5.0», «05.05.2025»)",
      (rev, dt) == ("5.0", "05.05.2025"))

# ---------------------------------------------------------------------------
#  БЛОК 1.4 — get_kit_revision_info: расчёт по БД (unique_vitro_sheets)
# ---------------------------------------------------------------------------
# KIT: три листа; у листа «1» две ревизии (старая 1.0, свежая 10.0) —
# дедуп оставляет последнюю по (status_date, id), значит «10.0».
# Крайняя дата — у листа «3» (статус «И»), 03.03.2026.
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "A", "1", "2025-01-10", "1.0"),
    (2, f"{KIT}-1.PDF", KIT, "A", "1", "2026-01-20", "10.0"),
    (3, f"{KIT}-2.PDF", KIT, "B", "2", "2025-06-15", "2.0"),
    (4, f"{KIT}-3.PDF", KIT, "И", "3", "2026-03-03", ""),
])
info = get_kit_revision_info(KIT)[KIT]
check("БД: max ревизия комплекта 10.0",
      info["revision"] == "10.0", f"получено «{info['revision']}»")
check("БД: крайняя дата 03.03.2026 (лист «И» учитывается)",
      info["date"] == "03.03.2026", f"получено «{info['date']}»")
check("БД: одиночный вызов возвращает dict с двумя ключами",
      set(info.keys()) == {"revision", "date"})

# ревизия листа — из ПОСЛЕДНЕЙ ревизии, а не максимум по всем строкам:
# у листа «1» старая строка имеет ревизию 99.0, свежая — 2.0
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "B", "1", "2025-01-10", "99.0"),
    (2, f"{KIT}-1.PDF", KIT, "B", "1", "2026-01-10", "2.0"),
])
info = get_kit_revision_info(KIT)[KIT]
check("БД: ревизия из ПОСЛЕДНЕЙ ревизии листа (2.0, не 99.0)",
      info["revision"] == "2.0", f"получено «{info['revision']}»")
check("БД: дата — по последней выгрузке листа (10.01.2026)",
      info["date"] == "10.01.2026", f"получено «{info['date']}»")

# «И»/«АННУЛИРОВАНО» учитываются в rev/date (информационные поля,
# согласовано на плане патча 22)
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "АННУЛИРОВАНО", "1", "2026-05-01", "7.0"),
])
info = get_kit_revision_info(KIT)[KIT]
check("БД: АННУЛИРОВАНО учитывается в ревизии (7.0)",
      info["revision"] == "7.0")
check("БД: АННУЛИРОВАНО учитывается в дате (01.05.2026)",
      info["date"] == "01.05.2026")

# пустая ревизия у всех листов → '', дата при этом считается
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "A", "1", "2025-06-01", ""),
])
info = get_kit_revision_info(KIT)[KIT]
check("БД: нет числовых ревизий → ''", info["revision"] == "")
check("БД: дата при пустой ревизии считается (01.06.2025)",
      info["date"] == "01.06.2025")

# латинские двойники шифра
seed_documents([
    (1, f"{KIT2}-1.PDF", KIT2, "A", "1", "2025-06-01", "3.0"),
])
latin_mix = "AT-PД-OB2-C-00"  # латиница: A, T, P, O, B, C; кириллица: Д
info = get_kit_revision_info(latin_mix)[KIT2]
check("БД: латинская запись шифра → та же ревизия",
      info["revision"] == "3.0")

# отсутствующий комплект → пустые строки
info = get_kit_revision_info(KIT_ABSENT)[KIT_ABSENT]
check("БД: отсутствующий комплект → пустые строки",
      info == {"revision": "", "date": ""})

# пустые шифры пропускаются, батч работает
info = get_kit_revision_info(["", None, KIT2])
check("БД: пустые шифры пропускаются",
      info == {KIT2: {"revision": "3.0", "date": "01.06.2025"}})

# codes=None → все комплекты базы; батч == codes=None
seed_documents([
    (1, f"{KIT}-1.PDF", KIT, "A", "1", "2025-06-01", "4.0"),
    (2, f"{KIT2}-1.PDF", KIT2, "B", "1", "2025-07-01", "5.0"),
])
all_info = get_kit_revision_info()
check("БД: codes=None → все комплекты",
      set(all_info.keys()) == {KIT, KIT2})
check("БД: батч == codes=None по общим ключам",
      get_kit_revision_info([KIT, KIT2]) == all_info)

# ---------------------------------------------------------------------------
#  БЛОК 1.5 — регрессия патча 21: get_kit_statuses не сломан
# ---------------------------------------------------------------------------
sts = get_kit_statuses()
check("регрессия 21: get_kit_statuses()[KIT] == «A»", sts[KIT] == "A")
check("регрессия 21: get_kit_statuses()[KIT2] == «B»", sts[KIT2] == "B")
check("регрессия 21: get_kit_status(KIT) == get_kit_statuses()[KIT]",
      get_kit_status(KIT) == sts[KIT])
check("регрессия 21: отсутствующий комплект → «План (не загружен)»",
      get_kit_statuses(KIT_ABSENT)
      == {KIT_ABSENT: KIT_STATUS_NOT_LOADED})

# ---------------------------------------------------------------------------
#  БЛОК 2 — _apply_kit_statuses(..., rev_info): три kit-колонки
# ---------------------------------------------------------------------------
df_src = pd.DataFrame({
    "Шифр РД": [KIT, KIT_ABSENT, "", None],
    "Название": ["Комплект КЖ1", "Нет в Витро", "", ""],
})
statuses = get_kit_statuses([KIT, KIT_ABSENT])
rev_info = get_kit_revision_info([KIT, KIT_ABSENT])
out = _apply_kit_statuses(df_src, "Шифр РД", statuses, rev_info)

check("колонки: исходные две первые",
      list(out.columns)[:2] == ["Шифр РД", "Название"])
check("порядок колонок: Статус → Ревизия → Дата",
      list(out.columns)[2:] == [KIT_STATUS_COL, KIT_REVISION_COL,
                                KIT_REV_DATE_COL])
check("статус найденного комплекта", out[KIT_STATUS_COL].iloc[0] == "A")
check("ревизия найденного комплекта",
      out[KIT_REVISION_COL].iloc[0] == "4.0")
check("дата найденного комплекта (ДД.ММ.ГГГГ)",
      out[KIT_REV_DATE_COL].iloc[0] == "01.06.2025")
check("отсутствующий комплект: статус «План (не загружен)»",
      out[KIT_STATUS_COL].iloc[1] == KIT_STATUS_NOT_LOADED)
check("отсутствующий комплект: ревизия ''",
      out[KIT_REVISION_COL].iloc[1] == "")
check("отсутствующий комплект: дата ''",
      out[KIT_REV_DATE_COL].iloc[1] == "")
check("пустой шифр: статус дефолт",
      out[KIT_STATUS_COL].iloc[2] == KIT_STATUS_NOT_LOADED)
check("пустой шифр: ревизия ''", out[KIT_REVISION_COL].iloc[2] == "")
check("None-шифр: дата ''", out[KIT_REV_DATE_COL].iloc[3] == "")
check("исходный df не мутировался (без kit-колонок)",
      list(df_src.columns) == ["Шифр РД", "Название"])

# обратная совместимость: без rev_info — только статус (патч 21)
out21 = _apply_kit_statuses(df_src, "Шифр РД", statuses)
check("обратная совместимость: без rev_info одна kit-колонка",
      list(out21.columns)[2:] == [KIT_STATUS_COL])

# ---------------------------------------------------------------------------
#  БЛОК 3 — _xlsx_add_kit_status: стилизованная выгрузка
# ---------------------------------------------------------------------------
# Синтетический «внешний перечень» → колонки → стилизованный Excel
buf = io.BytesIO()
wb = openpyxl.Workbook()
ws_src = wb.active
ws_src.append(["Наименование", "Шифр РД"])
ws_src.append(["Комплект КЖ1", KIT])
ws_src.append(["Комплект ОВ2", KIT2])
ws_src.append(["Нет в Витро", KIT_ABSENT])
wb.save(buf)
buf.seek(0)
df_up = pd.read_excel(buf)

col = _find_kit_code_column(df_up)
check("синтетический Excel: колонка шифров найдена", col == "Шифр РД")
statuses = get_kit_statuses([KIT, KIT2, KIT_ABSENT])
rev_info = get_kit_revision_info([KIT, KIT2, KIT_ABSENT])
result = _apply_kit_statuses(df_up, col, statuses, rev_info)

xlsx_bytes = _xlsx_add_kit_status(
    result, sync_ts="2026-11-19T11:46:00", sheet_name="Комплекты")
check("выгрузка: bytes непустые",
      isinstance(xlsx_bytes, bytes) and len(xlsx_bytes) > 0)

wb2 = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
ws = wb2.active
n_cols = len(result.columns)
n_rows = len(result)
last_letter = openpyxl.utils.get_column_letter(n_cols)
kit_idx = sorted(result.columns.get_loc(c) + 1
                 for c in (KIT_STATUS_COL, KIT_REVISION_COL,
                           KIT_REV_DATE_COL))
other_idx = [i for i in range(1, n_cols + 1) if i not in kit_idx]


def has_thin_border(c) -> bool:
    b = c.border
    return all(getattr(b, s).style == "thin"
               for s in ("left", "right", "top", "bottom"))


# строка 1 — объединённая ячейка с датой актуальности базы
merged = [str(r) for r in ws.merged_cells.ranges]
check(f"строка 1: объединена во всю ширину (A1:{last_letter}1)",
      f"A1:{last_letter}1" in merged, f"merged={merged}")
check("строка 1: текст «База актуальна на 19.11.2026, 11:46 (МСК)»",
      ws["A1"].value == "База актуальна на 19.11.2026, 11:46 (МСК)",
      f"получено «{ws['A1'].value}»")
check("строка 1: жирный шрифт", ws["A1"].font.bold is True)
check("строка 1: выравнивание по центру",
      ws["A1"].alignment.horizontal == "center")
check("строка 1: заливка как полоса даты (E8F0F8)",
      str(ws["A1"].fill.start_color.rgb)[-6:] == "E8F0F8",
      f"получено {ws['A1'].fill.start_color.rgb}")
# Рамка объединённой ячейки: openpyxl сохраняет для не-анкерных
# ячеек диапазона только контурные стороны (top/bottom) — их и
# проверяем, плюс левый край у A1 и правый у последней колонки.
check("строка 1: рамка по всей объединённой полосе",
      all(ws.cell(row=1, column=i).border.top.style == "thin"
          and ws.cell(row=1, column=i).border.bottom.style == "thin"
          for i in range(1, n_cols + 1))
      and ws.cell(row=1, column=1).border.left.style == "thin"
      and ws.cell(row=1, column=n_cols).border.right.style == "thin")

# строка 2 — шапка таблицы
check("шапка в строке 2",
      ws.cell(row=2, column=1).value == result.columns[0])
check("шапка: жирный шрифт у всех колонок",
      all(ws.cell(row=2, column=i).font.bold
          for i in range(1, n_cols + 1)))
check("шапка: границы у всех колонок",
      all(has_thin_border(ws.cell(row=2, column=i))
          for i in range(1, n_cols + 1)))
check("шапка: светло-голубая заливка D9E1F2 у трёх kit-колонок",
      all(str(ws.cell(row=2, column=i).fill.start_color.rgb)[-6:]
          == "D9E1F2" for i in kit_idx),
      f"kit_idx={kit_idx}")
check("шапка: прочие колонки без заливки kit-цвета",
      all(str(ws.cell(row=2, column=i).fill.start_color.rgb)[-6:]
          != "D9E1F2" for i in other_idx))

# данные — границы у всех, kit-колонки по центру
check("данные: тонкие границы у всех ячеек",
      all(has_thin_border(ws.cell(row=r, column=i))
          for r in range(3, 3 + n_rows) for i in range(1, n_cols + 1)))
check("данные: kit-колонки по центру",
      all(ws.cell(row=r, column=i).alignment.horizontal == "center"
          for r in range(3, 3 + n_rows) for i in kit_idx))
check("данные: прочие колонки — без центрирования",
      all(ws.cell(row=3, column=i).alignment.horizontal != "center"
          for i in other_idx))

# автофильтр по всей таблице (шапка — строка 2)
check(f"автофильтр A2:{last_letter}{n_rows + 2}",
      ws.auto_filter.ref == f"A2:{last_letter}{n_rows + 2}",
      f"получено {ws.auto_filter.ref}")

# значения данных на месте (строки 3–5: KIT, KIT2, KIT_ABSENT)
c_stat = result.columns.get_loc(KIT_STATUS_COL) + 1
c_rev = result.columns.get_loc(KIT_REVISION_COL) + 1
c_date = result.columns.get_loc(KIT_REV_DATE_COL) + 1
check("данные: статус KIT == «A»",
      ws.cell(row=3, column=c_stat).value == "A")
check("данные: ревизия KIT == «4.0»",
      ws.cell(row=3, column=c_rev).value == "4.0")
check("данные: дата KIT == «01.06.2025»",
      ws.cell(row=3, column=c_date).value == "01.06.2025")
check("данные: дата KIT2 == «01.07.2025»",
      ws.cell(row=4, column=c_date).value == "01.07.2025")
check("данные: отсутствующий комплект → статус «План (не загружен)»",
      ws.cell(row=5, column=c_stat).value == KIT_STATUS_NOT_LOADED)
check("данные: отсутствующий комплект → пустая ревизия",
      ws.cell(row=5, column=c_rev).value in ("", None))

# ширины колонок заданы
check("ширины колонок заданы",
      all((ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width
           or 0) > 0 for i in range(1, n_cols + 1)))

# нет синков → «База актуальна на —»
xlsx_empty = _xlsx_add_kit_status(result, sync_ts=None)
wb3 = openpyxl.load_workbook(io.BytesIO(xlsx_empty))
check("sync_ts=None → «База актуальна на —»",
      wb3.active["A1"].value == "База актуальна на —",
      f"получено «{wb3.active['A1'].value}»")

# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22 пройдены.")
