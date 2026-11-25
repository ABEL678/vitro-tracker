#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Диагностика 22.14 — leaf-рассинхроны «папка vs имя файла».

ЗАДАЧА
------
Найти файлы (documents), у которых шифр комплекта, распознанный из папки
на SharePoint (parse_path по FileDirRef → looks_like_rd_code), НЕ совпадает
с шифром комплекта, извлечённым из имени файла (leaf).

Скрипт НИЧЕГО не меняет: ни код приложения, ни базу, ни SharePoint.
Живая база открывается ТОЛЬКО на чтение, анализ идёт по её копии
(sqlite3 backup API — консистентный снимок, включая WAL).

ИСПОЛЬЗУЕМЫЕ ФУНКЦИИ ПРОЕКТА (своей дублирующей логики нет)
------------------------------------------------------------
* vitro.sharepoint.parse_path          — шифр комплекта из папки (FileDirRef);
* vitro.sharepoint.normalize_complex   — нормализация шифра (как в синке);
* vitro.sheets.is_valid_kit_code       — шаблон шифра комплекта (патчи 18/19);
* vitro.text_utils.clean_leaf_key      — снятие «.pdf» и гомоглифов из имени.

ПРАВИЛО ИЗВЛЕЧЕНИЯ ШИФРА ИЗ ИМЕНИ ФАЙЛА
---------------------------------------
1. leaf → clean_leaf_key → normalize_complex (как синк хранит complex).
2. Имя считается продолжением шифра папки, если начинается с него и далее
   идёт разделитель «-», «_» или «.» (номер листа/тип приклеен к шифру).
   Такие строки — СИНХРОННЫ и в перечень рассинхронов не попадают.
3. Иначе шифр из имени = самый длинный шифр комплекта, который является
   началом имени и за которым идёт «-», «_», «.» (граница) — из справочника
   шифров проекта (папки documents.complex + complexes.code + plan_kits.code,
   все прошли is_valid_kit_code). Это то же решение, что принял бы человек.
4. Если в справочнике ничего не нашлось — канонический шаблон листа:
   блоки делятся по «-» и «_»; берётся префикс из 4–6 блоков, проходящий
   is_valid_kit_code (приоритет 5 блоков — обычный комплект, затем 6 —
   приложения «-СО»/«-ВР» и ГИС, затем 4). Это ровно те границы, которые
   задаёт _KIT_CODE_PATTERN в vitro/sheets.py.
5. Если шифр не извлекается ни одним способом — категория (в)
   «leaf не парсится в шифр», извлечение не выдумывается.

КАТЕГОРИИ
---------
(а) вариант: шифр из имени — вариант шифра папки (совпадают организация и
    марка — первые 3 блока, различаются индекс/хвост; либо блок папки
    является объединением через запятую);
(б) другой шифр: различаются организация/марка или структура;
(в) не извлекается: is_valid_kit_code не принимает ни один префикс
    (папки с запятой «КЖ1,2», реестровые PDF «Р-NNN» без шифра в имени).

ВЫХОД
-----
* scripts/diag_patch22_14_mismatches.csv   — машиночитаемая копия листа
  «Рассинхроны» (те же колонки, тот же порядок);
* scripts/diag_patch22_14_mismatches.xlsx  — три листа:
  «Рассинхроны» (шапка закреплена, автофильтр, шифры подсвечены жёлтым),
  «Сводка» (статистика), «Примеры» (сценарные группы).

Запуск:
    .venv\\Scripts\\python.exe scripts\\diag_patch22_14.py
"""

import csv
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

# UTF-8 в консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout = __import__("io").TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = __import__("io").TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vitro.sharepoint import normalize_complex, parse_path  # noqa: E402
from vitro.sheets import is_valid_kit_code  # noqa: E402
from vitro.text_utils import clean_leaf_key  # noqa: E402

DB_SRC = ROOT / "data" / "vitro.db"
OUT_CSV = ROOT / "scripts" / "diag_patch22_14_mismatches.csv"
OUT_XLSX = ROOT / "scripts" / "diag_patch22_14_mismatches.xlsx"

SEP = ("-", "_", ".")
CSV_HEADERS = ["dir_ref", "leaf", "Шифр из папки",
               "Шифр из имени", "complex в базе", "id"]

# Маркеры служебных деревьев — тот же набор, что в sharepoint._EXCLUDED_MARKERS
# плюс «на удаление» из патча 22.16.
SERVICE_MARKERS = (
    "TECT", "ТЕСТ", "TEST", "НА УДАЛЕНИЕ",
    "_PRPDF_", "_EXPPDF_", "_BACKUP", "_OLD", "_TRASH", "~$",
    "АННУЛИРОВАН", "АРХИВ", "ARCHIVE",
)


# ---------------------------------------------------------------------------
#  1. Консистентная копия базы (живая база — только чтение)
# ---------------------------------------------------------------------------
def copy_db_readonly() -> Path:
    """Копия data/vitro.db через sqlite3 backup API.

    Живая база открывается в режиме mode=ro, поэтому даже случайная запись
    невозможна. Копия нужна, чтобы не держать блокировки и чтобы снимок
    включал данные из WAL."""
    if not DB_SRC.exists():
        raise SystemExit(f"БД не найдена: {DB_SRC}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="vitro_diag_22_14_"))
    dst_path = tmp_dir / "vitro_copy.db"
    src = sqlite3.connect(f"file:{DB_SRC.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    print(f"Копия БД: {dst_path}")
    return dst_path


# ---------------------------------------------------------------------------
#  2. Извлечение шифра из имени файла
# ---------------------------------------------------------------------------
def stem_of(leaf) -> str:
    """Имя файла без «.pdf» + нормализация шифра — как хранит синк."""
    return normalize_complex(clean_leaf_key(leaf))


def blocks_of(stem: str) -> list:
    """Блоки имени: разделители «-» и «_» (в ГИС-именах листов есть «_»)."""
    out, cur = [], ""
    for ch in stem:
        if ch in "-_":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [b for b in out if b]


def known_prefix(stem: str, known: set) -> str:
    """Самый длинный известный шифр комплекта — начало имени с границей."""
    best = ""
    for kit in known:
        if len(kit) <= len(best):
            continue
        if stem == kit or any(stem.startswith(kit + s) for s in SEP):
            best = kit
    return best


def pattern_prefix(stem: str) -> str:
    """Канонический шаблон листа: префикс 4–6 блоков, валидный как шифр.

    Приоритет: 5 блоков (обычный комплект), затем 6 (приложения «-СО»/«-ВР»
    и ГИС), затем 4. Внутри одного размера действует is_valid_kit_code —
    тот же шаблон, что отсекает номера листов («…-1.1»)."""
    parts = blocks_of(stem)
    cands = []
    for n in range(4, min(6, len(parts)) + 1):
        cand = "-".join(parts[:n])
        if is_valid_kit_code(cand):
            cands.append((n, cand))
    if not cands:
        return ""
    for n in (5, 6, 4):
        for size, cand in cands:
            if size == n:
                return cand
    return cands[-1][1]


def is_continuation(stem: str, kit: str) -> bool:
    """Имя продолжает шифр папки (тот же комплект, дальше номер/тип листа)."""
    if not kit:
        return False
    if stem == kit:
        return True
    return any(stem.startswith(kit + s) for s in SEP)


def mismatch_type(folder: str, leaf_kit: str) -> str:
    """(а) вариант / (б) другой шифр — по блокам шифров."""
    fb, lb = folder.split("-"), leaf_kit.split("-")
    # объединение через запятую: блок папки «КЖ1,2» содержит блок имени
    if "," in folder:
        fset = set()
        for b in fb:
            fset.update(b.split(","))
        if any(b in fset for b in lb):
            return "а"
    # совпали организация и марка — различие в индексе/хвосте
    if fb[:3] == lb[:3]:
        return "а"
    return "б"


# ---------------------------------------------------------------------------
#  3. Сбор данных
# ---------------------------------------------------------------------------
def load_rows(db_path: Path) -> list:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, leaf, dir_ref, complex, discipline FROM documents"
        ).fetchall()
        known = set()
        for r in conn.execute(
                "SELECT DISTINCT complex FROM documents "
                "WHERE complex IS NOT NULL AND TRIM(complex) != ''"):
            n = normalize_complex(r[0])
            if n and is_valid_kit_code(n):
                known.add(n)
        for table in ("complexes", "plan_kits"):
            try:
                for r in conn.execute(f"SELECT code FROM {table}"):
                    n = normalize_complex(r[0])
                    if n and is_valid_kit_code(n):
                        known.add(n)
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return [dict(r) for r in rows], known


def collect(rows: list, known: set) -> dict:
    """Полный разбор: синхронно / рассинхрон (а, б) / не извлекается (в)."""
    folder_kits = set()
    leaf_kits_seen = defaultdict(set)   # имя файла (lower) → {шифры папок}
    for r in rows:
        folder = normalize_complex(parse_path(r.get("dir_ref") or "")[2])
        folder_kits.add(folder)
        leaf_kits_seen[str(r.get("leaf") or "").strip().lower()].add(folder)

    mismatches, unparsed = [], []
    dot_family = []          # имя = шифр папки + «.» + номер/тип листа
    service = []
    consistent = 0

    for r in rows:
        leaf = r.get("leaf") or ""
        dir_ref = r.get("dir_ref") or ""
        folder = normalize_complex(parse_path(dir_ref)[2])
        stem = stem_of(leaf)
        upper_dir = dir_ref.upper()
        is_service = any(m in upper_dir for m in SERVICE_MARKERS)

        if is_service:
            service.append({
                "id": r.get("id"), "leaf": leaf, "dir_ref": dir_ref,
                "folder_kit": folder, "complex": r.get("complex"),
                "discipline": (r.get("discipline") or "").strip(),
                "leaf_kit": "", "type": "служебное дерево",
            })
            continue

        if is_continuation(stem, folder):
            # отдельно копим «точечные» имена: полный шифр листа из них
            # не собирается шаблоном (дефис перед номером листа заменён «.»)
            if stem != folder and stem.startswith(folder + "."):
                dot_family.append({
                    "id": r.get("id"), "leaf": leaf, "dir_ref": dir_ref,
                    "folder_kit": folder, "leaf_kit": folder,
                })
            consistent += 1
            continue

        leaf_kit = known_prefix(stem, known) or pattern_prefix(stem)
        rec = {
            "id": r.get("id"),
            "leaf": leaf,
            "dir_ref": dir_ref,
            "folder_kit": folder,
            "leaf_kit": leaf_kit,
            "complex": r.get("complex"),
            "discipline": (r.get("discipline") or "").strip(),
        }
        if leaf_kit:
            rec["type"] = mismatch_type(folder, leaf_kit)
            mismatches.append(rec)
        else:
            rec["type"] = "в"
            unparsed.append(rec)

    return {
        "mismatches": mismatches,
        "unparsed": unparsed,
        "dot_family": dot_family,
        "service": service,
        "consistent": consistent,
        "folder_kits": folder_kits,
        "leaf_kits_seen": leaf_kits_seen,
    }


# ---------------------------------------------------------------------------
#  4. Статистика и сценарии
# ---------------------------------------------------------------------------
def build_stats(data: dict, rows: list) -> dict:
    mism = data["mismatches"]
    types = Counter(m["type"] for m in mism)
    folder_kits = data["folder_kits"]
    leaf_seen = data["leaf_kits_seen"]

    # «переезд»: шифр из имени — реальная папка-комплект (есть в базе)
    for m in mism:
        m["copy_flag"] = len(leaf_seen[m["leaf"].strip().lower()]) > 1
        m["move_flag"] = (m["leaf_kit"] in folder_kits
                          and m["leaf_kit"] != m["folder_kit"])
        m["naming_flag"] = not m["move_flag"]

    top_folders = []
    for folder, cnt in Counter(m["folder_kit"] for m in mism).most_common(10):
        examples = [m for m in mism if m["folder_kit"] == folder][:3]
        top_folders.append((folder, cnt, examples))

    disciplines = Counter(m["discipline"] or "(пусто)" for m in mism)

    dup_names = {name: kits for name, kits in leaf_seen.items()
                 if len(kits) > 1}
    dup_in_mismatch = [m for m in mism
                       if len(leaf_seen[m["leaf"].strip().lower()]) > 1]

    # Контрольная (формальная) проверка «первые 5 блоков» — для сверки
    # с ориентиром ~767: она ловит и 6-блочные ГИС, и «точечные» имена.
    formal = 0
    for r in rows:
        folder = normalize_complex(parse_path(r.get("dir_ref") or "")[2])
        stem = stem_of(r.get("leaf") or "")
        if folder and "-".join(blocks_of(stem)[:5]) != folder:
            formal += 1

    # (в) по причинам
    unparsed_groups = Counter()
    for u in data["unparsed"]:
        if u["leaf"].strip().upper().startswith("Р-"):
            unparsed_groups["реестровый PDF «Р-NNN» (без шифра в имени)"] += 1
        elif "," in u["folder_kit"]:
            unparsed_groups["папка с запятой «КЖ1,2» (is_valid_kit_code отклоняет)"] += 1
        else:
            unparsed_groups["прочее"] += 1

    return {
        "types": types,
        "top_folders": top_folders,
        "disciplines": disciplines,
        "dup_names": dup_names,
        "dup_in_mismatch": dup_in_mismatch,
        "formal": formal,
        "unparsed_groups": unparsed_groups,
    }


def scenario_candidates(mism: list, flag: str) -> list:
    """Строки сценария; для «переезда» сначала те, что НЕ копии."""
    items = [m for m in mism if m.get(flag)]
    if flag == "move_flag":
        items.sort(key=lambda m: bool(m.get("copy_flag")))
    return items


def scenario_examples(mism: list, flag: str, limit: int = 5) -> list:
    """До limit примеров сценария, по возможности из разных папок."""
    items = scenario_candidates(mism, flag)
    out, used_folders = [], set()
    for m in items:
        if m["folder_kit"] in used_folders:
            continue
        out.append(m)
        used_folders.add(m["folder_kit"])
        if len(out) >= limit:
            break
    if len(out) < limit:  # добираем, если папок мало
        for m in items:
            if m not in out:
                out.append(m)
                if len(out) >= limit:
                    break
    return out


# ---------------------------------------------------------------------------
#  5. CSV и XLSX
# ---------------------------------------------------------------------------
def write_csv(mism: list) -> None:
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CSV_HEADERS)
        for m in mism:
            w.writerow([m["dir_ref"], m["leaf"], m["folder_kit"],
                        m["leaf_kit"], m["complex"], m["id"]])


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
SECTION_FONT = Font(bold=True, size=12, color="1F4E78")
YELLOW = PatternFill("solid", fgColor="FFFF00")
THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")


def _write_header(ws, row: int, headers: list) -> int:
    for col, title in enumerate(headers, 1):
        c = ws.cell(row=row, column=col, value=title)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = WRAP
        c.border = BORDER
    return row + 1


def _autofit(ws, widths_limit: dict) -> None:
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        longest = 0
        for row in range(1, ws.max_row + 1):
            v = ws.cell(row=row, column=col).value
            if v is None:
                continue
            longest = max(longest, max(
                (len(line) for line in str(v).splitlines()), default=0))
        cap = widths_limit.get(col, 45)
        ws.column_dimensions[letter].width = min(max(longest + 2, 10), cap)


def write_xlsx(mism: list, stats: dict, data: dict, rows: list) -> None:
    wb = Workbook()

    # ---------------- Лист «Рассинхроны» ----------------
    ws = wb.active
    ws.title = "Рассинхроны"
    r = _write_header(ws, 1, CSV_HEADERS)
    for m in mism:
        ws.cell(row=r, column=1, value=m["dir_ref"]).alignment = WRAP
        ws.cell(row=r, column=2, value=m["leaf"]).alignment = WRAP
        c_folder = ws.cell(row=r, column=3, value=m["folder_kit"])
        c_leaf = ws.cell(row=r, column=4, value=m["leaf_kit"])
        ws.cell(row=r, column=5, value=m["complex"]).alignment = WRAP
        ws.cell(row=r, column=6, value=m["id"])
        if m["folder_kit"] != m["leaf_kit"]:
            c_folder.fill = YELLOW
            c_leaf.fill = YELLOW
        for col in range(1, 7):
            ws.cell(row=r, column=col).border = BORDER
        r += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{max(ws.max_row, 1)}"
    _autofit(ws, {1: 70, 2: 55, 3: 32, 4: 32, 5: 34, 6: 10})

    # ---------------- Лист «Сводка» ----------------
    ws = wb.create_sheet("Сводка")
    r = 1

    def section(title):
        nonlocal r
        r += 1
        c = ws.cell(row=r, column=1, value=title)
        c.font = SECTION_FONT
        r += 1

    def kv(label, value):
        nonlocal r
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        ws.cell(row=r, column=2, value=value)
        r += 1

    def table(headers, data_rows, limits=None):
        nonlocal r
        r = _write_header(ws, r, headers)
        for row in data_rows:
            for col, v in enumerate(row, 1):
                c = ws.cell(row=r, column=col, value=v)
                c.alignment = WRAP
                c.border = BORDER
            r += 1

    ws.cell(row=1, column=1, value="Диагностика 22.14 — leaf-рассинхроны"
            ).font = Font(bold=True, size=14, color="1F4E78")

    section("1. Общие цифры")
    kv("Строк documents проанализировано", len(rows))
    kv("Шифров-папок (уникальных)", len(data["folder_kits"]))
    kv("Синхронно (имя продолжает шифр папки)", data["consistent"])
    kv("РАССИНХРОНОВ (оба шифра извлечены и различаются)", len(mism))
    kv("(в) шифр из имени не извлекается", len(data["unparsed"]))
    kv("Точечные имена «шифр.номер» (не рассинхрон)", len(data["dot_family"]))
    kv("Служебные деревья (ТЕСТ/на удаление/архив)", len(data["service"]))

    section("2. Разбивка рассинхронов по типу")
    table(["Тип", "Количество"], [
        ["(а) вариант шифра папки", stats["types"].get("а", 0)],
        ["(б) полностью другой шифр", stats["types"].get("б", 0)],
        ["(в) имя не парсится в шифр", len(data["unparsed"])],
    ])
    if stats["unparsed_groups"]:
        r += 1
        ws.cell(row=r, column=1, value="Причины (в):").font = Font(bold=True)
        r += 1
        table(["Причина", "Количество"],
              [[k, v] for k, v in stats["unparsed_groups"].most_common()])
    if data["unparsed"]:
        r += 1
        ws.cell(row=r, column=1,
                value="Шифры папок категории (в):").font = Font(bold=True)
        r += 1
        unp_by_folder = Counter(u["folder_kit"] for u in data["unparsed"])
        table(["Шифр папки", "Файлов", "Пример имени"], [
            [folder, cnt,
             next(u["leaf"] for u in data["unparsed"]
                  if u["folder_kit"] == folder)]
            for folder, cnt in unp_by_folder.most_common()
        ])

    section("3. Топ-10 папок по числу рассинхронов")
    rows_top = []
    for folder, cnt, examples in stats["top_folders"]:
        rows_top.append([folder, cnt,
                         "; ".join(e["leaf"] for e in examples)])
    table(["Шифр папки", "Рассинхронов", "Примеры (3 файла)"], rows_top)

    section("4. Распределение по дисциплинам")
    table(["Дисциплина", "Рассинхронов"],
          [[k, v] for k, v in stats["disciplines"].most_common()])

    section("5. Одинаковые имена файлов в разных комплектах")
    kv("Уникальных имён, встречающихся в 2+ комплектах (по всей базе)",
       len(stats["dup_names"]))
    kv("Из них в перечне рассинхронов", len(stats["dup_in_mismatch"]))

    section("6. Сценарии (гипотезы, признаки могут сочетаться)")
    kv("Скопированные листы (имя есть в 2+ комплектах)",
       sum(1 for m in mism if m["copy_flag"]))
    kv("Переезд (шифр из имени — существующий комплект базы)",
       sum(1 for m in mism if m["move_flag"]))
    kv("Ошибка именования (шифра из имени нет среди комплектов)",
       sum(1 for m in mism if m["naming_flag"]))
    kv("Переезд без признака копии", sum(
        1 for m in mism if m["move_flag"] and not m["copy_flag"]))

    section("7. Служебные деревья")
    if data["service"]:
        kv("Найдено рассинхронов в служебных деревьях", len(data["service"]))
    else:
        kv("Рассинхронов в ТЕСТ/«на удаление»/архивах", "не найдено (0)")
        kv("Проверялись маркеры", ", ".join(SERVICE_MARKERS))

    section("8. Контроль: почему цифра отличается от ориентира ~767")
    kv("Формальная проверка «первые 5 блоков ≠ папка»", stats["formal"])
    kv("Из них ложные: 6-блочные ГИС (…-С-00), «шифр.номер», «_»",
       stats["formal"] - len(mism) - len(data["dot_family"]))
    kv("«Точечные» имена (шифр папки + «.» + номер листа)",
       len(data["dot_family"]))
    kv("Точный перечень (этот отчёт)", len(mism))
    r += 1
    ws.cell(row=r, column=1, value=(
        "Пояснение: формальное сравнение «первых 5 блоков» даёт ~712 строк, "
        "но большая часть — не рассинхрон комплекта: у 6-блочных ГИС-шифров "
        "(ГИС-РД-СБ-ТСО-1-00) пятый блок обрывает шифр, а в «точечных» "
        "именах (АТ-РД-ВК-С-00.ЗД.01-2) номер листа приклеен через точку, "
        "хотя комплект тот же.")).alignment = WRAP
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    ws.row_dimensions[r].height = 60
    _autofit(ws, {1: 75, 2: 30, 3: 60, 4: 30})

    # ---------------- Лист «Примеры» ----------------
    ws = wb.create_sheet("Примеры")
    r = _write_header(ws, 1, ["Сценарий", "dir_ref", "leaf",
                              "Шифр из папки", "Шифр из имени", "id"])
    scenarios = [
        ("Скопированные листы: одно имя файла в нескольких комплектах",
         "copy_flag"),
        ("Переезд: шифр из имени — существующий комплект в базе",
         "move_flag"),
        ("Ошибка именования: шифра из имени нет среди комплектов базы",
         "naming_flag"),
    ]
    for idx, (title, flag) in enumerate(scenarios, 1):
        total = len(scenario_candidates(mism, flag))
        caption = f"{idx}. {title} — всего {total}"
        c = ws.cell(row=r, column=1, value=caption)
        c.font = SECTION_FONT
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        r += 1
        for m in scenario_examples(mism, flag):
            values = [title, m["dir_ref"], m["leaf"],
                      m["folder_kit"], m["leaf_kit"], m["id"]]
            for col, v in enumerate(values, 1):
                c = ws.cell(row=r, column=col, value=v)
                c.alignment = WRAP
                c.border = BORDER
                if col in (4, 5) and m["folder_kit"] != m["leaf_kit"]:
                    c.fill = YELLOW
            r += 1
        r += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{max(ws.max_row, 1)}"
    _autofit(ws, {1: 45, 2: 70, 3: 55, 4: 32, 5: 32, 6: 10})

    wb.save(OUT_XLSX)


# ---------------------------------------------------------------------------
#  6. Печать агрегатов в консоль
# ---------------------------------------------------------------------------
def print_report(mism, stats, data, rows) -> None:
    print()
    print("=" * 78)
    print("ДИАГНОСТИКА 22.14 — LEAF-РАССИНХРОНЫ «ПАПКА vs ИМЯ ФАЙЛА»")
    print("=" * 78)
    print(f"Строк documents:                 {len(rows)}")
    print(f"Шифров-папок:                    {len(data['folder_kits'])}")
    print(f"Синхронно:                       {data['consistent']}")
    print(f"РАССИНХРОНОВ:                    {len(mism)}")
    print(f"(в) не извлекается:              {len(data['unparsed'])}")
    print(f"Точечных имён (не рассинхрон):   {len(data['dot_family'])}")
    print(f"Служебные деревья:               {len(data['service'])}")
    print()
    print("Типы: а) вариант — {0}; б) другой шифр — {1}; в) не парсится — {2}"
          .format(stats["types"].get("а", 0),
                  stats["types"].get("б", 0), len(data["unparsed"])))
    print()
    print("Топ-10 папок:")
    for folder, cnt, examples in stats["top_folders"]:
        print(f"  {cnt:>4}  {folder}")
        for e in examples:
            print(f"          · {e['leaf']}  →  {e['leaf_kit']}")
    print()
    print("Дисциплины:")
    for k, v in stats["disciplines"].most_common():
        print(f"  {k:<12} {v}")
    print()
    print(f"Имён, встречающихся в 2+ комплектах: {len(stats['dup_names'])} "
          f"(в перечне — {len(stats['dup_in_mismatch'])})")
    print(f"Сценарии: копии — {sum(1 for m in mism if m['copy_flag'])}; "
          f"переезд — {sum(1 for m in mism if m['move_flag'])}; "
          f"ошибка именования — {sum(1 for m in mism if m['naming_flag'])}")
    print(f"Контроль «первые 5 блоков ≠ папка»: {stats['formal']}")
    print()
    print(f"CSV:  {OUT_CSV}")
    print(f"XLSX: {OUT_XLSX}")


def main() -> None:
    tmp_db = copy_db_readonly()
    try:
        rows, known = load_rows(tmp_db)
        print(f"Строк documents: {len(rows)}, шифров в справочнике: {len(known)}")
        data = collect(rows, known)
        stats = build_stats(data, rows)
        write_csv(data["mismatches"])
        write_xlsx(data["mismatches"], stats, data, rows)
        print_report(data["mismatches"], stats, data, rows)
    finally:
        shutil.rmtree(tmp_db.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
