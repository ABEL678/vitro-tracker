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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from openpyxl import Workbook

from vitro.ui.so_vr import (
    KIND_SO, KIND_VR, KIND_TITLES, SHEET_STATUS_COLORS,
    _apply_filters, _build_sovr_table, _fmt_date, _fmt_workflow,
    _parse_svok_workbook, _plan_donut_counts, _status_donut_data,
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
        ws.cell(row=r, column=20, value=row.get("vor", ""))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestSvokParser(unittest.TestCase):
    """C1: шапка в строке 13, сводный блок не мешает, T — признак ВОР."""

    def test_parse_mini_copy(self):
        data = _create_svok_file([
            {"code": "АТ-РД-КЖ1-С-00", "disc": "02_КР", "section": "КЖ",
             "marka": "КЖ1", "title": "Конструкции", "vor": "В работе"},
            {"code": "АТ-РД-ЭО1-С-00", "disc": "03_ЭС", "section": "ЭОМ",
             "marka": "ЭО1", "title": "Электроосвещение",
             "vor": "Не выдается"},
            {"code": "АТ-РД-ОВ1-С-00", "disc": "04_МЕХ", "section": "ОВ",
             "marka": "ОВ1", "title": "Отопление", "vor": ""},
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
                 "vor_not_issued": 0},
                {"kit_code": "АТ-РД-ЭО1-С-00", "title": "ЭО1",
                 "discipline": "03_ЭС", "section": "ЭОМ", "marka": "ЭО1",
                 "vor_not_issued": 1},
                {"kit_code": "АТ-РД-ОВ1-С-00", "title": "ОВ1",
                 "discipline": "04_МЕХ", "section": "ОВ", "marka": "ОВ1",
                 "vor_not_issued": 0},
            ]
            r1 = replace_sovr_template(first, source="f1.xlsx")
            self.assertEqual(r1["kits"], 3)
            self.assertEqual(r1["not_issued"], 1)

            df1 = load_sovr_template()
            self.assertEqual(len(df1), 3)
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

    def test_export_sheets_short_names(self):
        from vitro.ui.so_vr import _export_sovr_excel
        df = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО", "status": "A",
             "send_date": "2026-01-01", "status_date": "2026-01-02",
             "workflow_id": 1, "revision": "01", "name": "a"},
            {"complex": "B", "sheet_number": "ВР", "status": "B",
             "send_date": None, "status_date": None,
             "workflow_id": None, "revision": None, "name": "b"},
        ])
        data = _export_sovr_excel(df, {})
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames, ["СО", "ВОР"])
        # развёрнутые названия в строке 1 (A3)
        self.assertEqual(wb["СО"].cell(row=1, column=1).value,
                         "Спецификация оборудования и материалов")
        self.assertEqual(wb["ВОР"].cell(row=1, column=1).value,
                         "Ведомость объемов работ")
        # null не превращается в «None»
        self.assertIn(wb["ВОР"].cell(row=3, column=8).value, (None, ""))


if __name__ == "__main__":
    loader = unittest.TextTestRunner(verbosity=2)
    loader.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
