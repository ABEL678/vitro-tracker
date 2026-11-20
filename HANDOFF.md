# HANDOFF — Vitro-tracker

## Текущее состояние

### Последняя работа

**Ветка:** `release/1.5-plan`
**Последний коммит:** патч 22.6 — защита сводки «Плана выдачи» от текста в `qty`, `APP_VERSION` → «1.5 от 19.11.2026»

**Документация обновлена** после серии патчей 21 → 22.6 (см. ниже).

### Что сделано в релизе 1.5

1. **Патч 21 — «Статус комплекта»** во внешнем Excel: `vitro/sheets.py` → `get_kit_status()` / `get_kit_statuses()`. Правило: худший статус из A/B/C по уникальным листам комплекта; нет в Витро → «План (не загружен)».
2. **Патч 22 — «Ревизия комплекта» и «Дата ревизии»**: `get_kit_revision_info()` в `vitro/sheets.py`, `_apply_kit_statuses(..., rev_info)` в `export.py`. Ревизия — максимальная с числовым сравнением (`_rev_key`: «2.0» < «10.0»), дата — крайняя выгрузка в ДД.ММ.ГГГГ (`_format_rev_date`).
3. **Патч 22.2/22.3 — защита от «Ошибки в части содержимого»**: снятие Excel-таблиц (ListObject) с целевого листа; снятие и возврат merge-диапазонов при вставке строки.
4. **Патч 22.4 — геометрия полосы**: строка «База актуальна на …» вставляется **прямо над шапкой таблицы**, а не в строку 1 листа. Титульные строки бланка не сдвигаются. Текст объединён ровно над шапками трёх kit-колонок.
5. **Из 5d465a4 сохранено**: словарь `{строка: шифр}` — пустые строки внутри данных не сдвигают kit-значения вверх; `last_row = header_row_idx` — пустая таблица не даёт отрицательных диапазонов.
6. **Патч 22.5 — ArrowInvalid в журнале загрузок**: `_build_import_log_rows` не подставляет `""` в «Строк», колонка приводится к nullable `Int64`.
7. **Патч 22.6 — защита `qty`**: `_build_overview` приводит `qty` через `pd.to_numeric(..., errors="coerce")` перед `groupby` — текст/`inf`/дробь считаются отсутствием плана. `APP_VERSION` → **«1.5 от 19.11.2026»**.

### История релиза 1.4

1. **Единый источник листов** — `vitro/sheets.py` → `unique_vitro_sheets(rows)`.
2. **Вкладка «План выдачи»** — ведомости Excel, бублики «всего/план», детальная таблица.
3. **Единые фильтры** — `vitro/ui/_filters.py` → `render_standard_filters()` во всех вкладках.
4. **Патч 17**: сходимость «Выполнено» со Сводкой, редизайн Динамики, метрики листов A/B.
5. **Второй ряд фильтров** — «Статус замечания» + «Статус листа» (8 групп).
6. **Ссылки на Витро** — колонка «Витро» в трёх местах (Авторы, Сроки, Поиск).
7. **`_urls.py` / `_auth.py`** — URL Витро и авторизация по требованию.
8. **Фильтр «Марка комплекта»** во всех вкладках — строгое совпадение 2-го блока шифра.
9. **Версия приложения** в футере (`APP_VERSION` в `vitro/config.py`).
10. **Часовой пояс МСК** — `vitro/time_utils.py`.

### Последние коммиты (release/1.5-plan)

```
a06483f feat(export): patch 22.4 v4 - date strip directly above table header, title rows unsifted
62940f9 feat(export): patch 22.4 - date strip above header, text over kit columns
1f3fe00 fix(export): restore date strip above header, drop ListObjects and re-anchor merges
5d465a4 fix(export): move date strip below table, drop insert_rows in kit status export
3129ed7 feat(export): patch 22 - revision and revision date columns in Excel export
c7c0f73 Merge branch 'release/1.5-plan' into main (release 1.5 final)
5af8de9 fix(export): format kit status column — width, center alignment, fix duplicate return
4897107 fix(export): rewrite _xlsx_add_kit_status — simple style, no copy.copy
8252e90 Merge branch 'release/1.5-plan' into main (release 1.5)
62389c5 release: 1.5 — GIS sync, Excel status column, plan fixes, UI improvements
```

### Файлы, которые НЕ пушить

- `.env` — секреты
- `data/vitro.db` — база данных
- `COMMIT_MSG.txt` / `.commit_msg.txt` — временные файлы сообщений коммитов
- `.merge_msg.txt` — временный файл сообщения merge
- `.gigacode/` — файлы планов

## Критические детали

### `vitro/sheets.py` — единый источник листов + статусы/ревизии комплектов

- `unique_vitro_sheets(rows)` — дедуп ревизий: последняя по `(complex, leaf)`.
- `dedup_document_rows(rows)` — обёртка для pandas/списка.
- `normalize_kit_code(code)` — нормализация шифра: регистр, пробелы, латинские двойники → кириллица.
- `is_valid_kit_code(code)` / `looks_like_rd_code(name)` — валидация шифров.
- `sql_count_sheets(alias)` — SQL-выражение числа уникальных листов.
- **Патч 21:** `get_kit_status(code)` / `get_kit_statuses(codes=None)` — статус комплекта `A`/`B`/`C`/«План (не загружен)». `codes=None` → все комплекты базы, один проход по БД.
- **Патч 22:** `get_kit_revision_info(codes=None)` → `{шифр: {"revision": …, "date": …}}`. Внутренние хелперы: `_rev_key()` (числовое сравнение ревизий), `_format_rev_date()` (ISO → ДД.ММ.ГГГГ), `_kit_revision_from_rows()` (чистое ядро без БД). Листы — тот же `unique_vitro_sheets`, ревизия — максимум, дата — крайняя выгрузка. «И» / «АННУЛИРОВАНО» учитываются (информационные поля).
- Используется в: `dynamics.py`, `summary.py`, `export.py`, `plan.py`.

### `vitro/ui/export.py` — режим «Обновить статусы» (патчи 21/22)

Константы: `KIT_STATUS_COL`, `KIT_REVISION_COL`, `KIT_REV_DATE_COL`.

- `_xlsx_add_kit_status(source, sheet_name, kit_col, statuses, status_col_name, sync_ts)` — диспетчер по типу `source`: `pd.DataFrame` → `_styled_df_to_xlsx`, файл → `_styled_upload_to_xlsx`.
- `_styled_df_to_xlsx(df, sheet_name, sync_ts)` — режим «с нуля» (тесты): строка 1 полоса, строка 2 шапка, данные с 3-й.
- `_styled_upload_to_xlsx(...)` — правка **исходного** файла, форматирование сохраняется.
- `_apply_kit_statuses(df, col, statuses, rev_info=None)` — дописывает kit-колонки в конец. Без `rev_info` работает по-старому (одна колонка, патч 21).
- `_load_all_kit_statuses()` / `_load_all_kit_revision_info()` — `@versioned_cache(600)`, сбрасываются при синке.
- `_kit_sync_caption(sync_ts)` — текст полосы; `sync_ts=None` → «База актуальна на —».
- `_find_kit_code_sheet(uploaded)` / `_find_kit_code_column(df)` — поиск листа и колонки шифров.

#### Геометрия полосы даты (финальная, патч 22.4 v4)

- `strip_row = header_row_idx`, затем `ws.insert_rows(strip_row)` — полоса встаёт **прямо над шапкой**, титул бланка выше шапки не сдвигается.
- Заливка `E8F0F8` + рамка `#808080` — на все ячейки полосы от колонки 1 до `col_date`; высота 22.
- Текст — в `col_status` (первая kit-колонка), объединён `col_status..col_date` (ровно над шапками трёх kit-колонок), жирный, по центру. НЕ `A1:{last}1`.
- Шапки kit-колонок — заливка `D9E1F2`, все ячейки таблицы — тонкие границы `#808080`, kit-колонки по центру.
- Автофильтр — `A{header_row}:{last_letter}{last_row}`, то есть шапка + данные. Полоса в него не входит. Без данных → `auto_filter.ref = None`.

#### Два источника «Ошибки в части содержимого» (обязательно оба фикса)

1. **22.2 — ListObject.** `ws.insert_rows()` не сдвигает `table.ref`. Таблицы снимаются с целевого листа ДО правок: `for _t in list(ws.tables): del ws.tables[_t]`. Иначе Excel пишет «Удалено: автофильтр … /xl/tables/table1.xml». Таблицы других листов не трогаются.
2. **22.3 — merge.** `openpyxl` не сдвигает merge при вставке строки. `src_merges` собираются и снимаются ДО `insert_rows`, возвращаются ПОСЛЕ через `range_boundaries`: `_r1 >= strip_row` → сдвиг обеих границ на +1; иначе если `_r2 >= strip_row` → расширение вниз (merge накрывал место вставки, как делает сам Excel); выше полосы — без изменений.

⚠️ Оба фикса работают только вместе с `insert_rows`. Если убрать снятие таблиц — вернётся ошибка `table1.xml`; если убрать снятие merge — пересечение с полосой.

⚠️ Не-анкерные ячейки merge (`J2`/`K2` при полосе `I2:K2`) в openpyxl остаются с `fill_type=None` — Excel рисует их заливкой анкера. Это ожидаемое поведение, не дефект.

### Тесты патчей 21/22

- `scripts/test_patch21.py` — статусы комплектов.
- `scripts/test_patch22.py` — **73 проверки**, режим сборки из DataFrame.
- `scripts/test_patch22_3.py` — **61 проверка**, режим правки загруженного файла: Excel-таблица, merge-заголовок, широкий баннер, пустая строка в данных, примечание под таблицей, пустая таблица, чужая таблица, титульные строки выше шапки.
- `scripts/test_patch22_5.py` — **39 проверок**, журнал загрузок «Плана выдачи» (Arrow/NumberColumn) и детальная таблица с позицией без планового количества.
- `scripts/test_patch22_6.py` — **23 проверки**, защита `_build_overview` от текста/`inf` в `qty` + `APP_VERSION`.

### Патч 22.5 — pyarrow ArrowInvalid в «Плане выдачи»

**Симптом на сервере:** вкладка «План выдачи» падала с
`pyarrow.lib.ArrowInvalid: Could not convert '' with type str: tried to convert to int64 — Conversion failed for column Строк with type object`.

**Причина:** журнал загрузок (`_render_import_log`) подставлял `""` вместо пустого числа строк, а колонка «Строк» рендерится `st.column_config.NumberColumn` → Arrow приводил object-колонку к `int64` и падал. Пустое значение возникает законно: `log_event("plan_clear", …)` вызывается **без** `rows` (по умолчанию `0`), и `if r["rows"]` для `0` ложно — как и для `NULL`.

**Фикс:** сборка строк вынесена в `_build_import_log_rows(rows)`:
- пустое → `None`, а не `""`;
- колонка приводится к nullable `Int64` — в таблице видно `421`, а не `421.0`, и Arrow получает `int64`;
- пустая выборка → DataFrame с теми же колонками (без падения).

⚠️ `0` в `log.rows` означает «событие без числа строк», а не «распознано 0 строк» — поэтому это пропуск, а не нуль.

**Детальная таблица** (`_build_sheet_plan_table`) позицию с `qty = NULL` обрабатывает корректно: строка не теряется, а в сводке `_build_overview` такая позиция даёт `0` плановых листов (проверено тестом).

### Патч 22.6 — защита сводки от текста в `qty` + `APP_VERSION` 1.5

**Симптом (закрыт):** если в `plan_drawings.qty` попадёт текст (SQLite не проверяет типы `INTEGER`-колонок), `_build_overview` падал на `groupby("kit_code")["qty"].sum()` — `TypeError: can only concatenate str (not "int") to str` (строки склеиваются, потом `int()` даёт `ValueError`). Путь недостижим на сервере (парсер ведомостей пишет `NULL`/число), но лечится одной строкой.

**Фикс** — `vitro/ui/plan.py`, `_build_overview`:
```python
qty_num = pd.to_numeric(drawings["qty"], errors="coerce")
qty_num = qty_num.mask(qty_num.isin([float("inf"), float("-inf")]))
plan_qty = (drawings.assign(qty=qty_num)
            .groupby("kit_code")["qty"].sum().fillna(0).to_dict())
```
- нечисловой текст → `NaN` → плановых листов `0` (как `NULL`, поведение 22.5 сохранено);
- число текстом (`"4"`, `" 7 "`, `"1e3"`) → число (`errors="coerce"` его парсит);
- `float("inf")` → пропуск: `int(inf)` даёт `OverflowError` и роняет сводку;
- `"3,5"` (дробь с запятой) → `NaN` → `0`: план в целых листах.

**`vitro/config.py`:** `APP_VERSION = "1.5 от 19.11.2026"` (было «1.4 от 12.11.2026»). Футер `app.py` берёт значение из конфига — правок не требует.

**Тесты:** `scripts/test_patch22_6.py` — **23 проверки**: текст в `qty`, смешанный набор (числа + текст + `NULL`), число текстом, детальная таблица, `_plan_totals`, регресс `NULL`, пустой план, `inf` и «3,5».
- Все тесты создают временную БД через `SQLITE_PATH` **до** импорта `vitro.sqlite_db` — реальная `data/vitro.db` не открывается.

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

### `vitro/ui/dynamics.py` — план и бублики

- `_load_plan(disciplines, sections, kits, block_codes)` — план из `plan_drawings` + `plan_kits` с фильтрами.
- `_load_doc_totals(disciplines, sections, kits, block_codes)` — уникальные листы через `unique_vitro_sheets`.
- `_render_progress_donuts()` — бублики «выполнено/план» с учётом фильтров.

### `_build_summary_rows`
- `n_customer = len(customer)` где `customer = sub[sub["holder"] == "customer"]`.
- `active_total = n_new + n_in_work + n_rejected + n_discussion + n_customer`.

### `_export_summary_to_excel`

- Выгружает `view_with_total` (все строки + ИТОГО).
- Цвета заголовков: бледные (#D6E4F0, #FCE4E4, #FFF2CC, #D0ECE7, #D9EAD3).
- Границы на всю таблицу, автофильтр, перенос строк в колонке B.

## Структура проекта

```
Vitro_tracker/
├── app.py                     # Главный Streamlit (9 вкладок, План выдачи — первая)
├── sync_runner.py             # Синхронизация по расписанию
├── requirements.txt
├── .env                       # НЕ в git
├── data/vitro.db              # НЕ в git
├── assets/logo.png
├── AGENTS.md                  # Инструкции для AI-агентов
├── CONTEXT.md                 # Для загрузки в AI-чаты
├── HANDOFF.md                 # Этот файл
├── README.md
├── patches/                   # Патчи по релизам (release_1.5/…)
├── scripts/                   # Диагностика + регресс-тесты
│   ├── check.py               # Диагностика
│   ├── test_patch21.py        # Статусы комплектов
│   ├── test_patch22.py        # Ревизии (режим DataFrame, 73 проверки)
│   ├── test_patch22_3.py      # Правка загруженного файла (61 проверка)
│   └── test_patch22_5.py      # Журнал загрузок + детальная таблица (39)
└── vitro/
    ├── config.py              # .env + переменные (APP_VERSION)
    ├── disciplines.py         # Справочник дисциплин
    ├── sharepoint.py          # Загрузка из Витрокад
    ├── sqlite_db.py           # Работа с БД (+ plan_kits, plan_drawings)
    ├── plan_xlsx.py           # Шаблоны/парсеры/выгрузка ведомостей плана
    ├── sync.py                # Логика синхронизации
    ├── workdays.py            # Производственный календарь РФ
    ├── text_utils.py          # clean_leaf_key, normalize_homoglyphs
    ├── time_utils.py          # Время UTC → МСК
    ├── _cache.py              # versioned_cache (TTL + PRAGMA data_version)
    ├── xlsx_builder.py        # Генерация XLSX
    ├── pdf_builder.py         # Генерация PDF
    ├── sheets.py              # Листы + статусы/ревизии комплектов
    └── ui/
        ├── _auth.py           # Авторизация по требованию
        ├── _filters.py        # Общие фильтры (статусы, марка)
        ├── _urls.py           # Ссылки на Витро
        ├── _utils.py          # download_plotly (только для Экспорта)
        ├── summary.py         # Сводка по проекту
        ├── dynamics.py        # Динамика (потоки по месяцам)
        ├── authors.py         # Авторы замечаний
        ├── deadlines.py       # Сроки + единый источник
        ├── plan.py            # План выдачи комплектов
        ├── categories.py      # Категории (ручная разметка)
        ├── search.py          # Поиск по тексту
        ├── export.py          # Экспорт (+ «Обновить статусы»)
        └── admin.py           # Управление + Логи
```

## Патч 23 (планируется)

- Уточнение правил «Ревизия комплекта» по итогам обкатки на реальных перечнях.
- Проверить открытие выгрузки в старых версиях Excel (2016 / LTSC).

## Команды

```bash
streamlit run app.py
.venv\Scripts\python.exe sync_runner.py
.venv\Scripts\python.exe scripts\check.py
.venv\Scripts\python.exe scripts\test_patch22.py
.venv\Scripts\python.exe scripts\test_patch22_3.py
.venv\Scripts\python.exe scripts\test_patch22_5.py
git commit -F COMMIT_MSG.txt
```

## Применение патчей (важно)

Патчи лежат в `patches/release_1.5/`. Схема применения:

```bash
git checkout <base> -- vitro/sheets.py vitro/ui/export.py
rm -f scripts/test_patch22.py scripts/test_patch22_3.py
git apply patches/release_1.5/<patch>.patch
```

⚠️ Команда из шапки патча (`git checkout <base> -- … scripts/test_patch22.py`) падает целиком, потому что в базовых коммитах этого файла нет — патч создаёт его как `new file`. Разбивайте на два шага, как выше.

⚠️ После `git checkout <base> -- <files>` индекс содержит базовые версии — перед коммитом нужен `git add -A`, иначе файлы уедут назад.

## Договорённости

- Пользователь — не программист. Объяснять пошагово.
- Давать ПОЛНЫЙ код файлов (не фрагменты).
- Писать на русском.
- Каждое изменение — коммит в Git.
- `COMMIT_MSG.txt` — можно создавать, но НИКОГДА не пушить.
