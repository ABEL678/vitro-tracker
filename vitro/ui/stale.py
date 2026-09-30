# vitro/ui/stale.py
"""
⏳ Зависшие — активные замечания с распределением по возрасту.

Особенности:
  - Слайдер + поле точного ввода, синхронизированные через session_state.
  - Метрики считаются точными SQL-запросами COUNT(*) без лимита.
  - Распределение по возрасту считается отдельным SQL-запросом
    без ограничения — все бакеты заполнены честно.
  - Таблица — первые 1000 строк, с датой в формате дд.мм.гггг чч:мм:сс.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
AGE_BUCKETS = [
    ("7–30 дней",     7,    30,    "#FFE082"),
    ("31–90 дней",    31,   90,    "#FFD54F"),
    ("91–180 дней",   91,   180,   "#FFB74D"),
    ("181–365 дней",  181,  365,   "#FF8A65"),
    ("1–2 года",      366,  730,   "#E57373"),
    ("2–3 года",      731,  1095,  "#C62828"),
    ("3+ лет",        1096, 99999, "#7F0000"),
]

ALL_ACTIVE_LIMIT = 10000   # для графиков топов и таблицы
TABLE_LIMIT      = 1000    # для отображения в таблице


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
def _load_kit_options(disciplines: tuple[str, ...] = ()) -> dict[str, str]:
    with get_conn() as conn:
        if disciplines:
            placeholders = ",".join("?" * len(disciplines))
            rows = conn.execute(f"""
                SELECT c.code, c.name
                FROM complexes c
                WHERE c.code IS NOT NULL AND c.discipline IN ({placeholders})
                ORDER BY c.code
            """, tuple(disciplines)).fetchall()
        else:
            rows = conn.execute("""
                SELECT c.code, c.name FROM complexes c
                WHERE c.code IS NOT NULL ORDER BY c.code
            """).fetchall()
    return {r["code"]: f"{r['code']} — {r['name']}" if r["name"] else r["code"]
            for r in rows}


@st.cache_data(ttl=600, show_spinner=False)
def _load_author_options() -> list[str]:
    with get_conn() as conn:
        return [r["author"] for r in conn.execute("""
            SELECT DISTINCT author FROM comments
            WHERE author IS NOT NULL AND author <> ''
              AND status IN ('Новое','Принято в работу','Не принято','К обсуждению')
            ORDER BY author
        """)]


# ---------------------------------------------------------------------------
#  Точные счётчики (без лимита)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _count_active(disciplines: tuple = (), kits: tuple = (),
                  authors: tuple = ()) -> int:
    """Точное число всех активных замечаний (без лимита)."""
    q = """
        SELECT COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
    """
    params: list = []
    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if authors:
        q += f" AND c.author IN ({','.join('?' * len(authors))})"
        params += list(authors)

    with get_conn() as conn:
        return conn.execute(q, params).fetchone()[0]


@st.cache_data(ttl=3600, show_spinner=False)
def _count_active_stale(min_days: int, disciplines: tuple = (),
                        kits: tuple = (), authors: tuple = ()) -> int:
    """Точное число активных замечаний старше min_days (без лимита)."""
    q = """
        SELECT COUNT(*) AS n
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
          AND julianday('now') - julianday(c.created) > ?
    """
    params: list = [min_days]
    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if authors:
        q += f" AND c.author IN ({','.join('?' * len(authors))})"
        params += list(authors)

    with get_conn() as conn:
        return conn.execute(q, params).fetchone()[0]


# ---------------------------------------------------------------------------
#  Распределение по возрасту — точное, через SQL (без лимита)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_age_distribution(disciplines: tuple = (), kits: tuple = (),
                           authors: tuple = ()) -> pd.DataFrame:
    """
    Возрастное распределение активных замечаний.
    Простой GROUP BY по диапазонам — работает надёжно в SQLite.
    """
    q = """
        SELECT
            CASE
                WHEN days BETWEEN 7    AND 30   THEN '7–30 дней'
                WHEN days BETWEEN 31   AND 90   THEN '31–90 дней'
                WHEN days BETWEEN 91   AND 180  THEN '91–180 дней'
                WHEN days BETWEEN 181  AND 365  THEN '181–365 дней'
                WHEN days BETWEEN 366  AND 730  THEN '1–2 года'
                WHEN days BETWEEN 731  AND 1095 THEN '2–3 года'
                WHEN days >= 1096                THEN '3+ лет'
                ELSE NULL
            END AS bucket,
            COUNT(*) AS n
        FROM (
            SELECT CAST(julianday('now') - julianday(c.created) AS INTEGER) AS days
            FROM comments c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
              AND c.created IS NOT NULL AND c.created <> ''
    """
    params: list = []
    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if authors:
        q += f" AND c.author IN ({','.join('?' * len(authors))})"
        params += list(authors)

    q += """
        )
        WHERE days IS NOT NULL AND days >= 7
        GROUP BY bucket
    """

    with get_conn() as conn:
        raw = pd.read_sql(q, conn, params=params)

    # Приводим к полному набору бакетов в правильном порядке
    bucket_order = [b[0] for b in AGE_BUCKETS]
    mapping = {r["bucket"]: int(r["n"]) for _, r in raw.iterrows()}
    rows = [{"Возраст": label, "Количество": mapping.get(label, 0)}
            for label in bucket_order]

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
#  DataFrame для топов и таблицы (с лимитом)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_active(disciplines: tuple = (), kits: tuple = (),
                     authors: tuple = ()) -> pd.DataFrame:
    """
    Все активные замечания (без фильтра по возрасту).
    Лимит ALL_ACTIVE_LIMIT — для производительности UI.
    Дата — в формате дд.мм.гггг чч:мм:сс.
    """
    q = """
        SELECT
            c.id AS id,
            d.discipline AS discipline,
            d.complex AS complex,
            REPLACE(REPLACE(d.leaf, '.pdf', ''), '.PDF', '') AS sheet,
            d.name AS sheet_name,
            c.comment AS comment,
            c.status AS status,
            c.author AS author,
            strftime('%d.%m.%Y %H:%M:%S', c.created) AS created_fmt,
            CAST(julianday('now') - julianday(c.created) AS INTEGER) AS days_old
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.status IN ('Новое','Принято в работу','Не принято','К обсуждению')
    """
    params: list = []

    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if authors:
        q += f" AND c.author IN ({','.join('?' * len(authors))})"
        params += list(authors)

    q += " ORDER BY days_old DESC LIMIT ?"
    params.append(ALL_ACTIVE_LIMIT)

    with get_conn() as conn:
        return pd.read_sql(q, conn, params=params)


# ---------------------------------------------------------------------------
#  Метрики
# ---------------------------------------------------------------------------
def _render_metrics(total_active: int, total_stale: int,
                    min_days: int, df_all: pd.DataFrame):
    if total_active == 0:
        st.info("Нет активных замечаний по заданным фильтрам.")
        return

    if total_stale == 0:
        st.success(f"🎉 Нет замечаний старше {min_days} дней.")
        return

    share_pct = round(total_stale / total_active * 100, 1)

    df_filtered = df_all[df_all["days_old"] > min_days] if not df_all.empty else df_all
    kits_count = df_filtered["complex"].nunique() if not df_filtered.empty else 0
    older_than_year = int((df_filtered["days_old"] > 365).sum()) if not df_filtered.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        f"Зависших (>{min_days} дн.)",
        f"{total_stale:,}".replace(",", " "),
        help=f"Всего активных замечаний: {total_active:,}".replace(",", " "),
    )
    c2.metric(
        "Доля от активных",
        f"{share_pct}%",
        help="Какая часть активных замечаний старше заданного порога",
    )
    c3.metric(
        "Затронуто комплектов",
        kits_count if kits_count > 0 else "—",
        help="Сколько разных комплектов содержат зависшие замечания "
             f"(рассчитано по первым {min(len(df_all), ALL_ACTIVE_LIMIT)} строкам)",
    )
    c4.metric(
        "Старше года",
        f"{older_than_year:,}".replace(",", " "),
        delta=f"{round(older_than_year / max(total_stale, 1) * 100, 1)}% от зависших",
        delta_color="inverse",
        help="Замечания в активном статусе более 365 дней",
    )

    if total_stale > ALL_ACTIVE_LIMIT:
        st.caption(
            f"⚠️ Полная выборка содержит **{total_stale:,}** замечаний. "
            f"Метрики «Затронуто комплектов» и «Старше года» рассчитаны "
            f"по первым **{ALL_ACTIVE_LIMIT:,}** строкам."
            .replace(",", " ")
        )


# ---------------------------------------------------------------------------
#  Графики
# ---------------------------------------------------------------------------
def _render_charts(df_all: pd.DataFrame, age_dist: pd.DataFrame):
    if df_all.empty and age_dist.empty:
        return

    col1, col2 = st.columns(2)

    # --- Распределение по возрасту (точно, из SQL) ---
    with col1:
        if not age_dist.empty and age_dist["Количество"].sum() > 0:
            color_map = {label: color for label, _, _, color in AGE_BUCKETS}
            fig = px.bar(
                age_dist, x="Возраст", y="Количество",
                color="Возраст", color_discrete_map=color_map,
                title="Распределение активных замечаний по возрасту",
                text="Количество",
                category_orders={"Возраст": [b[0] for b in AGE_BUCKETS]},
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(showlegend=False, height=420)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Диаграмма показывает **все активные замечания** с учётом "
                "фильтров (дисциплина / комплект / автор). Слайдер "
                "«минимальный возраст» на неё не влияет."
            )

    # --- Топ-15 комплектов ---
    with col2:
        top = (df_all.groupby("complex").size()
                 .reset_index(name="Количество")
                 .sort_values("Количество", ascending=True)
                 .tail(15))
        if not top.empty:
            fig = px.bar(
                top, x="Количество", y="complex", orientation="h",
                title="Топ-15 комплектов по активным замечаниям",
            )
            fig.update_layout(yaxis_title="", height=500)
            st.plotly_chart(fig, use_container_width=True)

    col3, col4 = st.columns(2)

    # --- Топ-10 авторов ---
    with col3:
        top_authors = (df_all.groupby("author").size()
                         .reset_index(name="Количество")
                         .sort_values("Количество", ascending=True)
                         .tail(10))
        if not top_authors.empty:
            fig = px.bar(
                top_authors, x="Количество", y="author", orientation="h",
                title="Топ-10 авторов активных замечаний",
                color_discrete_sequence=["#E57373"],
            )
            fig.update_layout(yaxis_title="", height=400)
            st.plotly_chart(fig, use_container_width=True)

    # --- Распределение по дисциплинам ---
    with col4:
        by_disc = (df_all.groupby("discipline").size()
                     .reset_index(name="Количество")
                     .sort_values("Количество", ascending=False))
        if not by_disc.empty:
            fig = px.bar(
                by_disc, x="discipline", y="Количество",
                title="Активные замечания по дисциплинам",
            )
            fig.update_layout(xaxis_title="Дисциплина", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
#  Слайдер + точный ввод (синхронизированы)
# ---------------------------------------------------------------------------
def _render_age_control() -> int:
    """
    Выбор минимального возраста через селект с пресетами.
    Один виджет — один источник правды, синхронизация гарантирована.
    """
    presets = [
        ("7 дней",    7),
        ("14 дней",   14),
        ("30 дней",   30),
        ("45 дней",   45),
        ("60 дней",   60),
        ("90 дней",   90),
        ("120 дней",  120),
        ("180 дней",  180),
        ("270 дней",  270),
        ("365 дней (1 год)",  365),
        ("545 дней (1.5 года)", 545),
        ("730 дней (2 года)", 730),
        ("1095 дней (3 года)", 1095),
    ]
    labels = [p[0] for p in presets]
    mapping = dict(presets)

    # Инициализация значения по умолчанию
    if "stale_age_label" not in st.session_state:
        st.session_state["stale_age_label"] = "60 дней"

    c_sel, c_val = st.columns([3, 1])

    with c_sel:
        st.selectbox(
            "Минимальный возраст замечаний для таблицы и метрик",
            options=labels,
            key="stale_age_label",
            help="Пресеты от 7 дней до 3 лет. Значение применяется к метрикам "
                 "и таблице, но не влияет на диаграмму распределения.",
        )

    min_days = mapping[st.session_state["stale_age_label"]]

    with c_val:
        st.markdown(
            f"<div style='padding-top: 32px; text-align: right;'>"
            f"<span style='font-size: 26px; font-weight: 700; color: #d32f2f;'>"
            f"{min_days}</span>"
            f"<span style='font-size: 14px; color: #888;'> дн.</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    return int(min_days)


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("⏳ Зависшие замечания")
    st.caption(
        "Активные замечания (Новое, Принято в работу, Не принято, К обсуждению). "
        "Диаграмма распределения показывает всю картину активных, "
        "метрики и таблица — замечания старше выбранного возраста."
    )

    # --- Возраст ---
    min_days = _render_age_control()

    # --- Фильтры ---
    disc_options = _load_discipline_options()
    author_options = _load_author_options()

    c1, c2, c3 = st.columns(3)

    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина",
            options=list(disc_options.values()),
            placeholder="Все дисциплины",
            key="stale_disc",
        )
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    kit_options = _load_kit_options(tuple(sel_disc) if sel_disc else ())

    with c2:
        sel_kit_labels = st.multiselect(
            "Комплект",
            options=list(kit_options.values()),
            placeholder="Все комплекты",
            key="stale_kit",
        )
        sel_kit = [code for code, label in kit_options.items()
                   if label in sel_kit_labels]

    with c3:
        sel_authors = st.multiselect(
            "Автор",
            options=author_options,
            placeholder="Все авторы",
            key="stale_author",
        )

    # --- Точные счётчики ---
    disc_t = tuple(sel_disc) if sel_disc else ()
    kit_t = tuple(sel_kit) if sel_kit else ()
    auth_t = tuple(sel_authors) if sel_authors else ()

    total_active = _count_active(disc_t, kit_t, auth_t)
    total_stale = _count_active_stale(min_days, disc_t, kit_t, auth_t)

    # --- Загрузки ---
    df_all = _load_all_active(disc_t, kit_t, auth_t)
    age_dist = _load_age_distribution(disc_t, kit_t, auth_t)

    # --- Метрики ---
    _render_metrics(total_active, total_stale, min_days, df_all)

    if df_all.empty and age_dist.empty:
        st.info("Нет данных для отображения графиков.")
        return

    st.divider()

    # --- Графики ---
    _render_charts(df_all, age_dist)

    st.divider()

    # --- Таблица ---
    st.subheader(f"📋 Замечания старше {min_days} дней")

    df_filtered = df_all[df_all["days_old"] > min_days].copy()

    if df_filtered.empty:
        st.info(f"Нет замечаний старше {min_days} дней в загруженной выборке.")
        return

    table_df = df_filtered.head(TABLE_LIMIT).copy()
    table_df = table_df.rename(columns={
        "id":          "ID",
        "discipline":  "Дисциплина",
        "complex":     "Комплект",
        "sheet":       "Лист",
        "sheet_name":  "Название листа",
        "comment":     "Замечание",
        "status":      "Статус",
        "author":      "Автор",
        "created_fmt": "Создано",
        "days_old":    "Дней в работе",
    })

    st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
            "Дней в работе": st.column_config.NumberColumn(
                "Дней в работе", format="%d", width="small"),
            "Создано": st.column_config.TextColumn(
                "Создано", width="medium"),
        },
    )

    shown = len(table_df)
    filtered_total = len(df_filtered)
    st.caption(
        f"Показано **{shown:,}** из **{filtered_total:,}** отфильтрованных строк "
        f"(лимит {TABLE_LIMIT:,}).".replace(",", " ")
    )

    st.info(
        "💡 Чтобы назначить категории для этих замечаний, перейдите в "
        "**🏷 Категории → 📝 Работа с замечаниями** и отфильтруйте "
        "по тем же дисциплинам/комплектам."
    )