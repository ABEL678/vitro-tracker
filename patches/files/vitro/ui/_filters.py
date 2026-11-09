# vitro/ui/_filters.py
"""
Общие фильтры для вкладок Vitro-tracker.

Состав:
  - Статусы листов (группы + обратный маппинг в сырые значения) и хелперы SQL.
  - Статусы замечаний.
  - Справочник «Расположение» (4-й блок шифра комплекта) — BLOCK_OPTIONS.
  - Общие справочники для каскадных фильтров (дисциплины / разделы /
    комплекты) — до версии 1.4 они дублировались в каждой вкладке.
  - Фильтрация DataFrame по стандартному набору фильтров (apply_df_filters).
  - ЕДИНЫЙ СТАНДАРТ БЛОКА ФИЛЬТРОВ — render_standard_filters().
    Эталон — блок фильтров вкладки «Сводка по проекту»:
      Ряд 1 (каскад): Дисциплина → Раздел → Расположение → Комплект → Марка.
      Ряд 2: Статус замечания + Статус листа.

Используется во всех вкладках (summary, dynamics, authors, deadlines,
categories, search, export, plan).
"""

import pandas as pd
import streamlit as st

from vitro.disciplines import discipline_name
from vitro.sqlite_db import get_conn


# ---------------------------------------------------------------------------
#  Статусы листов
# ---------------------------------------------------------------------------
SHEET_GROUP_OPTIONS = [
    "A — утверждён",
    "B — к сдаче",
    "C — в работе",
    "И — информационный",
    "На согласовании Заказчика",
    "Готовится к загрузке",
    "Аннулировано",
    "Прочее",
]

# Обратный маппинг: группа → список сырых статусов (для SQL-фильтра)
_SHEET_GROUP_TO_RAW = {
    "A — утверждён":              ["A"],
    "B — к сдаче":                ["B"],
    "C — в работе":               ["C"],
    "И — информационный":         ["И"],
    "На согласовании Заказчика":  ["ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"],
    "Готовится к загрузке":       ["НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"],
    "Аннулировано":               ["АННУЛИРОВАНО"],
    # «Прочее» обрабатывается отдельно — через NOT IN
}

# Все известные сырые статусы — для группы «Прочее»
_ALL_KNOWN_RAW_STATUSES = [
    "A", "B", "C", "И",
    "ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ",
    "НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО",
    "АННУЛИРОВАНО",
]


# ---------------------------------------------------------------------------
#  Статусы замечаний
# ---------------------------------------------------------------------------
REMARK_STATUS_OPTIONS = [
    "Новое",
    "Принято в работу",
    "Не принято",
    "К обсуждению",
    "Выполнено",
]


# ---------------------------------------------------------------------------
#  Расположение (4-й блок шифра комплекта)
#  Единый справочник — до версии 1.4 дублировался в 4 вкладках.
# ---------------------------------------------------------------------------
BLOCK_OPTIONS = {
    "Корпус 1":                    "1",
    "Корпус 2":                    "2",
    "Общие":                       "0",
    "Стилобат":                    "С",
    "Газовая котельная":           "ГК",
    "Генплан":                     "ГП",
    "Автомобильные дороги (УДС)":  "А",
}


# ---------------------------------------------------------------------------
#  Хелперы: блоки шифра
# ---------------------------------------------------------------------------
def _complex_col(df: pd.DataFrame) -> str:
    """
    Возвращает имя колонки с шифром комплекта в DataFrame:
    'complex' (служебные DataFrame) или 'Комплект' (результаты SQL
    с русскими алиасами, например в Поиске и Экспорте).
    """
    for c in ("complex", "Комплект"):
        if c in df.columns:
            return c
    return ""


def _extract_2nd_block(complex_code) -> str:
    """
    Вытащить 2-й блок шифра комплекта (марку).
    Пример: АТ-РД-КЖ1-1-00 → КЖ1.
    Возвращает верхний регистр или пустую строку.
    """
    if not complex_code:
        return ""
    parts = str(complex_code).split("-")
    if len(parts) >= 3:
        return parts[2].strip().upper()
    return ""


def _extract_4th_block(complex_code) -> str:
    """
    Вытащить 4-й блок шифра комплекта (расположение).
    Пример: АТ-РД-КЖ1-1-00 → 00.
    """
    if not complex_code:
        return ""
    parts = str(complex_code).split("-")
    if len(parts) >= 4:
        return parts[3].strip().upper()
    return ""


def _filter_by_marka(df, marka: str):
    """
    Строгий фильтр DataFrame по 2-му блоку шифра комплекта (марке).
    marka='КЖ1' → оставит только комплекты, у которых блок 2 = 'КЖ1'.
    Не найдёт КЖ11, КЖ10, КЖ1.2.
    Если marka пустая или df пустой — возвращает df без изменений.
    Работает и с колонкой 'complex', и с колонкой 'Комплект'.
    """
    if marka is None or df is None or df.empty:
        return df
    m = marka.strip().upper()
    if not m:
        return df
    col = _complex_col(df)
    if not col:
        return df
    mask = df[col].apply(_extract_2nd_block) == m
    return df[mask]


def _filter_by_blocks(df: pd.DataFrame,
                       block_codes: tuple = ()) -> pd.DataFrame:
    """
    Фильтр DataFrame по 4-му блоку шифра (расположению).
    Работает и с колонкой 'complex', и с колонкой 'Комплект'.
    """
    if df is None or df.empty or not block_codes:
        return df
    col = _complex_col(df)
    if not col:
        return df
    blocks = df[col].apply(_extract_4th_block)
    return df[blocks.isin(block_codes)]


# ---------------------------------------------------------------------------
#  Хелперы: SQL по группам статусов листов
# ---------------------------------------------------------------------------
def _doc_status_group(raw) -> str:
    """Сырой doc_status → группа (как в бублике Секции 2 Сводки)."""
    if raw is None:
        return "Прочее"
    s = str(raw).strip().upper()
    if s == "A":
        return "A — утверждён"
    if s == "B":
        return "B — к сдаче"
    if s == "C":
        return "C — в работе"
    if s == "И":
        return "И — информационный"
    if s in ("ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"):
        return "На согласовании Заказчика"
    if s in ("НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"):
        return "Готовится к загрузке"
    if s == "АННУЛИРОВАНО":
        return "Аннулировано"
    return "Прочее"


def _sheet_groups_to_sql(sheet_groups: tuple, doc_alias: str = "d"):
    """
    Возвращает (sql_condition, params) для фильтра по группам статусов листов.
    sql_condition — строка вида " AND (...)" или "".
    Использует NOT IN для группы «Прочее».
    """
    if not sheet_groups:
        return "", []

    raw = []
    for g in sheet_groups:
        raw += _SHEET_GROUP_TO_RAW.get(g, [])

    conditions = []
    params: list = []

    if raw:
        conditions.append(
            f"{doc_alias}.status IN ({','.join('?' * len(raw))})"
        )
        params += raw

    if "Прочее" in sheet_groups:
        placeholders = ",".join("?" * len(_ALL_KNOWN_RAW_STATUSES))
        conditions.append(
            f"({doc_alias}.status NOT IN ({placeholders}) "
            f"OR {doc_alias}.status IS NULL "
            f"OR {doc_alias}.status = '')"
        )
        params += _ALL_KNOWN_RAW_STATUSES

    if not conditions:
        return "", []

    return " AND (" + " OR ".join(conditions) + ")", params


def _sheet_groups_to_sql_or(sheet_groups: tuple, doc_alias: str = "d"):
    """
    Вариант _sheet_groups_to_sql, где группы соединяются через OR
    и условие НЕ начинается с " AND " — для вставки внутрь
    уже открытой скобки (например, внутри "(... OR ...)").
    Возвращает (sql_fragment, params) либо ("", []).
    """
    cond, params = _sheet_groups_to_sql(sheet_groups, doc_alias)
    if not cond:
        return "", []
    # _sheet_groups_to_sql возвращает " AND ( ... )" — отрезаем " AND "
    return "(" + cond[len(" AND ("):], params


# ---------------------------------------------------------------------------
#  Общие справочники для каскадных фильтров
#  До версии 1.4 эти три функции дублировались в каждой вкладке.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def load_discipline_options() -> dict[str, str]:
    """{код дисциплины: 'код — название'} из таблицы documents."""
    with get_conn() as conn:
        codes = [r["discipline"] for r in conn.execute(
            "SELECT DISTINCT discipline FROM documents "
            "WHERE discipline IS NOT NULL ORDER BY discipline")]
    return {c: f"{c} — {discipline_name(c)}" for c in codes}


@st.cache_data(ttl=600, show_spinner=False)
def load_section_options(disciplines: tuple = ()) -> dict[str, str]:
    """{раздел: раздел} с каскадом по дисциплинам."""
    with get_conn() as conn:
        if disciplines:
            ph = ",".join("?" * len(disciplines))
            rows = conn.execute(f"""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                  AND discipline IN ({ph})
                ORDER BY section
            """, tuple(disciplines)).fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                ORDER BY section
            """).fetchall()
    return {r["section"]: r["section"] for r in rows}


@st.cache_data(ttl=600, show_spinner=False)
def load_kit_options(disciplines: tuple = (),
                     sections: tuple = (),
                     block_codes: tuple = ()) -> dict[str, str]:
    """{шифр: 'шифр — название'} с каскадом по дисциплинам, разделам
    и расположению (4-й блок)."""
    with get_conn() as conn:
        where = ["c.code IS NOT NULL"]
        params: list = []

        if disciplines:
            where.append(
                f"c.discipline IN ({','.join('?' * len(disciplines))})")
            params += list(disciplines)

        if sections:
            ph = ",".join("?" * len(sections))
            where.append(f"""c.code IN (
                SELECT DISTINCT complex FROM documents
                WHERE section IN ({ph}) AND complex IS NOT NULL
            )""")
            params += list(sections)

        rows = conn.execute(f"""
            SELECT c.code, c.name FROM complexes c
            WHERE {' AND '.join(where)}
            ORDER BY c.code
        """, tuple(params)).fetchall()

    result = {r["code"]: f"{r['code']} — {r['name']}"
                       if r["name"] else r["code"]
              for r in rows}

    # Фильтр по расположению (4-й блок) — в Python
    if block_codes:
        filtered = {}
        for code, label in result.items():
            block = _extract_4th_block(code)
            if block in block_codes:
                filtered[code] = label
        return filtered
    return result


# ---------------------------------------------------------------------------
#  Фильтрация DataFrame по стандартному набору фильтров
#  Тот же порядок условий, что в Сводке по проекту (_filter_active).
# ---------------------------------------------------------------------------
def apply_df_filters(df: pd.DataFrame,
                     disciplines: tuple = (),
                     sections: tuple = (),
                     kits: tuple = (),
                     block_codes: tuple = (),
                     remark_statuses: tuple = (),
                     sheet_groups: tuple = (),
                     marka: str = "",
                     sheet_status_col: str = "doc_status",
                     remark_status_col: str = "status") -> pd.DataFrame:
    """
    Универсальный фильтр DataFrame по стандартным фильтрам вкладок.

    sheet_status_col  — колонка с сырым статусом листа (по умолчанию
                        'doc_status', как в _load_all_categorized).
    remark_status_col — колонка со статусом замечания
                        ('status' в _load_all_categorized,
                         'Статус' в результатах Поиска/Экспорта).
    """
    if df is None or df.empty:
        return df
    out = df
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        col = _complex_col(out)
        if col:
            out = out[out[col].isin(kits)]
    if block_codes:
        out = _filter_by_blocks(out, block_codes)
    if remark_statuses and remark_status_col in out.columns:
        out = out[out[remark_status_col].isin(remark_statuses)]
    if sheet_groups and sheet_status_col in out.columns:
        out = out[out[sheet_status_col]
                  .apply(_doc_status_group).isin(sheet_groups)]
    if marka:
        out = _filter_by_marka(out, marka)
    return out


# ===========================================================================
#  ЕДИНЫЙ СТАНДАРТ ФИЛЬТРОВ (эталон — «Сводка по проекту»)
# ===========================================================================
def render_standard_filters(
    key_prefix: str = "",
    expander_label: str = "Фильтры",
    expanded: bool = True,
    show_remark_status: bool = True,
    show_sheet_status: bool = True,
    remark_status_options: tuple = tuple(REMARK_STATUS_OPTIONS),
    kit_options_fn=None,
    row1_extra=None,
    extra_row=None,
) -> dict:
    """
    Единый блок фильтров для всех вкладок.

    Структура (эталон — «Сводка по проекту»):
      Ряд 1 (5 колонок, каскад):
        Дисциплина → Раздел → Расположение → Комплект → Марка комплекта.
        row1_extra(col, kits) — необязательная 6-я колонка
        (например, «Лист» на вкладке «Сроки»).
      Ряд 2 (2 колонки):
        Статус замечания + Статус листа
        (отключается флагами show_remark_status / show_sheet_status).
      extra_row() -> dict — необязательный 3-й ряд с вкладко-специфичными
        фильтрами (автор, категория, лимит строк, период...).

    Параметры:
      key_prefix             — префикс ключей виджетов
                               ('sum_', 'dyn_', 'dl_' ...). Сохраняет
                               прежние ключи каждой вкладки, поэтому
                               выбранные фильтры не сбрасываются.
      kit_options_fn         — функция (disc, sect, block_codes) -> dict
                               для опций «Комплект». По умолчанию —
                               load_kit_options (справочник из Витро).
                               Вкладка «План выдачи» подаёт свою,
                               объединяющую Витро и ведомости.
      remark_status_options  — переопределение списка статусов замечаний
                               (например, Поиск и Экспорт показывают
                               также «Закрыто» и «Аннулировано»).

    Возвращает dict:
      disc             — tuple выбранных кодов дисциплин
      section          — tuple выбранных разделов
      kits             — tuple выбранных шифров комплектов
      block_codes      — tuple кодов расположения (4-й блок)
      marka            — строка марки (2-й блок) или ""
      remark_statuses  — tuple статусов замечаний или ()
      sheet_groups     — tuple групп статусов листов или ()
      extra            — dict, который вернул extra_row (или {})
    """
    if kit_options_fn is None:
        kit_options_fn = load_kit_options

    result: dict = {
        "disc": (), "section": (), "kits": (), "block_codes": (),
        "marka": "", "remark_statuses": (), "sheet_groups": (),
        "extra": {},
    }

    with st.expander(expander_label, expanded=expanded):
        # ------- Ряд 1: каскад + марка (+ опциональная 6-я колонка) -------
        if row1_extra is not None:
            c1, c2, c3, c4, c5, c6 = st.columns(6)
        else:
            c1, c2, c3, c4, c5 = st.columns(5)

        # 1. Дисциплина
        with c1:
            disc_options = load_discipline_options()
            sel_disc_labels = st.multiselect(
                "Дисциплина",
                options=list(disc_options.values()),
                placeholder="Все",
                key=f"{key_prefix}disc",
            )
            sel_disc = [code for code, label in disc_options.items()
                        if label in sel_disc_labels]

        # 2. Раздел (зависит от дисциплины)
        section_options = load_section_options(
            tuple(sel_disc) if sel_disc else ())
        with c2:
            if section_options:
                sel_section_labels = st.multiselect(
                    "Раздел",
                    options=list(section_options.values()),
                    placeholder="Все",
                    key=f"{key_prefix}section",
                )
                sel_section = [
                    code for code, label in section_options.items()
                    if label in sel_section_labels
                ]
            else:
                sel_section = []
                st.multiselect(
                    "Раздел", options=[],
                    placeholder="—",
                    disabled=True, key=f"{key_prefix}section_empty",
                )

        # 3. Расположение
        with c3:
            sel_blocks = st.multiselect(
                "Расположение",
                options=list(BLOCK_OPTIONS.keys()),
                placeholder="Все",
                key=f"{key_prefix}blocks",
            )
            block_codes = tuple(BLOCK_OPTIONS[b] for b in sel_blocks)

        # 4. Комплект (зависит от всех трёх)
        kit_options = kit_options_fn(
            tuple(sel_disc) if sel_disc else (),
            tuple(sel_section) if sel_section else (),
            block_codes,
        )
        with c4:
            sel_kit_labels = st.multiselect(
                "Комплект",
                options=list(kit_options.values()),
                placeholder="Все",
                key=f"{key_prefix}kit",
            )
            sel_kit = [code for code, label in kit_options.items()
                       if label in sel_kit_labels]

        # 5. Марка комплекта (блок 2 шифра)
        with c5:
            sel_marka = st.text_input(
                "Марка комплекта",
                placeholder="например: КЖ1",
                key=f"{key_prefix}marka",
                help="Точное совпадение 2-го блока шифра (марки). "
                     "КЖ1 не найдёт КЖ11 или КЖ10.",
            )

        # 6. Необязательная вкладко-специфичная колонка ряда 1
        kits_t = tuple(sel_kit)
        if row1_extra is not None:
            extra1 = row1_extra(c6, kits_t) or {}
            if extra1:
                result["extra"].update(extra1)

        # ------- Ряд 2: статусы -------
        sel_remark_statuses = []
        sel_sheet_groups = []
        if show_remark_status or show_sheet_status:
            st.write("")
            col_a, col_b = st.columns(2)
            if show_remark_status:
                with col_a:
                    sel_remark_statuses = st.multiselect(
                        "Статус замечания",
                        options=list(remark_status_options),
                        placeholder="Все",
                        key=f"{key_prefix}remark_status",
                    )
            if show_sheet_status:
                # если показываем только статус листа — ставим в левую колонку
                with (col_b if show_remark_status else col_a):
                    sel_sheet_groups = st.multiselect(
                        "Статус листа",
                        options=SHEET_GROUP_OPTIONS,
                        placeholder="Все",
                        key=f"{key_prefix}sheet_group",
                    )

        # ------- Ряд 3: вкладко-специфичные фильтры -------
        if extra_row is not None:
            extra = extra_row() or {}
            if extra:
                result["extra"].update(extra)

    result["disc"] = tuple(sel_disc)
    result["section"] = tuple(sel_section)
    result["kits"] = kits_t
    result["block_codes"] = tuple(block_codes)
    result["marka"] = (sel_marka or "").strip()
    result["remark_statuses"] = (
        tuple(sel_remark_statuses)
        if show_remark_status and sel_remark_statuses else ()
    )
    result["sheet_groups"] = (
        tuple(sel_sheet_groups)
        if show_sheet_status and sel_sheet_groups else ()
    )
    return result
