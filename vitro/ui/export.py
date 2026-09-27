# vitro/ui/export.py
"""
📄 Экспорт — выгрузка сводок, замечаний и аналитики в Excel и PDF.

Разделы:
  - Сводные отчёты: дисциплины, комплекты, разделы.
  - Замечания: с фильтрами + выбор колонок.
  - Аналитика: авторы, динамика, топ отстающих.
"""

import io
from datetime import datetime

import pandas as pd
import streamlit as st
from pathlib import Path

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.pdf_builder import build_pdf


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

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


# ---------------------------------------------------------------------------
#  Сводки
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_discipline_summary() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                d.discipline AS "Код дисциплины",
                COUNT(DISTINCT d.complex) AS "Комплектов",
                COUNT(DISTINCT d.id) AS "Листов",
                COUNT(c.id) AS "Замечаний",
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS "Закрыто",
                SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS "Аннулировано",
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS "Активных",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Принято",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Формальное",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Доп.треб.",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Не принято",
                SUM(CASE WHEN c.category IS NULL OR c.category = '' THEN 1 ELSE 0 END) AS "Без категории"
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
            ORDER BY d.discipline
        """, conn, params=(CAT_1, CAT_2, CAT_3, CAT_4))


@st.cache_data(ttl=300, show_spinner=False)
def _load_complex_summary() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                d.discipline AS "Дисциплина",
                d.complex AS "Комплект",
                COUNT(DISTINCT d.id) AS "Листов",
                SUM(CASE WHEN UPPER(d.status) = 'A' THEN 1 ELSE 0 END) AS "Статус A",
                SUM(CASE WHEN UPPER(d.status) = 'B' THEN 1 ELSE 0 END) AS "Статус B",
                SUM(CASE WHEN UPPER(d.status) = 'C' THEN 1 ELSE 0 END) AS "Статус C",
                SUM(CASE WHEN d.status = 'И' THEN 1 ELSE 0 END) AS "Информац.",
                COUNT(c.id) AS "Замечаний",
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS "Закрыто",
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS "Активных"
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
            GROUP BY d.discipline, d.complex
            ORDER BY d.discipline, d.complex
        """, conn)


@st.cache_data(ttl=300, show_spinner=False)
def _load_section_summary() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                d.discipline AS "Дисциплина",
                d.section AS "Раздел",
                COUNT(DISTINCT d.complex) AS "Комплектов",
                COUNT(DISTINCT d.id) AS "Листов",
                COUNT(c.id) AS "Замечаний",
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS "Закрыто",
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS "Активных"
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.section IS NOT NULL AND d.section <> ''
            GROUP BY d.discipline, d.section
            ORDER BY d.discipline, d.section
        """, conn)


# ---------------------------------------------------------------------------
#  Замечания с фильтрами
# ---------------------------------------------------------------------------
def _load_comments_filtered(query: str,
                            disciplines=(), sections=(), kits=(),
                            statuses=(), categories=(), limit: int = 20000):
    q = """
        SELECT
            c.id AS "ID",
            d.discipline AS "Дисциплина",
            d.section AS "Раздел",
            d.complex AS "Комплект",
            REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS "Лист",
            d.name AS "Название листа",
            c.comment AS "Замечание",
            c.status AS "Статус",
            c.author AS "Автор",
            strftime('%d.%m.%Y %H:%M', c.created) AS "Создано",
            c.fix_date AS "Дата устранения",
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
    if categories:
        q += f" AND c.category IN ({','.join('?' * len(categories))})"
        params += list(categories)

    q += " ORDER BY d.discipline, d.complex, d.leaf, c.id LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


# ---------------------------------------------------------------------------
#  Аналитика
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_authors_analytics() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                c.author AS "Автор",
                COUNT(*) AS "Всего",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Принято",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Формальное",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Доп.треб.",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Не принято",
                SUM(CASE WHEN c.category IS NULL OR c.category = ''
                         THEN 1 ELSE 0 END) AS "Без категории",
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS "Закрыто",
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS "Активных"
            FROM comments c
            WHERE c.author IS NOT NULL AND c.author <> ''
            GROUP BY c.author
            ORDER BY "Всего" DESC
        """, conn, params=(CAT_1, CAT_2, CAT_3, CAT_4))


@st.cache_data(ttl=300, show_spinner=False)
def _load_monthly_dynamics() -> pd.DataFrame:
    """Выдача и закрытие по месяцам."""
    with get_conn() as conn:
        issue = pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS issued
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym
        """, conn)

        fixed = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS closed
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто', 'Выполнено')
            GROUP BY ym
        """, conn)

    merged = pd.merge(issue, fixed, on="ym", how="outer").fillna(0).sort_values("ym")
    merged.columns = ["Месяц", "Выдано", "Закрыто"]
    merged["Выдано"] = merged["Выдано"].astype(int)
    merged["Закрыто"] = merged["Закрыто"].astype(int)
    merged["Сальдо"] = merged["Выдано"] - merged["Закрыто"]
    return merged


@st.cache_data(ttl=300, show_spinner=False)
def _load_lagging_complexes(limit: int = 50) -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql("""
            SELECT
                d.complex AS complex,
                d.discipline AS discipline,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status IN ('Закрыто','Выполнено') THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status IN ('Новое','Принято в работу','Не принято','К обсуждению') THEN 1 ELSE 0 END) AS active
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
            GROUP BY d.complex, d.discipline
            HAVING total >= 10
        """, conn)

    df["pct"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1) if r["total"] else 0.0,
        axis=1)
    df = df.nsmallest(limit, "pct")

    return df[["complex", "discipline", "total", "closed", "active", "pct"]].rename(
        columns={
            "complex": "Комплект",
            "discipline": "Дисциплина",
            "total": "Всего",
            "closed": "Закрыто",
            "active": "Активных",
            "pct": "% выполнения",
        }
    )


# ---------------------------------------------------------------------------
#  Утилиты
# ---------------------------------------------------------------------------
def _df_to_xlsx(df: pd.DataFrame, sheet_name: str = "Данные") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])

        # Автоширина
        ws = writer.sheets[sheet_name[:31]]
        for col_idx, col_name in enumerate(df.columns, start=1):
            col_letter = chr(64 + col_idx) if col_idx <= 26 else None
            if col_letter:
                max_len = max(
                    len(str(col_name)),
                    df[col_name].astype(str).str.len().max()
                    if len(df) else 0,
                )
                ws.column_dimensions[col_letter].width = min(max_len + 2, 60)

    buf.seek(0)
    return buf.getvalue()


def _make_download(df: pd.DataFrame, filename_base: str, key_suffix: str,
                   formats=("xlsx", "pdf"), subtitle: str = "",
                   pdf_max_rows: int = 1000):
    """
    Универсальная функция выгрузки: XLSX + PDF.

    pdf_max_rows — ограничение строк для PDF (в Excel — все).
    Если строк больше, в PDF попадёт только часть, а пользователю
    покажется предупреждение.
    """
    cols = st.columns(len(formats))

    for col, fmt in zip(cols, formats):
        with col:
            if fmt == "xlsx":
                data = _df_to_xlsx(df)
                mime = ("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")
                ext = "xlsx"
                label = f"⬇️ Excel ({len(df):,} строк)".replace(",", " ")
            else:  # pdf
                if len(df) > pdf_max_rows:
                    st.warning(
                        f"PDF: только первые **{pdf_max_rows:,}** строк "
                        f"из {len(df):,}. Полная выгрузка — в Excel."
                        .replace(",", " ")
                    )
                data = build_pdf(filename_base, df, subtitle,
                                 max_rows=pdf_max_rows)
                mime = "application/pdf"
                ext = "pdf"
                label = f"⬇️ PDF ({min(len(df), pdf_max_rows):,} строк)".replace(",", " ")

            st.download_button(
                label,
                data=data,
                file_name=f"{filename_base}_{datetime.now():%Y%m%d}.{ext}",
                mime=mime,
                use_container_width=True,
                key=f"dl_{key_suffix}_{fmt}",
            )


# ---------------------------------------------------------------------------
#  Секция 1: Сводные отчёты
# ---------------------------------------------------------------------------
def _render_summaries():
    st.markdown("### 📊 Сводные отчёты")
    st.caption(
        "Готовые сводки по проекту. Форматированные XLSX — "
        "с гиперссылками, цветными процентами и листами по каждому комплекту."
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        "🏷 По дисциплинам",
        "🏗 По комплектам",
        "📂 По разделам",
        "💎 Форматированные XLSX",
    ])

    with tab1:
        df = _load_discipline_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True, height=400)
        _make_download(df, "Сводка_по_дисциплинам", "sum_disc",
                       subtitle="Сводка замечаний по дисциплинам проекта")

    with tab2:
        df = _load_complex_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True, height=400)
        _make_download(df, "Сводка_по_комплектам", "sum_kit",
                       subtitle="Сводка замечаний по комплектам проекта")

    with tab3:
        df = _load_section_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True, height=400)
        _make_download(df, "Сводка_по_разделам", "sum_sect",
                       subtitle="Сводка замечаний по разделам проекта")

    with tab4:
        st.markdown(
            "**Форматированные отчёты по дисциплинам** — цветные проценты, гиперссылки между листами, "
            "выпадающие списки для категорий. Создаётся 11 файлов + 1 сводный."
        )
        st.caption(
            "⏱ Генерация занимает 30–60 секунд (обрабатывается 64 000 замечаний)."
        )

        if st.button("🚀 Сгенерировать все XLSX", type="primary",
                     use_container_width=True):
            with st.spinner("Генерация отчётов... Пожалуйста, подождите"):
                try:
                    from vitro.xlsx_builder import build_all_files
                    files = build_all_files("Отчёты")
                    st.success(f"✅ Создано файлов: {len(files)}")
                    st.session_state["xlsx_generated"] = [str(f) for f in files]
                except Exception as e:
                    st.error(f"Ошибка: {e}")
                    import traceback
                    st.code(traceback.format_exc())

        # Список сгенерированных файлов + скачивание
        if "xlsx_generated" in st.session_state:
            files = st.session_state["xlsx_generated"]
            st.markdown(f"**Готово файлов: {len(files)}**")

            # Скачивание по одному
            for f in files:
                path = Path(f)
                if not path.exists():
                    continue
                with open(path, "rb") as fh:
                    st.download_button(
                        f"⬇️ {path.name}",
                        data=fh.read(),
                        file_name=path.name,
                        mime=("application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet"),
                        key=f"dl_xlsx_{path.name}",
                        use_container_width=True,
                    )


# ---------------------------------------------------------------------------
#  Секция 2: Замечания с фильтрами
# ---------------------------------------------------------------------------
def _render_comments_export():
    st.markdown("### 📋 Выгрузка замечаний")
    st.caption(
        "Фильтруйте как в других вкладках и выгружайте результат "
        "в Excel или PDF."
    )

    # --- Фильтры ---
    disc_options = _load_discipline_options()

    c1, c2, c3 = st.columns(3)
    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина", options=list(disc_options.values()),
            placeholder="Все дисциплины", key="exp_disc")
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    section_options = _load_section_options(
        tuple(sel_disc) if sel_disc else ())

    with c2:
        if section_options:
            sel_section_labels = st.multiselect(
                "Раздел", options=list(section_options.values()),
                placeholder="Все разделы", key="exp_section")
            sel_section = [code for code, label in section_options.items()
                           if label in sel_section_labels]
        else:
            sel_section = []
            st.multiselect("Раздел", options=[],
                           placeholder="Разделы не применимы",
                           disabled=True, key="exp_section_empty")

    kit_options = _load_kit_options(
        tuple(sel_disc) if sel_disc else (),
        tuple(sel_section) if sel_section else ())

    with c3:
        sel_kit_labels = st.multiselect(
            "Комплект", options=list(kit_options.values()),
            placeholder="Все комплекты", key="exp_kit")
        sel_kit = [code for code, label in kit_options.items()
                   if label in sel_kit_labels]

    c4, c5, c6 = st.columns(3)
    with c4:
        sel_status = st.multiselect("Статус", STATUSES,
                                    placeholder="Все статусы", key="exp_status")
    with c5:
        sel_category = st.multiselect("Категория", [CAT_1, CAT_2, CAT_3, CAT_4],
                                      placeholder="Все категории",
                                      key="exp_category")
    with c6:
        query = st.text_input("Поиск по тексту",
                              placeholder="Оставьте пустым для всех",
                              key="exp_query")

    limit = st.number_input(
        "Максимум строк", min_value=100, max_value=100000,
        value=20000, step=1000, key="exp_limit",
        help="При большом объёме выгрузка может занять время",
    )

    # --- Загрузка ---
    df = _load_comments_filtered(
        query=query,
        disciplines=tuple(sel_disc) if sel_disc else (),
        sections=tuple(sel_section) if sel_section else (),
        kits=tuple(sel_kit) if sel_kit else (),
        statuses=tuple(sel_status) if sel_status else (),
        categories=tuple(sel_category) if sel_category else (),
        limit=int(limit),
    )

    if df.empty:
        st.info("Нет данных по заданным фильтрам.")
        return

    st.caption(f"Найдено строк: **{len(df):,}**".replace(",", " "))

    # --- Колонки для выгрузки ---
    with st.expander("🎛 Выбрать колонки для выгрузки", expanded=False):
        all_cols = list(df.columns)
        default_cols = [c for c in all_cols
                        if c not in ("Дисциплина", "Раздел")]
        sel_cols = st.multiselect(
            "Колонки", options=all_cols, default=default_cols,
            key="exp_cols")
        if sel_cols:
            df = df[sel_cols]

    st.dataframe(df.head(200), use_container_width=True,
                 hide_index=True, height=400)
    if len(df) > 200:
        st.caption("Показаны первые 200 строк. В выгрузку попадут все.")

    st.divider()
    _make_download(
        df, "Замечания", "comments",
        subtitle=f"Выгрузка замечаний ({len(df)} строк)",
        pdf_max_rows=500,  # PDF — первые 500 строк, Excel — все
    )


# ---------------------------------------------------------------------------
#  Секция 3: Аналитика
# ---------------------------------------------------------------------------
def _render_analytics():
    st.markdown("### 📈 Аналитика")
    st.caption("Готовые аналитические выгрузки для презентаций и отчётов.")

    tab1, tab2, tab3 = st.tabs([
        "👤 Авторы",
        "📅 Динамика по месяцам",
        "⚠️ Топ отстающих",
    ])

    with tab1:
        df = _load_authors_analytics()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True, height=400)
        _make_download(df, "Аналитика_авторов", "authors",
                       subtitle="Аналитика по авторам замечаний")

    with tab2:
        df = _load_monthly_dynamics()
        st.caption(f"Месяцев: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True)
        _make_download(df, "Динамика_по_месяцам", "monthly",
                       subtitle="Выдача и закрытие замечаний по месяцам")

    with tab3:
        df = _load_lagging_complexes(limit=50)
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True, height=400)
        _make_download(df, "Топ_отстающих_комплектов", "lagging",
                       subtitle="Комплекты с наименьшим % выполнения")


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📄 Экспорт")
    st.caption(
        "Выгрузка данных в Excel и PDF: сводки, замечания, аналитика."
    )

    tab1, tab2, tab3 = st.tabs([
        "📊 Сводные отчёты",
        "📋 Замечания",
        "📈 Аналитика",
    ])

    with tab1:
        _render_summaries()
    with tab2:
        _render_comments_export()
    with tab3:
        _render_analytics()