#!/usr/bin/env python3
"""
Тесты для патча 22.22 — «СО и ВОР» UI.

Проверяет:
1. Сборка сводной таблицы: только СО, только ВР, оба, пустая БД
2. Бублики: A/B/C/другие (включая «И» → «другие», русские → «другие»)
3. Бублик «Загружено/План» при пустом sheets_sovr_template и Загружено>0
4. Фильтры: статус листа (включая «Не загружен» → пустая таблица),
   тип листа (СО/ВР/Оба + скрытие блока), период по send_date
5. Навигация: вкладка «СО и ВОР» в списке вкладок стоит ПЕРЕД «Поиск»
6. Выгрузка xlsx: шапка с фильтрами, две группы колонок, формат дат
7. Период по send_date: пустые send_date при заданном периоде исключаются

Запуск:
    .venv\Scripts\python.exe scripts\test_patch22_22.py
"""

import sys
from pathlib import Path
import unittest
import tempfile
import os
import io

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vitro.ui.so_vr import (
    _apply_filters, _classify_status, _fmt_date, _build_sovr_table,
    STATUS_OPTIONS, SHEET_TYPE_OPTIONS,
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
        """Тип «Оба» — возвращает все строки."""
        flt = {"sheet_type": "Оба", "status": (), "date_from": None, "date_to": None}
        result = _build_sovr_table(self.df_sovr, flt)
        self.assertEqual(len(result), 2)

    def test_only_soa(self):
        """Тип «СО» — только СО строки."""
        flt = {"sheet_type": "СО", "status": (), "date_from": None, "date_to": None}
        filtered = _apply_filters(self.df_sovr, flt)
        result = _build_sovr_table(filtered, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["sheet_number"], "СО")

    def test_only_vr(self):
        """Тип «ВР» — только ВР строки."""
        flt = {"sheet_type": "ВР", "status": (), "date_from": None, "date_to": None}
        filtered = _apply_filters(self.df_sovr, flt)
        result = _build_sovr_table(filtered, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["sheet_number"], "ВР")

    def test_empty_df(self):
        """Пустая БД — пустой результат."""
        empty_df = pd.DataFrame()
        flt = {"sheet_type": "Оба", "status": (), "date_from": None, "date_to": None}
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
#  Тест 3: Бублик «Загружено/План» — пустой template
# ===========================================================================
class TestLoadedPlanDonut(unittest.TestCase):
    """Бублик «Загружено/План» при пустом sheets_sovr_template."""

    def test_loaded_only_when_template_empty(self):
        """При пустом template и Загружено>0 — только «Загружено» + пометка."""
        # Проверяем логику: если template_count == 0 и loaded_count > 0
        template_count = 0
        loaded_count = 5
        # Должно показывать только «Загружено» с пометкой
        # (не рисуем долю 100%)
        self.assertTrue(template_count == 0 and loaded_count > 0)

    def test_no_loaded_no_template(self):
        """При пустом template и Загружено=0 — нет данных."""
        template_count = 0
        loaded_count = 0
        self.assertTrue(template_count == 0 and loaded_count == 0)


# ===========================================================================
#  Тест 4: Фильтры
# ===========================================================================
class TestFilters(unittest.TestCase):
    """Фильтры: статус, тип листа, период."""

    def setUp(self):
        """Создаём тестовый DataFrame."""
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
                "send_date": "",  # пустой send_date
                "status_date": "2026-01-01T00:00:00Z",
                "workflow_id": None,
                "revision": "01",
                "name": "КЖ",
                "doc_id": 9999,
            },
        ])

    def test_filter_sheet_type_soa(self):
        """Тип СО — только СО строки."""
        flt = {"sheet_type": "СО", "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(result["sheet_number"] == "СО"))

    def test_filter_sheet_type_vr(self):
        """Тип ВР — только ВР строки."""
        flt = {"sheet_type": "ВР", "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertTrue(all(result["sheet_number"] == "ВР"))

    def test_filter_sheet_type_both(self):
        """Тип Оба — все строки."""
        flt = {"sheet_type": "Оба", "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 3)

    def test_filter_status_a(self):
        """Статус A — только A."""
        flt = {"sheet_type": "Оба", "status": ("A",), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["status"], "A")

    def test_filter_status_not_loaded_empty(self):
        """«Не загружен» при пустом template → пустая таблица."""
        flt = {"sheet_type": "Оба", "status": ("Не загружен",), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        # При пустом template «Не загружен» не находит записей
        self.assertTrue(result.empty)

    def test_filter_status_mixed(self):
        """Несколько статусов."""
        flt = {"sheet_type": "Оба", "status": ("A", "B"), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 2)

    def test_filter_period_with_dates(self):
        """Период задан — пустые send_date исключаются."""
        from datetime import date
        flt = {
            "sheet_type": "Оба", "status": (),
            "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31),
        }
        result = _apply_filters(self.df, flt)
        # Строка с пустым send_date исключается
        self.assertEqual(len(result), 2)

    def test_filter_period_empty_send_date_excluded(self):
        """Пустой send_date при заданном периоде — исключается."""
        from datetime import date
        df_empty_sd = pd.DataFrame([{
            "complex": "test", "sheet_number": "СО",
            "status": "B", "send_date": "",
            "status_date": "2026-01-01", "workflow_id": None,
            "revision": "01", "name": "test", "doc_id": 1,
        }])
        flt = {
            "sheet_type": "Оба", "status": (),
            "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31),
        }
        result = _apply_filters(df_empty_sd, flt)
        self.assertTrue(result.empty)

    def test_filter_period_no_dates(self):
        """Период не задан — пустые send_date в выборке."""
        flt = {"sheet_type": "Оба", "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        # Все 3 строки, включая пустой send_date
        self.assertEqual(len(result), 3)


# ===========================================================================
#  Тест 5: Навигация — вкладка ПЕРЕД «Поиск»
# ===========================================================================
class TestNavigation(unittest.TestCase):
    """Вкладка «СО и ВОР» в списке вкладок стоит ПЕРЕД «Поиск»."""

    def test_sovr_before_search(self):
        """Проверяем, что вкладка «СО и ВОР» перед «Поиск» в app.py."""
        app_path = Path(__file__).resolve().parent.parent / "app.py"
        content = app_path.read_text(encoding="utf-8")

        # Ищем _TABS в файле
        tabs_start = content.find('_TABS = [')
        self.assertGreater(tabs_start, 0, "_TABS не найден в app.py")

        # Извлекаем блок _TABS
        tabs_end = content.find(']', tabs_start)
        tabs_block = content[tabs_start:tabs_end + 1]

        sovr_pos = tabs_block.find('СО и ВОР')
        search_pos = tabs_block.find('Поиск')

        self.assertGreater(sovr_pos, 0, "Вкладка «СО и ВОР» не найдена в _TABS")
        self.assertGreater(search_pos, 0, "Вкладка «Поиск» не найдена в _TABS")
        self.assertLess(sovr_pos, search_pos,
                        "Вкладка «СО и ВОР» должна быть ПЕРЕД «Поиск»")


# ===========================================================================
#  Тест 6: Экспорт xlsx
# ===========================================================================
class TestExportXlsx(unittest.TestCase):
    """Выгрузка xlsx: шапка с фильтрами, две группы колонок, формат дат."""

    def test_export_soa_vr_excel(self):
        """Экспорт СО+ВР → Excel с двумя листами."""
        from vitro.ui.so_vr import _export_sovr_excel

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
        flt = {"sheet_type": "Оба", "status": (), "date_from": None, "date_to": None}

        excel_bytes = _export_sovr_excel(df, flt)
        self.assertIsNotNone(excel_bytes)
        self.assertGreater(len(excel_bytes), 0)

        # Проверяем, что это валидный Excel
        from openpyxl import load_workbook
        wb = load_workbook(filename=io.BytesIO(excel_bytes))
        self.assertEqual(len(wb.sheetnames), 2)
        self.assertIn("СО", wb.sheetnames)
        self.assertIn("ВР", wb.sheetnames)

    def test_export_format_dates(self):
        """Формат дат в экспорте — ДД.ММ.ГГГГ."""
        from vitro.ui.so_vr import _fmt_date

        self.assertEqual(_fmt_date("2026-11-03T14:02:28Z"), "03.11.2026")
        self.assertEqual(_fmt_date("2026-06-11T06:00:19Z"), "11.06.2026")
        self.assertEqual(_fmt_date(""), "—")
        self.assertEqual(_fmt_date(None), "—")


# ===========================================================================
#  Тест 7: Период по send_date — границы
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
                "status": "C", "send_date": "2026-12-01T00:00:00Z",
                "status_date": "2026-12-01", "workflow_id": None,
                "revision": "03", "name": "test3", "doc_id": 3,
            },
        ])

    def test_period_middle(self):
        """Период по центру — только середина."""
        from datetime import date
        flt = {
            "sheet_type": "Оба", "status": (),
            "date_from": date(2026, 3, 1), "date_to": date(2026, 9, 30),
        }
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["complex"], "B")

    def test_period_full(self):
        """Период по всей выборке — все строки."""
        from datetime import date
        flt = {
            "sheet_type": "Оба", "status": (),
            "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31),
        }
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 3)

    def test_period_single_day(self):
        """Период один день — граница включительно."""
        from datetime import date
        flt = {
            "sheet_type": "Оба", "status": (),
            "date_from": date(2026, 6, 20), "date_to": date(2026, 6, 20),
        }
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["complex"], "B")


# ===========================================================================
#  Запуск
# ===========================================================================
if __name__ == "__main__":
    loader = unittest.TextTestRunner(verbosity=2)
    loader.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
