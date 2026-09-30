# vitro/ui/export.py
"""
📄 Экспорт — выгрузка сводок, замечаний и аналитики в Excel и PDF.

Вся аналитика синхронизирована с дашбордом через `_load_all_categorized`.
Колонки по ответственным: 🔴 АТП ТЛП / 🔵 Ждут заказчика / 🔴 Хроника / 🟢 Учтено A/B.

Форматированные XLSX-шаблоны генерируются в разделе «⚙️ Управление».
"""

import io
from datetime import datetime

import pandas as pd
import streamlit as st

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
#  Единый источник активных
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_active_df() -> pd.DataFrame:
    """Активные замечания из deadlines._load_all_categorized."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return df
    return df[df["category_flag"] != "abandoned"].copy()


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
#  Сводки через _load_all_categorized
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_discipline_summary() -> pd.DataFrame:
    """
    Сводка по дисциплинам:
      - Всего / Закрыто / Аннулировано — по всей базе
      - Активных + ответственные + категории — из _load_all_categorized
      - Листов — из documents
    """
    active = _load_active_df()

    with get_conn() as conn:
        # ---- Общие цифры по всей базе ----
        base = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                COUNT(DISTINCT d.complex) AS complexes,
                COUNT(DISTINCT d.id) AS docs_total,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.discipline IS NOT NULL
            GROUP BY d.discipline
        """, conn)

    # ---- Активные по дисциплинам ----
    if not active.empty:
        active_d = active.copy()
        active_d["Категория"] = (
            active_d["category"].fillna("Без категории")
            .replace("", "Без категории")
        )

        rows = []
        for disc, sub in active_d.groupby("discipline"):
            row = {"discipline": disc}
            row["active_total"] = len(sub)
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

            cc = sub["Категория"].value_counts()
            row["cat_1"] = int(cc.get(CAT_1, 0))
            row["cat_2"] = int(cc.get(CAT_2, 0))
            row["cat_3"] = int(cc.get(CAT_3, 0))
            row["cat_4"] = int(cc.get(CAT_4, 0))
            row["cat_none"] = int(cc.get("Без категории", 0))

            rows.append(row)

        active_disc = pd.DataFrame(rows)
    else:
        active_disc = pd.DataFrame(columns=[
            "discipline", "active_total", "ours", "waiting", "chronic",
            "closed_doc", "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
        ])

    df = base.merge(active_disc, on="discipline", how="outer").fillna(0)

    df["pct_closed"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1)
        if r["total"] else 0.0,
        axis=1,
    )
    df["pct_cat"] = df.apply(
        lambda r: round((r["active_total"] - r["cat_none"])
                        / r["active_total"] * 100, 1)
        if r["active_total"] else 0.0,
        axis=1,
    )

    return df.rename(columns={
        "discipline": "Код",
        "complexes": "Комплектов",
        "docs_total": "Листов",
        "total": "Всего замечаний",
        "closed": "Закрыто",
        "annulled": "Аннулировано",
        "active_total": "Активных",
        "ours": "🔴 АТП ТЛП",
        "waiting": "🔵 Ждут заказ.",
        "chronic": "🔴 Хроника",
        "closed_doc": "🟢 Учтено A/B",
        "cat_1": "Принято",
        "cat_2": "Формальное",
        "cat_3": "Доп.треб.",
        "cat_4": "Не принято",
        "cat_none": "Без категории",
        "pct_closed": "% закрыто",
        "pct_cat": "% разбора",
    })


@st.cache_data(ttl=3600, show_spinner=False)
def _load_complex_summary() -> pd.DataFrame:
    """Сводка по комплектам."""
    active = _load_active_df()

    with get_conn() as conn:
        base = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                d.complex AS complex,
                COUNT(DISTINCT d.id) AS docs_total,
                SUM(CASE WHEN UPPER(d.status) = 'A' THEN 1 ELSE 0 END) AS doc_a,
                SUM(CASE WHEN UPPER(d.status) = 'B' THEN 1 ELSE 0 END) AS doc_b,
                SUM(CASE WHEN UPPER(d.status) = 'C' THEN 1 ELSE 0 END) AS doc_c,
                SUM(CASE WHEN d.status = 'И' THEN 1 ELSE 0 END) AS doc_info,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
            GROUP BY d.discipline, d.complex
        """, conn)

    if not active.empty:
        rows = []
        for (disc, cx), sub in active.groupby(["discipline", "complex"]):
            rows.append({
                "discipline": disc,
                "complex": cx,
                "active_total": len(sub),
                "ours": sub[sub["category_flag"].isin([
                    "new_overdue", "new_in_progress",
                    "in_work_overdue", "in_work_in_progress",
                    "rejected_overdue", "rejected_in_progress",
                    "discussion_overdue", "discussion_in_progress",
                ])].shape[0],
                "waiting": sub[sub["category_flag"].isin([
                    "waiting_customer", "waiting_customer_overdue",
                    "waiting_customer_ontime",
                ])].shape[0],
                "chronic": sub[
                    sub["category_flag"] == "waiting_customer_chronic"
                ].shape[0],
                "closed_doc": sub[
                    sub["category_flag"] == "closed_by_doc_status"
                ].shape[0],
            })
        active_cx = pd.DataFrame(rows)
    else:
        active_cx = pd.DataFrame(columns=[
            "discipline", "complex", "active_total",
            "ours", "waiting", "chronic", "closed_doc",
        ])

    df = base.merge(active_cx, on=["discipline", "complex"],
                     how="outer").fillna(0)

    df["pct_closed"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)

    return df.rename(columns={
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "docs_total": "Листов",
        "doc_a": "Статус A",
        "doc_b": "Статус B",
        "doc_c": "Статус C",
        "doc_info": "Информац.",
        "total": "Всего",
        "closed": "Закрыто",
        "annulled": "Аннулировано",
        "active_total": "Активных",
        "ours": "🔴 АТП ТЛП",
        "waiting": "🔵 Ждут заказ.",
        "chronic": "🔴 Хроника",
        "closed_doc": "🟢 Учтено A/B",
        "pct_closed": "% закрыто",
    })


@st.cache_data(ttl=3600, show_spinner=False)
def _load_section_summary() -> pd.DataFrame:
    """Сводка по разделам."""
    active = _load_active_df()

    with get_conn() as conn:
        base = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                d.section AS section,
                COUNT(DISTINCT d.complex) AS complexes,
                COUNT(DISTINCT d.id) AS docs_total,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN c.status = 'Аннулировано'
                         THEN 1 ELSE 0 END) AS annulled
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.section IS NOT NULL AND d.section <> ''
            GROUP BY d.discipline, d.section
        """, conn)

    if not active.empty:
        rows = []
        for (disc, sect), sub in active.groupby(["discipline", "section"]):
            rows.append({
                "discipline": disc,
                "section": sect,
                "active_total": len(sub),
                "ours": sub[sub["category_flag"].isin([
                    "new_overdue", "new_in_progress",
                    "in_work_overdue", "in_work_in_progress",
                    "rejected_overdue", "rejected_in_progress",
                    "discussion_overdue", "discussion_in_progress",
                ])].shape[0],
                "waiting": sub[sub["category_flag"].isin([
                    "waiting_customer", "waiting_customer_overdue",
                    "waiting_customer_ontime",
                ])].shape[0],
                "chronic": sub[
                    sub["category_flag"] == "waiting_customer_chronic"
                ].shape[0],
                "closed_doc": sub[
                    sub["category_flag"] == "closed_by_doc_status"
                ].shape[0],
            })
        active_sect = pd.DataFrame(rows)
    else:
        active_sect = pd.DataFrame(columns=[
            "discipline", "section", "active_total",
            "ours", "waiting", "chronic", "closed_doc",
        ])

    df = base.merge(active_sect, on=["discipline", "section"],
                     how="outer").fillna(0)

    df["pct_closed"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)

    return df.rename(columns={
        "discipline": "Дисциплина",
        "section": "Раздел",
        "complexes": "Комплектов",
        "docs_total": "Листов",
        "total": "Всего",
        "closed": "Закрыто",
        "annulled": "Аннулировано",
        "active_total": "Активных",
        "ours": "🔴 АТП ТЛП",
        "waiting": "🔵 Ждут заказ.",
        "chronic": "🔴 Хроника",
        "closed_doc": "🟢 Учтено A/B",
        "pct_closed": "% закрыто",
    })


# ---------------------------------------------------------------------------
#  Замечания с фильтрами
# ---------------------------------------------------------------------------
def _load_comments_filtered(query: str,
                             disciplines=(), sections=(), kits=(),
                             statuses=(), categories=(),
                             limit: int = 20000) -> pd.DataFrame:
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
            c.fix_date AS "Наш ответ",
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
@st.cache_data(ttl=3600, show_spinner=False)
def _load_authors_analytics() -> pd.DataFrame:
    """Аналитика по авторам — только активные."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    df = active.copy()
    df["Категория"] = (
        df["category"].fillna("Без категории")
        .replace("", "Без категории")
    )

    rows = []
    for author, sub in df.groupby("author"):
        if not author:
            continue

        cc = sub["Категория"].value_counts()
        rows.append({
            "author": author,
            "total": len(sub),
            "ours": sub[sub["category_flag"].isin([
                "new_overdue", "new_in_progress",
                "in_work_overdue", "in_work_in_progress",
                "rejected_overdue", "rejected_in_progress",
                "discussion_overdue", "discussion_in_progress",
            ])].shape[0],
            "waiting": sub[sub["category_flag"].isin([
                "waiting_customer", "waiting_customer_overdue",
                "waiting_customer_ontime",
            ])].shape[0],
            "chronic": sub[
                sub["category_flag"] == "waiting_customer_chronic"
            ].shape[0],
            "closed_doc": sub[
                sub["category_flag"] == "closed_by_doc_status"
            ].shape[0],
            "cat_1": int(cc.get(CAT_1, 0)),
            "cat_2": int(cc.get(CAT_2, 0)),
            "cat_3": int(cc.get(CAT_3, 0)),
            "cat_4": int(cc.get(CAT_4, 0)),
            "cat_none": int(cc.get("Без категории", 0)),
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    result["pct_cat"] = result.apply(
        lambda r: round((r["total"] - r["cat_none"]) / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)

    return result.rename(columns={
        "author": "Автор",
        "total": "Активных",
        "ours": "🔴 АТП ТЛП",
        "waiting": "🔵 Ждут заказ.",
        "chronic": "🔴 Хроника",
        "closed_doc": "🟢 Учтено A/B",
        "cat_1": "Принято",
        "cat_2": "Формальное",
        "cat_3": "Доп.треб.",
        "cat_4": "Не принято",
        "cat_none": "Без категории",
        "pct_cat": "% разбора",
    }).sort_values("Активных", ascending=False)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_monthly_dynamics() -> pd.DataFrame:
    """
    Динамика по месяцам: выдача / наши ответы / закрыто заказчиком.
    """
    with get_conn() as conn:
        issue = pd.read_sql("""
            SELECT substr(created, 1, 7) AS ym, COUNT(*) AS issued
            FROM comments
            WHERE created IS NOT NULL AND created <> ''
            GROUP BY ym
        """, conn)

        our = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS our
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status IN ('Закрыто', 'Выполнено')
            GROUP BY ym
        """, conn)

        cust = pd.read_sql("""
            SELECT substr(fix_date, 1, 7) AS ym, COUNT(*) AS cust
            FROM comments
            WHERE fix_date IS NOT NULL AND fix_date <> ''
              AND status = 'Закрыто'
            GROUP BY ym
        """, conn)

    merged = pd.merge(
        issue, our, on="ym", how="outer",
    ).merge(cust, on="ym", how="outer").fillna(0).sort_values("ym")

    merged.columns = ["Месяц", "Выдано", "Наши ответы", "Закрыто заказчиком"]
    for c in ["Выдано", "Наши ответы", "Закрыто заказчиком"]:
        merged[c] = merged[c].astype(int)

    merged["Баланс (наш)"] = merged["Выдано"] - merged["Наши ответы"]
    return merged


@st.cache_data(ttl=3600, show_spinner=False)
def _load_lagging_complexes(limit: int = 50) -> pd.DataFrame:
    """Комплекты с наибольшим числом просрочек АТП ТЛП."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    overdue_flags = [
        "new_overdue", "in_work_overdue",
        "rejected_overdue", "discussion_overdue",
    ]
    sub = active[active["category_flag"].isin(overdue_flags)]

    if sub.empty:
        return pd.DataFrame()

    # Общие цифры по комплектам (для контекста)
    with get_conn() as conn:
        base = pd.read_sql("""
            SELECT
                d.complex AS complex,
                d.discipline AS discipline,
                COUNT(c.id) AS total,
                SUM(CASE WHEN c.status = 'Закрыто'
                         THEN 1 ELSE 0 END) AS closed
            FROM documents d
            LEFT JOIN comments c ON c.doc_id = d.id
            WHERE d.complex IS NOT NULL
            GROUP BY d.complex, d.discipline
        """, conn)

    overdue_cx = (sub.groupby("complex").size()
                     .reset_index(name="ours_overdue")
                     .sort_values("ours_overdue", ascending=False)
                     .head(limit))

    df = overdue_cx.merge(base, on="complex", how="left").fillna(0)
    df["pct_closed"] = df.apply(
        lambda r: round(r["closed"] / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)

    return df.rename(columns={
        "complex": "Комплект",
        "discipline": "Дисциплина",
        "total": "Всего",
        "closed": "Закрыто",
        "ours_overdue": "🔴 Просрочек АТП ТЛП",
        "pct_closed": "% закрыто",
    })


# ---------------------------------------------------------------------------
#  Утилиты
# ---------------------------------------------------------------------------
def _df_to_xlsx(df: pd.DataFrame, sheet_name: str = "Данные") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
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
                label = (f"⬇️ PDF "
                         f"({min(len(df), pdf_max_rows):,} строк)"
                         .replace(",", " "))

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
        "Готовые сводки по проекту. Все оперативные цифры "
        "синхронизированы с дашбордом."
    )

    tab1, tab2, tab3 = st.tabs([
        "🏷 По дисциплинам",
        "🏗 По комплектам",
        "📂 По разделам",
    ])

    with tab1:
        df = _load_discipline_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True,
                      height=400)
        _make_download(df, "Сводка_по_дисциплинам", "sum_disc",
                        subtitle="Сводка замечаний по дисциплинам проекта")

    with tab2:
        df = _load_complex_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True,
                      height=400)
        _make_download(df, "Сводка_по_комплектам", "sum_kit",
                        subtitle="Сводка замечаний по комплектам проекта")

    with tab3:
        df = _load_section_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True,
                      height=400)
        _make_download(df, "Сводка_по_разделам", "sum_sect",
                        subtitle="Сводка замечаний по разделам проекта")

    st.divider()
    st.info(
        "💡 **Форматированные XLSX-отчёты** (с гиперссылками, цветами "
        "и выпадающими списками) генерируются в разделе "
        "**⚙️ Управление → Генерация XLSX**."
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
                                     placeholder="Все статусы",
                                     key="exp_status")
    with c5:
        sel_category = st.multiselect(
            "Категория", [CAT_1, CAT_2, CAT_3, CAT_4],
            placeholder="Все категории", key="exp_category")
    with c6:
        query = st.text_input("Поиск по тексту",
                               placeholder="Оставьте пустым для всех",
                               key="exp_query")

    limit = st.number_input(
        "Максимум строк", min_value=100, max_value=100000,
        value=20000, step=1000, key="exp_limit",
        help="При большом объёме выгрузка может занять время",
    )

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
        pdf_max_rows=500,
    )


# ---------------------------------------------------------------------------
#  Секция 3: Аналитика
# ---------------------------------------------------------------------------
def _render_analytics():
    st.markdown("### 📈 Аналитика")
    st.caption(
        "Готовые аналитические выгрузки. Всё — по активным замечаниям, "
        "синхронизировано с дашбордом."
    )

    tab1, tab2, tab3 = st.tabs([
        "👤 Авторы",
        "📅 Динамика по месяцам",
        "⚠️ Топ просрочек",
    ])

    with tab1:
        df = _load_authors_analytics()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True,
                      height=400)
        _make_download(df, "Аналитика_авторов", "authors",
                        subtitle="Аналитика по авторам (активные замечания)")

    with tab2:
        df = _load_monthly_dynamics()
        st.caption(f"Месяцев: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True)
        _make_download(df, "Динамика_по_месяцам", "monthly",
                        subtitle="Динамика: выдача, ответы АТП ТЛП, "
                                  "закрытие заказчиком")

    with tab3:
        df = _load_lagging_complexes(limit=50)
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, use_container_width=True, hide_index=True,
                      height=400)
        _make_download(df, "Топ_просрочек_АТП_ТЛП", "lagging",
                        subtitle="Комплекты с наибольшим числом "
                                  "просрочек АТП ТЛП")


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