# HANDOFF — Vitro-tracker

## Текущее состояние

### Последняя работа

**Документация обновлена** после серии из 11 задач (см. ниже).

### Что сделано за последние заходы

1. **Второй ряд фильтров в Сводке** — «Статус замечания» (5 сырых) + «Статус листа» (8 групп). DISPLAY_LIMIT убран — все строки разреза видны.
2. **Ссылки на Витро** — колонка «Витро» в трёх местах (Авторы drilldown, Сроки список, Поиск результаты). URL: `https://pdm-lakhta.ru/okhta/_layouts/15/Vitro/Viewer/PDF/web/Viewer.aspx?Id={guid}&version={v}&noteId={id}`.
3. **Рефакторинг `_filters.py`** — вынос общих фильтров (статусы листов, статусы замечаний, марка комплекта).
4. **`_urls.py`** — хелперы для URL Витро.
5. **`_auth.py`** — авторизация по требованию (диалог только при сохранении).
6. **Фильтр «Статус листа» в Поиске** — 8 групп.
7. **Фильтр «Статус листа» в Авторах** — прокинут во все функции аналитики.
8. **Фильтр «Статус замечания» в Авторах** — 4 статуса (без «Выполнено»).
9. **Фильтр «Марка комплекта» (блок 2 шифра)** во всех 5 вкладках (Сводка, Сроки, Авторы, Категории, Поиск) — строгое совпадение.
10. **Авторизация + версия** — диалог ввода имени только при сохранении, версия приложения в футере.
11. **Часовой пояс МСК** — все timestamp из БД показываются в МСК (+3), `vitro/time_utils.py`.

### Последние коммиты (main)

```
2ed75ab fix(time): show UTC timestamps in MSK (+3) across all tabs
8079662 feat(auth): on-demand user dialog + version in footer
140da6f feat(categories,search): add marka (block 2) filter — strict match
e6b4f04 feat(deadlines,authors): add marka (block 2) filter — strict match
7f8134f feat(summary): add marka (block 2) filter — strict match
691d8d8 feat(search): add direct links to Vitro comments in results
66969aa refactor(filters): move REMARK_STATUS_OPTIONS to _filters.py
a1764e7 feat(urls): extract build_vitro_comment_url to _urls.py + add links to authors and deadlines
3dbb23f feat(authors): add direct links to Vitro comments in drilldown
fbcc92a feat(sync): add vitro_guid and vitro_version to documents
7c8ff69 feat(authors): add sheet status filter to analytics tab
be860af feat(search): add sheet status filter
43f4105 refactor(ui): extract sheet-status filters to _filters.py
8f74804 docs(readme): add filters section + terminology rows
c14a6bd docs(agents): describe 2nd row filters in Summary
20c4e28 docs(handoff): update after 97dc07b — 2nd row filters
97dc07b fix(summary): filter Section 1 by sheet status + show all rows (no DISPLAY_LIMIT)
765c70d docs: add HANDOFF.md for AI agent handoff
ce612c1 fix(summary): fixed column widths and freeze header
62e06d3 fix(summary): replace all len(display_df) with display_count
```

### Файлы, которые НЕ пушить

- `.env` — секреты
- `data/vitro.db` — база данных
- `COMMIT_MSG.txt` — временный файл для `git commit -F`
- `.gigacode/` — файлы планов

## Критические детали

### `vitro/ui/_filters.py` — общие фильтры

Содержит:
- `SHEET_GROUP_OPTIONS` — 8 групп статусов листов.
- `REMARK_STATUS_OPTIONS` — 5 сырых статусов замечаний.
- `_SHEET_GROUP_TO_RAW` — обратный маппинг групп в сырые статусы.
- `_ALL_KNOWN_RAW_STATUSES` — для группы «Прочее».
- `_doc_status_group(raw)` — сырой doc_status → группа.
- `_sheet_groups_to_sql(sheet_groups, doc_alias)` — SQL-условие для фильтра по группам статусов листов.
- `_extract_2nd_block(complex_code)` — 2-й блок шифра (марка).
- `_filter_by_marka(df, marka)` — строгий Python-фильтр по марке.

Используется в: `summary.py`, `search.py`, `authors.py`, `deadlines.py`, `categories.py`.

### `vitro/ui/_urls.py` — ссылки на Витро

- `VITRO_BASE_URL = "https://pdm-lakhta.ru/okhta"`.
- `build_vitro_comment_url(vitro_guid, vitro_version, note_id)` — строит URL для ссылки на комментарий в Витро.

Используется в: `authors.py`, `deadlines.py`, `search.py`.

### `vitro/ui/_auth.py` — авторизация по требованию

- `ask_user_name_dialog()` — модальное окно для ввода имени пользователя.
- `ensure_user()` — возвращает имя из session_state или None (если отмена).

Используется в: `categories.py`, `authors.py`.

⚠️ Диалог показывается **ТОЛЬКО при сохранении**. Если пользователь отменил — сохранение прерывается.

### `vitro/time_utils.py` — время UTC → МСК

- `parse_utc(timestamp_str)` — парсит строку из БД.
- `to_msk(dt)` — сдвигает на +3 часа.
- `fmt_msk(timestamp_str, with_msk_label=False)` — форматирует timestamp.

⚠️ Все timestamp в БД — **UTC**. При показе — **МСК (+3)**.
- В хедерах (`app.py`, `deadlines.py`) — с пометкой «(МСК)».
- В логах и таблицах — без пометки.

### Поля `documents.vitro_guid` и `documents.vitro_version`

- Добавлены в схему БД (мягкая миграция).
- Заполняются при синхронизации (из `UniqueId` и `OData__UIVersionString` в SharePoint).
- Используются для построения ссылок на Витро.

### Фильтр «Марка комплекта»

- Строгое совпадение 2-го блока шифра (`АТ-РД-КЖ1-1-00` → `КЖ1`).
- `КЖ1` **не найдёт** `КЖ11`, `КЖ10`, `КЖ1.2`.
- В SQL: `LIKE '%-{marka}-%'` (в `_load_scale`, `_load_status_distribution_by_filter`).
- В Python: `_filter_by_marka(df, marka)` (в остальных функциях).

### `_render_summary_table` в `vitro/ui/summary.py`

- `display_count` — считать ДО `.style.apply()` (Styler не имеет len).
- **DISPLAY_LIMIT больше НЕТ** — показываются все строки, строка ИТОГО всегда видна.
- `ws.freeze_panes = "A2"` — закреплённый заголовок в Excel.
- Ширина колонок: A=20, B=50, C-J=14.

### `_sheet_groups_to_sql()` в `vitro/ui/_filters.py`

- Генерирует SQL-условие для фильтра по группам статусов листов.
- Использует `IN (...)` для обычных групп.
- Для группы «Прочее» — `NOT IN (...) OR status IS NULL OR status = ''`.
- Используется в `_load_scale()` и `_load_status_distribution_by_filter()`.

### `_LAST_FILTERS` в `vitro/ui/summary.py`

- Расширен ключами `sheet_groups`, `remark_status`, `marka` — хранит выбранные фильтры.
- Передаётся в `_filter_key()` для кэширования.

### `_build_summary_rows`

- Использует `our_status` (не `status`): `"new"`, `"in_work"`, `"rejected"`, `"discussion"`.
- `n_customer = len(customer)` где `customer = sub[sub["holder"] == "customer"]`.
- `active_total = n_new + n_in_work + n_rejected + n_discussion + n_customer`.

### `_export_summary_to_excel`

- Выгружает `view_with_total` (все строки + ИТОГО).
- Цвета заголовков: бледные (#D6E4F0, #FCE4E4, #FFF2CC, #D0ECE7, #D9EAD3).
- Границы на всю таблицу, автофильтр, перенос строк в колонке B.

## Структура проекта

```
Vitro_tracker/
├── app.py                     # Главный Streamlit (8 вкладок)
├── sync_runner.py             # Синхронизация по расписанию
├── requirements.txt
├── .env                       # НЕ в git
├── data/vitro.db              # НЕ в git
├── assets/logo.png
├── scripts/check.py           # Диагностика
├── AGENTS.md                  # Инструкции для AI-агентов
├── CONTEXT.md                 # Для загрузки в AI-чаты
├── README.md
└── vitro/
    ├── config.py              # .env + переменные (APP_VERSION)
    ├── disciplines.py         # Справочник дисциплин
    ├── sharepoint.py          # Загрузка из Витрокад
    ├── sqlite_db.py           # Работа с БД
    ├── sync.py                # Логика синхронизации
    ├── workdays.py            # Производственный календарь РФ
    ├── text_utils.py          # clean_leaf_key, normalize_homoglyphs
    ├── time_utils.py          # Время UTC → МСК
    ├── xlsx_builder.py        # Генерация XLSX
    ├── pdf_builder.py         # Генерация PDF
    └── ui/
        ├── _auth.py           # Авторизация по требованию
        ├── _filters.py        # Общие фильтры (статусы, марка)
        ├── _urls.py           # Ссылки на Витро
        ├── _utils.py          # download_plotly (только для Экспорта)
        ├── summary.py         # Сводка по проекту
        ├── dynamics.py        # Динамика (потоки по месяцам)
        ├── authors.py         # Авторы замечаний
        ├── deadlines.py       # Сроки + единый источник
        ├── categories.py      # Категории (ручная разметка)
        ├── search.py          # Поиск по тексту
        ├── export.py          # Экспорт
        └── admin.py           # Управление + Логи
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
