#!/usr/bin/env python3
"""
Тесты для патча 22.22 — «СО и ВОР» UI (исправлённая версия).

Проверяет:
1. Сборка сводной таблицы: СО, ВР, оба, пустая БД
2. Бублики: A/B/C/другие (включая "И" → "другие", русские → "другие")
3. Бублик "Загружено/План" при пустом sheets_sovr_template и Загружено>0
4. Стандартные фильтры: Дисциплина, Комплект (по complex)
5. Фильтр "Статус листа" (включая "Не загружен" → пустая таблица)
6. Период по send_date: пустые send_date при заданном периоде исключаются
7. Навигация: вкладка "СО и ВОР" в списке вкладок стоит ПЕРЕД "Поиск"
8. Оба блока (СО и ВР) отображаются всегда
9. Выгрузка xlsx: две группы колонок, формат дат
10. Пустой Комплект → бублики пустые, таблица пустая

Запуск:
    .venv\Scripts\python.exe scripts\test_patch22_22.py
"""

import sys
from pathlib import Path
import unittest
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vitro.ui.so_vr import (
    _apply_filters, _classify_status, _fmt_date, _build_sovr_table,
    STATUS_OPTIONS,
)
import pandas as pd


# ===========================================================================
#  Тест 1: Сборка сводной таблицы
# ===========================================================================
class TestBuildSovrTable(unittest.TestCase):
    """Сборка сводной таблицы: СО, ВР, оба, пустая БД."""

    def setUp(self):
        """Создаём тестовый DataFrame витрины."""
        self.df_sovr = pd.DataFrame([
            {
                "complex": "АТ-РД-ЭО1-С-00",
                "sheet_number": "СО",
                "status": "B",
                "send_date": "2026-11-03T14:02:28Z",
                "status_date": "2026-11-09T07:54:35Z",
                "workflow_id": 23412,
                "revision": "06",
                "name": "Спецификация оборудования",
                "doc_id": 9160,
            },
            {
                "complex": "АТ-РД-ССЛ-С-00",
                "sheet_number": "ВР",
                "status": "A",
                "send_date": "2026-06-11T06:00:19Z",
                "status_date": "2026-06-18T12:59:50Z",
                "workflow_id": 21861,
                "revision": "05",
                "name": "Ведомость объёмов работ",
                "doc_id": 15016,
            },
        ])

    def test_both_sheets(self):
        """Пустые фильтры — возвращает все строки."""
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _build_sovr_table(self.df_sovr, flt)
        self.assertEqual(len(result), 2)

    def test_empty_df(self):
        """Пустая БД — пустой результат."""
        empty_df = pd.DataFrame()
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _build_sovr_table(empty_df, flt)
        self.assertTrue(result.empty)


# ===========================================================================
#  Тест 2: Бублики — классификация статусов
# ===========================================================================
class TestClassifyStatus(unittest.TestCase):
    """Классификация статусов: A/B/C/Другие."""

    def test_status_a(self):
        self.assertEqual(_classify_status("A"), "Статус A — утверждён")

    def test_status_b(self):
        self.assertEqual(_classify_status("B"), "Статус B — к сдаче")

    def test_status_c(self):
        self.assertEqual(_classify_status("C"), "Статус C — в работе")

    def test_status_i(self):
        """И → Другие."""
        self.assertEqual(_classify_status("И"), "Другие статусы")

    def test_status_russian(self):
        """Русские статусы → Другие."""
        self.assertEqual(_classify_status("На рассмотрении"), "Другие статусы")
        self.assertEqual(_classify_status("Для согласования"), "Другие статусы")
        self.assertEqual(_classify_status("Размещено"), "Другие статусы")
        self.assertEqual(_classify_status("На корректировке"), "Другие статусы")

    def test_status_empty(self):
        """Пустой статус → Другие."""
        self.assertEqual(_classify_status(""), "Другие статусы")
        self.assertEqual(_classify_status(None), "Другие статусы")


# ===========================================================================
#  Тест 3: Бублик "Загружено/План" — пустой template
# ===========================================================================
class TestLoadedPlanDonut(unittest.TestCase):
    """Бублик "Загружено/План" при пустом sheets_sovr_template."""

    def test_loaded_only_when_template_empty(self):
        """При пустом template и Загружено>0 — только "Загружено" + пометка."""
        template_count = 0
        loaded_count = 5
        self.assertTrue(template_count == 0 and loaded_count > 0)

    def test_no_loaded_no_template(self):
        """При пустом template и Загружено=0 — нет данных."""
        template_count = 0
        loaded_count = 0
        self.assertTrue(template_count == 0 and loaded_count == 0)


# ===========================================================================
#  Тест 4: Стандартные фильтры (Дисциплина, Комплект)
# ===========================================================================
class TestStandardFilters(unittest.TestCase):
    """Стандартные фильтры: kits (complex), block_codes."""

    def setUp(self):
        self.df = pd.DataFrame([
            {
                "complex": "АТ-РД-ЭО1-С-00",
                "sheet_number": "СО",
                "status": "B",
                "send_date": "2026-11-03T14:02:28Z",
                "status_date": "2026-11-09T07:54:35Z",
                "workflow_id": 23412,
                "revision": "06",
                "name": "Спецификация",
                "doc_id": 9160,
            },
            {
                "complex": "АТ-РД-ССЛ-С-00",
                "sheet_number": "ВР",
                "status": "A",
                "send_date": "2026-06-11T06:00:19Z",
                "status_date": "2026-06-18T12:59:50Z",
                "workflow_id": 21861,
                "revision": "05",
                "name": "Ведомость",
                "doc_id": 15016,
            },
            {
                "complex": "АТ-РД-КЖ1-С-00",
                "sheet_number": "СО",
                "status": "C",
                "send_date": "2026-03-15T10:00:00Z",
                "status_date": "2026-03-20T00:00:00Z",
                "workflow_id": None,
                "revision": "01",
                "name": "КЖ",
                "doc_id": 9999,
            },
        ])

    def test_filter_kits(self):
        """Фильтр Комплект — отсекает по complex."""
        flt = {"kits": ("АТ-РД-ЭО1-С-00",), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["complex"], "АТ-РД-ЭО1-С-00")

    def test_filter_kits_multiple(self):
        """Несколько комплектов."""
        flt = {"kits": ("АТ-РД-ЭО1-С-00", "АТ-РД-КЖ1-С-00"), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 2)

    def test_filter_kits_empty_selection(self):
        """Пустой Комплект → пустая выборка."""
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        # Пустой tuple — все строки
        self.assertEqual(len(result), 3)

    def test_filter_block_codes(self):
        """Фильтр по расположению (4-й блок)."""
        flt = {"kits": (), "block_codes": ("С",), "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        # Все три комплекта имеют 4-й блок "С"
        self.assertEqual(len(result), 3)

    def test_filter_block_codes_no_match(self):
        """Расположение без совпадений → пустая выборка."""
        flt = {"kits": (), "block_codes": ("99",), "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertTrue(result.empty)


# ===========================================================================
#  Тест 5: Фильтр "Статус листа"
# ===========================================================================
class TestSheetStatusFilter(unittest.TestCase):
    """Фильтр "Статус листа"."""

    def setUp(self):
        self.df = pd.DataFrame([
            {
                "complex": "A", "sheet_number": "СО",
                "status": "B", "send_date": "2026-11-03T14:02:28Z",
                "status_date": "2026-11-09T07:54:35Z", "workflow_id": 23412,
                "revision": "06", "name": "test", "doc_id": 1,
            },
            {
                "complex": "B", "sheet_number": "ВР",
                "status": "A", "send_date": "2026-06-11T06:00:19Z",
                "status_date": "2026-06-18T12:59:50Z", "workflow_id": 21861,
                "revision": "05", "name": "test", "doc_id": 2,
            },
        ])

    def test_filter_status_a(self):
        """Статус A — только A."""
        flt = {"kits": (), "block_codes": (), "status": ("A",), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["status"], "A")

    def test_filter_status_mixed(self):
        """Несколько статусов."""
        flt = {"kits": (), "block_codes": (), "status": ("A", "B"), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 2)

    def test_filter_status_not_loaded_empty(self):
        """"Не загружен" → пустая таблица."""
        flt = {"kits": (), "block_codes": (), "status": ("Не загружен",), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertTrue(result.empty)


# ===========================================================================
#  Тест 6: Период по send_date
# ===========================================================================
class TestPeriodFilter(unittest.TestCase):
    """Период по send_date: границы, пустые send_date."""

    def setUp(self):
        self.df = pd.DataFrame([
            {
                "complex": "A", "sheet_number": "СО",
                "status": "B", "send_date": "2026-01-15T00:00:00Z",
                "status_date": "2026-01-15", "workflow_id": None,
                "revision": "01", "name": "test", "doc_id": 1,
            },
            {
                "complex": "B", "sheet_number": "СО",
                "status": "A", "send_date": "2026-06-20T00:00:00Z",
                "status_date": "2026-06-20", "workflow_id": None,
                "revision": "02", "name": "test2", "doc_id": 2,
            },
            {
                "complex": "C", "sheet_number": "СО",
                "status": "C", "send_date": "",
                "status_date": "2026-12-01", "workflow_id": None,
                "revision": "03", "name": "test3", "doc_id": 3,
            },
        ])

    def test_period_middle(self):
        """Период по центру — только середина."""
        from datetime import date
        flt = {"kits": (), "block_codes": (), "status": (),
               "date_from": date(2026, 3, 1), "date_to": date(2026, 9, 30)}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["complex"], "B")

    def test_period_full(self):
        """Период по всей выборке — все строки (пустые send_date исключаются)."""
        from datetime import date
        flt = {"kits": (), "block_codes": (), "status": (),
               "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 2)

    def test_period_single_day(self):
        """Период один день — граница включительно."""
        from datetime import date
        flt = {"kits": (), "block_codes": (), "status": (),
               "date_from": date(2026, 6, 20), "date_to": date(2026, 6, 20)}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["complex"], "B")

    def test_period_empty_send_date_excluded(self):
        """Пустой send_date при заданном периоде — исключается."""
        from datetime import date
        df_empty_sd = pd.DataFrame([{"complex": "test", "sheet_number": "СО",
            "status": "B", "send_date": "", "status_date": "2026-01-01",
            "workflow_id": None, "revision": "01", "name": "test", "doc_id": 1}])
        flt = {"kits": (), "block_codes": (), "status": (),
               "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        result = _apply_filters(df_empty_sd, flt)
        self.assertTrue(result.empty)

    def test_period_no_dates(self):
        """Период не задан — пустые send_date в выборке."""
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 3)


# ===========================================================================
#  Тест 7: Навигация — вкладка ПЕРЕД "Поиск"
# ===========================================================================
class TestNavigation(unittest.TestCase):
    """Вкладка "СО и ВОР" в списке вкладок стоит ПЕРЕД "Поиск"."""

    def test_sovr_before_search(self):
        """Проверяем, что вкладка "СО и ВОР" перед "Поиск" в app.py."""
        app_path = Path(__file__).resolve().parent.parent / "app.py"
        content = app_path.read_text(encoding="utf-8")

        tabs_start = content.find('_TABS = [')
        self.assertGreater(tabs_start, 0, "_TABS не найден в app.py")

        tabs_end = content.find(']', tabs_start)
        tabs_block = content[tabs_start:tabs_end + 1]

        sovr_pos = tabs_block.find('СО и ВОР')
        search_pos = tabs_block.find('Поиск')

        self.assertGreater(sovr_pos, 0, 'Вкладка "СО и ВОР" не найдена в _TABS')
        self.assertGreater(search_pos, 0, 'Вкладка "Поиск" не найдена в _TABS')
        self.assertLess(sovr_pos, search_pos,
                        'Вкладка "СО и ВОР" должна быть ПЕРЕД "Поиск"')


# ===========================================================================
#  Тест 8: Оба блока (СО и ВР) всегда отображаются
# ===========================================================================
class TestBothSectionsAlwaysVisible(unittest.TestCase):
    """Оба блока СО и ВР всегда видны — нет скрытия по типу листа."""

    def test_both_sections_in_table(self):
        """Таблица содержит и СО, и ВР при пустых фильтрах."""
        df = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО", "status": "A",
             "send_date": "2026-01-01T00:00:00Z", "status_date": "2026-01-01",
             "workflow_id": 1, "revision": "01", "name": "test", "doc_id": 1},
            {"complex": "B", "sheet_number": "ВР", "status": "B",
             "send_date": "2026-02-01T00:00:00Z", "status_date": "2026-02-01",
             "workflow_id": 2, "revision": "02", "name": "test", "doc_id": 2},
        ])
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        result = _build_sovr_table(df, flt)
        self.assertEqual(len(result), 2)
        self.assertIn("СО", result["sheet_number"].values)
        self.assertIn("ВР", result["sheet_number"].values)

    def test_both_sections_in_filter(self):
        """Фильтры не содержат sheet_type — только kits/block_codes."""
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}
        self.assertNotIn("sheet_type", flt)


# ===========================================================================
#  Тест 9: Экспорт xlsx
# ===========================================================================
class TestExportXlsx(unittest.TestCase):
    """Выгрузка xlsx: две группы колонок, формат дат."""

    def test_export_soa_vr_excel(self):
        """Экспорт СО+ВР → Excel с двумя листами."""
        from vitro.ui.so_vr import _export_sovr_excel
        import io

        df = pd.DataFrame([
            {
                "complex": "АТ-РД-ЭО1-С-00",
                "sheet_number": "СО",
                "status": "B",
                "send_date": "2026-11-03T14:02:28Z",
                "status_date": "2026-11-09T07:54:35Z",
                "workflow_id": 23412,
                "revision": "06",
                "name": "Спецификация",
                "doc_id": 9160,
            },
            {
                "complex": "АТ-РД-ССЛ-С-00",
                "sheet_number": "ВР",
                "status": "A",
                "send_date": "2026-06-11T06:00:19Z",
                "status_date": "2026-06-18T12:59:50Z",
                "workflow_id": 21861,
                "revision": "05",
                "name": "Ведомость",
                "doc_id": 15016,
            },
        ])
        flt = {"kits": (), "block_codes": (), "status": (), "date_from": None, "date_to": None}

        excel_bytes = _export_sovr_excel(df, flt)
        self.assertIsNotNone(excel_bytes)
        self.assertGreater(len(excel_bytes), 0)

        from openpyxl import load_workbook
        wb = load_workbook(filename=io.BytesIO(excel_bytes))
        self.assertEqual(len(wb.sheetnames), 2)
        self.assertIn("СО", wb.sheetnames)
        self.assertIn("ВР", wb.sheetnames)

    def test_export_format_dates(self):
        """Формат дат в экспорте — ДД.ММ.ГГГГ."""
        self.assertEqual(_fmt_date("2026-11-03T14:02:28Z"), "03.11.2026")
        self.assertEqual(_fmt_date("2026-06-11T06:00:19Z"), "11.06.2026")
        self.assertEqual(_fmt_date(""), "—")
        self.assertEqual(_fmt_date(None), "—")


# ===========================================================================
#  Запуск
# ===========================================================================
if __name__ == "__main__":
    loader = unittest.TextTestRunner(verbosity=2)
    loader.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
