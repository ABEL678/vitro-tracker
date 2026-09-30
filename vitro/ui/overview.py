# vitro/ui/overview.py
"""
📊 Обзор — главная витрина проекта.

KPI, сводка по дисциплинам, графики с кнопками скачивания.

ВАЖНО: все показатели по замечаниям синхронизированы с дашбордом
через единый источник — `_load_all_categorized` из deadlines.py.
Закрытые и аннулированные учитываются только в блоке «Масштаб»
(для контекста «сколько всего работы»), а вся оперативная
аналитика — только по АКТИВНЫМ замечаниям.
"""

import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
CAT_1 = "Принято/корректное"
CAT_2 = "Формальное/нет влияния на СМР"
CAT_3 = "Доп.требование/отсутствует в ТЗ"
CAT_4 = "Не принято/нарушение ТНПА"

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

STATUS_COLORS = {
    "Закрыто":          "#2E7D32",
    "Выполнено":        "#A5D6A7",
    "Новое":            "#64B5F6",
    "Принято в работу": "#FFD54F",
    "К обсуждению":     "#FFB74D",
    "Не принято":       "#E57373",
    "Аннулировано":     "#BDBDBD",
    "Не указан":        "#EEEEEE",
}

SHEET_STATUS_COLORS = {
    "A — Утверждён":      "#2E7D32",
    "B — Готов к сдаче":  "#A5D6A7",
    "И — Информационный": "#81C784",
    "C — В работе":       "#F48FB1",
    "На согласовании":    "#FFB74D",
    "Корректировка":      "#E57373",
    "Аннулировано":       "#BDBDBD",
    "Нет статуса":        "#EEEEEE",
    "Прочее":             "#90CAF9",
}


# ---------------------------------------------------------------------------
#  Масштаб проекта — общие цифры
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_scale() -> dict:
    with get_conn() as conn:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        comms = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        kits = conn.execute("SELECT COUNT(*) FROM complexes").fetchone()[0]

        closed = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE status = 'Закрыто'"
        ).fetchone()[0]
        annulled = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE status = 'Аннулировано'"
        ).fetchone()[0]

    return {
        "complexes": kits,
        "documents": docs,
        "comments": comms,
        "closed": closed,
        "annulled": annulled,
    }


# ---------------------------------------------------------------------------
#  Единый срез активных (из deadlines._load_all_categorized)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_active_df() -> pd.DataFrame:
    """
    Активные замечания = `_load_all_categorized` без 'abandoned'.
    Единый источник истины для всей аналитики Обзора.
    """
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return df
    return df[df["category_flag"] != "abandoned"].copy()


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi():
    """Ряд 1 — Масштаб. Ряд 2 — Категории (только активные)."""

    scale = _load_scale()

    # --- Ряд 1: масштаб ---
    st.markdown("##### 📦 Масштаб проекта")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Комплектов", f"{scale['complexes']:,}".replace(",", " "))
    c2.metric("Листов РД", f"{scale['documents']:,}".replace(",", " "))
    c3.metric("Всего замечаний", f"{scale['comments']:,}".replace(",", " "))
    c4.metric(
        "Активных",
        f"{_active_count():,}".replace(",", " "),
        help="Замечания в работе: 5 активных статусов. "
             "Без закрытых и аннулированных.",
    )
    c5.metric(
        "Закрыто",
        f"{scale['closed']:,}".replace(",", " "),
        help="Замечания, полностью закрытые.",
    )
    c6.metric(
        "Аннулировано",
        f"{scale['annulled']:,}".replace(",", " "),
        help="Замечания, снятые заказчиком.",
    )

    # --- Ряд 2: категории по активным ---
    st.markdown("##### 🏷 Категории замечаний")
    st.caption(
        "Считается **только по активным замечаниям** "
        "(5 статусов). Закрытые/аннулированные не входят."
    )

    active = _load_active_df()
    if active.empty:
        st.info("Нет активных замечаний.")
        return

    counts = active["category"].fillna("Без категории").replace(
        "", "Без категории").value_counts()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "🟢 Принято/корректное",
        f"{int(counts.get(CAT_1, 0)):,}".replace(",", " "),
        help="Замечание корректное, влияет на СМР. Принимаем.",
    )
    c2.metric(
        "🟡 Формальное",
        f"{int(counts.get(CAT_2, 0)):,}".replace(",", " "),
        help="Формальное, нет влияния на СМР.",
    )
    c3.metric(
        "🔵 Доп.требование",
        f"{int(counts.get(CAT_3, 0)):,}".replace(",", " "),
        help="Дополнительное требование, отсутствует в ТЗ.",
    )
    c4.metric(
        "🔴 Не принято",
        f"{int(counts.get(CAT_4, 0)):,}".replace(",", " "),
        help="Не принимаем: нарушение ТНПА.",
    )
    c5.metric(
        "⚪ Без категории",
        f"{int(counts.get('Без категории', 0)):,}".replace(",", " "),
        help="Ещё не разобрано специалистами.",
    )


def _active_count() -> int:
    """Сколько активных замечаний (для метрики «Активных»)."""
    active = _load_active_df()
    return len(active) if not active.empty else 0


# ---------------------------------------------------------------------------
#  Сводка по дисциплинам (расширенная)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_discipline_summary() -> pd.DataFrame:
    """
    Сводка по дисциплинам.

    Замечания — из _load_all_categorized (активные),
    плюс отдельные столбцы: АТП ТЛП / Ждут заказчика / Хроника /
    Учтено A/B.

    Листы — из documents.
    """
    active = _load_active_df()

    # ---- Листы по дисциплинам ----
    with get_conn() as conn:
        docs = pd.read_sql("""
            SELECT
                discipline,
                COUNT(DISTINCT id) AS docs_total,
                SUM(CASE WHEN UPPER(status) = 'A' THEN 1 ELSE 0 END) AS doc_a,
                SUM(CASE WHEN UPPER(status) = 'B' THEN 1 ELSE 0 END) AS doc_b,
                SUM(CASE WHEN UPPER(status) = 'C' THEN 1 ELSE 0 END) AS doc_c,
                SUM(CASE WHEN status = 'И' THEN 1 ELSE 0 END) AS doc_info,
                SUM(CASE WHEN status IN ('Для согласования','На рассмотрении')
                         THEN 1 ELSE 0 END) AS doc_review,
                SUM(CASE WHEN status = 'На корректировке' THEN 1 ELSE 0 END) AS doc_fix,
                SUM(CASE WHEN status = 'Аннулировано' THEN 1 ELSE 0 END) AS doc_annulled,
                SUM(CASE WHEN status IS NULL OR status = ''
                          OR status = 'Размещено' THEN 1 ELSE 0 END) AS doc_misc,
                COUNT(DISTINCT complex) AS complexes
            FROM documents
            WHERE discipline IS NOT NULL
            GROUP BY discipline
        """, conn)

        # ---- Комментарии по всей базе (для колонок «Всего/Закрыто/Аннул.») ----
        comms_base = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status = 'Закрыто' THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END) AS annulled
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
        """, conn)

    # ---- Активные по дисциплинам (из единого источника) ----
    if not active.empty:
        active_d = active.copy()
        active_d["Категория"] = (
            active_d["category"].fillna("Без категории")
            .replace("", "Без категории")
        )

        rows = []
        for disc, sub in active_d.groupby("discipline"):
            row = {"discipline": disc}

            # Замечания — общие
            row["active_total"] = len(sub)

            # Наши / заказчик / учтено
            row["ours"] = sub[sub["category_flag"].isin([
                "new_overdue", "new_in_progress",
                "in_work_overdue", "in_work_in_progress",
                "rejected_overdue", "rejected_in_progress",
                "discussion_overdue", "discussion_in_progress",
            ])].shape[0]

            row["waiting"] = sub[sub["category_flag"].isin([
                "waiting_customer", "waiting_customer_overdue",
                "waiting_customer_ontime",
            ])].shape[0]

            row["chronic"] = sub[
                sub["category_flag"] == "waiting_customer_chronic"
            ].shape[0]

            row["closed_doc"] = sub[
                sub["category_flag"] == "closed_by_doc_status"
            ].shape[0]

            # Категории
            cat_counts = sub["Категория"].value_counts()
            row["cat_1"] = int(cat_counts.get(CAT_1, 0))
            row["cat_2"] = int(cat_counts.get(CAT_2, 0))
            row["cat_3"] = int(cat_counts.get(CAT_3, 0))
            row["cat_4"] = int(cat_counts.get(CAT_4, 0))
            row["cat_none"] = int(cat_counts.get("Без категории", 0))

            rows.append(row)

        active_disc = pd.DataFrame(rows)
    else:
        active_disc = pd.DataFrame(columns=[
            "discipline", "active_total", "ours", "waiting", "chronic",
            "closed_doc", "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
        ])

    # ---- Склейка ----
    df = comms_base.merge(docs, on="discipline", how="outer")
    df = df.merge(active_disc, on="discipline", how="outer")
    df = df.fillna(0)

    # ---- Проценты ----
    df["Выполнено, %"] = df.apply(
        lambda r: round((r["closed"] + r["annulled"]) / r["total"] * 100, 1)
        if r["total"] else 0.0,
        axis=1,
    )
    df["Листы готовы, %"] = df.apply(
        lambda r: round((r["doc_a"] + r["doc_b"] + r["doc_info"])
                        / r["docs_total"] * 100, 1)
        if r["docs_total"] else 0.0,
        axis=1,
    )

    return df.rename(columns={
        "discipline":   "Код",
        "complexes":    "Комплектов",
        "total":        "Всего замечаний",
        "closed":       "Закрыто",
        "annulled":     "Аннулировано",
        "active_total": "Активных",
        "ours":         "АТП ТЛП",
        "waiting":      "Ждут заказчика",
        "chronic":      "Хроника",
        "closed_doc":   "Учтено A/B",
        "cat_1":        "Принято",
        "cat_2":        "Формальное",
        "cat_3":        "Доп.треб.",
        "cat_4":        "Не принято",
        "cat_none":     "Без категории",
        "docs_total":   "Всего листов",
        "doc_a":        "Статус A",
        "doc_b":        "Статус B",
        "doc_c":        "Статус C",
        "doc_info":     "Информац.",
        "doc_review":   "На согласовании",
        "doc_fix":      "Корректировка",
        "doc_annulled": "Аннул. листы",
        "doc_misc":     "Прочее",
    })


def _render_discipline_table(df: pd.DataFrame):
    st.subheader("Сводка по дисциплинам")
    st.caption(
        "Колонки «Активных» / «АТП ТЛП» / «Ждут заказчика» / «Хроника» / "
        "«Учтено A/B» и категории — считаются **только по активным**. "
        "«Всего замечаний» / «Закрыто» / «Аннулировано» — по всей базе."
    )

    if df.empty:
        st.info("Нет данных.")
        return

    view = df.copy()
    view.insert(1, "Наименование", view["Код"].map(discipline_name))

    ordered = [
        "Код", "Наименование", "Комплектов",
        # Замечания — общие
        "Всего замечаний", "Активных", "Закрыто", "Аннулировано",
        "Выполнено, %",
        # Замечания — по ответственным
        "АТП ТЛП", "Ждут заказчика", "Хроника", "Учтено A/B",
        # Категории
        "Принято", "Формальное", "Доп.треб.", "Не принято",
        "Без категории",
        # Листы
        "Всего листов",
        "Статус A", "Статус B", "Статус C", "Информац.",
        "На согласовании", "Корректировка", "Аннул. листы", "Прочее",
        "Листы готовы, %",
    ]
    view = view[[c for c in ordered if c in view.columns]]

    st.dataframe(
        view, use_container_width=True, hide_index=True,
        column_config={
            "Выполнено, %":    st.column_config.ProgressColumn(
                "Выполнено, %", min_value=0, max_value=100,
                format="%.1f%%"),
            "Листы готовы, %": st.column_config.ProgressColumn(
                "Листы готовы, %", min_value=0, max_value=100,
                format="%.1f%%"),
        },
    )

    # ---- Итоги ----
    totals = {"Код": "ИТОГО", "Наименование": ""}
    for col in view.columns:
        if col in ("Код", "Наименование"):
            continue
        if col in ("Выполнено, %", "Листы готовы, %"):
            continue
        if col in view.columns:
            totals[col] = int(view[col].sum())

    total = totals.get("Всего замечаний", 0)
    totals["Выполнено, %"] = (
        round((totals.get("Закрыто", 0) + totals.get("Аннулировано", 0))
              / total * 100, 1) if total else 0.0
    )
    dtotal = totals.get("Всего листов", 0)
    totals["Листы готовы, %"] = (
        round((totals.get("Статус A", 0) + totals.get("Статус B", 0)
               + totals.get("Информац.", 0)) / dtotal * 100, 1)
        if dtotal else 0.0
    )

    st.markdown("**Итоги:**")
    st.dataframe(pd.DataFrame([totals]),
                 use_container_width=True, hide_index=True)

    # ---- Экспорт ----
    with st.expander("📥 Выгрузить сводку в Excel", expanded=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            view.to_excel(writer, index=False, sheet_name="Дисциплины")
        buf.seek(0)
        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Обзор_дисциплины_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
#  Загрузчики для графиков
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_status_distribution() -> pd.DataFrame:
    """Пирог: все статусы по всей базе."""
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                CASE WHEN status IS NULL OR status = ''
                     THEN 'Не указан' ELSE status END AS "Статус",
                COUNT(*) AS "Количество"
            FROM comments GROUP BY "Статус" ORDER BY "Количество" DESC
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_sheet_status_distribution() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT
                CASE
                    WHEN UPPER(status) = 'A' THEN 'A — Утверждён'
                    WHEN UPPER(status) = 'B' THEN 'B — Готов к сдаче'
                    WHEN UPPER(status) = 'C' THEN 'C — В работе'
                    WHEN status = 'И' THEN 'И — Информационный'
                    WHEN status IN ('Для согласования', 'На рассмотрении')
                         THEN 'На согласовании'
                    WHEN status = 'На корректировке' THEN 'Корректировка'
                    WHEN status = 'Аннулировано' THEN 'Аннулировано'
                    WHEN status IS NULL OR status = '' THEN 'Нет статуса'
                    ELSE 'Прочее'
                END AS "Статус листа",
                COUNT(*) AS "Количество"
            FROM documents GROUP BY "Статус листа"
            ORDER BY "Количество" DESC
        """, conn)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_top_complexes(limit: int = 15) -> pd.DataFrame:
    """
    Топ комплектов по ПРОСРОЧКАМ АТП ТЛП.
    Единая логика с дашбордом (флаги _overdue из _load_all_categorized).
    """
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    overdue_flags = [
        "new_overdue", "in_work_overdue",
        "rejected_overdue", "discussion_overdue",
    ]
    sub = active[active["category_flag"].isin(overdue_flags)]

    top = (sub.groupby(["complex", "discipline"]).size()
             .reset_index(name="Просрочек")
             .sort_values("Просрочек", ascending=False)
             .head(limit))
    return top


# ---------------------------------------------------------------------------
#  Графики
# ---------------------------------------------------------------------------
def _render_charts(df_disc: pd.DataFrame):
    # =================================================================
    #  Ряд 1: пироги статусов
    # =================================================================
    col1, col2 = st.columns(2)

    with col1:
        dist = _load_status_distribution()
        if not dist.empty:
            fig = px.pie(
                dist, names="Статус", values="Количество", hole=0.45,
                color="Статус", color_discrete_map=STATUS_COLORS,
                title="Замечания по статусам (вся база)",
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_статусы_замечаний",
                            "ov_status")

    with col2:
        sheets = _load_sheet_status_distribution()
        if not sheets.empty:
            fig = px.pie(
                sheets, names="Статус листа", values="Количество",
                hole=0.45,
                color="Статус листа",
                color_discrete_map=SHEET_STATUS_COLORS,
                title="Статусы листов РД",
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_статусы_листов", "ov_sheets")

    # =================================================================
    #  Ряд 2: активные — по категориям + топ комплектов
    # =================================================================
    col3, col4 = st.columns(2)

    with col3:
        active = _load_active_df()
        if not active.empty:
            cat_counts = (
                active["category"].fillna("Без категории")
                .replace("", "Без категории")
                .value_counts().reset_index()
            )
            cat_counts.columns = ["Категория", "Количество"]

            fig = px.pie(
                cat_counts, names="Категория", values="Количество",
                hole=0.45,
                color="Категория", color_discrete_map=CAT_COLORS,
                title="Активные замечания по категориям",
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_активные_категории",
                            "ov_active_cat")

    with col4:
        top = _load_top_complexes(limit=15)
        if not top.empty:
            top_chart = top.sort_values("Просрочек", ascending=True)
            fig = px.bar(
                top_chart,
                x="Просрочек", y="complex", orientation="h",
                color="discipline",
                text="Просрочек",
                labels={"complex": "", "discipline": "Дисциплина"},
                title="Топ-15 комплектов по просрочкам АТП ТЛП",
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(height=max(500, 30 * len(top_chart)))
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_топ15_комплектов", "ov_top",
                            width=1400, height=700)

    # =================================================================
    #  Ряд 3: замечания по дисциплинам (стек по ответственным)
    # =================================================================
    if not df_disc.empty:
        st.markdown("##### 📊 Активные замечания по дисциплинам")

        rows_flat = []
        for _, row in df_disc.iterrows():
            disc = row["Код"]
            if not disc:
                continue
            for label, key in [
                ("АТП ТЛП", "АТП ТЛП"),
                ("Ждут заказчика", "Ждут заказчика"),
                ("Хроника", "Хроника"),
                ("Учтено A/B", "Учтено A/B"),
            ]:
                if key in row:
                    rows_flat.append({
                        "Код": disc,
                        "Ответственный": label,
                        "Количество": int(row[key]),
                    })

        if rows_flat:
            flat = pd.DataFrame(rows_flat)
            color_map = {
                "АТП ТЛП":          "#E57373",
                "Ждут заказчика":   "#64B5F6",
                "Хроника":          "#7F0000",
                "Учтено A/B":       "#2E7D32",
            }
            fig = px.bar(
                flat, x="Код", y="Количество", color="Ответственный",
                barmode="stack",
                color_discrete_map=color_map,
                category_orders={"Ответственный": [
                    "АТП ТЛП", "Ждут заказчика", "Хроника",
                    "Учтено A/B"]},
                title="Активные замечания по дисциплинам",
            )
            fig.update_layout(
                legend_title_text="",
                xaxis_tickangle=-45,
                height=450,
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Обзор_активные_по_дисциплинам",
                            "ov_active_disc",
                            width=1400, height=600)


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📊 Обзор")
    st.caption(
        "Главная витрина проекта. Оперативные цифры синхронизированы "
        "с дашбордом через единый источник — активные замечания "
        "из всех выданных."
    )

    # Кнопка «Обновить» убрана — данные из кэша (TTL 1 час).

    _render_kpi()
    st.divider()

    df_disc = _load_discipline_summary()
    _render_discipline_table(df_disc)
    st.divider()

    _render_charts(df_disc)