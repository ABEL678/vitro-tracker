# vitro/ui/search.py
"""
🔍 Поиск — поиск по тексту замечаний с фильтрами и экспортом.

Возможности:
  - Полнотекстовый поиск (LIKE %text%).
  - Каскадные фильтры: дисциплина → раздел → комплект.
  - Дополнительные фильтры: статус, автор, категория.
  - KPI по результатам: количество, распределение.
  - Экспорт результатов в Excel.
"""

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

CATEGORIES = [CAT_1, CAT_2, CAT_3, CAT_4]

STATUSES = [
    "Новое", "Принято в работу", "Не принято",
    "К обсуждению", "Выполнено", "Закрыто", "Аннулировано",
]


# ---------------------------------------------------------------------------
#  Справочники
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _load_discipline_options() -> dict[str, str]:
    with get_conn() as conn:
        codes = [r["discipline"] for r in conn.execute(
            "SELECT DISTINCT discipline FROM documents "
            "WHERE discipline IS NOT NULL ORDER BY discipline")]
    return {c: f"{c} — {discipline_name(c)}" for c in codes}


@st.cache_data(ttl=600, show_spinner=False)
def _load_section_options(disciplines: tuple[str, ...] = ()) -> dict[str, str]:
    with get_conn() as conn:
        if disciplines:
            placeholders = ",".join("?" * len(disciplines))
            rows = conn.execute(f"""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                  AND discipline IN ({placeholders})
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
def _load_kit_options(disciplines: tuple[str, ...] = (),
                      sections: tuple[str, ...] = ()) -> dict[str, str]:
    with get_conn() as conn:
        where = ["c.code IS NOT NULL"]
        params: list = []

        if disciplines:
            where.append(f"c.discipline IN ({','.join('?' * len(disciplines))})")
            params += list(disciplines)

        if sections:
            placeholders = ",".join("?" * len(sections))
            where.append(f"""c.code IN (
                SELECT DISTINCT complex FROM documents
                WHERE section IN ({placeholders}) AND complex IS NOT NULL
            )""")
            params += list(sections)

        rows = conn.execute(f"""
            SELECT c.code, c.name FROM complexes c
            WHERE {' AND '.join(where)}
            ORDER BY c.code
        """, tuple(params)).fetchall()

    return {r["code"]: f"{r['code']} — {r['name']}" if r["name"] else r["code"]
            for r in rows}


@st.cache_data(ttl=600, show_spinner=False)
def _load_author_options() -> list[str]:
    with get_conn() as conn:
        return [r["author"] for r in conn.execute("""
            SELECT DISTINCT author FROM comments
            WHERE author IS NOT NULL AND author <> ''
            ORDER BY author
        """)]


# ---------------------------------------------------------------------------
#  Основной поиск
# ---------------------------------------------------------------------------
def _search_comments(query: str,
                     disciplines=(), sections=(), kits=(),
                     statuses=(), authors=(), categories=(),
                     limit: int = 2000) -> pd.DataFrame:
    """
    Поиск замечаний по подстроке с фильтрами.
    Возвращает DataFrame с полной информацией.
    """
    q = """
        SELECT
            c.id AS "ID",
            d.discipline AS discipline,
            d.section AS section,
            d.complex AS "Комплект",
            REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS "Лист",
            d.name AS "Название листа",
            c.comment AS "Замечание",
            c.status AS "Статус",
            c.author AS "Автор",
            strftime('%d.%m.%Y %H:%M:%S', c.created) AS "Создано",
            c.category AS "Категория"
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE 1=1
    """
    params: list = []

    if query and query.strip():
        q += " AND LOWER(c.comment) LIKE LOWER(?)"
        params.append(f"%{query.strip()}%")

    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if sections:
        q += f" AND d.section IN ({','.join('?' * len(sections))})"
        params += list(sections)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if statuses:
        q += f" AND c.status IN ({','.join('?' * len(statuses))})"
        params += list(statuses)
    if authors:
        q += f" AND c.author IN ({','.join('?' * len(authors))})"
        params += list(authors)
    if categories:
        q += f" AND c.category IN ({','.join('?' * len(categories))})"
        params += list(categories)

    q += " ORDER BY c.created DESC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


# ---------------------------------------------------------------------------
#  KPI по результатам
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    total = len(df)
    if total == 0:
        return

    st.markdown(f"##### 🎯 Найдено замечаний: **{total:,}**".replace(",", " "))

    c1, c2, c3, c4, c5 = st.columns(5)

    def count_by(col, val):
        return int((df[col] == val).sum())

    c1.metric("Принято/корректное",       count_by("Категория", CAT_1))
    c2.metric("Формальное",               count_by("Категория", CAT_2))
    c3.metric("Доп.треб.",                count_by("Категория", CAT_3))
    c4.metric("Не принято/нарушение",     count_by("Категория", CAT_4))
    c5.metric("Без категории",
              int(df["Категория"].isna().sum()
                  + (df["Категория"] == "").sum()))


# ---------------------------------------------------------------------------
#  Экспорт в Excel
# ---------------------------------------------------------------------------
def _export_to_excel(df: pd.DataFrame, query: str) -> bytes:
    """Формирует XLSX в память и возвращает байты."""
    buf = io.BytesIO()

    export_df = df.drop(columns=["discipline", "section"], errors="ignore").copy()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Результаты поиска")

        # Автоширина
        ws = writer.sheets["Результаты поиска"]
        for col_idx, col_name in enumerate(export_df.columns, start=1):
            max_len = max(
                len(str(col_name)),
                export_df[col_name].astype(str).str.len().max() if len(export_df) else 0,
            )
            ws.column_dimensions[chr(64 + col_idx)].width = min(max_len + 2, 80)

    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Рендер
# ---------------------------------------------------------------------------
def render():
    st.header("🔍 Поиск по замечаниям")
    st.caption(
        "Полнотекстовый поиск по тексту замечаний с фильтрами и экспортом. "
        "Начните вводить запрос — результаты появятся ниже."
    )

    # --- Строка поиска ---
    query = st.text_input(
        "Поисковый запрос",
        placeholder="например: фундаментная плита / арматура / высотная отметка",
        key="search_query",
    )

    # --- Каскадные фильтры ---
    disc_options = _load_discipline_options()
    author_options = _load_author_options()

    with st.expander("🎛 Фильтры", expanded=True):
        c1, c2, c3 = st.columns(3)

        with c1:
            sel_disc_labels = st.multiselect(
                "Дисциплина",
                options=list(disc_options.values()),
                placeholder="Все дисциплины",
                key="search_disc",
            )
            sel_disc = [code for code, label in disc_options.items()
                        if label in sel_disc_labels]

        section_options = _load_section_options(
            tuple(sel_disc) if sel_disc else ())

        with c2:
            if section_options:
                sel_section_labels = st.multiselect(
                    "Раздел",
                    options=list(section_options.values()),
                    placeholder="Все разделы",
                    key="search_section",
                )
                sel_section = [code for code, label in section_options.items()
                               if label in sel_section_labels]
            else:
                sel_section = []
                st.multiselect(
                    "Раздел", options=[],
                    placeholder="Разделы не применимы",
                    disabled=True,
                    key="search_section_empty",
                )

        kit_options = _load_kit_options(
            tuple(sel_disc) if sel_disc else (),
            tuple(sel_section) if sel_section else (),
        )

        with c3:
            sel_kit_labels = st.multiselect(
                "Комплект",
                options=list(kit_options.values()),
                placeholder="Все комплекты",
                key="search_kit",
            )
            sel_kit = [code for code, label in kit_options.items()
                       if label in sel_kit_labels]

        c4, c5, c6 = st.columns(3)
        with c4:
            sel_status = st.multiselect(
                "Статус", STATUSES,
                placeholder="Все статусы", key="search_status")
        with c5:
            sel_author = st.multiselect(
                "Автор", author_options,
                placeholder="Все авторы", key="search_author")
        with c6:
            sel_category = st.multiselect(
                "Категория", CATEGORIES,
                placeholder="Все категории", key="search_category")

        c7, c8 = st.columns([1, 1])
        with c7:
            limit = st.number_input(
                "Лимит результатов", min_value=100, max_value=10000,
                value=2000, step=100, key="search_limit",
                help="Максимум строк, которые будут загружены для отображения",
            )
        with c8:
            # Показывать ли пустой поиск (без запроса)
            show_all = st.checkbox(
                "Показывать всё без запроса", value=False,
                key="search_show_all",
                help="Если выключено, результаты появятся только после ввода запроса",
            )

    # --- Условия запуска поиска ---
    if not query.strip() and not show_all:
        st.info("💡 Введите поисковый запрос — например, «фундамент» или «арматура».")
        return

    # --- Поиск ---
    with st.spinner("Поиск..."):
        df = _search_comments(
            query=query,
            disciplines=tuple(sel_disc) if sel_disc else (),
            sections=tuple(sel_section) if sel_section else (),
            kits=tuple(sel_kit) if sel_kit else (),
            statuses=tuple(sel_status) if sel_status else (),
            authors=tuple(sel_author) if sel_author else (),
            categories=tuple(sel_category) if sel_category else (),
            limit=int(limit),
        )

    if df.empty:
        st.warning(f"Ничего не найдено по запросу «{query}» с текущими фильтрами.")
        return

    # --- KPI ---
    _render_kpi(df)

    st.divider()

    # --- Таблица ---
    table_df = df.drop(columns=["discipline", "section"], errors="ignore")
    st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "Замечание":     st.column_config.TextColumn(width="large"),
            "Название листа": st.column_config.TextColumn(width="large"),
            "Создано":       st.column_config.TextColumn(width="medium"),
        },
    )

    st.caption(
        f"Показано **{len(table_df):,}** из **{len(df):,}** найденных строк "
        f"(лимит {int(limit):,}).".replace(",", " ")
    )

    # --- Экспорт ---
    st.divider()
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("📥 Подготовить выгрузку", use_container_width=True):
            xlsx_bytes = _export_to_excel(df, query)
            st.session_state["search_export_bytes"] = xlsx_bytes
            st.session_state["search_export_filename"] = (
                f"Поиск_{datetime.now():%Y%m%d_%H%M}.xlsx"
            )
            st.success("Файл готов")

    with c2:
        if "search_export_bytes" in st.session_state:
            st.download_button(
                "⬇️ Скачать Excel",
                data=st.session_state["search_export_bytes"],
                file_name=st.session_state.get(
                    "search_export_filename", "search.xlsx"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    # --- Подсказка ---
    st.info(
        "💡 **Совет.** Чтобы найти замечания с определённым словом "
        "в конкретном комплекте — задайте фильтр по комплекту и введите "
        "одно слово. Поиск регистронезависимый."
    )