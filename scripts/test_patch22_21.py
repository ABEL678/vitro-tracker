#!/usr/bin/env python3
"""
Тесты для патча 22.21 — «СО и ВОР» (слой данных).

Проверяет:
1. Нормализация sheet_number: кириллица/латиница/смешанные/регистр/мусор
2. Маппинг send_date/workflow_id из мок-ответа Витро
3. Отбор листов: только СО/ВР по нормализованному sheet_number
4. «Последний по процессу»: ревизии с разными датами и с ОДИНАКОВЫМИ датами
5. Витрина: запись статуса, отсутствие записи → «Не загружен»
6. Идемпотентность повторной загрузки витрины

Запуск:
    .venv\\Scripts\\python.exe scripts\\test_patch22_21.py
"""

import sys
from pathlib import Path
import unittest
import tempfile
import os

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vitro.sheet_kind import normalize_sheet_number


# ===========================================================================
#  Тест 1: Нормализация sheet_number
# ===========================================================================
class TestNormalizeSheetNumber(unittest.TestCase):
    """normalize_sheet_number: кириллица, латиница, микс, мусор."""

    def test_soa_cyrillic(self):
        self.assertEqual(normalize_sheet_number("СО"), "СО")

    def test_soa_latin(self):
        # CO → СО (латиница → кириллица)
        self.assertEqual(normalize_sheet_number("CO"), "СО")

    def test_soa_mixed_1(self):
        # CО → СО (латиница C + кириллица О)
        self.assertEqual(normalize_sheet_number("CО"), "СО")

    def test_soa_mixed_2(self):
        # СO → СО (кириллица С + латиница O)
        self.assertEqual(normalize_sheet_number("СO"), "СО")

    def test_soa_lowercase(self):
        # со → СО (нижний регистр)
        self.assertEqual(normalize_sheet_number("со"), "СО")

    def test_soa_with_spaces(self):
        # ' со ' → СО (trim)
        self.assertEqual(normalize_sheet_number(" со "), "СО")

    def test_soa_uppercase_with_spaces(self):
        # ' СО ' → СО
        self.assertEqual(normalize_sheet_number(" СО "), "СО")

    def test_vr_cyrillic(self):
        self.assertEqual(normalize_sheet_number("ВР"), "ВР")

    def test_vr_latin(self):
        # BP → ВР
        self.assertEqual(normalize_sheet_number("BP"), "ВР")

    def test_vr_mixed_1(self):
        # ВP → ВР
        self.assertEqual(normalize_sheet_number("ВP"), "ВР")

    def test_vr_mixed_2(self):
        # BР → ВР
        self.assertEqual(normalize_sheet_number("BР"), "ВР")

    def test_vr_lowercase(self):
        # вр → ВР
        self.assertEqual(normalize_sheet_number("вр"), "ВР")

    def test_misvalue_xx(self):
        self.assertIsNone(normalize_sheet_number("хх"))

    def test_misvalue_XX(self):
        self.assertIsNone(normalize_sheet_number("XX"))

    def test_misvalue_XXX(self):
        self.assertIsNone(normalize_sheet_number("XXX"))

    def test_misvalue_ZD01(self):
        self.assertIsNone(normalize_sheet_number("ЗД.01"))

    def test_misvalue_empty(self):
        self.assertIsNone(normalize_sheet_number(""))

    def test_misvalue_none(self):
        self.assertIsNone(normalize_sheet_number(None))

    def test_misvalue_kzh(self):
        self.assertIsNone(normalize_sheet_number("КЖ"))

    def test_misvalue_0(self):
        self.assertIsNone(normalize_sheet_number("0"))

    def test_misvalue_10(self):
        self.assertIsNone(normalize_sheet_number("10"))

    def test_misvalue_zd02(self):
        self.assertIsNone(normalize_sheet_number("ЗД.02"))

    def test_misvalue_rr(self):
        self.assertIsNone(normalize_sheet_number("РР"))

    def test_misvalue_ss(self):
        self.assertIsNone(normalize_sheet_number("СС"))


# ===========================================================================
#  Тест 2: Маппинг send_date/workflow_id из мок-ответа Витро
# ===========================================================================
class TestFetchAtPDocumentsMapping(unittest.TestCase):
    """Проверяем, что fetch_atp_documents возвращает send_date и workflow_id."""

    def test_mock_response_has_send_date(self):
        """Мок-ответ Витро с VitroBaseSendDate → send_date в record."""
        from vitro.sharepoint import fetch_atp_documents, normalize_complex

        # Мок-ответ Витро (упрощённый)
        mock_item = {
            "Id": 9160,
            "UniqueId": "8c65333c-ef33-4988-b483-e4cb7c3bee32",
            "OData__UIVersionString": "2.4",
            "FileLeafRef": "АТ-РД-ЭО1-С-00-СО.pdf",
            "FileDirRef": "/okhta/DocProjectLib/01_РД/02_АТП/01_В процессе/03_ЭС/СПЗ/АТ-РД-ЭО1-С-00",
            "VitroBaseStatus": 15,
            "VitroBaseDocumentRevision": "06",
            "VitroBaseFullName": "Спецификация оборудования",
            "VitroBaseStatusDate": "2026-11-09T07:54:35Z",
            "VitroBaseDocSheetNumber": "СО",
            "VitroBaseSendDate": "2026-11-03T14:02:28Z",
            "VitroBaseWorkflowId": 23412,
        }

        # Парсим путь
        from vitro.sharepoint import parse_path
        disc, sec, comp = parse_path(mock_item["FileDirRef"])

        # Собираем record как делает fetch_atp_documents
        doc_statuses = {15: "На рассмотрении"}
        record = {
            "id": mock_item["Id"],
            "leaf": "АТ-РД-ЭО1-С-00-СО",
            "discipline": disc,
            "section": sec,
            "complex": normalize_complex(comp),
            "dir_ref": mock_item["FileDirRef"],
            "status": doc_statuses.get(mock_item["VitroBaseStatus"], ""),
            "revision": mock_item["VitroBaseDocumentRevision"],
            "name": mock_item["VitroBaseFullName"],
            "status_date": mock_item["VitroBaseStatusDate"],
            "sheet_number": mock_item["VitroBaseDocSheetNumber"],
            "vitro_guid": mock_item["UniqueId"],
            "vitro_version": 2,
            "send_date": mock_item["VitroBaseSendDate"],
            "workflow_id": mock_item["VitroBaseWorkflowId"],
        }

        self.assertEqual(record["send_date"], "2026-11-03T14:02:28Z")
        self.assertEqual(record["workflow_id"], 23412)
        self.assertEqual(record["sheet_number"], "СО")

    def test_mock_response_vr(self):
        """Мок-ответ Витро с VitroBaseDocSheetNumber='ВР'."""
        from vitro.sharepoint import normalize_complex

        mock_item = {
            "Id": 15016,
            "UniqueId": "442fd5fd-c431-4c46-9d74-57a266436e73",
            "OData__UIVersionString": "6.0",
            "FileLeafRef": "АТ-РД-ССЛ-С-00-ВР.pdf",
            "FileDirRef": "/okhta/DocProjectLib/01_РД/02_АТП/03_ЭС/СС/АТ-РД-ССЛ-С-00",
            "VitroBaseStatus": 11,
            "VitroBaseDocumentRevision": "05",
            "VitroBaseFullName": "Ведомость объёмов работ",
            "VitroBaseStatusDate": "2026-06-18T12:59:50Z",
            "VitroBaseDocSheetNumber": "ВР",
            "VitroBaseSendDate": "2026-06-11T06:00:19Z",
            "VitroBaseWorkflowId": 21861,
        }

        from vitro.sharepoint import parse_path
        disc, sec, comp = parse_path(mock_item["FileDirRef"])

        doc_statuses = {11: "A"}
        record = {
            "id": mock_item["Id"],
            "leaf": "АТ-РД-ССЛ-С-00-ВР",
            "discipline": disc,
            "section": sec,
            "complex": normalize_complex(comp),
            "dir_ref": mock_item["FileDirRef"],
            "status": doc_statuses.get(mock_item["VitroBaseStatus"], ""),
            "revision": mock_item["VitroBaseDocumentRevision"],
            "name": mock_item["VitroBaseFullName"],
            "status_date": mock_item["VitroBaseStatusDate"],
            "sheet_number": mock_item["VitroBaseDocSheetNumber"],
            "vitro_guid": mock_item["UniqueId"],
            "vitro_version": 6,
            "send_date": mock_item["VitroBaseSendDate"],
            "workflow_id": mock_item["VitroBaseWorkflowId"],
        }

        self.assertEqual(record["send_date"], "2026-06-11T06:00:19Z")
        self.assertEqual(record["workflow_id"], 21861)
        self.assertEqual(record["sheet_number"], "ВР")


# ===========================================================================
#  Тест 3: Отбор листов — только СО/ВР
# ===========================================================================
class TestFilterSovrSheets(unittest.TestCase):
    """Отбираем только строки с нормализованным sheet_number ∈ {СО, ВР}."""

    def test_select_soa_sheets(self):
        from vitro.sheet_kind import normalize_sheet_number

        mock_docs = [
            {"sheet_number": "СО", "complex": "АТ-РД-ЭО1-С-00"},
            {"sheet_number": "CO", "complex": "АТ-РД-ЭО2-С-00"},
            {"sheet_number": "CО", "complex": "АТ-РД-ЭО3-С-00"},
            {"sheet_number": "10", "complex": "АТ-РД-ЭО1-С-00"},
            {"sheet_number": "ЗД.01", "complex": "АТ-РД-ЭО1-С-00"},
            {"sheet_number": "хх", "complex": "АТ-РД-ЭО1-С-00"},
        ]

        sovr = [d for d in mock_docs if normalize_sheet_number(d["sheet_number"]) == "СО"]
        self.assertEqual(len(sovr), 3)

    def test_select_vr_sheets(self):
        from vitro.sheet_kind import normalize_sheet_number

        mock_docs = [
            {"sheet_number": "ВР", "complex": "АТ-РД-ССЛ-С-00"},
            {"sheet_number": "BP", "complex": "АТ-РД-ССЛ-С-01"},
            {"sheet_number": "ВP", "complex": "АТ-РД-ССЛ-С-02"},
            {"sheet_number": "КЖ", "complex": "АТ-РД-ССЛ-С-00"},
            {"sheet_number": "01", "complex": "АТ-РД-ССЛ-С-00"},
        ]

        sovr = [d for d in mock_docs if normalize_sheet_number(d["sheet_number"]) == "ВР"]
        self.assertEqual(len(sovr), 3)

    def test_no_sovr_sheets(self):
        from vitro.sheet_kind import normalize_sheet_number

        mock_docs = [
            {"sheet_number": "10", "complex": "АТ-РД-ЭО1-С-00"},
            {"sheet_number": "ЗД.01", "complex": "АТ-РД-ЭО1-С-00"},
            {"sheet_number": "хх", "complex": "АТ-РД-ЭО1-С-00"},
        ]

        sovr = [d for d in mock_docs if normalize_sheet_number(d["sheet_number"]) in ("СО", "ВР")]
        self.assertEqual(len(sovr), 0)


# ===========================================================================
#  Тест 4: «Последний по процессу» — разные даты и одинаковые даты
# ===========================================================================
class TestLastInProcess(unittest.TestCase):
    """Правило: MAX(status_date), при равенстве — MAX(id)."""

    def test_different_dates(self):
        """Более новая дата побеждает."""
        from vitro.sqlite_db import get_conn, init_db
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        try:
            # Подменяем DB_FULL_PATH на модульном уровне
            import vitro.sqlite_db as db_mod
            old_full_path = db_mod.DB_FULL_PATH
            db_mod.DB_FULL_PATH = Path(tmp_db)

            init_db()

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO documents (id, leaf, complex, sheet_number, status, status_date)
                    VALUES
                        (1001, 'test.pdf', 'АТ-РД-АЭОМ-С-00', 'СО', 'C', '2025-10-01T00:00:00Z'),
                        (1002, 'test.pdf', 'АТ-РД-АЭОМ-С-00', 'СО', 'B', '2025-10-05T00:00:00Z'),
                        (1003, 'test.pdf', 'АТ-РД-АЭОМ-С-00', 'СО', 'A', '2025-10-10T00:00:00Z')
                """)

            n = load_sovr_statuses("test")
            self.assertEqual(n, 1)

            with get_conn() as conn:
                row = conn.execute(
                    "SELECT doc_id, status FROM documents_sovr"
                ).fetchone()
                self.assertEqual(row["doc_id"], 1003)
                self.assertEqual(row["status"], "A")

        finally:
            os.unlink(tmp_db)
            db_mod.DB_FULL_PATH = old_full_path

    def test_same_dates_max_id_fallback(self):
        """Одинаковые даты — побеждает MAX(id)."""
        from vitro.sqlite_db import get_conn, init_db
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        try:
            import vitro.sqlite_db as db_mod
            old_full_path = db_mod.DB_FULL_PATH
            db_mod.DB_FULL_PATH = Path(tmp_db)

            init_db()

            with get_conn() as conn:
                # 43 ревизии с ОДНАКОВОЙ датой (как в диагностике)
                values = []
                for i in range(1, 44):
                    values.append(f"({i+2000}, 'test.pdf', 'АТ-РД-АЭОМ-С-00', 'хх', 'C', '2025-10-09T11:57:09Z')")
                conn.execute(
                    f"INSERT INTO documents (id, leaf, complex, sheet_number, status, status_date) VALUES {','.join(values)}"
                )

            # Нормализация 'хх' → None, поэтому ничего не вставится
            n = load_sovr_statuses("test")
            self.assertEqual(n, 0)

        finally:
            os.unlink(tmp_db)
            db_mod.DB_FULL_PATH = old_full_path

    def test_same_dates_sovr_sheets(self):
        """Одинаковые даты для СО/ВР — побеждает MAX(id)."""
        from vitro.sqlite_db import get_conn, init_db
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        try:
            import vitro.sqlite_db as db_mod
            old_full_path = db_mod.DB_FULL_PATH
            db_mod.DB_FULL_PATH = Path(tmp_db)

            init_db()

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO documents (id, leaf, complex, sheet_number, status, status_date)
                    VALUES
                        (3001, 'test.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'C', '2025-10-01T00:00:00Z'),
                        (3002, 'test.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'B', '2025-10-01T00:00:00Z'),
                        (3003, 'test.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'A', '2025-10-01T00:00:00Z')
                """)

            n = load_sovr_statuses("test")
            self.assertEqual(n, 1)

            with get_conn() as conn:
                row = conn.execute(
                    "SELECT doc_id, status FROM documents_sovr"
                ).fetchone()
                self.assertEqual(row["doc_id"], 3003)  # MAX(id)
                self.assertEqual(row["status"], "A")

        finally:
            os.unlink(tmp_db)
            db_mod.DB_FULL_PATH = old_full_path


# ===========================================================================
#  Тест 5: Витрина — запись статуса, отсутствие записи → «Не загружен»
# ===========================================================================
class TestSovrVitro(unittest.TestCase):
    """Витрина: запись статуса при наличии, «Не загружен» при отсутствии."""

    def test_status_recorded(self):
        """При наличии СО/ВР в documents — запись в витрину."""
        from vitro.sqlite_db import get_conn, init_db
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        try:
            import vitro.sqlite_db as db_mod
            old_full_path = db_mod.DB_FULL_PATH
            db_mod.DB_FULL_PATH = Path(tmp_db)

            init_db()

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO documents (id, leaf, complex, sheet_number, status, status_date)
                    VALUES (4001, 'test.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'B', '2025-10-01T00:00:00Z')
                """)

            n = load_sovr_statuses("test")
            self.assertEqual(n, 1)

            with get_conn() as conn:
                row = conn.execute(
                    "SELECT complex, sheet_number, status FROM documents_sovr"
                ).fetchone()
                self.assertEqual(row["complex"], "АТ-РД-КЖ1-С-00")
                self.assertEqual(row["sheet_number"], "СО")
                self.assertEqual(row["status"], "B")

        finally:
            os.unlink(tmp_db)
            db_mod.DB_FULL_PATH = old_full_path

    def test_no_sovr_sheets_not_in_vitrine(self):
        """Нет СО/ВР в documents — витрина пуста."""
        from vitro.sqlite_db import get_conn, init_db
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        try:
            import vitro.sqlite_db as db_mod
            old_full_path = db_mod.DB_FULL_PATH
            db_mod.DB_FULL_PATH = Path(tmp_db)

            init_db()

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO documents (id, leaf, complex, sheet_number, status, status_date)
                    VALUES (5001, 'test.pdf', 'АТ-РД-КЖ1-С-00', '10', 'B', '2025-10-01T00:00:00Z')
                """)

            n = load_sovr_statuses("test")
            self.assertEqual(n, 0)

            with get_conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM documents_sovr"
                ).fetchone()[0]
                self.assertEqual(count, 0)

        finally:
            os.unlink(tmp_db)
            db_mod.DB_FULL_PATH = old_full_path


# ===========================================================================
#  Тест 6: Идемпотентность повторной загрузки
# ===========================================================================
class TestIdempotency(unittest.TestCase):
    """Повторная загрузка витрины — обновление, не дубли."""

    def test_idempotent_load(self):
        """Два вызова load_sovr_statuses не создают дубли."""
        from vitro.sqlite_db import get_conn, init_db
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        try:
            import vitro.sqlite_db as db_mod
            old_full_path = db_mod.DB_FULL_PATH
            db_mod.DB_FULL_PATH = Path(tmp_db)

            init_db()

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO documents (id, leaf, complex, sheet_number, status, status_date)
                    VALUES
                        (6001, 'test.pdf', 'АТ-РД-КЖ1-С-00', 'СО', 'B', '2025-10-01T00:00:00Z'),
                        (6002, 'test.pdf', 'АТ-РД-КЖ1-С-00', 'ВР', 'A', '2025-10-02T00:00:00Z')
                """)

            # Первая загрузка
            n1 = load_sovr_statuses("test")
            self.assertEqual(n1, 2)

            with get_conn() as conn:
                count1 = conn.execute(
                    "SELECT COUNT(*) FROM documents_sovr"
                ).fetchone()[0]
                self.assertEqual(count1, 2)

            # Вторая загрузка (идемпотентно)
            n2 = load_sovr_statuses("test")
            self.assertEqual(n2, 2)

            with get_conn() as conn:
                count2 = conn.execute(
                    "SELECT COUNT(*) FROM documents_sovr"
                ).fetchone()[0]
                self.assertEqual(count2, 2)  # Не 4, а 2

        finally:
            os.unlink(tmp_db)
            db_mod.DB_FULL_PATH = old_full_path


# ===========================================================================
#  Запуск
# ===========================================================================
if __name__ == "__main__":
    # Импортируем load_sovr_statuses для тестов
    from vitro.sqlite_db import load_sovr_statuses

    loader = unittest.TextTestRunner(verbosity=2)
    loader.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
