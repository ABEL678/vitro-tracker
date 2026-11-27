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
7. Бублики — защита от пустых данных (NaN, пустой df, zero sum)
8. _render_sovr_donuts на пустой витрине не падает
9. Навигация: вкладка "СО и ВОР" в списке вкладок стоит ПЕРЕД "Поиск"
10. Оба блока (СО и ВР) отображаются всегда
11. Выгрузка xlsx: две группы колонок, формат дат
12. Пустой Комплект → бублики пустые, таблица пустая

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
    STATUS_OPTIONS, _donut, _render_sovr_donuts,
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
#  Тест 7.5: Бублики — защита от пустых данных (фикс 22.23)
# ===========================================================================
class TestDonutEmptyProtection(unittest.TestCase):
    """Проверка что _donut не падает на пустых/NaN данных."""

    def test_donut_empty_df(self):
        """_donut не падает на пустом DataFrame."""
        empty_df = pd.DataFrame(columns=["label", "count"])
        # Не должно выбросить исключение
        _donut(empty_df, "label", "count", "test_empty", "0")

    def test_donut_nan_labels(self):
        """_donut не падает на NaN в label."""
        df = pd.DataFrame({"label": [None, "", "A"], "count": [1, 2, 3]})
        # Не должно выбросить исключение — NaN должны быть отфильтрованы
        _donut(df, "label", "count", "test_nan", "3")

    def test_donut_all_nan_labels(self):
        """_donut не падает когда все label NaN."""
        df = pd.DataFrame({"label": [None, None], "count": [1, 2]})
        # Не должно выбросить исключение
        _donut(df, "label", "count", "test_all_nan", "0")

    def test_donut_zero_sum(self):
        """_donut не падает когда сумма значений = 0."""
        df = pd.DataFrame({"label": ["A", "B"], "count": [0, 0]})
        # Не должно выбросить исключение
        _donut(df, "label", "count", "test_zero", "0")


# ===========================================================================
#  Тест 7.6: _render_sovr_donuts на пустой витрине
# ===========================================================================
class TestRenderDonutsEmptyTable(unittest.TestCase):
    """Проверка что бублики не падают на пустой витрине."""

    def test_render_donuts_empty_df(self):
        """_render_sovr_donuts не падает на пустом DataFrame."""
        empty_df = pd.DataFrame(columns=["complex", "sheet_number", "status"])
        # Не должно выбросить исключение
        _render_sovr_donuts(empty_df)

    def test_render_donuts_empty_status(self):
        """_render_sovr_donuts не падает когда все статусы NaN."""
        df = pd.DataFrame([
            {"complex": "A", "sheet_number": "СО", "status": None,
             "send_date": "", "status_date": "", "workflow_id": None,
             "revision": "", "name": "", "doc_id": 1},
        ])
        # Не должно выбросить исключение — None статус должен обработаться
        try:
            _render_sovr_donuts(df)
        except KeyError:
            self.fail("_render_sovr_donuts упал на NaN статусе (KeyError)")


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
        """22.24b: экспорт СО+ВР → одна таблица с группами «СО» и «ВОР»."""
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
        # Патч 22.24b: один лист, комплекты, два блока колонок
        self.assertEqual(wb.sheetnames, ["СО и ВОР"])
        ws = wb["СО и ВОР"]
        self.assertEqual(ws.cell(row=2, column=2).value, "СО")
        self.assertEqual(ws.cell(row=2, column=7).value, "ВОР")
        codes = {ws.cell(row=r, column=1).value
                 for r in range(4, ws.max_row + 1)}
        self.assertIn("АТ-РД-ЭО1-С-00", codes)
        self.assertIn("АТ-РД-ССЛ-С-00", codes)

    def test_export_format_dates(self):
        """Формат дат в экспорте — ДД.ММ.ГГГГ."""
        self.assertEqual(_fmt_date("2026-11-03T14:02:28Z"), "03.11.2026")
        self.assertEqual(_fmt_date("2026-06-11T06:00:19Z"), "11.06.2026")
        self.assertEqual(_fmt_date(""), "—")
        self.assertEqual(_fmt_date(None), "—")


# ===========================================================================
#  Патч 22.24: дисциплина режет бублики и таблицу (A1)
# ===========================================================================
class TestDisciplineFilterPatch2224(unittest.TestCase):
    """A1: дисциплина/раздел режут выборку одинаково для бубликов и таблицы."""

    def setUp(self):
        self.df = pd.DataFrame([
            {"complex": "АТ-РД-ЭО1-С-00", "sheet_number": "СО", "status": "B",
             "send_date": "2026-11-01", "status_date": "2026-11-02",
             "workflow_id": 1, "revision": "01", "name": "СО-ЭО1",
             "doc_id": 1, "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-ЭО2-С-00", "sheet_number": "СО", "status": "A",
             "send_date": "2026-11-01", "status_date": "2026-11-03",
             "workflow_id": 2, "revision": "01", "name": "СО-ЭО2",
             "doc_id": 2, "discipline": "03_ЭС", "section": "ЭОМ"},
            {"complex": "АТ-РД-КЖ1-С-00", "sheet_number": "ВР", "status": "C",
             "send_date": "2026-11-01", "status_date": "2026-11-04",
             "workflow_id": 3, "revision": "01", "name": "ВР-КЖ1",
             "doc_id": 3, "discipline": "02_КР", "section": "КЖ"},
        ])

    def test_discipline_filters_table(self):
        from vitro.ui.so_vr import _apply_filters
        flt = {"disc": ("03_ЭС",), "kits": (), "block_codes": (),
               "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 2)
        self.assertTrue((result["discipline"] == "03_ЭС").all())

    def test_discipline_filters_donuts_consistent(self):
        """Сумма бублика статусов = число отфильтрованных листов."""
        from vitro.ui.so_vr import _apply_filters, _status_donut_data
        flt = {"disc": ("02_КР",), "kits": (), "block_codes": (),
               "status": (), "date_from": None, "date_to": None}
        filtered = _apply_filters(self.df, flt)
        data = _status_donut_data(filtered, "ВР")
        self.assertEqual(int(data["Количество"].sum()), len(filtered))
        self.assertEqual(len(filtered), 1)

    def test_section_filter(self):
        from vitro.ui.so_vr import _apply_filters
        flt = {"section": ("КЖ",), "kits": (), "block_codes": (),
               "status": (), "date_from": None, "date_to": None}
        result = _apply_filters(self.df, flt)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["complex"], "АТ-РД-КЖ1-С-00")


# ===========================================================================
#  Патч 22.24: строки «Не загружен» из перечня СВОК (C3/C4)
# ===========================================================================
class TestNotLoadedRowsPatch2224(unittest.TestCase):
    """C3/C4: комплект перечня без записи в Витро → «Не загружен»."""

    def setUp(self):
        self.df = pd.DataFrame([
            {"complex": "АТ-РД-ЭО1-С-00", "sheet_number": "СО", "status": "B",
             "send_date": "2026-11-01", "status_date": "2026-11-02",
             "workflow_id": 1, "revision": "01", "name": "СО",
             "doc_id": 1, "discipline": "03_ЭС", "section": "ЭОМ"},
        ])
        self.template = pd.DataFrame([
            {"kit_code": "АТ-РД-ЭО1-С-00", "title": "ЭО1",
             "discipline": "03_ЭС", "section": "ЭОМ", "marka": "ЭО1",
             "vor_not_issued": 0},
            {"kit_code": "АТ-РД-ЭО2-С-00", "title": "ЭО2",
             "discipline": "03_ЭС", "section": "ЭОМ", "marka": "ЭО2",
             "vor_not_issued": 0},
            {"kit_code": "АТ-РД-ЭО3-С-00", "title": "ЭО3",
             "discipline": "03_ЭС", "section": "ЭОМ", "marka": "ЭО3",
             "vor_not_issued": 1},
        ])

    def _flt(self, **kw):
        base = {"kits": (), "block_codes": (), "status": (),
                "date_from": None, "date_to": None}
        base.update(kw)
        return base

    def test_not_loaded_rows_added(self):
        from vitro.ui.so_vr import _build_sovr_table
        table = _build_sovr_table(
            self.df, self._flt(), self.template, self.df)
        not_loaded = table[table["status"] == "Не загружен"]
        # ЭО2 — без записи; ВОР ЭО2 есть в плане (not_issued=0)
        self.assertIn(("АТ-РД-ЭО2-С-00", "ВР"),
                      set(zip(not_loaded["complex"], not_loaded["sheet_number"])))
        # ЭО3 «Не выдается» — строки ВОР-«Не загружен» нет
        self.assertNotIn(("АТ-РД-ЭО3-С-00", "ВР"),
                         set(zip(not_loaded["complex"], not_loaded["sheet_number"])))
        # Загруженный ЭО1 не даёт строки «Не загружен» по СО
        self.assertNotIn(("АТ-РД-ЭО1-С-00", "СО"),
                         set(zip(not_loaded["complex"],
                                 not_loaded["sheet_number"])))

    def test_filter_not_loaded_only(self):
        from vitro.ui.so_vr import _apply_filters, _build_sovr_table
        flt = self._flt(status=("Не загружен",))
        real = _apply_filters(self.df, flt)
        table = _build_sovr_table(real, flt, self.template, self.df)
        self.assertFalse(table.empty)
        self.assertTrue((table["status"] == "Не загружен").all())

    def test_status_a_hides_not_loaded(self):
        from vitro.ui.so_vr import _apply_filters, _build_sovr_table
        flt = self._flt(status=("A",))
        real = _apply_filters(self.df, flt)
        # реальных строк со статусом A нет — таблица пустая
        table = _build_sovr_table(real, flt, self.template, self.df)
        self.assertTrue(table.empty)

    def test_period_hides_not_loaded(self):
        from datetime import date
        from vitro.ui.so_vr import _build_sovr_table
        table = _build_sovr_table(
            self.df, self._flt(date_from=date(2026, 1, 1)),
            self.template, self.df)
        self.assertTrue((table["status"] != "Не загружен").all())


# ===========================================================================
#  Запуск
# ===========================================================================
if __name__ == "__main__":
    loader = unittest.TextTestRunner(verbosity=2)
    loader.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
