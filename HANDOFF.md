# HANDOFF — Vitro-tracker

## Текущее состояние

### Последняя работа
Обновлён HANDOFF.md (этот файл).

### Работа до этого (97dc07b)
Доработка фильтров в Сводке по проекту (`vitro/ui/summary.py`):
- Второй ряд фильтров: «Статус замечания» (5 сырых значений) + «Статус листа» (8 групп).
- Секция 1 (Объём замечаний) теперь фильтруется по статусу листа через SQL — `IN (...) OR NOT IN (...)` для группы «Прочее».
- DISPLAY_LIMIT убран — строка ИТОГО в разрезе всегда видна.
- Ключевые функции: `_doc_status_group()`, `_sheet_groups_to_sql()`.
- `_LAST_FILTERS` расширен ключом `sheet_groups`.

### Работа до этого (первый ряд фильтров + DISPLAY_LIMIT)
Переделана таблица разреза в Сводке по проекту (`vitro/ui/summary.py`):
- 10 колонок (Код/Раздел/Шифр, Полное наименование, Открытых замечаний, Всего у АТП ТЛП, Новое, Не принято, К обсуждению, Принято в работу, Выполнено, Пояснения)
- Отображение: первые 100 строк + ИТОГО, дальше пагинация
- Excel-выгрузка: все строки с красивым оформлением (границы, бледные цвета заголовков, автофильтр, закреплённый заголовок, фиксированная ширина колонок)
- `display_count` используется вместо `len(display_df)` (Styler не имеет len)

### Последние коммиты (main)
```
97dc07b fix(summary): filter Section 1 by sheet status + show all rows (no DISPLAY_LIMIT)
ce612c1 fix(summary): fixed column widths and freeze header
62e06d3 fix(summary): replace all len(display_df) with display_count
e53430a fix(summary): Styler has no len() — count rows before styling
98287c4 feat(summary): add "Всего у АТП ТЛП" column + 100-row display limit
```

### Файлы, которые НЕ пушить
- `.env` — секреты
- `data/vitro.db` — база данных
- `COMMIT_MSG.txt` — временный файл для `git commit -F`
- `.gigacode/` — файлы планов

## Критические детали

### `_render_summary_table` в `vitro/ui/summary.py`
- `display_count = len(display_df)` — считать ДО `.style.apply()`
- Не использовать `len(display_df)` после `.style.apply()` — Styler не имеет len
- **DISPLAY_LIMIT больше НЕТ** — показываются все строки, строка ИТОГО всегда видна
- `ws.freeze_panes = "A2"` — закреплённый заголовок в Excel
- Ширина колонок: A=20, B=50, C-J=14

### `_sheet_groups_to_sql()` в `vitro/ui/summary.py`
- Генерирует SQL-условие для фильтра по группам статусов листов.
- Использует `IN (...)` для обычных групп.
- Для группы «Прочее» — `NOT IN (...) OR status IS NULL OR status = ''`.
- Используется в `_load_scale()` и `_load_status_distribution_by_filter()`.

### `_LAST_FILTERS` в `vitro/ui/summary.py`
- Расширен ключом `sheet_groups` — хранит выбранные группы статусов листов.
- Передаётся в `_filter_key()` для кэширования бублика Секции 1.

### `_build_summary_rows`
- Использует `our_status` (не `status`): `"new"`, `"in_work"`, `"rejected"`, `"discussion"`
- `n_customer = len(customer)` где `customer = sub[sub["holder"] == "customer"]`
- `active_total = n_new + n_in_work + n_rejected + n_discussion + n_customer`

### `_export_summary_to_excel`
- Выгружает `view_with_total` (все строки + ИТОГО)
- Цвета заголовков: бледные (#D6E4F0, #FCE4E4, #FFF2CC, #D0ECE7, #D9EAD3)
- Границы на всю таблицу, автофильтр, перенос строк в колонке B

## Структура проекта
```
Vitro_tracker/
├── app.py                     # Главный Streamlit (8 вкладок)
├── sync_runner.py             # Синхронизация
├── requirements.txt
├── .env                       # НЕ в git
├── data/vitro.db              # НЕ в git
├── AGENTS.md                  # Инструкции для AI-агентов
├── CONTEXT.md                 # Для загрузки в AI-чаты
├── README.md
└── vitro/
    ├── config.py
    ├── disciplines.py
    ├── sharepoint.py
    ├── sqlite_db.py
    ├── sync.py
    ├── workdays.py
    ├── text_utils.py
    ├── xlsx_builder.py
    ├── pdf_builder.py
    └── ui/
        ├── summary.py         # Сводка (последние изменения)
        ├── dynamics.py
        ├── authors.py
        ├── deadlines.py       # Единый источник (_load_all_categorized)
        ├── categories.py
        ├── search.py
        ├── export.py
        └── admin.py
```

## Команды
```bash
streamlit run app.py
.venv\Scripts\python.exe sync_runner.py
.venv\Scripts\python.exe scripts\check.py
git commit -F COMMIT_MSG.txt
```

## Договорённости
- Пользователь — не программист. Объяснять пошагово.
- Давать ПОЛНЫЙ код файлов (не фрагменты).
- Писать на русском.
- Каждое изменение — коммит в Git.
- `COMMIT_MSG.txt` — можно создавать, но НИКОГДА не пушить.
