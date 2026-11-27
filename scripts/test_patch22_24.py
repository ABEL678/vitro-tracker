#!/usr/bin/env python3
"""
Тесты для патча 22.24 — «СО и ВОР»: UI-фиксы, маппинг полей, перечень СВОК.

Проверяет:
A1. Фильтр дисциплины/раздела меняет суммы бубликов; согласованность
    бублика и таблицы.
A2. Переключатель подвкладок «Основная» / «Сервисная».
A3. Развёрнутые названия блоков без иконок, краткие «СО»/«ВОР» в группах.
A4. Легенда статусов — общий компонент проекта (_pie_with_legend).
B.  send_date/workflow_id из записи-победителя; null → пустые ячейки.
C1. Парсинг мини-копии СВОК (шапка в строке 13, сводный блок сверху).
C1. Идемпотентность загрузки перечня: повтор не дублирует, исчезнувший
    комплект удаляется.
C2. Плановые бублики: План СО = все комплекты; План ВОР = без
    «Не выдается»; «Не выдается» с записью ВР не попадает в план.
C3/C4. Строки «Не загружен» и фильтр по ним.

Запуск:
    .venv\\Scripts\\python.exe scripts\\test_patch22_24.py
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from openpyxl import Workbook

from vitro.ui.so_vr import (
    KIND_SO, KIND_VR, KIND_TITLES, SHEET_STATUS_COLORS,
    _apply_filters, _build_sovr_export_rows, _build_sovr_table,
    _enrich_svok_excel, _export_sovr_excel, _filter_loaded_by_location,
    _filter_template, _fmt_date, _fmt_workflow, _location_codes,
    _parse_svok_workbook, _plan_donut_counts, _sovr_enriched_preview,
    _sovr_export_filename, _status_donut_data,
)


# ===========================================================================
#  A1 — фильтр дисциплины режет бублики и таблицу
# ===========================================================================
class TestDisciplineAffectsDonuts(unittest.TestCase):
    """A1: сумма статусного бублика = число отфильтрованных листов."""

    def setUp(self):
        self.df = pd.DataFrame([
            {"complex": "АТ-РД-ЭО1-С-00", "sheet_number": "СО", "status": "B",
             "send_date": "2026-11-01", "status_date": "2026-11-02",
             "workflow_id": 1, "revision": "01", "name": "1", "doc_id": 1,
             "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-ЭО2-С-00", "sheet_number": "СО", "status": "A",
             "send_date": "2026-11-01", "status_date": "2026-11-03",
             "workflow_id": 2, "revision": "01", "name": "2", "doc_id": 2,
             "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-ЭО3-С-00", "sheet_number": "СО", "status": "C",
             "send_date": "2026-11-01", "status_date": "2026-11-04",
             "workflow_id": 3, "revision": "01", "name": "3", "doc_id": 3,
             "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-КЖ1-С-00", "sheet_number": "ВР", "status": "A",
             "send_date": "2026-11-01", "status_date": "2026-11-05",
             "workflow_id": None, "revision": "01", "name": "4", "doc_id": 4,
             "discipline": "02_КР", "section": "КЖ"},
        ])

    def _flt(self, **kw):
        base = {"kits": (), "block_codes": (), "status": (),
                "date_from": None, "date_to": None}
        base.update(kw)
        return base

    def test_no_filter_full_showcase(self):
        filtered = _apply_filters(self.df, self._flt())
        data = _status_donut_data(filtered, KIND_SO)
        self.assertEqual(int(data["Количество"].sum()), 3)

    def test_discipline_reduces_donut(self):
        filtered = _apply_filters(self.df, self._flt(disc=("03_ЭС",)))
        data = _status_donut_data(filtered, KIND_SO)
        self.assertEqual(int(data["Количество"].sum()), 3)
        filtered_kr = _apply_filters(self.df, self._flt(disc=("02_КР",)))
        self.assertEqual(int(_status_donut_data(filtered_kr, KIND_SO)
                             ["Количество"].sum()), 0)
        self.assertEqual(int(_status_donut_data(filtered_kr, KIND_VR)
                             ["Количество"].sum()), 1)

    def test_donut_matches_table(self):
        flt = self._flt(disc=("02_КР",))
        filtered = _apply_filters(self.df, flt)
        table = _build_sovr_table(filtered, flt)
        donut_total = int(_status_donut_data(filtered, KIND_VR)
                          ["Количество"].sum())
        self.assertEqual(donut_total, len(table))


# ===========================================================================
#  A2 — подвкладки
# ===========================================================================
class TestSubtabs(unittest.TestCase):
    """A2: в render() есть переключатель «Основная» / «Сервисная»."""

    def test_render_has_tabs(self):
        src = (Path(__file__).resolve().parent.parent
               / "vitro" / "ui" / "so_vr.py").read_text(encoding="utf-8")
        self.assertIn('st.tabs(["Основная", "Сервисная"])', src)
        self.assertIn("def render_service", src)
        # по умолчанию первая (Основная) — порядок в списке
        self.assertLess(src.find('"Основная"'), src.find('"Сервисная"'))


# ===========================================================================
#  A3/A4 — названия блоков и легенда
# ===========================================================================
class TestTitlesAndLegend(unittest.TestCase):
    """A3/A4: развёрнутые названия, краткие группы, общая легенда."""

    def test_full_titles(self):
        self.assertEqual(KIND_TITLES[KIND_SO],
                         "Спецификация оборудования и материалов")
        self.assertEqual(KIND_TITLES[KIND_VR],
                         "Ведомость объемов работ")
        for v in KIND_TITLES.values():
            self.assertNotIn("🍩", v)
            self.assertNotIn("📑", v)

    def test_legend_component_reused(self):
        from vitro.ui.deadlines import _pie_with_legend
        self.assertTrue(callable(_pie_with_legend))
        src = (Path(__file__).resolve().parent.parent
               / "vitro" / "ui" / "so_vr.py").read_text(encoding="utf-8")
        self.assertIn("_pie_with_legend", src)
        self.assertNotIn("🍩", src)

    def test_status_colors_present(self):
        self.assertEqual(len(SHEET_STATUS_COLORS), 4)


# ===========================================================================
#  B — маппинг send_date/workflow_id
# ===========================================================================
class TestFieldsMapping(unittest.TestCase):
    """B: поля берутся из записи-победителя, null → пусто."""

    def test_winner_has_same_record_fields(self):
        from vitro.sqlite_db import _sovr_latest_rows
        rows = [
            {"id": 1, "complex": "АТ-РД-КЖ1-С-00", "sheet_number": "СО",
             "status": "C", "status_date": "2025-10-01",
             "send_date": "2025-09-01", "workflow_id": 111},
            {"id": 2, "complex": "АТ-РД-КЖ1-С-00", "sheet_number": "СО",
             "status": "A", "status_date": "2025-10-10",
             "send_date": "2025-10-09", "workflow_id": 222},
        ]
        best = _sovr_latest_rows(rows)
        self.assertEqual(len(best), 1)
        self.assertEqual(best[0]["id"], 2)
        self.assertEqual(best[0]["send_date"], "2025-10-09")
        self.assertEqual(best[0]["workflow_id"], 222)

    def test_same_date_max_id_fields(self):
        from vitro.sqlite_db import _sovr_latest_rows
        rows = [
            {"id": 10, "complex": "X", "sheet_number": "ВР",
             "status": "B", "status_date": "2025-10-01",
             "send_date": "2025-09-01", "workflow_id": 10},
            {"id": 11, "complex": "X", "sheet_number": "ВР",
             "status": "A", "status_date": "2025-10-01",
             "send_date": "2025-09-02", "workflow_id": 11},
        ]
        best = _sovr_latest_rows(rows)
        self.assertEqual(best[0]["id"], 11)
        self.assertEqual(best[0]["send_date"], "2025-09-02")
        self.assertEqual(best[0]["workflow_id"], 11)

    def test_null_formatting(self):
        self.assertEqual(_fmt_workflow(None), "")
        self.assertEqual(_fmt_workflow("None"), "")
        self.assertEqual(_fmt_workflow(float("nan")), "")
        self.assertEqual(_fmt_workflow(23412), "23412")
        self.assertEqual(_fmt_workflow(23412.0), "23412")
        self.assertEqual(_fmt_date(None), "—")
        self.assertEqual(_fmt_date("2026-11-03T14:02:28Z"), "03.11.2026")
        self.assertEqual(_fmt_date("не дата"), "")

    def test_end_to_end_vitrine(self):
        """documents → documents_sovr: поля победителя не теряются."""
        import vitro.sqlite_db as db_mod
        from vitro.sqlite_db import get_conn, init_db, load_sovr_statuses

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        old = db_mod.DB_FULL_PATH
        try:
            db_mod.DB_FULL_PATH = Path(tmp_db)
            init_db()
            with get_conn() as conn:
                cols = {r["name"] for r in conn.execute(
                    "PRAGMA table_info(documents)")}
                self.assertIn("send_date", cols)
                self.assertIn("workflow_id", cols)
                conn.execute("""
                    INSERT INTO documents
                        (id, leaf, complex, sheet_number, status,
                         status_date, send_date, workflow_id)
                    VALUES
                        (1, 'a.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'C',
                         '2025-10-01', '2025-09-01', 111),
                        (2, 'a.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'A',
                         '2025-10-10', '2025-10-09', 222)
                """)
            n = load_sovr_statuses("test")
            self.assertEqual(n, 1)
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT doc_id, status, send_date, workflow_id "
                    "FROM documents_sovr").fetchone()
            self.assertEqual(row["doc_id"], 2)
            self.assertEqual(row["status"], "A")
            self.assertEqual(row["send_date"], "2025-10-09")
            self.assertEqual(row["workflow_id"], 222)
        finally:
            db_mod.DB_FULL_PATH = old
            os.unlink(tmp_db)


# ===========================================================================
#  C1 — парсер перечня СВОК
# ===========================================================================
def _create_svok_file(rows: list[dict]) -> bytes:
    """Мини-копия СВОК: сводный блок 3–8, шапка 13, данные с 16."""
    wb = Workbook()
    ws = wb.active
    ws.title = "СВОК"

    for r in range(3, 9):
        ws.cell(row=r, column=1, value=f"Сводная строка {r}")
        ws.cell(row=r, column=3, value=f"Итого {r}")

    headers = {
        3: "Шифр РД",
        4: "Дисциплина (Группа)",
        5: "Раздел",
        6: "Марка",
        7: "Наименование основного комплекта рабочих чертежей",
        8: "Здание, Сооружение, Корпус",
        20: "Текущий статус ВОР",
    }
    for col, title in headers.items():
        ws.cell(row=13, column=col, value=title)

    start = 16
    for i, row in enumerate(rows):
        r = start + i
        ws.cell(row=r, column=1, value=f"строка {r}")
        ws.cell(row=r, column=3, value=row["code"])
        ws.cell(row=r, column=4, value=row.get("disc", ""))
        ws.cell(row=r, column=5, value=row.get("section", ""))
        ws.cell(row=r, column=6, value=row.get("marka", ""))
        ws.cell(row=r, column=7, value=row.get("title", ""))
        ws.cell(row=r, column=8, value=row.get("location", ""))
        ws.cell(row=r, column=20, value=row.get("vor", ""))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestSvokParser(unittest.TestCase):
    """C1: шапка в строке 13, сводный блок не мешает, T — признак ВОР."""

    def test_parse_mini_copy(self):
        data = _create_svok_file([
            {"code": "АТ-РД-КЖ1-С-00", "disc": "02_КР", "section": "КЖ",
             "marka": "КЖ1", "title": "Конструкции",
             "location": "С (Стилобат)", "vor": "В работе"},
            {"code": "АТ-РД-ЭО1-С-00", "disc": "03_ЭС", "section": "ЭОМ",
             "marka": "ЭО1", "title": "Электроосвещение",
             "location": "1 (Корпус 1)", "vor": "Не выдается"},
            {"code": "АТ-РД-ОВ1-С-00", "disc": "04_МЕХ", "section": "ОВ",
             "marka": "ОВ1", "title": "Отопление",
             "location": "ГП,АД (Генеральный план, УСД)", "vor": ""},
        ])
        parsed = _parse_svok_workbook(data, "test.xlsx")
        self.assertEqual(parsed["header_row"], 13)
        self.assertEqual(parsed["sheet"], "СВОК")
        self.assertEqual(len(parsed["rows"]), 3)

        by_code = {r["kit_code"]: r for r in parsed["rows"]}
        self.assertIn("АТ-РД-КЖ1-С-00", by_code)
        self.assertEqual(by_code["АТ-РД-КЖ1-С-00"]["discipline"], "02_КР")
        self.assertEqual(by_code["АТ-РД-КЖ1-С-00"]["section"], "КЖ")
        self.assertEqual(by_code["АТ-РД-КЖ1-С-00"]["marka"], "КЖ1")
        self.assertEqual(by_code["АТ-РД-КЖ1-С-00"]["title"], "Конструкции")
        self.assertEqual(by_code["АТ-РД-КЖ1-С-00"]["location"],
                         "С (Стилобат)")
        self.assertEqual(by_code["АТ-РД-ЭО1-С-00"]["location"],
                         "1 (Корпус 1)")
        self.assertEqual(by_code["АТ-РД-КЖ1-С-00"]["vor_not_issued"], 0)
        self.assertEqual(by_code["АТ-РД-ЭО1-С-00"]["vor_not_issued"], 1)
        self.assertEqual(parsed["not_issued"], 1)

    def test_parse_case_insensitive_not_issued(self):
        data = _create_svok_file([
            {"code": "АТ-РД-КЖ1-С-00", "vor": "  НЕ ВЫДАЁТСЯ "},
        ])
        parsed = _parse_svok_workbook(data, "test.xlsx")
        self.assertEqual(parsed["rows"][0]["vor_not_issued"], 1)

    def test_parse_skips_summary_and_garbage(self):
        data = _create_svok_file([
            {"code": "ИТОГО", "title": "не шифр"},
            {"code": "ат-рд-кж1-с-00", "title": "нижний регистр"},
        ])
        parsed = _parse_svok_workbook(data, "test.xlsx")
        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["rows"][0]["kit_code"], "АТ-РД-КЖ1-С-00")

    def test_no_svok_sheet(self):
        wb = Workbook()
        wb.active.title = "Лист1"
        buf = io.BytesIO()
        wb.save(buf)
        parsed = _parse_svok_workbook(buf.getvalue(), "x.xlsx")
        self.assertEqual(parsed["rows"], [])
        self.assertTrue(parsed["warnings"])


# ===========================================================================
#  B — upsert документов сервисной кнопкой (comments не трогаются)
# ===========================================================================
class TestUpsertDocuments(unittest.TestCase):
    """Сервисная кнопка сохраняет документы, не удаляя замечания."""

    def test_upsert_updates_fields_and_keeps_comments(self):
        import vitro.sqlite_db as db_mod
        from vitro.sqlite_db import get_conn, init_db, upsert_documents

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        old = db_mod.DB_FULL_PATH
        try:
            db_mod.DB_FULL_PATH = Path(tmp_db)
            init_db()
            doc = {
                "id": 1, "leaf": "a.pdf", "complex": "АТ-РД-КЖ1-С-00",
                "sheet_number": "СО", "status": "C",
                "status_date": "2025-10-01", "send_date": "2025-09-01",
                "workflow_id": 111, "revision": "01", "name": "a",
                "discipline": "02_КР", "section": "КЖ", "dir_ref": "/x",
                "vitro_guid": "g", "vitro_version": 1,
            }
            self.assertEqual(upsert_documents([doc]), 1)
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO comments (id, doc_id, comment, status) "
                    "VALUES (10, 1, 'замечание', 'Новое')")

            doc2 = dict(doc)
            doc2.update({"status": "A", "status_date": "2025-10-10",
                         "send_date": "2025-10-09", "workflow_id": 222})
            self.assertEqual(upsert_documents([doc2]), 1)

            with get_conn() as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM documents").fetchone()[0]
                row = conn.execute(
                    "SELECT status, send_date, workflow_id FROM documents "
                    "WHERE id = 1").fetchone()
                comments = conn.execute(
                    "SELECT COUNT(*) FROM comments").fetchone()[0]
            self.assertEqual(cnt, 1)  # не дублирует
            self.assertEqual(row["status"], "A")
            self.assertEqual(row["send_date"], "2025-10-09")
            self.assertEqual(row["workflow_id"], 222)
            self.assertEqual(comments, 1)  # замечания целы
        finally:
            db_mod.DB_FULL_PATH = old
            os.unlink(tmp_db)


# ===========================================================================
#  C1 — идемпотентность загрузки перечня
# ===========================================================================
class TestTemplateIdempotency(unittest.TestCase):
    """C1: повтор обновляет, исчезнувший комплект удаляется."""

    def test_replace_twice(self):
        import vitro.sqlite_db as db_mod
        from vitro.sqlite_db import (
            get_conn, init_db, load_sovr_template, replace_sovr_template)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        old = db_mod.DB_FULL_PATH
        try:
            db_mod.DB_FULL_PATH = Path(tmp_db)
            init_db()

            first = [
                {"kit_code": "АТ-РД-КЖ1-С-00", "title": "КЖ1",
                 "discipline": "02_КР", "section": "КЖ", "marka": "КЖ1",
                 "location": "С (Стилобат)", "vor_not_issued": 0},
                {"kit_code": "АТ-РД-ЭО1-С-00", "title": "ЭО1",
                 "discipline": "03_ЭС", "section": "ЭОМ", "marka": "ЭО1",
                 "location": "1 (Корпус 1)", "vor_not_issued": 1},
                {"kit_code": "АТ-РД-ОВ1-С-00", "title": "ОВ1",
                 "discipline": "04_МЕХ", "section": "ОВ", "marka": "ОВ1",
                 "location": "ГП,АД (Генеральный план, УСД)",
                 "vor_not_issued": 0},
            ]
            r1 = replace_sovr_template(first, source="f1.xlsx")
            self.assertEqual(r1["kits"], 3)
            self.assertEqual(r1["not_issued"], 1)

            df1 = load_sovr_template()
            self.assertEqual(len(df1), 3)
            self.assertIn("location", df1.columns)
            loc1 = df1[df1["kit_code"] == "АТ-РД-КЖ1-С-00"].iloc[0]
            self.assertEqual(loc1["location"], "С (Стилобат)")
            created = dict(zip(df1["kit_code"], df1["created_at"]))

            # Повтор: без ОВ1, с новым статусом «Не выдается» у КЖ1
            second = [dict(r) for r in first[:2]]
            second[0]["vor_not_issued"] = 1
            r2 = replace_sovr_template(second, source="f2.xlsx")
            self.assertEqual(r2["kits"], 2)
            self.assertEqual(r2["removed"], 1)
            self.assertEqual(r2["not_issued"], 2)

            df2 = load_sovr_template()
            self.assertEqual(sorted(df2["kit_code"]),
                             ["АТ-РД-КЖ1-С-00", "АТ-РД-ЭО1-С-00"])
            # created_at сохраняется у оставшихся
            kept = df2[df2["kit_code"] == "АТ-РД-КЖ1-С-00"].iloc[0]
            self.assertEqual(kept["created_at"], created["АТ-РД-КЖ1-С-00"])

            # Повторная загрузка того же состава не дублирует
            r3 = replace_sovr_template(second, source="f2.xlsx")
            self.assertEqual(r3["kits"], 2)
            self.assertEqual(r3["added"], 0)
            self.assertEqual(r3["removed"], 0)
            with get_conn() as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM sheets_sovr_template").fetchone()[0]
            self.assertEqual(cnt, 2)
        finally:
            db_mod.DB_FULL_PATH = old
            os.unlink(tmp_db)


# ===========================================================================
#  C2 — плановые бублики
# ===========================================================================
class TestPlanDonutCounts(unittest.TestCase):
    """C2: План СО — все; План ВОР — без «Не выдается»."""

    def setUp(self):
        self.template = pd.DataFrame([
            {"kit_code": "A", "vor_not_issued": 0},
            {"kit_code": "B", "vor_not_issued": 1},
            {"kit_code": "C", "vor_not_issued": 0},
        ])
        self.loaded = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО"},
            {"complex": "A", "sheet_number": "ВР"},
            {"complex": "B", "sheet_number": "ВР"},
            {"complex": "Z", "sheet_number": "СО"},  # вне перечня
        ])

    def test_so_plan_all_kits(self):
        c = _plan_donut_counts(self.template, self.loaded, KIND_SO)
        self.assertTrue(c["has_template"])
        self.assertEqual(c["plan"], 3)
        self.assertEqual(c["loaded_total"], 2)       # A и Z
        self.assertEqual(c["loaded_in_plan"], 1)     # A
        self.assertEqual(c["remaining"], 2)          # B, C
        self.assertEqual(c["extra"], 1)              # Z

    def test_vr_plan_excludes_not_issued(self):
        c = _plan_donut_counts(self.template, self.loaded, KIND_VR)
        self.assertEqual(c["plan"], 2)               # A, C
        self.assertEqual(c["loaded_in_plan"], 1)     # A

    def test_not_issued_with_vr_not_in_plan(self):
        """B помечен «Не выдается», но запись ВР есть — в план не входит."""
        c = _plan_donut_counts(self.template, self.loaded, KIND_VR)
        self.assertEqual(c["plan"], 2)
        loaded_in_plan_codes = {"A"}  # B не должен попасть в план
        self.assertEqual(c["loaded_in_plan"], len(loaded_in_plan_codes))

    def test_no_template(self):
        c = _plan_donut_counts(pd.DataFrame(), self.loaded, KIND_SO)
        self.assertFalse(c["has_template"])
        self.assertEqual(c["plan"], 0)
        self.assertEqual(c["loaded_total"], 2)


# ===========================================================================
#  C3/C4 — строки «Не загружен»
# ===========================================================================
class TestNotLoadedRows(unittest.TestCase):
    """C3/C4: «Не загружен» в таблице и фильтре."""

    def setUp(self):
        self.df = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО", "status": "A",
             "send_date": "2026-01-01", "status_date": "2026-01-02",
             "workflow_id": 1, "revision": "01", "name": "a", "doc_id": 1,
             "discipline": "03_ЭС", "section": "ЭОМ"},
        ])
        self.template = pd.DataFrame([
            {"kit_code": "A", "title": "A", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "A", "vor_not_issued": 0},
            {"kit_code": "B", "title": "B", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "B", "vor_not_issued": 0},
            {"kit_code": "C", "title": "C", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "C", "vor_not_issued": 1},
        ])

    def _flt(self, **kw):
        base = {"kits": (), "block_codes": (), "status": (),
                "date_from": None, "date_to": None}
        base.update(kw)
        return base

    def test_not_loaded_row_has_empty_fields(self):
        table = _build_sovr_table(self.df, self._flt(),
                                  self.template, self.df)
        row = table[(table["complex"] == "B") &
                    (table["sheet_number"] == "СО")].iloc[0]
        self.assertEqual(row["status"], "Не загружен")
        # pandas хранит пустые как NaN — отображение обязано давать «—»/«»
        self.assertTrue(pd.isna(row["send_date"]))
        self.assertTrue(pd.isna(row["status_date"]))
        self.assertTrue(pd.isna(row["workflow_id"]))
        self.assertTrue(pd.isna(row["revision"]))
        self.assertEqual(_fmt_workflow(row["workflow_id"]), "")
        self.assertEqual(_fmt_date(row["send_date"]), "—")

    def test_vr_not_issued_no_not_loaded_row(self):
        table = _build_sovr_table(self.df, self._flt(),
                                  self.template, self.df)
        vr_rows = table[(table["sheet_number"] == "ВР") &
                        (table["status"] == "Не загружен")]
        self.assertNotIn("C", set(vr_rows["complex"]))
        self.assertIn("B", set(vr_rows["complex"]))

    def test_filter_not_loaded_returns_rows(self):
        flt = self._flt(status=("Не загружен",))
        real = _apply_filters(self.df, flt)
        table = _build_sovr_table(real, flt, self.template, self.df)
        self.assertFalse(table.empty)
        self.assertTrue((table["status"] == "Не загружен").all())

    def test_no_status_filter_includes_all(self):
        table = _build_sovr_table(self.df, self._flt(),
                                  self.template, self.df)
        statuses = set(table["status"])
        self.assertIn("A", statuses)
        self.assertIn("Не загружен", statuses)


# ===========================================================================
#  22.24b — «Загружено / План» под фильтрами
# ===========================================================================
class TestPlanDonutFilters(unittest.TestCase):
    """22.24b: план и загруженное — от одного отфильтрованного набора.

    Реагируют на дисциплину/раздел/расположение/комплект/марку,
    НЕ реагируют на «Статус листа» и период. Расположение — из колонки
    СВОК «Здание, Сооружение, Корпус», а не из 4-го блока шифра.
    """

    def setUp(self):
        self.template = pd.DataFrame([
            {"kit_code": "АТ-РД-ЭО1-1-00", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "ЭО1",
             "location": "1 (Корпус 1)", "vor_not_issued": 0},
            {"kit_code": "АТ-РД-ЭО2-1-00", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "ЭО2",
             "location": "1 (Корпус 1)", "vor_not_issued": 0},
            {"kit_code": "АТ-РД-КЖ1-2-00", "discipline": "02_КР",
             "section": "КЖ", "marka": "КЖ1",
             "location": "2 (Корпус 2)", "vor_not_issued": 0},
            {"kit_code": "АТ-РД-КЖ2-С-00", "discipline": "02_КР",
             "section": "КЖ", "marka": "КЖ2",
             "location": "С (Стилобат)", "vor_not_issued": 1},
            {"kit_code": "АТ-РД-АД1-А-00", "discipline": "02_КР",
             "section": "КЖ", "marka": "АД1",
             "location": "ГП,АД (Генеральный план, УСД)",
             "vor_not_issued": 0},
            # Шифр говорит «С», а СВОК — «Корпус 1»: фильтр обязан
            # использовать колонку СВОК (22.24b).
            {"kit_code": "АТ-РД-ХХ1-С-00", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "ХХ1",
             "location": "1 (Корпус 1)", "vor_not_issued": 0},
        ])
        self.showcase = pd.DataFrame([
            {"complex": "АТ-РД-ЭО1-1-00", "sheet_number": "СО",
             "status": "A", "send_date": "2026-01-10",
             "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-ЭО2-1-00", "sheet_number": "СО",
             "status": "B", "send_date": "2026-02-10",
             "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-КЖ1-2-00", "sheet_number": "ВР",
             "status": "A", "send_date": "2026-01-15",
             "discipline": "02_КР", "section": "КЖ"},
            {"complex": "АТ-РД-КЖ2-С-00", "sheet_number": "ВР",
             "status": "C", "send_date": "2026-02-15",
             "discipline": "02_КР", "section": "КЖ"},
            {"complex": "АТ-РД-ХХ1-С-00", "sheet_number": "СО",
             "status": "A", "send_date": "2026-01-12",
             "discipline": "03_ЭС", "section": "ЭОМ"},
            # вне перечня (4-й блок шифра = 2)
            {"complex": "АТ-РД-ЭО9-2-00", "sheet_number": "СО",
             "status": "A", "send_date": "2026-01-20",
             "discipline": "03_ЭС", "section": "ЭОМ"},
        ])

    def _flt(self, **kw):
        base = {"disc": (), "section": (), "kits": (), "block_codes": (),
                "marka": "", "status": (), "date_from": None,
                "date_to": None}
        base.update(kw)
        return base

    def _counts(self, kind: str, **kw):
        """Повторяет логику _render_main для планового бублика."""
        flt = self._flt(**kw)
        plan_flt = {**flt, "status": (), "date_from": None, "date_to": None}
        loaded = _apply_filters(
            self.showcase, {**plan_flt, "block_codes": ()},
            apply_period=False)
        loaded = _filter_loaded_by_location(
            loaded, plan_flt["block_codes"], self.template)
        tpl = _filter_template(self.template, plan_flt)
        all_codes = set(self.template["kit_code"])
        return _plan_donut_counts(tpl, loaded, kind, all_codes)

    def test_no_filter(self):
        c = self._counts(KIND_SO)
        self.assertTrue(c["has_template"])
        self.assertEqual(c["plan"], 6)
        self.assertEqual(c["loaded_total"], 4)      # A, B, F, Z
        self.assertEqual(c["loaded_in_plan"], 3)    # A, B, F
        self.assertEqual(c["remaining"], 3)         # C, D, E
        self.assertEqual(c["extra"], 1)             # Z

        vr = self._counts(KIND_VR)
        self.assertEqual(vr["plan"], 5)             # без D («Не выдается»)
        self.assertEqual(vr["loaded_total"], 2)     # C, D
        self.assertEqual(vr["loaded_in_plan"], 1)   # D вне плана
        self.assertEqual(vr["extra"], 0)

    def test_discipline_and_section_recalculate(self):
        c = self._counts(KIND_SO, disc=("03_ЭС",))
        self.assertEqual(c["plan"], 3)              # A, B, F
        self.assertEqual(c["loaded_in_plan"], 3)    # A, B, F
        self.assertEqual(c["extra"], 1)             # Z (03_ЭС)
        kr = self._counts(KIND_SO, disc=("02_КР",))
        self.assertEqual(kr["plan"], 3)             # C, D, E
        self.assertEqual(kr["loaded_in_plan"], 0)   # у 02_КР только ВР
        sec = self._counts(KIND_VR, section=("ЭОМ",))
        self.assertEqual(sec["plan"], 3)            # A, B, F
        self.assertEqual(sec["loaded_in_plan"], 0)

    def test_marka_and_kit_recalculate(self):
        m = self._counts(KIND_SO, marka="ЭО1")
        self.assertEqual(m["plan"], 1)
        self.assertEqual(m["loaded_in_plan"], 1)
        k = self._counts(KIND_SO, kits=("АТ-РД-ЭО2-1-00",))
        self.assertEqual(k["plan"], 1)
        self.assertEqual(k["loaded_in_plan"], 1)

    def test_kit_outside_template_only_extra(self):
        c = self._counts(KIND_SO, kits=("АТ-РД-ЭО9-2-00",))
        self.assertTrue(c["has_template"])          # полный перечень есть
        self.assertEqual(c["plan"], 0)
        self.assertEqual(c["loaded_in_plan"], 0)
        self.assertEqual(c["extra"], 1)

    def test_status_and_period_do_not_change_plan(self):
        base = self._counts(KIND_SO)
        by_status = self._counts(KIND_SO, status=("A",))
        by_period = self._counts(
            KIND_SO, date_from=pd.Timestamp("2099-01-01").date(),
            date_to=pd.Timestamp("2099-12-31").date())
        for c in (by_status, by_period):
            self.assertEqual(c["plan"], base["plan"])
            self.assertEqual(c["loaded_total"], base["loaded_total"])
            self.assertEqual(c["loaded_in_plan"], base["loaded_in_plan"])
            self.assertEqual(c["extra"], base["extra"])

    def test_not_issued_never_in_vr_plan(self):
        c = self._counts(KIND_VR, kits=("АТ-РД-КЖ2-С-00",))
        self.assertEqual(c["plan"], 0)              # «Не выдается»
        self.assertEqual(c["loaded_total"], 1)      # запись ВР есть
        self.assertEqual(c["loaded_in_plan"], 0)
        self.assertEqual(c["extra"], 0)             # не «сверх перечня»

        by_disc = self._counts(KIND_VR, disc=("02_КР",))
        self.assertEqual(by_disc["plan"], 2)        # C, E (без D)
        self.assertEqual(by_disc["loaded_in_plan"], 1)  # C

    def test_location_from_svok_not_cipher(self):
        # F: шифр «С», СВОК «1 (Корпус 1)» → должен попасть в «Корпус 1»
        c1 = self._counts(KIND_SO, block_codes=("1",))
        self.assertEqual(c1["plan"], 3)             # A, B, F
        self.assertEqual(c1["loaded_in_plan"], 3)   # A, B, F
        self.assertEqual(c1["extra"], 0)            # Z (блок 2)
        # «АД» в СВОК соответствует коду фильтра «А»
        ca = self._counts(KIND_SO, block_codes=("А",))
        self.assertEqual(ca["plan"], 1)             # E
        # «С» — только D (F имеет СВОК «Корпус 1»)
        cs = self._counts(KIND_SO, block_codes=("С",))
        self.assertEqual(cs["plan"], 1)             # D
        self.assertEqual(cs["loaded_in_plan"], 0)   # у D запись только ВР

    def test_location_codes_parser(self):
        self.assertEqual(_location_codes("С (Стилобат)"), {"С"})
        self.assertEqual(_location_codes("1 (Корпус 1)"), {"1"})
        self.assertEqual(_location_codes("ГП,АД (Генплан, УСД)"),
                         {"ГП", "А"})
        self.assertEqual(_location_codes(None), set())
        self.assertEqual(_location_codes(""), set())


# ===========================================================================
#  22.24b — витрина СО/ВР в регулярном синке
# ===========================================================================
class TestSovrSyncStep(unittest.TestCase):
    """Шаг синка: fetch → upsert_documents → load_sovr_statuses.

    Идемпотентен, не дублирует записи; сбой fetch/шага логируется и
    не прерывает остальные сущности синка.
    """

    DOCS = [
        {"id": 1, "leaf": "a.pdf", "complex": "АТ-РД-КЖ1-С-00",
         "sheet_number": "СО", "status": "C", "status_date": "2025-10-01",
         "send_date": "2025-09-01", "workflow_id": 111,
         "revision": "01", "name": "a", "discipline": "02_КР",
         "section": "КЖ"},
        {"id": 2, "leaf": "a.pdf", "complex": "АТ-РД-КЖ1-С-00",
         "sheet_number": "СО", "status": "A", "status_date": "2025-10-10",
         "send_date": "2025-10-09", "workflow_id": 222,
         "revision": "02", "name": "a", "discipline": "02_КР",
         "section": "КЖ"},
        {"id": 3, "leaf": "b.pdf", "complex": "АТ-РД-КЖ1-С-00",
         "sheet_number": "ВР", "status": "B", "status_date": "2025-10-05",
         "send_date": "2025-10-04", "workflow_id": 333,
         "revision": "01", "name": "b", "discipline": "02_КР",
         "section": "КЖ"},
    ]

    def setUp(self):
        import vitro.sqlite_db as db_mod
        self.db_mod = db_mod
        fd, self.tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.tmp_db)
        self.old_path = db_mod.DB_FULL_PATH
        db_mod.DB_FULL_PATH = Path(self.tmp_db)
        db_mod.init_db()

    def tearDown(self):
        self.db_mod.DB_FULL_PATH = self.old_path
        try:
            os.unlink(self.tmp_db)
        except OSError:
            pass

    def _counts(self):
        with self.db_mod.get_conn() as conn:
            docs = conn.execute(
                "SELECT COUNT(*) FROM documents").fetchone()[0]
            sovr = conn.execute(
                "SELECT COUNT(*) FROM documents_sovr").fetchone()[0]
            row = conn.execute(
                "SELECT status, send_date, workflow_id, updated_by "
                "FROM documents_sovr WHERE sheet_number = 'СО'").fetchone()
        return docs, sovr, row

    def test_step_updates_and_is_idempotent(self):
        from vitro.sync import refresh_sovr_statuses

        with patch("vitro.sync.fetch_atp_documents",
                   return_value=list(self.DOCS)):
            r1 = refresh_sovr_statuses(verbose=False, user="sync")
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["documents"], 3)
        self.assertEqual(r1["sheets"], 2)            # СО + ВР
        docs, sovr, row = self._counts()
        self.assertEqual(docs, 3)
        self.assertEqual(sovr, 2)
        self.assertEqual(row["status"], "A")         # последний по процессу
        self.assertEqual(row["send_date"], "2025-10-09")
        self.assertEqual(row["workflow_id"], 222)
        self.assertEqual(row["updated_by"], "sync")

        # Повторный запуск не дублирует записи
        with patch("vitro.sync.fetch_atp_documents",
                   return_value=list(self.DOCS)):
            r2 = refresh_sovr_statuses(verbose=False, user="sync")
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["sheets"], 2)
        docs2, sovr2, _ = self._counts()
        self.assertEqual(docs2, 3)
        self.assertEqual(sovr2, 2)

    def test_step_accepts_docs_without_second_fetch(self):
        from vitro.sync import refresh_sovr_statuses

        with patch("vitro.sync.fetch_atp_documents",
                   side_effect=AssertionError("fetch не должен вызываться")):
            r = refresh_sovr_statuses(docs=list(self.DOCS), verbose=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["sheets"], 2)

    def test_fetch_failure_logged_not_raised(self):
        """Ручной путь кнопки: сбой fetch логируется, наружу не бросается."""
        from vitro.ui.so_vr import _refresh_sovr_manual

        with patch("vitro.sync.fetch_atp_documents",
                   side_effect=RuntimeError("Витро недоступно")):
            r = _refresh_sovr_manual()   # не должно бросить
        self.assertFalse(r["ok"])
        self.assertIn("Витро недоступно", r["error"])
        with self.db_mod.get_conn() as conn:
            row = conn.execute(
                "SELECT status, message FROM log WHERE event = 'sovr_sync' "
                "ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("Витро недоступно", row["message"])

    def test_step_failure_does_not_break_sync(self):
        """Сбой load_sovr_statuses не мешает документам/журналу синка."""
        from vitro.sync import refresh_data

        with patch("vitro.sync.fetch_atp_documents",
                   return_value=list(self.DOCS)), \
             patch("vitro.sync.fetch_atp_comments", return_value=[]), \
             patch("vitro.sync.fetch_comment_statuses", return_value={}), \
             patch("vitro.sync.fetch_authors", return_value={}), \
             patch("vitro.sync.fetch_complex_names", return_value=[]), \
             patch("vitro.sync.build_comments_summary", return_value=[]), \
             patch("vitro.sync.load_sovr_statuses",
                   side_effect=RuntimeError("db fail")):
            result = refresh_data(verbose=False)

        self.assertEqual(result["documents"], 3)
        self.assertFalse(result["sovr_ok"])
        self.assertEqual(result["sovr_sheets"], 0)
        # Документы и журнал синка записаны, несмотря на сбой шага.
        # Отдельного события sovr_sync синк НЕ пишет: итог шага витрины
        # складывается в единственное событие refresh.
        with self.db_mod.get_conn() as conn:
            n_docs = conn.execute(
                "SELECT COUNT(*) FROM documents").fetchone()[0]
            sovr_events = conn.execute(
                "SELECT COUNT(*) FROM log WHERE event = 'sovr_sync'"
            ).fetchone()[0]
            refresh_rows = conn.execute(
                "SELECT message FROM log WHERE event = 'refresh' "
                "AND status = 'OK'").fetchall()
        self.assertEqual(n_docs, 3)
        self.assertEqual(sovr_events, 0)
        self.assertEqual(len(refresh_rows), 1)
        self.assertIn("Витрина СО/ВР", refresh_rows[0]["message"])
        self.assertIn("db fail", refresh_rows[0]["message"])


# ===========================================================================
#  22.24b — выгрузка Excel «Основной» подвкладки
# ===========================================================================
class TestSovrExcelExportFilters(unittest.TestCase):
    """Пункт 1: файл отражает фильтры экрана и формат 22.24b."""

    def setUp(self):
        self.template = pd.DataFrame([
            {"kit_code": "А", "title": "A", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "ЭО1", "location": "1 (Корпус 1)",
             "vor_not_issued": 0},
            {"kit_code": "Б", "title": "B", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "ЭО2", "location": "1 (Корпус 1)",
             "vor_not_issued": 0},
            {"kit_code": "В", "title": "C", "discipline": "03_ЭС",
             "section": "ЭОМ", "marka": "ЭО3", "location": "1 (Корпус 1)",
             "vor_not_issued": 1},
            {"kit_code": "Г", "title": "D", "discipline": "02_КР",
             "section": "КЖ", "marka": "КЖ1", "location": "2 (Корпус 2)",
             "vor_not_issued": 0},
        ])
        self.showcase = pd.DataFrame([
            {"complex": "А", "sheet_number": "СО", "status": "A",
             "send_date": "2026-01-05", "status_date": "2026-01-07",
             "workflow_id": 111, "revision": "01", "name": "a",
             "doc_id": 1, "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "А", "sheet_number": "ВР", "status": "B",
             "send_date": "2026-02-05", "status_date": "2026-02-07",
             "workflow_id": 222, "revision": "02", "name": "b",
             "doc_id": 2, "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "Г", "sheet_number": "СО", "status": "C",
             "send_date": "2026-03-05", "status_date": "2026-03-07",
             "workflow_id": 333, "revision": "03", "name": "d",
             "doc_id": 3, "discipline": "02_КР", "section": "КЖ"},
        ])

    def _flt(self, **kw):
        base = {"disc": (), "section": (), "kits": (), "block_codes": (),
                "marka": "", "status": (), "date_from": None,
                "date_to": None}
        base.update(kw)
        return base

    def _export_sheet(self, flt):
        filtered = _apply_filters(self.showcase, flt)
        table = _build_sovr_table(filtered, flt, self.template,
                                  self.showcase)
        data = _export_sovr_excel(table, flt)
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data))
        return table, wb["СО и ВОР"]

    def _codes(self, ws):
        return {
            ws.cell(row=r, column=1).value
            for r in range(4, ws.max_row + 1)
            if ws.cell(row=r, column=1).value
        }

    def test_filters_are_respected(self):
        """Фильтр дисциплины: комплект другой дисциплины в файле отсутствует."""
        flt = self._flt(disc=("03_ЭС",))
        _, ws = self._export_sheet(flt)
        self.assertEqual(self._codes(ws), {"А", "Б", "В"})
        self.assertNotIn("Г", self._codes(ws))

    def test_dates_and_empty_cells(self):
        """Даты — ДД.ММ.ГГГГ; пустые значения — пустые ячейки."""
        flt = self._flt(disc=("03_ЭС",))
        _, ws = self._export_sheet(flt)
        # А — строка 4: оба блока заполнены
        self.assertEqual(ws.cell(row=4, column=4).value, "05.01.2026")
        self.assertEqual(ws.cell(row=4, column=5).value, "07.01.2026")
        self.assertEqual(ws.cell(row=4, column=9).value, "05.02.2026")
        self.assertEqual(ws.cell(row=4, column=10).value, "07.02.2026")
        # Б — «Не загружен»: обе даты и все поля пустые, без «None»
        for col in range(2, 12):
            self.assertIn(ws.cell(row=5, column=col).value, (None, ""))
            self.assertNotEqual(ws.cell(row=5, column=col).value, "None")

    def test_not_loaded_rows_present(self):
        """Комплекты «Не загружен» остаются в файле с пустыми блоками."""
        flt = self._flt(disc=("03_ЭС",))
        table, ws = self._export_sheet(flt)
        self.assertEqual(
            len(table[table["status"] == "Не загружен"]), 3)  # Б СО/ВР + В СО
        self.assertIn("Б", self._codes(ws))
        self.assertIn("В", self._codes(ws))

    def test_both_blocks_in_one_row(self):
        """У комплекта с СО и ВР — одна строка с обоими блоками."""
        flt = self._flt(disc=("03_ЭС",))
        _, ws = self._export_sheet(flt)
        a_rows = [r for r in range(4, ws.max_row + 1)
                  if ws.cell(row=r, column=1).value == "А"]
        self.assertEqual(len(a_rows), 1)
        row = a_rows[0]
        self.assertEqual(ws.cell(row=row, column=2).value, "01")   # СО
        self.assertEqual(ws.cell(row=row, column=7).value, "02")   # ВОР
        self.assertEqual(ws.cell(row=row, column=8).value, "B")

    def test_filename_with_discipline_suffix(self):
        """Имя файла: дата + суффикс дисциплины."""
        name = _sovr_export_filename(self._flt(disc=("03_ЭС",)))
        self.assertTrue(name.startswith("СО_ВОР_выгрузка_"))
        self.assertTrue(name.endswith("_03_ЭС.xlsx"))
        plain = _sovr_export_filename(self._flt())
        self.assertTrue(plain.endswith(".xlsx"))
        self.assertNotIn("_03_ЭС", plain)


# ===========================================================================
#  Пункт 2 — блок обогащения на «Сервисной», отсутствие пункта в Экспорте
# ===========================================================================
class TestEnrichExcelPlacement(unittest.TestCase):
    """Обогащение СВОК живёт в «Сервисной», в Экспорте такого пункта нет."""

    def test_export_has_no_sovr_enrichment_item(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "vitro" / "ui" / "export.py").read_text(encoding="utf-8")
        self.assertNotIn("_enrich_svok_excel", src)
        self.assertNotIn("Добавить статусы СО", src)
        self.assertNotIn("СО и ВОР", src)

    def test_service_subtab_calls_enrichment(self):
        import inspect
        import vitro.ui.so_vr as so_vr
        service_src = inspect.getsource(so_vr.render_service)
        self.assertIn("_render_enrich_excel()", service_src)
        block_src = inspect.getsource(so_vr._render_enrich_excel)
        self.assertIn("_render_enrich_download(", block_src)
        download_src = inspect.getsource(so_vr._render_enrich_download)
        self.assertIn("_enrich_pipeline(", download_src)
        pipeline_src = inspect.getsource(so_vr._enrich_pipeline)
        self.assertIn("_enrich_svok_excel(", pipeline_src)
        self.assertIn("_СО_ВОР.xlsx", download_src)

    def test_enriched_preview(self):
        source = _create_svok_file([
            {"code": "АТ-РД-КЖ1-С-00", "disc": "02_КР", "section": "КЖ",
             "marka": "КЖ1", "title": "Конструкции", "vor": ""},
        ])
        sovr_data = pd.DataFrame([
            {"complex": "АТ-РД-КЖ1-С-00", "sheet_number": "СО",
             "revision": "01", "status": "A", "send_date": "2026-01-15",
             "status_date": "2026-01-20", "workflow_id": 100},
        ])
        enriched = _enrich_svok_excel(source, "СВОК", sovr_data, False)
        preview = _sovr_enriched_preview(enriched)
        self.assertFalse(preview.empty)
        self.assertIn("Ревизия СО", preview.columns)
        self.assertIn("Ревизия ВР", preview.columns)
        row = preview.iloc[0]
        self.assertEqual(row["Шифр РД"], "АТ-РД-КЖ1-С-00")
        self.assertEqual(row["Статус СО"], "A")
        self.assertEqual(row["Дата выдачи СО"], "15.01.2026")
        self.assertEqual(row["Дата получения статуса СО"], "20.01.2026")


# ===========================================================================
#  Регресс — базовые функции прошлых патчей не сломаны
# ===========================================================================
class TestBackwardCompatibility(unittest.TestCase):
    """Регресс контрактов 22.21–22.23."""

    def test_build_table_two_args(self):
        df = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО", "status": "B",
             "send_date": "", "status_date": "", "workflow_id": None,
             "revision": "", "name": "", "doc_id": 1},
        ])
        flt = {"kits": (), "block_codes": (), "status": (),
               "date_from": None, "date_to": None}
        table = _build_sovr_table(df, flt)
        self.assertEqual(len(table), 1)

    def test_export_single_table_two_groups(self):
        """22.24b: одна таблица, группы «СО»/«ВОР», даты ДД.ММ.ГГГГ."""
        from vitro.ui.so_vr import _export_sovr_excel
        df = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО", "status": "A",
             "send_date": "2026-01-01", "status_date": "2026-01-02",
             "workflow_id": 1, "revision": "01", "name": "a"},
            {"complex": "A", "sheet_number": "ВР", "status": "B",
             "send_date": "2026-02-03", "status_date": "2026-02-04",
             "workflow_id": 2, "revision": "02", "name": "b"},
        ])
        data = _export_sovr_excel(df, {})
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames, ["СО и ВОР"])
        ws = wb["СО и ВОР"]
        # Строка 1 — полоса актуальности базы (как в «Экспорте»)
        self.assertIn("База актуальна на", str(ws.cell(row=1, column=1).value))
        # Двухэтажная шапка: Комплект | СО (B:F) | ВОР (G:K)
        self.assertEqual(ws.cell(row=2, column=1).value, "Комплект")
        self.assertEqual(ws.cell(row=2, column=2).value, "СО")
        self.assertEqual(ws.cell(row=2, column=7).value, "ВОР")
        self.assertEqual(ws.cell(row=3, column=2).value, "Ревизия")
        self.assertEqual(ws.cell(row=3, column=6).value, "Номер процесса")
        self.assertEqual(ws.cell(row=3, column=11).value, "Номер процесса")
        # Одна строка на комплект: оба блока рядом
        self.assertEqual(ws.cell(row=4, column=1).value, "A")
        self.assertEqual(ws.cell(row=4, column=2).value, "01")
        self.assertEqual(ws.cell(row=4, column=4).value, "01.01.2026")
        self.assertEqual(ws.cell(row=4, column=7).value, "02")
        self.assertEqual(ws.cell(row=4, column=9).value, "03.02.2026")
        self.assertEqual(ws.max_row, 4)


if __name__ == "__main__":
    loader = unittest.TextTestRunner(verbosity=2)
    loader.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
