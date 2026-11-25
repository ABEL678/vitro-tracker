# vitro/ui/export.py
"""
📄 Экспорт — выгрузка сводок, замечаний и аналитики в Excel и PDF.

Вся аналитика синхронизирована с дашбордом через `_load_all_categorized`.
Колонки по ответственным: 🔴 АТП ТЛП / 🔵 Ждут заказчика / 🔴 Хроника / 🟢 Учтено A/B.

Подвкладка «🔄 Обновить статусы» (патч 21): во внешний перечень
комплектов (Excel) добавляется колонка «Статус комплекта» —
логика в vitro/sheets.py (get_kit_status / get_kit_statuses).

Патч 22: рядом со статусом добавляются «Ревизия комплекта» и
«Дата ревизии» (get_kit_revision_info), а строкой ВЫШЕ шапки
таблицы — объединённая полоса «База актуальна на … (МСК)» с датой
того же источника, что и полоса даты в шапке приложения
(deadlines._load_last_sync_date).

Патч 22.18: сопоставление шифров Excel с базой — ЕДИНЫЙ хелпер
«Плана выдачи» _kit_matches_complex (патч 22.15): точное →
нормализованное → «шифр + хвост текста» (КСТ2) → ГИС-база без
дробной части. КСТ2 и загруженные ГИС-комплекты больше не получают
«План (не загружен)»: у них «Загружено в Витро» + ревизия/дата из
базы; ГИС без документов — «Передан (вне Витро)» с пустыми
ревизией/датой; несопоставленные шифры выводятся списком в отчёте.
Обычные АТ-комплекты со статусом листов сохраняют A/B/C (патч 21).

Форматированные XLSX-шаблоны генерируются в разделе «⚙️ Управление».
"""

import io
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, range_boundaries
import pandas as pd
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro._cache import versioned_cache
from vitro.sheets import (
    KIT_STATUS_NOT_LOADED,
    get_kit_revision_info,
    get_kit_statuses,
    normalize_kit_code,
    unique_vitro_sheets,
)
from vitro.time_utils import fmt_msk
from vitro.pdf_builder import build_pdf
from vitro.ui._filters import render_standard_filters
# Патч 22.18: сопоставление шифров экспорта — ЕДИНЫЙ хелпер «Плана
# выдачи» (патч 22.15) и признак ГИС-комплекта (22.11/22.17).
# Своей логики сверки в экспорте нет: одна правда для диаграммы,
# «Плана выдачи» и «Обновить статусы».
from vitro.ui.plan import (
    KIT_STATUS_PASSED,
    _is_gis_kit,
    _vitro_claim_index,
)


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
@versioned_cache(3600)
def _load_active_df() -> pd.DataFrame:
    """Активные замечания из deadlines._load_all_categorized."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return df
    return df[df["category_flag"] != "abandoned"].copy()


# ---------------------------------------------------------------------------
#  Справочники — общий модуль _filters.py (дисциплины / разделы / комплекты)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
#  Сводки через _load_all_categorized
# ---------------------------------------------------------------------------
@versioned_cache(3600)
def _load_discipline_summary() -> pd.DataFrame:
    """
    Сводка по дисциплинам:
      - Всего / Закрыто / Аннулировано — по всей базе
      - Активных + ответственные + категории — из _load_all_categorized
      - Листов — ЕДИНЫЙ СТАНДАРТ «Плана выдачи» (патч 14):
        sheets.unique_vitro_sheets — дедуп ревизий, нормализация
        шифров (в т.ч. латинских двойников), отсечение мусорных.
        Раньше считали SQL-выражением sql_count_sheets — без
        нормализации и отсечения, поэтому цифры расходились
        с «Планом выдачи».
    """
    active = _load_active_df()

    with get_conn() as conn:
        # ---- Листы: единый стандарт (дедуп + валидные шифры) ----
        docs = pd.read_sql("""
            SELECT id, discipline, complex, sheet_number
            FROM documents
            WHERE discipline IS NOT NULL
        """, conn)

        # ---- Замечания: считаем по ВСЕМ строкам documents (без дедупликации:
        #      замечание, привязанное к старой ревизии, продолжает учитываться)
        remarks = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
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

    kept = unique_vitro_sheets(docs.to_dict("records"))
    if kept:
        ddf = pd.DataFrame(kept)
        base = (ddf.groupby("discipline", dropna=False)
                .agg(complexes=("complex", "nunique"),
                     docs_total=("id", "count"))
                .reset_index())
    else:
        base = pd.DataFrame(columns=["discipline", "complexes",
                                     "docs_total"])

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

    # base (листы) + remarks (замечания, docs-driven) + active_disc
    df = (base.merge(remarks, on="discipline", how="outer")
              .merge(active_disc, on="discipline", how="outer")
              .fillna(0))

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


@versioned_cache(3600)
def _load_complex_summary() -> pd.DataFrame:
    """Сводка по комплектам.

    Патчи 11/14 — единый стандарт листов:
      * Листы и статусы A/B/C/И — по УНИКАЛЬНЫМ листам (complex +
        sheet_number), статус берётся с ПОСЛЕДНЕЙ ревизии листа;
        sheets.unique_vitro_sheets — тот же код, что в «Плане
        выдачи»: нормализация шифров (латинские двойники) +
        отсечение мусорных.
      * Замечания (Всего/Закрыто/Аннулировано) — по всем замечаниям
        без дедупликации: замечание, привязанное к старой ревизии,
        продолжает учитываться.
    """
    active = _load_active_df()

    with get_conn() as conn:
        # ---- Листы: дедуплицируются в pandas (нужен статус последней ревизии)
        docs = pd.read_sql("""
            SELECT id, discipline, complex, status, sheet_number, status_date
            FROM documents
            WHERE complex IS NOT NULL
        """, conn)

        # ---- Замечания: считаем по ВСЕМ строкам documents (без дедупликации)
        remarks = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                d.complex AS complex,
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

    # Уникальные листы: одна строка = один лист (последняя ревизия,
    # нормализованный шифр, только валидные комплекты)
    kept = unique_vitro_sheets(docs.to_dict("records"))
    if not kept:
        docs = docs.iloc[0:0]
    else:
        docs = pd.DataFrame(kept).reindex(columns=docs.columns)

    if docs.empty:
        base = remarks.iloc[0:0].copy()
        for c in ("docs_total", "doc_a", "doc_b", "doc_c", "doc_info"):
            base[c] = 0
    else:
        st_u = docs["status"].fillna("").astype(str).str.strip().str.upper()
        docs = docs.assign(
            _st=st_u,
            _info=(st_u == "И").astype(int),
        )
        base = (
            docs.groupby(["discipline", "complex"], dropna=False)
            .agg(
                docs_total=("id", "count"),
                doc_a=("_st", lambda s: int((s == "A").sum())),
                doc_b=("_st", lambda s: int((s == "B").sum())),
                doc_c=("_st", lambda s: int((s == "C").sum())),
                doc_info=("_info", "sum"),
            )
            .reset_index()
        )
        base["doc_info"] = base["doc_info"].astype(int)

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

    # Ключи merge: groupby pandas по колонке из одних None даёт float64,
    # а SQL/read_sql — object → выравниваем типы перед merge (иначе ValueError).
    def _norm_cx_keys(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["discipline"] = out["discipline"].fillna("").astype(str)
        out["complex"] = out["complex"].fillna("").astype(str)
        return out

    df = _norm_cx_keys(base).merge(
        _norm_cx_keys(remarks), on=["discipline", "complex"], how="outer")
    df = df.merge(_norm_cx_keys(active_cx), on=["discipline", "complex"],
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


@versioned_cache(3600)
def _load_section_summary() -> pd.DataFrame:
    """Сводка по разделам.

    Патч 14: листы — единый стандарт «Плана выдачи»
    (sheets.unique_vitro_sheets) вместо SQL-выражения
    sql_count_sheets (без нормализации шифров и отсечения мусорных).
    """
    active = _load_active_df()

    with get_conn() as conn:
        # ---- Листы: единый стандарт (дедуп + валидные шифры) ----
        docs = pd.read_sql("""
            SELECT id, discipline, section, complex, sheet_number
            FROM documents
            WHERE section IS NOT NULL AND section <> ''
        """, conn)

        # ---- Замечания: по всем строкам documents (без дедупликации)
        remarks = pd.read_sql("""
            SELECT
                d.discipline AS discipline,
                d.section AS section,
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

    kept = unique_vitro_sheets(docs.to_dict("records"))
    if kept:
        ddf = pd.DataFrame(kept)
        base = (ddf.groupby(["discipline", "section"], dropna=False)
                .agg(complexes=("complex", "nunique"),
                     docs_total=("id", "count"))
                .reset_index())
    else:
        base = pd.DataFrame(columns=["discipline", "section",
                                     "complexes", "docs_total"])

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

    # base (листы) + remarks (замечания, docs-driven) + active_sect
    df = (base.merge(remarks, on=["discipline", "section"], how="outer")
              .merge(active_sect, on=["discipline", "section"],
                     how="outer")
              .fillna(0))

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
                             block_codes=(), marka: str = "",
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
    if block_codes:
        ors = []
        for bc in block_codes:
            ors.append("d.complex LIKE '%-" + str(bc) + "-%'")
            ors.append("d.complex LIKE '%-" + str(bc).lower() + "-%'")
        q += " AND (" + " OR ".join(ors) + ")"
    if marka:
        m = marka.strip().upper()
        if m:
            q += " AND d.complex LIKE ?"
            params.append(f"%-{m}-%")
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
@versioned_cache(3600)
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


@versioned_cache(3600)
def _load_monthly_dynamics() -> pd.DataFrame:
    """
    Динамика по месяцам: выдача / выполнено АТП ТЛП /
    закрыто заказчиком.
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
              AND status = 'Выполнено'
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

    merged.columns = ["Месяц", "Выдано", "Выполнено", "Закрыто заказчиком"]
    for c in ["Выдано", "Выполнено", "Закрыто заказчиком"]:
        merged[c] = merged[c].astype(int)

    # Сколько замечаний мы ещё не выполнили (Выдано − Выполнено).
    merged["Баланс (наш)"] = merged["Выдано"] - merged["Выполнено"]
    return merged


@versioned_cache(3600)
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
                width='stretch',
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
        st.dataframe(df, width='stretch', hide_index=True,
                      height=400)
        _make_download(df, "Сводка_по_дисциплинам", "sum_disc",
                        subtitle="Сводка замечаний по дисциплинам проекта")

    with tab2:
        df = _load_complex_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, width='stretch', hide_index=True,
                      height=400)
        _make_download(df, "Сводка_по_комплектам", "sum_kit",
                        subtitle="Сводка замечаний по комплектам проекта")

    with tab3:
        df = _load_section_summary()
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, width='stretch', hide_index=True,
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

    # --- Единый стандарт фильтров (как в Сводке) ---
    def _export_extra_row():
        c4, c5, c6 = st.columns(3)
        with c4:
            sel_category = st.multiselect(
                "Категория", [CAT_1, CAT_2, CAT_3, CAT_4],
                placeholder="Все категории", key="exp_category")
        with c5:
            query = st.text_input(
                "Поиск по тексту",
                placeholder="Оставьте пустым для всех",
                key="exp_query")
        with c6:
            limit = st.number_input(
                "Максимум строк", min_value=100, max_value=100000,
                value=20000, step=1000, key="exp_limit",
                help="При большом объёме выгрузка может занять время",
            )
        return {
            "category": tuple(sel_category),
            "query": query or "",
            "limit": int(limit),
        }

    # Экспорт ищет также по «Закрыто» и «Аннулировано» — поэтому
    # расширенный список статусов вместо стандартных пяти.
    flt = render_standard_filters(
        key_prefix="exp_",
        expander_label="🎛 Фильтры",
        remark_status_options=tuple(STATUSES),
        extra_row=_export_extra_row,
    )

    sel_disc = flt["disc"]
    sel_section = flt["section"]
    sel_kit = flt["kits"]
    sel_marka = flt["marka"]
    sel_status = flt["remark_statuses"]
    sel_category = list(flt["extra"].get("category", ()))
    query = flt["extra"].get("query", "")
    limit = flt["extra"].get("limit", 20000)

    df = _load_comments_filtered(
        query=query,
        disciplines=tuple(sel_disc) if sel_disc else (),
        sections=tuple(sel_section) if sel_section else (),
        kits=tuple(sel_kit) if sel_kit else (),
        statuses=tuple(sel_status) if sel_status else (),
        categories=tuple(sel_category) if sel_category else (),
        block_codes=flt["block_codes"],
        marka=sel_marka,
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

    st.dataframe(df.head(200), width='stretch',
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
        st.dataframe(df, width='stretch', hide_index=True,
                      height=400)
        _make_download(df, "Аналитика_авторов", "authors",
                        subtitle="Аналитика по авторам (активные замечания)")

    with tab2:
        df = _load_monthly_dynamics()
        st.caption(f"Месяцев: **{len(df)}**")
        st.dataframe(df, width='stretch', hide_index=True)
        _make_download(df, "Динамика_по_месяцам", "monthly",
                        subtitle="Динамика: выдача, выполнено АТП ТЛП, "
                                  "закрытие заказчиком")

    with tab3:
        df = _load_lagging_complexes(limit=50)
        st.caption(f"Строк: **{len(df)}**")
        st.dataframe(df, width='stretch', hide_index=True,
                      height=400)
        _make_download(df, "Топ_просрочек_АТП_ТЛП", "lagging",
                        subtitle="Комплекты с наибольшим числом "
                                  "просрочек АТП ТЛП")


# ---------------------------------------------------------------------------
#  Секция 4: Обновить статусы комплектов (патчи 21/22/22.18)
# ---------------------------------------------------------------------------
KIT_STATUS_COL = "Статус комплекта"
KIT_REVISION_COL = "Ревизия комплекта"
KIT_REV_DATE_COL = "Дата ревизии"

# Патч 22.18: статус сопоставленного комплекта, у которого в базе есть
# документы, но статус листов A/B/C ранее не вычислялся (КСТ2 с
# текстовым хвостом) либо это ГИС-комплект (приоритет 22.17).
# Обычные АТ-комплекты со статусом листов сохраняют A/B/C (патч 21,
# регресс test_patch22_3) — «нет регресса» из ТЗ 22.18.
KIT_STATUS_LOADED = "Загружено в Витро"

# Стили патча 22 (согласованы): шапки трёх kit-колонок — светло-
# голубая заливка D9E1F2; у всех ячеек таблицы тонкие границы
# #808080; полоса «База актуальна на …» строкой выше шапки —
# заливка #E8F0F8, как у полосы даты в шапке приложения
# (app.py .date-bar).
_KIT_HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
_KIT_BORDER_SIDE = Side(style="thin", color="808080")
_KIT_TABLE_BORDER = Border(left=_KIT_BORDER_SIDE,
                           right=_KIT_BORDER_SIDE,
                           top=_KIT_BORDER_SIDE,
                           bottom=_KIT_BORDER_SIDE)
_KIT_DATE_FILL = PatternFill("solid", fgColor="E8F0F8")


def _kit_sync_caption(sync_ts) -> str:
    """Текст полосы актуальности базы (патч 22).

    Тот же источник даты, что и полоса в шапке приложения:
    sync_ts — результат deadlines._load_last_sync_date(); без
    синков fmt_msk вернёт «—» → «База актуальна на —».
    """
    return f"База актуальна на {fmt_msk(sync_ts, with_msk_label=True)}"

# Заголовки колонки шифров в порядке приоритета (ТЗ патча 21);
# сравнение — по канонической форме _norm_header
_KIT_CODE_HEADERS = ("ШИФР РД", "ШИФР КОМПЛЕКТА", "КОМПЛЕКТ")


def _norm_header(h) -> str:
    """Заголовок колонки Excel → каноническая форма для сравнения:
    неразрывный пробел → пробел, сжатие повторов, trim, верхний
    регистр, латинские двойники → кириллица (normalize_kit_code)."""
    s = " ".join(str(h).replace("\xa0", " ").split())
    return normalize_kit_code(s)


def _find_kit_code_column(df: pd.DataFrame):
    """Колонка с шифрами комплектов во внешнем перечне (патч 21).

    Ищет заголовок «Шифр РД» / «Шифр комплекта» / «Комплект» (в
    порядке приоритета), фолбэк — первый заголовок, содержащий
    «ШИФР». Возвращает имя колонки или None (UI покажет ошибку со
    списком найденных заголовков).
    """
    norm = {_norm_header(h): h for h in df.columns}
    for want in _KIT_CODE_HEADERS:
        if want in norm:
            return norm[want]
    for h_norm, h in norm.items():
        if "ШИФР" in h_norm:
            return h
    return None


@versioned_cache(600)
def _load_all_kit_statuses() -> dict:
    """{шифр комплекта: статус} — один срез БД (патч 21).

    sheets.get_kit_statuses(None): unique_vitro_sheets (последние
    ревизии, «липкое» аннулирование, нормализация шифров) + правила
    статусов патча 21. versioned_cache — сброс при синке, как у
    остальных загрузчиков вкладки.
    """
    return get_kit_statuses()


@versioned_cache(600)
def _load_all_kit_revision_info() -> dict:
    """{шифр: {"revision": …, "date": …}} — один срез БД (патчи 22/22.18).

    sheets.get_kit_revision_info(None, include_invalid=True): дедуп
    документов БЕЗ отсечения невалидных написаний (стандарт
    «Плана выдачи», патч 22.15) — так в индекс попадают комплексы
    с текстовым хвостом («АТ-РД-КСТ2-А-00  КОНТАКТНАЯ СЕТЬ …»),
    которые раньше терялись и получали «План (не загружен)».
    Ревизия — максимум по числовому сравнению, дата — крайняя
    выгрузка ДД.ММ.ГГГГ. Ключи в индексе — нормализованные шифры
    комплексов базы. versioned_cache — сброс при синке, как у
    остальных загрузчиков вкладки.
    """
    return get_kit_revision_info(include_invalid=True)


def _empty_kit_metrics() -> dict:
    """Пустые kit-значения для строки без сопоставления (патч 22.18)."""
    return {"status": KIT_STATUS_NOT_LOADED, "revision": "", "date": ""}


def _rev_sort_key(value) -> float:
    """Ключ сравнения ревизий при склейке нескольких комплексов базы:
    числовое сравнение («2.0» < «10.0»), нечисловое → -1."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return -1.0


def _date_sort_key(value) -> tuple:
    """Ключ сравнения дат «ДД.ММ.ГГГГ» (формат _format_rev_date):
    неразбираемая дата → (0, 0, 0)."""
    try:
        d = datetime.strptime(str(value).strip(), "%d.%m.%Y")
    except (TypeError, ValueError):
        return (0, 0, 0)
    return (d.year, d.month, d.day)


def _merge_kit_metrics(items) -> dict:
    """Максимум ревизии и крайняя дата по нескольким комплексам базы.

    Нужен, когда одному шифру Excel сопоставилось больше одного
    написания комплекса (например, базовый и дробный ГИС): ревизия —
    максимум по числовому сравнению, дата — самая поздняя. Пустые
    значения не выигрывают (ключи -1 / (0,0,0)).
    """
    best_rev, best_rev_key = "", -1.0
    best_date, best_date_key = "", (0, 0, 0)
    for m in items or []:
        rev = str((m or {}).get("revision") or "")
        rk = _rev_sort_key(rev)
        if rk > best_rev_key:
            best_rev_key, best_rev = rk, rev
        dt = str((m or {}).get("date") or "")
        dk = _date_sort_key(dt)
        if dk > best_date_key:
            best_date_key, best_date = dk, dt
    return {"revision": best_rev, "date": best_date}


@versioned_cache(600)
def _resolve_kit_codes(keys: tuple) -> dict:
    """Патч 22.18: {нормализованный шифр Excel: kit-значения}.

    Сопоставление — ЕДИНЫЙ индекс зачёта «Плана выдачи»
    _vitro_claim_index (патч 22.15) → _kit_matches_complex: точное/
    нормализованное совпадение, затем «шифр + пробел(ы) + не-шифровый
    хвост» (КСТ2), ГИС-дроби, близнецы. Каждый комплекс базы
    зачитывается НЕ БОЛЕЕ чем одному шифру Excel — двойного счёта нет:
    если в перечне есть и базовый ГИС-код, и его дробный вариант,
    документы достаются базовому (точное совпадение сильнее), а
    дробный остаётся «Передан (вне Витро)» — как в диаграмме 22.17
    (5 загруженных ГИС / 26 переданных, а не 7/24). Своей сверки на
    равенство в экспорте нет.

    Правила значений (ТЗ 22.18):
      * сопоставился и есть документы:
          - ГИС-комплект → «Загружено в Витро» (приоритет 22.17);
          - обычный АТ со статусом листов A/B/C → этот статус
            (патч 21, без регресса);
          - все листы «И»/аннулированы → «План (не загружен)»
            (правило 21 не меняем);
          - сопоставился только хелпером 22.15 (хвост/дробь) →
            «Загружено в Витро»;
        ревизия/дата — по данным комплекса из базы;
      * ГИС без документов → «Передан (вне Витро)», ревизия/дата
        пустые (ничего не выдумываем);
      * иначе → «План (не загружен)», ревизия/дата пустые.

    keys — кортеж нормализованных шифров (хешируемый аргумент кэша);
    пустые пропускаются. versioned_cache — сброс при синке.
    """
    metrics = _load_all_kit_revision_info()
    valid_statuses = _load_all_kit_statuses()

    # Единый индекс зачёта 22.15: {шифр комплекса в базе: шифр Excel}.
    claims = _vitro_claim_index(list(metrics), list(keys or ()))
    matched_by_key: dict = {}
    for db_code, excel_code in claims.items():
        matched_by_key.setdefault(excel_code, []).append(db_code)

    resolved: dict = {}
    for key in keys or ():
        if not key:
            continue
        matched = matched_by_key.get(key, [])
        if not matched:
            if _is_gis_kit(key):
                resolved[key] = {
                    "status": KIT_STATUS_PASSED, "revision": "", "date": "",
                }
            else:
                resolved[key] = _empty_kit_metrics()
            continue

        merged = _merge_kit_metrics([metrics[cx] for cx in matched])
        if _is_gis_kit(key):
            status = KIT_STATUS_LOADED
        else:
            known = [valid_statuses[cx] for cx in matched
                     if cx in valid_statuses]
            graded = [s for s in known if s in ("A", "B", "C")]
            if graded:
                status = max(graded, key="ABC".index)
            elif known:
                # Все листы «И»/аннулированы — правило 21 (не регресс)
                status = KIT_STATUS_NOT_LOADED
            else:
                # Документы есть, но комплекс не проходил валидацию
                # (хвост/дробь-вариант) — ровно случай КСТ2 (22.18)
                status = KIT_STATUS_LOADED
        resolved[key] = {
            "status": status,
            "revision": merged["revision"],
            "date": merged["date"],
        }
    return resolved


def _apply_kit_statuses_resolved(df: pd.DataFrame, col: str) -> tuple:
    """Патч 22.18: kit-колонки по базе + список несопоставленных.

    Возвращает (DataFrame с тремя kit-колонками, [шифры-расхождения]).
    Сопоставление — _resolve_kit_codes (единый хелпер 22.15);
    исходный DataFrame не мутируется.

    В расхождения попадают шифры со статусом «План (не загружен)»
    (нет документов в базе и это не ГИС-комплект вне Витро) — чтобы
    пользователь видел опечатки и чужие строки перечня, а не молча
    получал пустой статус. Пустые ячейки не считаются расхождением.
    """
    codes = df[col].tolist()
    keys: list = []
    seen: set = set()
    for code in codes:
        key = normalize_kit_code(code)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    resolved = _resolve_kit_codes(tuple(keys))

    out = df.copy()
    statuses: list = []
    revisions: list = []
    dates: list = []
    unmatched: list = []
    for code in codes:
        key = normalize_kit_code(code)
        info = resolved.get(key) if key else None
        if info is None:
            info = _empty_kit_metrics()
        statuses.append(info["status"])
        revisions.append(info["revision"])
        dates.append(info["date"])
        if key and info["status"] == KIT_STATUS_NOT_LOADED:
            try:
                raw = "" if pd.isna(code) else str(code).strip()
            except (TypeError, ValueError):
                raw = str(code).strip()
            if raw and raw not in unmatched:
                unmatched.append(raw)

    out[KIT_STATUS_COL] = statuses
    out[KIT_REVISION_COL] = revisions
    out[KIT_REV_DATE_COL] = dates
    return out, unmatched


def _apply_kit_statuses(df: pd.DataFrame, col: str, statuses: dict,
                        rev_info: dict | None = None) -> pd.DataFrame:
    """Добавляет kit-колонки В КОНЕЦ таблицы (патчи 21/22).

    col — колонка шифров (результат _find_kit_code_column); шифры
    нормализуются перед сопоставлением со словарём статусов
    (латинские двойники, регистр, пробелы). Комплекта нет в Витро /
    пустой шифр → «План (не загружен)» (правило 2, согласовано).
    Исходный DataFrame не мутируется.

    Патч 22: если передан rev_info (результат
    _load_all_kit_revision_info), справа от статуса добавляются
    «Ревизия комплекта» (максимум, как в Сводке) и «Дата ревизии»
    (ДД.ММ.ГГГГ); комплекта нет в rev_info → пустые строки.
    rev_info=None → только статус (обратная совместимость,
    патч 21).
    """
    out = df.copy()
    out[KIT_STATUS_COL] = [
        statuses.get(normalize_kit_code(code), KIT_STATUS_NOT_LOADED)
        for code in out[col].tolist()
    ]
    if rev_info is not None:
        rev_rows = [
            rev_info.get(normalize_kit_code(code), {})
            for code in out[col].tolist()
        ]
        out[KIT_REVISION_COL] = [r.get("revision", "")
                                 for r in rev_rows]
        out[KIT_REV_DATE_COL] = [r.get("date", "") for r in rev_rows]
    return out


def _find_header_row_in_excel(uploaded_file) -> int:
    """Найти строку заголовков в загруженном Excel (патч 21.1).

    Файлы с шапкой проекта (например «График… 18.11.xlsx») имеют
    заголовки таблицы не в строке 0, а в строке 7-15. Функция читает
    первые 50 строк без header и ищет ту, где есть ячейка, содержащая
    «шифр» / «комплект» (регистронезависимо).

    Возвращает номер строки (0-based). Если не найдено — 0.
    """
    raw = pd.read_excel(uploaded_file, header=None, nrows=50)
    target_patterns = ("шифр", "комплект")

    for row_idx in range(len(raw)):
        for val in raw.iloc[row_idx].tolist():
            # Пустые ячейки (NaN) → пропускаем
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            s = str(val).replace("\xa0", " ").lower()
            for pat in target_patterns:
                if pat in s:
                    return row_idx

    return 0  # fallback: первая строка


def _find_kit_code_sheet(uploaded_file) -> tuple:
    """Найти лист и колонку с шифрами комплектов (патч 21.2).

    Файлы могут содержать несколько листов (например «Легенда от
    Заказчика» без таблицы и «График РД» с данными). Для каждого листа:
    1) читает без header и ищет строку с «шифр»/«комплект»;
    2) если строка найдена — перечитывает с правильным header;
    3) проверяет наличие колонки шифров.

    Возвращает (имя_листа, DataFrame, имя_колонки) или (None, None, None).
    """
    try:
        # Получаем список всех листов
        xl = pd.ExcelFile(uploaded_file)
        sheet_names = xl.sheet_names
    except Exception:
        return None, None, None

    for sheet_name in sheet_names:
        # Шаг 1: ищем строку заголовка БЕЗ header
        header_row = _find_header_row_in_excel_for_sheet(
            uploaded_file, sheet_name
        )

        # Шаг 2: перечитываем с правильным header
        try:
            df = pd.read_excel(uploaded_file, sheet_name=sheet_name,
                               header=header_row)
        except Exception:
            continue

        if df.empty:
            continue

        # Шаг 3: проверяем наличие колонки шифров
        col = _find_kit_code_column(df)
        if col is not None:
            return sheet_name, df, col

    return None, None, None


def _find_header_row_in_excel_for_sheet(uploaded_file, sheet_name: str) -> int:
    """Найти строку заголовков на конкретном листе Excel.

    Аналог _find_header_row_in_excel, но для конкретного листа.
    """
    raw = pd.read_excel(uploaded_file, sheet_name=sheet_name,
                        header=None, nrows=50)
    target_patterns = ("шифр", "комплект")

    for row_idx in range(len(raw)):
        for val in raw.iloc[row_idx].tolist():
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            s = str(val).replace("\xa0", " ").lower()
            for pat in target_patterns:
                if pat in s:
                    return row_idx

    return 0  # fallback: первая строка


def _render_kit_status_update():
    """Подвкладка «🔄 Обновить статусы» (патчи 21/22).

    Пользователь загружает Excel с перечнем комплектов — для каждого
    шифра подставляются «Статус комплекта», «Ревизия комплекта» и
    «Дата ревизии» по актуальным листам Витро; предпросмотр — первые
    10 строк, выгрузка — обновлённый исходный Excel (через openpyxl,
    сохраняя исходное форматирование) с полосой «База актуальна
    на …» строкой выше шапки таблицы.
    """
    st.markdown("### 🔄 Обновить статусы комплектов")
    st.caption(
        "Загрузите перечень комплектов (Excel) — для каждого шифра "
        "подставятся «Статус комплекта», «Ревизия комплекта» и "
        "«Дата ревизии» по базе Витро. Статус: «A» / «B» / «C» — "
        "известный статус листов комплекта; «Загружено в Витро» — "
        "документы в базе есть, но комплекс сопоставлен по хвосту/"
        "дроби (КСТ2, ГИС); «Передан (вне Витро)» — ГИС-комплект без "
        "документов; «План (не загружен)» — нет ни в базе, ни вне Витро."
    )

    uploaded = st.file_uploader(
        "Загрузите Excel с перечнем комплектов",
        type=["xlsx"],
        key="kit_status_upload",
    )
    if uploaded is None:
        st.info(
            "Файл не загружен. Формат: .xlsx; шифры — в колонке "
            "«Шифр РД» (или «Шифр комплекта», «Комплект»)."
        )
        return

    # --- Поиск листа с таблицей (патч 21.2) ---
    sheet_name, df, col = _find_kit_code_sheet(uploaded)

    if sheet_name is None:
        # Покажем список листов и заголовки для диагностики
        try:
            sheets = pd.read_excel(uploaded, sheet_name=None)
            st.error(
                "Не найдена колонка с шифрами ни на одном листе. "
                "Ожидался заголовок «Шифр РД» (или «Шифр комплекта», «Комплект»)."
            )
            st.markdown("**Листы в файле:**")
            for name, dframe in sheets.items():
                hdrs = ", ".join(f"«{h}»" for h in dframe.columns[:10])
                if len(dframe.columns) > 10:
                    hdrs += " ..."
                st.caption(f"• «{name}» ({len(dframe)} строк): {hdrs}")
        except Exception:
            st.error("Не удалось прочитать файл. Нужен Excel в формате .xlsx.")
        return

    if df.empty:
        st.warning("Файл прочитан, но таблица пуста.")
        return

    st.info(f"Найден лист «{sheet_name}» с колонкой «{col}».")

    # --- Предпросмотр через pandas ---
    result, unmatched = _apply_kit_statuses_resolved(df, col)

    n_loaded = int((result[KIT_STATUS_COL] == KIT_STATUS_LOADED).sum())
    n_graded = int(result[KIT_STATUS_COL].isin(["A", "B", "C"]).sum())
    n_passed = int((result[KIT_STATUS_COL] == KIT_STATUS_PASSED).sum())
    n_plan = int((result[KIT_STATUS_COL] == KIT_STATUS_NOT_LOADED).sum())
    st.success(
        f"Колонки «{KIT_STATUS_COL}», «{KIT_REVISION_COL}», "
        f"«{KIT_REV_DATE_COL}» добавлены (шифры — «{col}»). "
        f"Строк: {len(result)}: в Витро {n_graded + n_loaded} "
        f"(со статусом A/B/C — {n_graded}, «{KIT_STATUS_LOADED}» — "
        f"{n_loaded}), «{KIT_STATUS_PASSED}» — {n_passed}, "
        f"«{KIT_STATUS_NOT_LOADED}» — {n_plan}."
    )

    if unmatched:
        st.warning(
            f"Не сопоставлено с базой и не ГИС вне Витро: "
            f"{len(unmatched)} шифр(ов). Проверьте написание — эти "
            f"строки получат «{KIT_STATUS_NOT_LOADED}»."
        )
        with st.expander("Показать несопоставленные шифры"):
            for code in unmatched:
                st.markdown(f"• `{code}`")

    result = result.astype(str)
    st.dataframe(
        result.head(10).astype(str),
        width='stretch',
        hide_index=True,
        height=300,
    )
    if len(result) > 10:
        st.caption("Показаны первые 10 строк. В выгрузку попадут все.")

    # --- Выгрузка через openpyxl (сохраняет форматирование) ---
    st.divider()
    from vitro.ui.deadlines import _load_last_sync_date
    st.download_button(
        "📥 Скачать обновлённый Excel",
        data=_xlsx_add_kit_status(
            uploaded, sheet_name, col, None, KIT_STATUS_COL,
            sync_ts=_load_last_sync_date(),
        ),
        file_name=f"{_extract_filename(uploaded)}_статусы_{datetime.now():%Y%m%d}.xlsx",
        mime=("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
        width='stretch',
        key="dl_kit_status_xlsx",
    )


def _extract_filename(uploaded_file) -> str:
    """Извлечь имя файла без расширения (патч 21.3)."""
    name = uploaded_file.name
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def _xlsx_add_kit_status(
    source,
    sheet_name: str = "Комплекты",
    kit_col: str | None = None,
    statuses: dict | None = None,
    status_col_name: str = KIT_STATUS_COL,
    sync_ts=None,
) -> bytes:
    """Стилизованная выгрузка перечня комплектов (патчи 21/22).

    Два режима по типу source:

    1. Загруженный Excel (file_uploader) — ОСНОВНОЙ режим вызова
       из UI (фиксы 21.x): ИСХОДНЫЙ файл открывается через openpyxl
       и сохраняет своё форматирование; справа от таблицы
       добавляются три kit-колонки («Статус комплекта», «Ревизия
       комплекта», «Дата ревизии»), строкой выше шапки таблицы
       вставляется объединённая полоса «База актуальна на …» —
       шапка и данные сдвигаются на строку вниз, титульные строки
       выше шапки не трогаются. Шапки kit-колонок — заливка
       D9E1F2 (согласовано), у всех ячеек таблицы тонкие границы
       #808080, kit-колонки по центру, автофильтр по диапазону
       таблицы.

    2. pd.DataFrame (уже с kit-колонками от _apply_kit_statuses /
       _apply_kit_statuses_resolved — так зовут тесты
       scripts/test_patch22.py и fallback) —
       сборка новой книги «с нуля»: строка 1 — полоса даты,
       строка 2 — шапка, данные с 3-й, те же стили; sheet_name —
       имя листа.

    statuses (патч 21) в режиме 1 больше не используется: значения
    трёх колонок вычисляются единым резолвером _resolve_kit_codes
    (патч 22.18) — параметр сохранён для совместимости вызовов.

    sync_ts — timestamp последнего синка
    (deadlines._load_last_sync_date()); пусто → «База актуальна
    на —», как в полосе даты приложения.
    """
    if isinstance(source, pd.DataFrame):
        return _styled_df_to_xlsx(source, sheet_name=sheet_name,
                                  sync_ts=sync_ts)
    return _styled_upload_to_xlsx(
        source, sheet_name, kit_col, statuses, status_col_name, sync_ts
    )


def _styled_df_to_xlsx(df: pd.DataFrame, sheet_name: str = "Комплекты",
                       sync_ts=None) -> bytes:
    """Режим 2 _xlsx_add_kit_status: сборка книги «с нуля» (патч 22).

    Строка 1 — полоса «База актуальна на …» во всю ширину таблицы
    (заливка #E8F0F8, как .date-bar в app.py; рамка по контуру),
    ТЕКСТ полосы объединён над шапками kit-колонок (уточнение
    от 19.11.2026: дата стоит прямо над «Статус/Ревизия/Дата»),
    строка 2 — шапка (у трёх kit-колонок заливка D9E1F2, у всех
    колонок жирный шрифт и тонкие границы #808080), данные с 3-й
    строки (тонкие границы у всех ячеек, kit-колонки по центру),
    автофильтр A2:…, ширины колонок по содержимому.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Комплекты")[:31]

    n_cols = len(df.columns)
    last_letter = get_column_letter(max(n_cols, 1))
    kit_idx = {i for i, name in enumerate(df.columns, start=1)
               if name in (KIT_STATUS_COL, KIT_REVISION_COL,
                           KIT_REV_DATE_COL)}

    # --- Строка 1: полоса даты актуальности базы ---
    # Полоса (заливка E8F0F8, рамка по контуру) идёт во всю ширину
    # таблицы, а ТЕКСТ объединён над шапками kit-колонок — дата видна
    # прямо над «Статус/Ревизия/Дата ревизии», а не по центру всей
    # ширины (уточнение 19.11.2026). Без kit-колонок — во всю ширину.
    for i in range(1, n_cols + 1):
        _c = ws.cell(row=1, column=i)
        _c.border = Border(
            left=_KIT_BORDER_SIDE if i == 1 else None,
            right=_KIT_BORDER_SIDE if i == n_cols else None,
            top=_KIT_BORDER_SIDE, bottom=_KIT_BORDER_SIDE,
        )
        _c.fill = _KIT_DATE_FILL
    _ks = min(kit_idx) if kit_idx else 1
    _ke = max(kit_idx) if kit_idx else n_cols
    strip = ws.cell(row=1, column=_ks, value=_kit_sync_caption(sync_ts))
    strip.font = Font(bold=True)
    strip.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=1, start_column=_ks,
                   end_row=1, end_column=_ke)
    ws.row_dimensions[1].height = 22

    # --- Строка 2: шапка таблицы ---
    for i, name in enumerate(df.columns, start=1):
        c = ws.cell(row=2, column=i, value=str(name))
        c.font = Font(bold=True)
        c.border = _KIT_TABLE_BORDER
        if i in kit_idx:
            c.fill = _KIT_HEADER_FILL
            c.alignment = Alignment(wrap_text=True, vertical="center")

    # --- Данные с 3-й строки ---
    for r_offset, (_, row) in enumerate(df.iterrows()):
        r = 3 + r_offset
        for i, name in enumerate(df.columns, start=1):
            val = row[name]
            if pd.isna(val):
                val = ""
            elif not isinstance(val, (str, int, float, bool)):
                val = str(val)
            c = ws.cell(row=r, column=i, value=val)
            c.border = _KIT_TABLE_BORDER
            if i in kit_idx:
                c.alignment = Alignment(horizontal="center",
                                        vertical="center")

    # --- Автофильтр по всей таблице (шапка — строка 2) ---
    ws.auto_filter.ref = f"A2:{last_letter}{2 + len(df)}"

    # --- Ширины колонок по содержимому (шапка + до 200 строк) ---
    for i, name in enumerate(df.columns, start=1):
        lens = [len(str(name))]
        lens += [len(str(v)) for v in df[name].tolist()[:200]
                 if v is not None and not pd.isna(v)]
        ws.column_dimensions[get_column_letter(i)].width = \
            min(max(lens, default=8) + 2, 60)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _styled_upload_to_xlsx(
    uploaded_file,
    sheet_name: str,
    kit_col: str | None,
    statuses: dict | None,
    status_col_name: str,
    sync_ts=None,
) -> bytes:
    """Режим 1 _xlsx_add_kit_status: правка ИСХОДНОГО файла (21.x+22).

    Находит строку заголовка «Шифр РД», добавляет справа ТРИ
    kit-колонки (статус, ревизия, дата ревизии) и вставляет СРАЗУ
    НАД строкой шапки полосу «База актуальна на …» — шапка и данные
    сдвигаются на строку вниз, а строки ВЫШЕ шапки (титул бланка)
    остаются на своих местах: полоса стоит на строку выше шапок
    kit-колонок независимо от того, с какой строки листа начинается
    таблица (уточнение 19.11.2026).
    Форматирование
    исходного файла сохраняется: меняются только границы ячеек таблицы
    и добавленные колонки. Шапки kit-колонок — заливка D9E1F2
    (согласовано), тонкие границы #808080 у всех ячеек таблицы,
    kit-колонки по центру, автофильтр по диапазону шапка+данные
    (полоса в него не входит), без данных автофильтр не ставится.

    Фиксы 22.2/22.3 — чтобы Excel не «чинил» файл («Ошибка в части
    содержимого»):
    1. Таблицы Excel (ListObject) с целевого листа снимаются:
       ws.insert_rows() не сдвигает table.ref, старый диапазон
       пересекается с полосой даты → «Удалено: автофильтр …
       /xl/tables/table1.xml». Таблицы на других листах не трогаются.
    2. Merge-диапазоны исходника снимаются перед insert_rows() и
       возвращаются: ниже строки полосы — со сдвигом на строку вниз,
       выше — без сдвига: openpyxl НЕ сдвигает merges, старые
       пересеклись бы с полосой. (Любой merge исходника ограничен
       max_column, а kit-колонки — правее, так что в зону kit-колонок
       merges попасть не могут.)
    3. kit-значения пишутся в ФАКТИЧЕСКИЕ строки (словарь
       {строка: шифр}): пустые строки в середине данных не сдвигают
       значения вверх.
    4. last_row инициализируется строкой шапки: пустая таблица не
       уводит полосу/автофильтр в отрицательные диапазоны.
    """
    wb = openpyxl.load_workbook(uploaded_file, read_only=False,
                                keep_vba=False)
    ws = wb[sheet_name]

    # --- 0. Таблицы Excel (ListObject) в исходном файле (фикс 22.2) ---
    # ws.insert_rows() НЕ сдвигает table.ref, а добавленные справа
    # kit-колонки остаются вне таблицы: старый диапазон table1.xml
    # пересекается с merge-полосой даты и не совпадает
    # ни с шапкой, ни с числом колонок. Excel при открытии «чинит»
    # такой файл: «Удалено: автофильтр … /xl/tables/table1.xml».
    # Снимаем таблицы с листа — остаётся обычный диапазон с нашим
    # автофильтром; файл открывается без диалога восстановления.
    if ws.tables:
        for _tbl_name in list(ws.tables):
            del ws.tables[_tbl_name]

    # --- 1. Находим строку заголовка и колонку шифров ---
    header_row_idx = None
    kit_col_num = None
    for cell_row in ws.iter_rows(min_row=1, max_row=50,
                                 max_col=ws.max_column):
        for cell in cell_row:
            if cell.value is not None and \
                    str(cell.value).replace("\xa0", " ").strip():
                norm = _norm_header(str(cell.value))
                for want in _KIT_CODE_HEADERS:
                    if norm == want:
                        header_row_idx = cell.row
                        kit_col_num = cell.column
                        break
                if header_row_idx:
                    break
            if header_row_idx:
                break
        if header_row_idx:
            break

    if header_row_idx is None:
        # Не нашли — fallback к pandas + стилизованная сборка «с нуля».
        # Патч 22.18: значения — тем же резолвером, что и в основном
        # режиме (колонка шифров ищется, если не передана).
        df_fb = pd.read_excel(uploaded_file, sheet_name=sheet_name)
        fb_col = kit_col or _find_kit_code_column(df_fb)
        if fb_col is None:
            return _styled_df_to_xlsx(
                df_fb, sheet_name=(sheet_name or "Комплекты")[:31],
                sync_ts=sync_ts,
            )
        df_fb, _ = _apply_kit_statuses_resolved(df_fb, fb_col)
        return _styled_df_to_xlsx(
            df_fb,
            sheet_name=(sheet_name or "Комплекты")[:31],
            sync_ts=sync_ts,
        )

    # --- 2. Merge-диапазоны исходника (снимаем ДО вставки строки):
    # openpyxl не сдвигает merges при insert_rows, старые диапазоны
    # пересеклись бы с полосой даты. Снимаем все; после вставки
    # возвращаем: что ниже строки полосы — со сдвигом на строку вниз,
    # что выше (титул бланка) — без сдвига (см. шаг 5.1).
    src_merges = [str(_r) for _r in ws.merged_cells.ranges]
    for _m in src_merges:
        ws.unmerge_cells(_m)

    # --- 3. Последняя колонка и последняя строка данных ---
    # (init = строка шапки: пустая таблица → last_row не уезжает)
    max_col = ws.max_column
    last_row = header_row_idx
    for r in range(header_row_idx + 1, ws.max_row + 1):
        val = ws.cell(row=r, column=kit_col_num).value
        if val is not None and not pd.isna(val):
            last_row = r

    # --- 4. Три новые kit-колонки справа от таблицы ---
    col_status = max_col + 1
    col_rev = max_col + 2
    col_date = max_col + 3
    last_letter = get_column_letter(col_date)

    # --- 5. Полоса даты СРАЗУ НАД строкой шапки (а НЕ в строке 1
    # листа): строка полосы вставляется ПЕРЕД строкой шапки, шапка и
    # данные уезжают на строку вниз, титульные строки бланка выше
    # шапки не сдвигаются (уточнение 19.11.2026: дата — на строку
    # выше шапок kit-колонок, даже если таблица начинается не с
    # первой строки листа). Полоса — заливка/рамка во всю ширину
    # таблицы, а ТЕКСТ объединён над шапками трёх kit-колонок: дата
    # стоит прямо над «Статус/Ревизия/Дата ревизии», а не по центру
    # всей ширины бланка.
    strip_row = header_row_idx
    ws.insert_rows(strip_row)
    header_row_idx += 1
    last_row += 1

    for i in range(1, col_date + 1):
        _c = ws.cell(row=strip_row, column=i)
        _c.border = Border(
            left=_KIT_BORDER_SIDE if i == 1 else None,
            right=_KIT_BORDER_SIDE if i == col_date else None,
            top=_KIT_BORDER_SIDE, bottom=_KIT_BORDER_SIDE,
        )
        _c.fill = _KIT_DATE_FILL
    strip = ws.cell(row=strip_row, column=col_status,
                    value=_kit_sync_caption(sync_ts))
    strip.font = Font(bold=True)
    strip.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=strip_row, start_column=col_status,
                   end_row=strip_row, end_column=col_date)
    ws.row_dimensions[strip_row].height = 22

    # --- 5.1. Возвращаем merges исходника: ниже вставленной строки
    # полосы — со сдвигом на строку вниз (контент уехал вниз вместе с
    # вставленной строкой), выше полосы (титул бланка) — без сдвига.
    # Merge, охватывающий место вставки, расширяется на строку — так
    # же поступает сам Excel при вставке строки внутрь объединения.
    for _m in src_merges:
        _c1, _r1, _c2, _r2 = range_boundaries(_m)
        if _r1 >= strip_row:
            _r1 += 1
            _r2 += 1
        elif _r2 >= strip_row:
            _r2 += 1
        ws.merge_cells(start_row=_r1, start_column=_c1,
                       end_row=_r2, end_column=_c2)

    # --- 6. Шапки трёх kit-колонок (D9E1F2, согласовано) ---
    for col_num, name in ((col_status, status_col_name),
                          (col_rev, KIT_REVISION_COL),
                          (col_date, KIT_REV_DATE_COL)):
        c = ws.cell(row=header_row_idx, column=col_num, value=name)
        c.font = Font(bold=True)
        c.fill = _KIT_HEADER_FILL
        c.border = _KIT_TABLE_BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")
        # Ширина — 16 символов (статус/ревизия/дата помещаются)
        ws.column_dimensions[get_column_letter(col_num)].width = 16

    # --- 7. Батч-вычисление kit-значений: словарь
    # {фактическая строка: шифр} — пустые строки в середине данных
    # не сдвигают kit-значения вверх. Патч 22.18: ЕДИНЫЙ резолвер
    # _resolve_kit_codes (хелпер сопоставления 22.15): собственной
    # проверки на равенство здесь нет ---
    row_codes = {}
    for r in range(header_row_idx + 1, last_row + 1):
        val = ws.cell(row=r, column=kit_col_num).value
        if val is not None and not pd.isna(val):
            row_codes[r] = val

    norm_keys: list = []
    seen_keys: set = set()
    for code in row_codes.values():
        key = normalize_kit_code(code)
        if key and key not in seen_keys:
            seen_keys.add(key)
            norm_keys.append(key)
    resolved = _resolve_kit_codes(tuple(norm_keys))

    # --- 8. Значения трёх kit-колонок в фактические строки ---
    for r, code in row_codes.items():
        norm = normalize_kit_code(code)
        info = resolved.get(norm) or _empty_kit_metrics()
        for col_num, value in (
            (col_status, info["status"]),
            (col_rev, info["revision"]),
            (col_date, info["date"]),
        ):
            c = ws.cell(row=r, column=col_num, value=value)
            c.alignment = Alignment(horizontal="center",
                                    vertical="center")

    # --- 9. Тонкие границы #808080 у всех ячеек таблицы ---
    for r in range(header_row_idx, last_row + 1):
        for i in range(1, col_date + 1):
            ws.cell(row=r, column=i).border = _KIT_TABLE_BORDER

    # --- 10. Автофильтр по шапке+данным (полоса даты в него не
    # входит); без данных автофильтр не ставится вовсе ---
    if last_row > header_row_idx:
        ws.auto_filter.ref = f"A{header_row_idx}:{last_letter}{last_row}"
    else:
        ws.auto_filter.ref = None

    # --- 11. Сохраняем в буфер ---
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _int_to_column_letter(col_num: int) -> str:
    """Преобразовать номер колонки Excel в буквенное обозначение."""
    letter = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        letter = chr(65 + remainder) + letter
    return letter


def col_letter_to_int(col_letter: str) -> int:
    """Преобразовать буквенную обозначение колонки Excel в номер."""
    result = 0
    for ch in col_letter.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📄 Экспорт")
    st.caption(
        "Выгрузка данных в Excel и PDF: сводки, замечания, "
        "аналитика, статусы комплектов."
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Сводные отчёты",
        "📋 Замечания",
        "📈 Аналитика",
        "🔄 Обновить статусы",
    ])

    with tab1:
        _render_summaries()
    with tab2:
        _render_comments_export()
    with tab3:
        _render_analytics()
    with tab4:
        _render_kit_status_update()