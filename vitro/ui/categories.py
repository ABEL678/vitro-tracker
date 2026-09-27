# vitro/ui/categories.py
"""
🏷 Категории — аналитика + работа с замечаниями.

Под-вкладки:
  📈 Анализ — распределение, по дисциплинам, по авторам.
  📝 Работа с замечаниями — st.data_editor с каскадными фильтрами.
  📥 Импорт из Excel — массовая загрузка категорий из файлов.
"""

import io

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import (
    get_conn,
    load_remarks_for_editor,
    update_category_safe,
)
from vitro.disciplines import discipline_name


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

CAT_PREFIX = {
    CAT_1: "🟢",
    CAT_2: "🟡",
    CAT_3: "🔵",
    CAT_4: "🔴",
}

CAT_OPTIONS_DISPLAY = [f"{CAT_PREFIX[c]} {c}" for c in [CAT_1, CAT_2, CAT_3, CAT_4]]
CAT_OPTIONS_RAW = [CAT_1, CAT_2, CAT_3, CAT_4]


def _strip_prefix(display_value: str) -> str:
    if not display_value:
        return ""
    for prefix in CAT_PREFIX.values():
        if display_value.startswith(prefix):
            return display_value[len(prefix):].strip()
    return display_value.strip()


def _add_prefix(raw_value: str):
    if not raw_value or pd.isna(raw_value):
        return None
    for cat, prefix in CAT_PREFIX.items():
        if raw_value == cat:
            return f"{prefix} {cat}"
    return raw_value


CAT_COLORS = {
    CAT_1:           "#C6EFCE",
    CAT_2:           "#FFEB9C",
    CAT_3:           "#BDD7EE",
    CAT_4:           "#FFC7CE",
    "Без категории": "#D9D9D9",
    "Принято":       "#C6EFCE",
    "Формальное":    "#FFEB9C",
    "Доп.треб.":     "#BDD7EE",
    "Не принято":    "#FFC7CE",
}

API_STATUSES = [
    "Новое", "Принято в работу", "Не принято",
    "К обсуждению", "Выполнено",
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
                SELECT DISTINCT section
                FROM documents
                WHERE section IS NOT NULL AND section <> ''
                  AND discipline IN ({placeholders})
                ORDER BY section
            """, tuple(disciplines)).fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT section
                FROM documents
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
                WHERE section IN ({placeholders})
                  AND complex IS NOT NULL
            )""")
            params += list(sections)

        rows = conn.execute(f"""
            SELECT c.code, c.name
            FROM complexes c
            WHERE {' AND '.join(where)}
            ORDER BY c.code
        """, tuple(params)).fetchall()

    result = {}
    for r in rows:
        code = r["code"]
        name = (r["name"] or "").strip()
        result[code] = f"{code} — {name}" if name else code
    return result


# ---------------------------------------------------------------------------
#  Аналитика
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_overall_distribution() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                CASE WHEN category IS NULL OR category = ''
                     THEN 'Без категории' ELSE category END AS "Категория",
                COUNT(*) AS "Количество"
            FROM comments
            GROUP BY "Категория"
            ORDER BY "Количество" DESC
        """, conn)


@st.cache_data(ttl=300, show_spinner=False)
def _load_by_discipline() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                d.discipline AS "Код",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Принято",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Формальное",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Доп.треб.",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Не принято",
                SUM(CASE WHEN c.category IS NULL OR c.category = ''
                         THEN 1 ELSE 0 END) AS "Без категории"
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
            ORDER BY d.discipline
        """, conn, params=(CAT_1, CAT_2, CAT_3, CAT_4))


@st.cache_data(ttl=300, show_spinner=False)
def _load_by_author(limit: int = 30) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(f"""
            SELECT
                c.author AS "Автор",
                COUNT(*) AS "Всего",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Принято",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Формальное",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Доп.треб.",
                SUM(CASE WHEN c.category = ? THEN 1 ELSE 0 END) AS "Не принято",
                SUM(CASE WHEN c.category IS NULL OR c.category = ''
                         THEN 1 ELSE 0 END) AS "Без категории"
            FROM comments c
            WHERE c.author IS NOT NULL AND c.author <> ''
            GROUP BY c.author
            ORDER BY "Всего" DESC
            LIMIT {limit}
        """, conn, params=(CAT_1, CAT_2, CAT_3, CAT_4))


@st.cache_data(ttl=300, show_spinner=False)
def _load_categorization_progress() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        done  = conn.execute(
            "SELECT COUNT(*) FROM comments "
            "WHERE category IS NOT NULL AND category <> ''"
        ).fetchone()[0]
        return {"total": total, "done": done, "left": total - done}


@st.cache_data(ttl=300, show_spinner=False)
def _load_user_activity() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                user AS "Специалист",
                COUNT(*) AS "Изменений",
                COUNT(DISTINCT comment_id) AS "Уникальных замечаний",
                MIN(timestamp) AS "Первое",
                MAX(timestamp) AS "Последнее"
            FROM users_activity
            WHERE user IS NOT NULL AND user <> ''
            GROUP BY user
            ORDER BY "Изменений" DESC
        """, conn)


def _render_analysis():
    # Кнопка принудительного обновления данных (сброс кэша)
    col_refresh, _ = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Обновить данные", key="cat_refresh"):
            st.cache_data.clear()
            st.rerun()

    prog = _load_categorization_progress()
    c1, c2, c3 = st.columns(3)
    c1.metric("Всего замечаний", f"{prog['total']:,}".replace(",", " "))
    c2.metric("С категорией",    f"{prog['done']:,}".replace(",", " "))
    c3.metric("Осталось",        f"{prog['left']:,}".replace(",", " "))

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        dist = _load_overall_distribution()
        if not dist.empty:
            fig = px.pie(
                dist, names="Категория", values="Количество", hole=0.45,
                color="Категория", color_discrete_map=CAT_COLORS,
                title="Распределение по категориям",
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        by_disc = _load_by_discipline()
        if not by_disc.empty:
            df_long = by_disc.melt(
                id_vars="Код",
                value_vars=["Принято", "Формальное", "Доп.треб.",
                            "Не принято", "Без категории"],
                var_name="Категория", value_name="Количество",
            )
            fig = px.bar(
                df_long, x="Код", y="Количество", color="Категория",
                barmode="stack",
                color_discrete_map=CAT_COLORS,
                category_orders={"Категория": [
                    "Принято", "Формальное", "Доп.треб.",
                    "Не принято", "Без категории"]},
                title="Категории по дисциплинам",
            )
            fig.update_layout(legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Категории по авторам замечаний")
    by_author = _load_by_author(limit=30)
    if not by_author.empty:
        st.dataframe(by_author, use_container_width=True, hide_index=True)
    else:
        st.info("Нет данных по авторам.")

    st.subheader("Активность специалистов по категоризации")
    activity = _load_user_activity()
    if not activity.empty:
        st.dataframe(activity, use_container_width=True, hide_index=True)
    else:
        st.info("Пока никто не назначал категории.")


# ---------------------------------------------------------------------------
#  Визуальная группировка
# ---------------------------------------------------------------------------
def _collapse_repeats(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    key_prev = None
    for idx, row in out.iterrows():
        key = tuple(row[c] for c in cols)
        if key == key_prev:
            for c in cols:
                out.at[idx, c] = ""
        key_prev = key
    return out


# ---------------------------------------------------------------------------
#  Редактор
# ---------------------------------------------------------------------------
def _render_editor():
    st.markdown("### 📝 Работа с замечаниями")
    st.caption(
        "Выберите дисциплину → раздел → комплект, заполните колонку «Категория» "
        "и нажмите «Сохранить». Пустая ячейка в колонках комплекта/листа "
        "означает «то же, что выше»."
    )

    st.markdown("""
    <style>
    div[data-testid="stDataFrame"] div[role="gridcell"] {
        white-space: normal !important;
        word-wrap: break-word !important;
        line-height: 1.25 !important;
    }
    div[data-testid="stDataFrame"] div[role="row"] {
        min-height: 56px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    disc_options = _load_discipline_options()

    # --- Каскад: Дисциплина → Раздел → Комплект ---
    c1, c2, c3 = st.columns(3)

    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина",
            options=list(disc_options.values()),
            placeholder="Все дисциплины",
            key="cat_disc",
        )
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    section_options = _load_section_options(tuple(sel_disc) if sel_disc else ())

    with c2:
        if section_options:
            sel_section_labels = st.multiselect(
                "Раздел",
                options=list(section_options.values()),
                placeholder="Все разделы",
                key="cat_section",
            )
            sel_section = [code for code, label in section_options.items()
                           if label in sel_section_labels]
        else:
            sel_section = []
            # Заглушка-мультиселект: визуально такой же, но отключён
            st.multiselect(
                "Раздел",
                options=[],
                placeholder="Разделы не применимы (нет у выбранных дисциплин)",
                disabled=True,
                key="cat_section_empty",
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
            key="cat_kit",
        )
        sel_kit = [code for code, label in kit_options.items()
                   if label in sel_kit_labels]

    # --- Статус и лимит ---
    c4, c5 = st.columns([2, 1])
    with c4:
        sel_status = st.multiselect(
            "Текущий статус замечания",
            API_STATUSES,
            default=[],
            placeholder="Все статусы",
            key="cat_status",
        )
    with c5:
        limit = st.number_input("Лимит строк", min_value=50, max_value=2000,
                                value=300, step=50, key="cat_limit")

    only_uncat = st.checkbox("Только без категории", value=False,
                             key="cat_only_uncat")

    # --- Загрузка ---
    df = load_remarks_for_editor(
        disciplines=sel_disc or None,
        sections=sel_section or None,
        kits=sel_kit or None,
        statuses=sel_status or None,
        only_uncategorized=only_uncat,
        limit=int(limit),
    )

    if df.empty:
        st.info("Нет замечаний по заданным фильтрам.")
        return

    st.caption(
        f"Загружено **{len(df)}** строк. "
        f"Пустые ячейки в колонках «Дисциплина», «Раздел», «Комплект», "
        f"«Лист», «Название листа» означают, что значение совпадает с ячейкой выше."
    )

    df["category"] = df["category"].apply(_add_prefix)

    group_cols = ["discipline", "section", "complex", "sheet", "sheet_name"]
    view = _collapse_repeats(df, group_cols)

    desired_order = [
        "id", "discipline", "section", "complex",
        "sheet", "sheet_name", "comment",
        "api_status", "author", "created",
        "category", "category_user", "category_date",
    ]
    view = view[[c for c in desired_order if c in view.columns]]

    edited = st.data_editor(
        view,
        column_config={
            "id":               st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "discipline":       st.column_config.TextColumn("Дисциплина", disabled=True, width="small"),
            "section":          st.column_config.TextColumn("Раздел", disabled=True, width="small"),
            "complex":          st.column_config.TextColumn("Комплект", disabled=True, width="medium"),
            "sheet":            st.column_config.TextColumn("Лист", disabled=True, width="medium"),
            "sheet_name":       st.column_config.TextColumn("Название листа", disabled=True, width="large"),
            "comment":          st.column_config.TextColumn("Замечание", disabled=True, width="large"),
            "api_status":       st.column_config.TextColumn("Текущий статус замечания", disabled=True),
            "author":           st.column_config.TextColumn("Автор", disabled=True),
            "created":          st.column_config.TextColumn("Создано", disabled=True, width="small"),
            "category":         st.column_config.SelectboxColumn(
                                    "Категория", options=CAT_OPTIONS_DISPLAY,
                                    required=False, help="Выберите категорию"),
            "category_user":    st.column_config.TextColumn("Кто", disabled=True, width="small"),
            "category_date":    st.column_config.TextColumn("Когда", disabled=True, width="small"),
            "category_version": None,
        },
        disabled=["id", "discipline", "section", "complex", "sheet",
                  "sheet_name", "comment", "api_status", "author",
                  "created", "category_user", "category_date"],
        hide_index=True,
        use_container_width=True,
        key="categorization_editor",
    )

    col_save, _ = st.columns([1, 3])
    with col_save:
        save_clicked = st.button("💾 Сохранить изменения", type="primary",
                                 use_container_width=True)

    if save_clicked:
        user = st.session_state.get("user", "инженер")
        saved, conflicts = 0, []

        for _, row in edited.iterrows():
            orig = df[df["id"] == row["id"]].iloc[0]

            new_cat_display = None if pd.isna(row["category"]) else str(row["category"])
            new_cat = _strip_prefix(new_cat_display) if new_cat_display else None

            old_cat_display = None if pd.isna(orig["category"]) else str(orig["category"])
            old_cat = _strip_prefix(old_cat_display) if old_cat_display else None

            if new_cat == old_cat:
                continue
            if new_cat is None:
                new_cat = ""

            ok, msg = update_category_safe(
                int(row["id"]), new_cat, user=user,
                expected_version=int(orig["category_version"] or 0),
            )
            if ok:
                saved += 1
            else:
                conflicts.append(f"ID {row['id']}: {msg}")

        if saved:
            st.success(f"✅ Сохранено: {saved} строк(и)")
            st.cache_data.clear()
            st.rerun()

        if conflicts:
            st.warning("⚠️ Обнаружены конфликты:")
            for c in conflicts[:20]:
                st.write(f"- {c}")
            if len(conflicts) > 20:
                st.write(f"... и ещё {len(conflicts) - 20}")


# ---------------------------------------------------------------------------
#  Импорт из Excel
# ---------------------------------------------------------------------------
def _normalize_category(value) -> str | None:
    """
    Приводит значение категории из Excel к каноничному виду.
    Поддерживает: полные названия, с эмодзи, обрезанные варианты.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None

    # Убираем эмодзи-префиксы, если есть
    s = _strip_prefix(s)

    # Точное совпадение с каноничными
    if s in CAT_OPTIONS_RAW:
        return s

    # Нечёткое совпадение по началу строки / ключевым словам
    s_low = s.lower()
    for cat in CAT_OPTIONS_RAW:
        if cat.lower() == s_low:
            return cat
    if "принято" in s_low and "не" not in s_low.split("принято")[0][-5:]:
        # Осторожно: "Не принято" содержит "принято"
        if "не принято" in s_low:
            return CAT_4
        return CAT_1
    if "формальн" in s_low:
        return CAT_2
    if "доп" in s_low and "треб" in s_low:
        return CAT_3
    if "не принято" in s_low:
        return CAT_4

    return None  # непонятное значение


# Алиасы колонок — поддерживаем разные варианты названий
COL_ID_ALIASES  = ["ИД", "ID", "Id", "id", "Код", "№", "N"]
COL_CAT_ALIASES = ["Категория", "категория", "Category", "category", "Кат."]
COL_USER_ALIASES = ["Кто изменил", "Кто", "Пользователь", "User", "user", "Специалист"]
COL_DATE_ALIASES = ["Когда изменил", "Когда", "Дата", "Date", "date", "Дата изменения"]


def _find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    """Ищет колонку по списку алиасов (регистронезависимо, с обрезкой пробелов)."""
    cols_norm = {str(c).strip().lower(): c for c in df.columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in cols_norm:
            return cols_norm[key]
    return None


def _parse_excel_file(uploaded_file) -> pd.DataFrame:
    """
    Читает Excel-файл, ищет колонки по алиасам.
    Возвращает DataFrame с колонками:
      id, category_raw, category, user_raw, date_raw, source_sheet, source_file
    """
    rows = []
    try:
        xls = pd.ExcelFile(uploaded_file)
    except Exception as e:
        st.error(f"Не удалось открыть файл {uploaded_file.name}: {e}")
        return pd.DataFrame()

    for sheet in xls.sheet_names:
        if sheet.startswith("Сводка") or sheet.startswith("Свод"):
            continue

        # Пробуем header=1 и header=0 — что найдёт нужные колонки, то и берём
        df = None
        for header_row in (1, 0):
            try:
                candidate = pd.read_excel(xls, sheet_name=sheet, header=header_row)
            except Exception:
                continue
            if _find_column(candidate, COL_ID_ALIASES) and \
               _find_column(candidate, COL_CAT_ALIASES):
                df = candidate
                break

        if df is None:
            continue

        id_col  = _find_column(df, COL_ID_ALIASES)
        cat_col = _find_column(df, COL_CAT_ALIASES)
        usr_col = _find_column(df, COL_USER_ALIASES)
        dat_col = _find_column(df, COL_DATE_ALIASES)

        # Оставляем только строки с заполненной категорией
        mask = df[cat_col].notna() & (df[cat_col].astype(str).str.strip() != "")
        filled = df[mask]

        for _, row in filled.iterrows():
            try:
                cid = int(row[id_col])
            except (ValueError, TypeError):
                continue

            cat_raw = str(row[cat_col]).strip()
            cat_norm = _normalize_category(cat_raw)

            user_raw = None
            if usr_col:
                v = row.get(usr_col)
                if v is not None and str(v).strip() and str(v).strip().lower() != "none":
                    user_raw = str(v).strip()

            date_raw = None
            if dat_col:
                v = row.get(dat_col)
                if v is not None and str(v).strip() and str(v).strip().lower() != "none":
                    date_raw = str(v).strip()

            rows.append({
                "id": cid,
                "category_raw": cat_raw,
                "category": cat_norm,
                "user_raw": user_raw,
                "date_raw": date_raw,
                "source_sheet": sheet,
                "source_file": uploaded_file.name,
            })

    return pd.DataFrame(rows)


def _render_import():
    st.markdown("### 📥 Массовая загрузка категорий из Excel")

    # --- Проверка имени пользователя ---
    user_default = st.session_state.get("user", "").strip()
    if not user_default or user_default == "инженер":
        st.warning(
            "⚠️ **Введите имя в сайдбаре слева** — оно будет записано "
            "как автор категорий. Без имени импорт заблокирован."
        )

    st.caption(
        "Перетащите один или несколько Excel-файлов. Мы ищем колонки "
        "«ИД» (или ID/Код/№) и «Категория». Если в файле есть колонки "
        "«Кто изменил» и «Когда изменил» — они используются как автор "
        "и дата; иначе берётся имя из сайдбара и текущее время."
    )

    uploaded = st.file_uploader(
        "Файлы Excel",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key="cat_import_files",
    )

    if not uploaded:
        st.info("Файлы не выбраны.")
        return

    st.markdown(f"**Получено файлов:** {len(uploaded)}")

    # --- Предпросмотр ---
    with st.expander("🔍 Предпросмотр распознанных категорий", expanded=True):
        all_rows = []
        for f in uploaded:
            df_file = _parse_excel_file(f)
            if not df_file.empty:
                all_rows.append(df_file)

        if not all_rows:
            st.warning("Ни в одном файле не найдено колонок «ИД» и «Категория».")
            return

        combined = pd.concat(all_rows, ignore_index=True)
        combined_unique = combined.drop_duplicates(subset=["id"], keep="last")

        total_rows = len(combined)
        unique_rows = len(combined_unique)
        unknown_cat = combined_unique["category"].isna().sum()
        users_from_file = combined_unique["user_raw"].notna().sum()
        dates_from_file = combined_unique["date_raw"].notna().sum()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Всего строк", total_rows)
        c2.metric("Уникальных ID", unique_rows)
        c3.metric("Нераспознанных", int(unknown_cat))
        c4.metric("«Кто» из файла", int(users_from_file))
        c5.metric("«Когда» из файла", int(dates_from_file))

        dist = combined_unique["category"].value_counts(dropna=False).reset_index()
        dist.columns = ["Категория", "Количество"]
        st.dataframe(dist, use_container_width=True, hide_index=True)

        if unknown_cat > 0:
            st.warning("Значения, которые не удалось распознать:")
            unknown_df = combined_unique[combined_unique["category"].isna()][
                ["id", "category_raw", "source_file", "source_sheet"]
            ].head(20)
            st.dataframe(unknown_df, use_container_width=True, hide_index=True)

        with st.expander("Первые 10 строк для контроля", expanded=False):
            st.dataframe(
                combined_unique.head(10)[
                    ["id", "category", "category_raw",
                     "user_raw", "date_raw",
                     "source_file", "source_sheet"]
                ],
                use_container_width=True, hide_index=True,
            )

    # --- Автор импорта ---
    st.divider()
    st.markdown("#### 👤 Автор импорта")

    c1, c2 = st.columns(2)
    with c1:
        import_user = st.text_input(
            "Кто выполняет импорт (имя для записи)",
            value=user_default if user_default != "инженер" else "",
            placeholder="Фамилия Имя Отчество",
            key="cat_import_user",
        )
    with c2:
        use_file_author = st.checkbox(
            "Использовать «Кто изменил» из файла, если заполнено",
            value=True,
            key="cat_import_use_file_author",
            help="Если в Excel заполнена колонка «Кто изменил» — она имеет приоритет над полем слева",
        )

    st.caption(
        f"Итоговая логика: **{'из файла → иначе из поля «Кто выполняет импорт»' if use_file_author else 'всегда из поля «Кто выполняет импорт»'}**. "
        f"Дата: из файла (если есть) → иначе текущее время."
    )

    # --- Применение ---
    apply = st.button(
        "✅ Применить категории к базе",
        type="primary",
        use_container_width=True,
        disabled=not import_user.strip(),
    )

    if not import_user.strip():
        st.info("Введите имя автора импорта, чтобы разблокировать кнопку.")

    if apply:
        if not all_rows:
            st.error("Нет данных для применения.")
            return

        ids = combined_unique["id"].astype(int).tolist()
        with get_conn() as conn:
            placeholders = ",".join("?" * len(ids))
            existing_rows = conn.execute(f"""
                SELECT id, COALESCE(category_version, 0) AS v
                FROM comments WHERE id IN ({placeholders})
            """, tuple(ids)).fetchall()
        existing = {r["id"]: r["v"] for r in existing_rows}

        saved = 0
        skipped_missing = 0
        skipped_unknown = 0
        conflicts = []

        for _, row in combined_unique.iterrows():
            cid = int(row["id"])
            cat = row["category"]

            if cat is None or pd.isna(cat):
                skipped_unknown += 1
                continue
            if cid not in existing:
                skipped_missing += 1
                continue

            # Определяем автора: из файла или из поля
            if use_file_author and row.get("user_raw"):
                row_user = str(row["user_raw"])
            else:
                row_user = import_user.strip()

            ok, msg = update_category_safe(
                cid, cat, user=row_user,
                expected_version=existing[cid],
            )
            if ok:
                saved += 1
                existing[cid] = existing[cid] + 1
            else:
                conflicts.append(f"ID {cid}: {msg}")

        st.success(
            f"✅ Обновлено: **{saved}** · "
            f"Пропущено (нет в БД): **{skipped_missing}** · "
            f"Нераспознанных: **{skipped_unknown}** · "
            f"Конфликтов: **{len(conflicts)}**"
        )

        if conflicts:
            with st.expander("⚠️ Конфликты (первые 30)"):
                for c in conflicts[:30]:
                    st.write(f"- {c}")

        if saved:
            st.cache_data.clear()
            st.success(f"✅ Обновлено {saved} записей. Данные обновлены.")
            st.rerun()

# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("🏷 Категории")
    tab_analysis, tab_editor, tab_import = st.tabs([
        "📈 Анализ",
        "📝 Работа с замечаниями",
        "📥 Импорт из Excel",
    ])

    with tab_analysis:
        _render_analysis()
    with tab_editor:
        _render_editor()
    with tab_import:
        _render_import()