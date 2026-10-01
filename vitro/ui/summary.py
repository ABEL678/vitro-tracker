# vitro/ui/summary.py
"""
📊 Сводка по проекту — главная витрина для Заказчика.

Структура:
  1. Объём замечаний — общая картина по всей базе.
  2. Листы РД — статусы A/B/C/И + промежуточные.
  3. Замечания на стороне АТП ТЛП — что ждёт нашего ответа.
  4. Замечания на стороне Заказчика — что ждёт его решения.
     Включает особые категории: К листам A, К аннулированным листам.
  5. Категории замечаний, оценка АТП ТЛП — ручная разметка.
  + Разрез по дисциплинам / разделам / комплектам.

Источник: `_load_all_categorized` из deadlines.py (единый).
Фильтры общие, каскадные: дисциплина → раздел → комплект.
Опционально: только стилобат (`-С-` в шифре комплекта).
"""

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

OURS_ACTIVE_FLAGS = [
    "new_overdue", "new_in_progress",
    "in_work_overdue", "in_work_in_progress",
    "rejected_overdue", "rejected_in_progress",
    "discussion_overdue", "discussion_in_progress",
]
OURS_OVERDUE_FLAGS = [
    "new_overdue", "in_work_overdue",
    "rejected_overdue", "discussion_overdue",
]
WAITING_FLAGS = [
    "waiting_customer_ontime",
    "waiting_customer",
    "waiting_customer_overdue",
]
CHRONIC_FLAG = "waiting_customer_chronic"
ABANDONED_FLAG = "abandoned"

DS_A = "A"
DS_ANNULLED = "АННУЛИРОВАНО"

STATUS_NEW = "Новое"
STATUS_IN_WORK = "Принято в работу"
STATUS_REJECTED = "Не принято"
STATUS_DISCUSSION = "К обсуждению"
STATUS_DONE = "Выполнено"


# ---------------------------------------------------------------------------
#  Источник активных замечаний (единый)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_categorized() -> pd.DataFrame:
    from vitro.ui.deadlines import _load_all_categorized as _src
    return _src()


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
def _load_section_options(disciplines: tuple = ()) -> dict[str, str]:
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
def _load_kit_options(disciplines: tuple = (),
                      sections: tuple = ()) -> dict[str, str]:
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

    return {r["code"]: f"{r['code']} — {r['name']}"
                       if r["name"] else r["code"]
            for r in rows}


# ---------------------------------------------------------------------------
#  Фильтрация
# ---------------------------------------------------------------------------
def _filter_active(df: pd.DataFrame,
                    disciplines: tuple = (),
                    sections: tuple = (),
                    kits: tuple = (),
                    stilobat_only: bool = False) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        out = out[out["complex"].isin(kits)]
    if stilobat_only:
        out = out[
            out["complex"].astype(str)
            .str.contains("-С-", case=False, na=False, regex=False)
        ]
    return out


def _filter_documents(df: pd.DataFrame,
                       disciplines: tuple = (),
                       sections: tuple = (),
                       kits: tuple = (),
                       stilobat_only: bool = False) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        out = out[out["complex"].isin(kits)]
    if stilobat_only:
        out = out[
            out["complex"].astype(str)
            .str.contains("-С-", case=False, na=False, regex=False)
        ]
    return out


# ---------------------------------------------------------------------------
#  Документы
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_documents() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT id, leaf, discipline, section, complex,
                   status, revision, name, status_date, sheet_number
            FROM documents
        """, conn)


# ---------------------------------------------------------------------------
#  Общий объём
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_scale(disciplines: tuple = (),
                 sections: tuple = (),
                 kits: tuple = (),
                 stilobat_only: bool = False) -> dict:
    q = """
        SELECT
            COUNT(*) AS total_all,
            SUM(CASE WHEN c.status = 'Закрыто' THEN 1 ELSE 0 END)
                AS closed,
            SUM(CASE WHEN c.status = 'Аннулировано' THEN 1 ELSE 0 END)
                AS annulled,
            SUM(CASE WHEN c.status IS NULL OR c.status = ''
                     THEN 1 ELSE 0 END) AS no_status
        FROM comments c
        JOIN documents d ON c.doc_id = d.id
        WHERE 1=1
    """
    params: list = []
    if disciplines:
        q += f" AND d.discipline IN ({','.join('?' * len(disciplines))})"
        params += list(disciplines)
    if sections:
        q += f" AND d.section IN ({','.join('?' * len(sections))})"
        params += list(sections)
    if kits:
        q += f" AND d.complex IN ({','.join('?' * len(kits))})"
        params += list(kits)
    if stilobat_only:
        q += " AND (d.complex LIKE '%-С-%' OR d.complex LIKE '%-с-%')"

    with get_conn() as conn:
        row = conn.execute(q, tuple(params)).fetchone()

    return {
        "total_all": _safe_int(row["total_all"]),
        "closed": _safe_int(row["closed"]),
        "annulled": _safe_int(row["annulled"]),
        "no_status": _safe_int(row["no_status"]),
    }


# ---------------------------------------------------------------------------
#  Утилиты
# ---------------------------------------------------------------------------
def _safe_int(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _fmt_num(n) -> str:
    return f"{_safe_int(n):,}".replace(",", " ")


def _metric(col, label, value, help_text=None):
    col.metric(
        label,
        value if isinstance(value, str) else _fmt_num(value),
        help=help_text,
    )


# ---------------------------------------------------------------------------
#  СЕКЦИЯ 1. Объём замечаний
# ---------------------------------------------------------------------------
def _render_scale_section(scale: dict, active_count: int) -> None:
    st.markdown("##### Объём замечаний")

    c1, c2, c3, c4, c5, c6 = st.columns(6)

    _metric(
        c1, "Всего замечаний", scale["total_all"],
        "Выгрузка из Витро: COUNT(comments).\n\n"
        "Включает: закрытые, аннулированные, без статуса, активные.",
    )
    _metric(
        c2, "Активных", active_count,
        "Выгрузка из Витро: замечания в 5 статусах — Новое, "
        "Принято в работу, Не принято, К обсуждению, Выполнено.\n\n"
        "Формула: Всего − Закрыто − Аннулировано − Без статуса.",
    )
    _metric(
        c3, "Закрыто", scale["closed"],
        "Выгрузка из Витро: COUNT(comments WHERE status='Закрыто').\n\n"
        "Входят в «Всего замечаний».",
    )
    _metric(
        c4, "Аннулировано", scale["annulled"],
        "Выгрузка из Витро: "
        "COUNT(comments WHERE status='Аннулировано').\n\n"
        "Входят в «Всего замечаний».",
    )
    _metric(
        c5, "Без статуса", scale["no_status"],
        "Выгрузка из Витро: COUNT(comments WHERE status IS NULL "
        "OR status='').\n\n"
        "Входят в «Всего замечаний».",
    )
    pct_done = (
        round((scale["closed"] + scale["annulled"])
              / scale["total_all"] * 100, 1)
        if scale["total_all"] else 0.0
    )
    _metric(
        c6, "% снято от всего", f"{pct_done}%",
        "Расчёт: (Закрыто + Аннулировано) / Всего × 100%.\n\n"
        f"Арифметика: ({_fmt_num(scale['closed'])} + "
        f"{_fmt_num(scale['annulled'])}) / "
        f"{_fmt_num(scale['total_all'])} = {pct_done}%.",
    )


# ---------------------------------------------------------------------------
#  СЕКЦИЯ 2. Листы РД
# ---------------------------------------------------------------------------
def _render_sheets_section(docs_df: pd.DataFrame) -> None:
    st.markdown("##### Листы РД")

    if docs_df.empty:
        st.info("Нет данных по листам.")
        return

    statuses = docs_df["status"].astype(str).str.strip().str.upper()

    total = len(docs_df)
    n_a = int((statuses == "A").sum())
    n_b = int((statuses == "B").sum())
    n_c = int((statuses == "C").sum())
    n_i = int((statuses == "И").sum())
    n_annulled = int((statuses == "АННУЛИРОВАНО").sum())
    n_review = int(statuses.isin(
        ["ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"]).sum())
    n_loading = int(statuses.isin(
        ["НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"]).sum())

    # % готовых = (A + B) / (Всего − И − Аннулировано)
    denominator = total - n_i - n_annulled
    pct_ab = (round((n_a + n_b) / denominator * 100, 1)
              if denominator > 0 else 0.0)

    c1, c2, c3, c4 = st.columns(4)
    _metric(c1, "Листов всего", total,
            "Выгрузка из Витро: COUNT(documents).\n\n"
            "Все листы РД в проекте.")
    _metric(c2, "Статус A — утверждён", n_a,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='A').\n\n"
            "Лист утверждён заказчиком.\n\n"
            "Входит в «Листов всего».")
    _metric(c3, "Статус B — к сдаче", n_b,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='B').\n\n"
            "Лист готов к сдаче, но не утверждён.\n\n"
            "Входит в «Листов всего».")
    _metric(c4, "Статус C — в работе", n_c,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='C').\n\n"
            "Лист в работе, есть активные замечания.\n\n"
            "Входит в «Листов всего».")

    c5, c6, c7, c8 = st.columns(4)
    _metric(c5, "Статус И — информационный", n_i,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='И').\n\n"
            "Информационный лист. Утверждение не требуется.\n\n"
            "Входит в «Листов всего».")
    _metric(c6, "На согласовании Заказчика", n_review,
            "Расчёт: «Для согласования» + «На рассмотрении».\n\n"
            "Заказчик рассматривает.")
    _metric(c7, "Готовится к загрузке", n_loading,
            "Расчёт: «На корректировке» + «Размещено».")
    _metric(c8, "Аннулировано", n_annulled,
            "Выгрузка из Витро: "
            "COUNT(documents WHERE status='Аннулировано').\n\n"
            "Листы, аннулированные заказчиком.\n\n"
            "Входит в «Листов всего».")

    c9, _, _, _ = st.columns(4)
    _metric(c9, "% готовых (A+B)", f"{pct_ab}%",
            "Расчёт: (A + B) / (Всего − И − Аннулировано) × 100%.\n\n"
            f"Арифметика: ({n_a} + {n_b}) / "
            f"({total} − {n_i} − {n_annulled}) = {pct_ab}%.\n\n"
            "Из знаменателя исключены информационные "
            "и аннулированные листы — они не требуют утверждения.")


# ---------------------------------------------------------------------------
#  СЕКЦИЯ 3. Замечания на стороне АТП ТЛП
# ---------------------------------------------------------------------------
def _render_ours_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Замечания на стороне АТП ТЛП")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()
    mask_special = ds.isin([DS_A, DS_ANNULLED])
    clean = active_df[~mask_special]
    statuses = clean["status"]

    n_new = int((statuses == STATUS_NEW).sum())
    n_in_work = int((statuses == STATUS_IN_WORK).sum())
    n_rejected = int((statuses == STATUS_REJECTED).sum())
    n_disc = int((statuses == STATUS_DISCUSSION).sum())

    n_ours_total = n_new + n_in_work + n_rejected + n_disc

    # Просрочено = overdue + abandoned (по флагам)
    flags = clean["category_flag"]
    mask_overdue = flags.isin(OURS_OVERDUE_FLAGS)
    mask_abandoned = flags == ABANDONED_FLAG
    n_overdue = int((mask_overdue | mask_abandoned).sum())

    c1, c2, c3, c4 = st.columns(4)
    _metric(
        c1, "Всего у АТП ТЛП", n_ours_total,
        "Расчёт: Новое + Принято в работу + Не принято + "
        "К обсуждению.\n\n"
        f"Арифметика: {_fmt_num(n_new)} + {_fmt_num(n_in_work)} + "
        f"{_fmt_num(n_rejected)} + {_fmt_num(n_disc)} = "
        f"{_fmt_num(n_ours_total)}.\n\n"
        "Не включает замечания к листам A и к аннулированным — "
        "они в блоке «Замечания на стороне Заказчика».",
    )
    _metric(
        c2, "Новое", n_new,
        "Выгрузка из Витро: COUNT(comments WHERE status='Новое').\n\n"
        "Из них исключены замечания к листам A и аннулированным.\n\n"
        "Входит в «Всего у АТП ТЛП».",
    )
    _metric(
        c3, "Принято в работу", n_in_work,
        "Выгрузка из Витро: "
        "COUNT(comments WHERE status='Принято в работу').\n\n"
        "Из них исключены замечания к листам A и аннулированным.\n\n"
        "Входит в «Всего у АТП ТЛП».",
    )
    _metric(
        c4, "Не принято", n_rejected,
        "Выгрузка из Витро: "
        "COUNT(comments WHERE status='Не принято').\n\n"
        "Из них исключены замечания к листам A и аннулированным.\n\n"
        "Входит в «Всего у АТП ТЛП».",
    )

    c5, c6, _, _ = st.columns(4)
    _metric(
        c5, "К обсуждению", n_disc,
        "Выгрузка из Витро: "
        "COUNT(comments WHERE status='К обсуждению').\n\n"
        "Из них исключены замечания к листам A и аннулированным.\n\n"
        "Входит в «Всего у АТП ТЛП».",
    )
    _metric(
        c6, "из них Просрочено (>10 р.д.)", n_overdue,
        "Расчёт: замечания из состава «Всего у АТП ТЛП», "
        "у которых срок ответа (10 рабочих дней) истёк.\n\n"
        "Входит в «Всего у АТП ТЛП».",
    )


# ---------------------------------------------------------------------------
#  СЕКЦИЯ 4. Замечания на стороне Заказчика
# ---------------------------------------------------------------------------
def _render_customer_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Замечания на стороне Заказчика")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()

    mask_a = ds == DS_A
    mask_annul = ds == DS_ANNULLED
    mask_special = mask_a | mask_annul

    n_a_total = int(mask_a.sum())
    n_annul_total = int(mask_annul.sum())

    # «Выполнено» без A и аннул.
    mask_done = active_df["status"] == STATUS_DONE
    mask_done_clean = mask_done & ~mask_special
    n_done_clean = int(mask_done_clean.sum())

    done = active_df[mask_done_clean]
    done_flags = done["category_flag"]

    n_ontime = int((done_flags == "waiting_customer_ontime").sum())
    n_waiting = int((done_flags == "waiting_customer").sum())
    n_overdue = int((done_flags == "waiting_customer_overdue").sum())
    n_chronic = int((done_flags == CHRONIC_FLAG).sum())

    n_overdue_review = n_waiting + n_overdue + n_chronic

    n_total = n_done_clean + n_a_total + n_annul_total

    c1, c2, c3, c4 = st.columns(4)
    _metric(
        c1, "Всего у заказчика", n_total,
        "Расчёт: со статусом Выполнено (без особых) "
        "+ К листам A + К аннулированным листам.\n\n"
        f"Арифметика: {_fmt_num(n_done_clean)} + "
        f"{_fmt_num(n_a_total)} + {_fmt_num(n_annul_total)} = "
        f"{_fmt_num(n_total)}.",
    )
    _metric(
        c2, "со статусом Выполнено", n_done_clean,
        "Выгрузка из Витро: "
        "COUNT(comments WHERE status='Выполнено').\n\n"
        "Из них исключены замечания к листам A и аннулированным.",
    )
    _metric(
        c3, "из них срок рассмотрения не превышен",
        n_ontime,
        "Расчёт по датам: заказчик ещё в пределах 10 рабочих дней "
        "с момента нашего ответа.\n\n"
        "Входит в «со статусом Выполнено».",
    )
    _metric(
        c4, "из них Просрочено рассмотрение (>10 р.д.)",
        n_overdue_review,
        "Расчёт: заказчик не рассмотрел наш ответ более "
        "10 рабочих дней.\n\n"
        "Входит в «со статусом Выполнено».",
    )

    c5, c6, _, _ = st.columns(4)
    _metric(
        c5, "К листам A (утверждён)", n_a_total,
        "Расчёт: все замечания к листам со статусом A (утверждён).\n\n"
        "Учтены здесь, независимо от статуса самого замечания:\n"
        "• со статусом «Выполнено» — перенесены из «со статусом "
        "Выполнено»;\n"
        "• со статусом «Новое» / «Принято в работу» / "
        "«Не принято» / «К обсуждению» — исключены из "
        "«Всего у АТП ТЛП».\n\n"
        "Ожидаем снятия от заказчика — без условий.",
    )
    _metric(
        c6, "К аннулированным листам", n_annul_total,
        "Расчёт: все замечания к аннулированным листам.\n\n"
        "Учтены здесь, независимо от статуса самого замечания:\n"
        "• со статусом «Выполнено» — перенесены из «со статусом "
        "Выполнено»;\n"
        "• со статусом «Новое» / «Принято в работу» / "
        "«Не принято» / «К обсуждению» — исключены из "
        "«Всего у АТП ТЛП».\n\n"
        "Ожидаем снятия от заказчика — без условий.",
    )


# ---------------------------------------------------------------------------
#  СЕКЦИЯ 5. Категории замечаний, оценка АТП ТЛП
# ---------------------------------------------------------------------------
def _render_categories_section(active_df: pd.DataFrame) -> None:
    st.markdown("##### Категории замечаний, оценка АТП ТЛП")

    if active_df.empty:
        st.info("Нет данных.")
        return

    ds = active_df["doc_status"].astype(str).str.strip().str.upper()
    mask_special = ds.isin([DS_A, DS_ANNULLED])

    ours_statuses = [STATUS_NEW, STATUS_IN_WORK,
                     STATUS_REJECTED, STATUS_DISCUSSION]
    mask_ours = (
        active_df["status"].isin(ours_statuses) & ~mask_special
    )
    ours = active_df[mask_ours]

    counts = (
        ours["category"].fillna("Без категории")
        .replace("", "Без категории")
        .value_counts()
    )
    total = int(counts.sum())

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    _metric(
        c1, "Всего", total,
        "Расчёт: Новое + Принято в работу + Не принято + "
        "К обсуждению.\n\n"
        "Совпадает с «Всего у АТП ТЛП» в блоке выше.\n\n"
        "Категории ставятся специалистами АТП ТЛП для замечаний, "
        "ожидающих нашего ответа. На замечания у заказчика "
        "категории не ставятся — если замечание вернётся со "
        "статусом «Не принято», тогда и проставим.",
    )
    _metric(
        c2, "Принято/корректное", int(counts.get(CAT_1, 0)),
        "Замечание корректное, влияет на СМР. Принимаем.\n\n"
        "Входит в «Всего».",
    )
    _metric(
        c3, "Формальное", int(counts.get(CAT_2, 0)),
        "Формальное, не влияет на СМР.\n\n"
        "Входит в «Всего».",
    )
    _metric(
        c4, "Доп.требование", int(counts.get(CAT_3, 0)),
        "Дополнительное требование, отсутствует в ТЗ.\n\n"
        "Входит в «Всего».",
    )
    _metric(
        c5, "Не принято/нарушение", int(counts.get(CAT_4, 0)),
        "Не принимаем: нарушение ТНПА.\n\n"
        "Входит в «Всего».",
    )
    _metric(
        c6, "Без категории", int(counts.get("Без категории", 0)),
        "Ещё не разобрано специалистами.\n\n"
        "Входит в «Всего».",
    )


# ---------------------------------------------------------------------------
#  Разрез: по дисциплинам / разделам / комплектам
# ---------------------------------------------------------------------------
def _build_summary_rows(group_col: str,
                         active_df: pd.DataFrame,
                         docs_df: pd.DataFrame) -> pd.DataFrame:
    if docs_df.empty and active_df.empty:
        return pd.DataFrame()

    # --- Листы ---
    if not docs_df.empty:
        doc_rows = []
        for grp, sub in docs_df.groupby(group_col, dropna=True):
            if not grp:
                continue
            total = len(sub)
            statuses = sub["status"].astype(str).str.strip().str.upper()
            a = int((statuses == "A").sum())
            b = int((statuses == "B").sum())
            c = int((statuses == "C").sum())
            i = int((statuses == "И").sum())
            annulled = int((statuses == "АННУЛИРОВАНО").sum())
            denom = total - i - annulled
            pct_ab = (round((a + b) / denom * 100, 1)
                      if denom > 0 else 0.0)
            doc_rows.append({
                group_col: grp,
                "docs_total": total,
                "doc_a": a, "doc_b": b, "doc_c": c, "doc_i": i,
                "doc_annulled": annulled,
                "doc_other": total - a - b - c - i - annulled,
                "pct_ab": pct_ab,
            })
        docs_grp = pd.DataFrame(doc_rows)
    else:
        docs_grp = pd.DataFrame()

    # --- Замечания ---
    if not active_df.empty:
        df = active_df.copy()
        df["Категория"] = (
            df["category"].fillna("Без категории")
            .replace("", "Без категории")
        )

        rows = []
        for grp, sub in df.groupby(group_col, dropna=True):
            if not grp:
                continue

            sdss = sub["doc_status"].astype(str).str.strip().str.upper()
            smask_special = sdss.isin([DS_A, DS_ANNULLED])
            sflags = sub["category_flag"]
            sstatus = sub["status"]
            cat_counts = sub["Категория"].value_counts()

            rows.append({
                group_col: grp,
                "n_new": int(((sstatus == STATUS_NEW)
                              & ~smask_special).sum()),
                "n_in_work": int(((sstatus == STATUS_IN_WORK)
                                  & ~smask_special).sum()),
                "n_rejected": int(((sstatus == STATUS_REJECTED)
                                   & ~smask_special).sum()),
                "n_discussion": int(((sstatus == STATUS_DISCUSSION)
                                     & ~smask_special).sum()),
                "ours_overdue": int((
                    (sflags.isin(OURS_OVERDUE_FLAGS)
                     | (sflags == ABANDONED_FLAG))
                    & ~smask_special
                ).sum()),
                "waiting": int((
                    sflags.isin(WAITING_FLAGS) & ~smask_special
                ).sum()),
                "chronic": int((
                    (sflags == CHRONIC_FLAG) & ~smask_special
                ).sum()),
                "cat_1": int(cat_counts.get(CAT_1, 0)),
                "cat_2": int(cat_counts.get(CAT_2, 0)),
                "cat_3": int(cat_counts.get(CAT_3, 0)),
                "cat_4": int(cat_counts.get(CAT_4, 0)),
                "cat_none": int(cat_counts.get("Без категории", 0)),
            })
        active_grp = pd.DataFrame(rows)
    else:
        active_grp = pd.DataFrame()

    if docs_grp.empty and active_grp.empty:
        return pd.DataFrame()
    if docs_grp.empty:
        result = active_grp
    elif active_grp.empty:
        result = docs_grp
    else:
        result = docs_grp.merge(active_grp, on=group_col, how="outer")

    result = result.fillna(0)

    group_col_names = {
        "discipline": "Код",
        "section":    "Раздел",
        "complex":    "Комплект",
    }
    result = result.rename(columns={group_col: group_col_names[group_col]})
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_discipline(
    disciplines: tuple = (), sections: tuple = (),
    kits: tuple = (), stilobat_only: bool = False,
) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             stilobat_only)
    docs = _load_documents()
    docs = _filter_documents(docs, disciplines, sections, kits,
                              stilobat_only)
    return _build_summary_rows("discipline", active, docs)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_section(
    disciplines: tuple = (), sections: tuple = (),
    kits: tuple = (), stilobat_only: bool = False,
) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             stilobat_only)
    docs = _load_documents()
    docs = _filter_documents(docs, disciplines, sections, kits,
                              stilobat_only)
    return _build_summary_rows("section", active, docs)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_summary_by_complex(
    disciplines: tuple = (), sections: tuple = (),
    kits: tuple = (), stilobat_only: bool = False,
) -> pd.DataFrame:
    active = _load_all_categorized()
    active = _filter_active(active, disciplines, sections, kits,
                             stilobat_only)
    docs = _load_documents()
    docs = _filter_documents(docs, disciplines, sections, kits,
                              stilobat_only)
    return _build_summary_rows("complex", active, docs)


def _render_summary_table(df: pd.DataFrame,
                            group_label: str,
                            level: str) -> None:
    if df.empty:
        st.info("Нет данных по заданным фильтрам.")
        return

    view = df.copy()

    if level == "discipline" and "Код" in view.columns:
        view.insert(
            1, "Наименование",
            view["Код"].map(
                lambda c: discipline_name(c) if c else ""
            ),
        )

    rename_map = {
        "n_new":          "Новое",
        "n_in_work":      "В работе",
        "n_rejected":     "Не принято",
        "n_discussion":   "К обсуждению",
        "ours_overdue":   "Просрочено (>10 р.д.)",
        "waiting":        "Ждут заказчика",
        "chronic":        "Заброшено (>90 р.д.)",
        "cat_1":          "Принято",
        "cat_2":          "Формальное",
        "cat_3":          "Доп.требование",
        "cat_4":          "Не принято (кат.)",
        "cat_none":       "Без категории",
        "docs_total":     "Листов",
        "doc_a":          "A", "doc_b": "B", "doc_c": "C",
        "doc_i":          "И",
        "doc_annulled":   "Аннул.",
        "doc_other":      "Прочие",
        "pct_ab":         "% A+B",
    }
    view = view.rename(columns=rename_map)

    base_order = [group_label]
    if level == "discipline":
        base_order.append("Наименование")

    cols_order = base_order + [
        "Новое", "В работе", "Не принято", "К обсуждению",
        "Просрочено (>10 р.д.)",
        "Ждут заказчика", "Заброшено (>90 р.д.)",
        "Принято", "Формальное", "Доп.требование",
        "Не принято (кат.)", "Без категории",
        "Листов", "A", "B", "C", "И", "Аннул.", "Прочие",
        "% A+B",
    ]
    cols_order = [c for c in cols_order if c in view.columns]
    view = view[cols_order]

    sort_col = "Новое"
    if sort_col in view.columns:
        view = view.sort_values(sort_col, ascending=False)

    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        height=520,
        column_config={
            "% A+B": st.column_config.ProgressColumn(
                "% A+B", min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    totals = {group_label: "ИТОГО"}
    if level == "discipline":
        totals["Наименование"] = ""

    numeric_cols = [
        c for c in view.columns
        if c not in (group_label, "Наименование", "% A+B")
    ]
    for c in numeric_cols:
        totals[c] = int(view[c].sum())

    if "Листов" in totals and totals["Листов"] > 0:
        a = totals.get("A", 0)
        b = totals.get("B", 0)
        i = totals.get("И", 0)
        annulled = totals.get("Аннул.", 0)
        denom = totals["Листов"] - i - annulled
        totals["% A+B"] = (
            round((a + b) / denom * 100, 1)
            if denom > 0 else 0.0
        )

    ordered_keys = [group_label]
    if level == "discipline":
        ordered_keys.append("Наименование")
    ordered_keys += [c for c in view.columns if c not in ordered_keys]
    totals = {k: totals.get(k, 0) for k in ordered_keys}

    st.markdown("**Итоги:**")
    st.dataframe(
        pd.DataFrame([totals]),
        use_container_width=True,
        hide_index=True,
    )


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("Сводка по проекту")
    st.caption(
        "Ключевые показатели проекта, распределение замечаний и листов, "
        "разрез по дисциплинам, разделам и комплектам. "
        "Все цифры — из единого источника, синхронизированы с "
        "остальными вкладками."
    )

    disc_options = _load_discipline_options()

    with st.expander("Фильтры", expanded=True):
        c1, c2, c3 = st.columns(3)

        with c1:
            sel_disc_labels = st.multiselect(
                "Дисциплина",
                options=list(disc_options.values()),
                placeholder="Все дисциплины",
                key="sum_disc",
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
                    key="sum_section",
                )
                sel_section = [
                    code for code, label in section_options.items()
                    if label in sel_section_labels
                ]
            else:
                sel_section = []
                st.multiselect(
                    "Раздел",
                    options=[],
                    placeholder="Разделы не применимы",
                    disabled=True,
                    key="sum_section_empty",
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
                key="sum_kit",
            )
            sel_kit = [
                code for code, label in kit_options.items()
                if label in sel_kit_labels
            ]

        stilobat_only = st.checkbox(
            "Только стилобат (признак «-С-» в шифре)",
            value=False,
            key="sum_stilobat",
            help="Показать только комплекты, в шифре которых есть "
                 "сегмент «-С-».",
        )

    disc_t = tuple(sel_disc)
    sect_t = tuple(sel_section)
    kit_t = tuple(sel_kit)

    with st.spinner("Загрузка данных..."):
        active_all = _load_all_categorized()
        active_filtered = _filter_active(
            active_all, disc_t, sect_t, kit_t, stilobat_only)
        docs_all = _load_documents()
        docs_filtered = _filter_documents(
            docs_all, disc_t, sect_t, kit_t, stilobat_only)
        scale = _load_scale(disc_t, sect_t, kit_t, stilobat_only)

    # «Активных» = выгрузка из Витро (5 статусов)
    active_count = len(active_filtered)

    _render_scale_section(scale, active_count)
    st.divider()

    _render_sheets_section(docs_filtered)
    st.divider()

    _render_ours_section(active_filtered)
    st.divider()

    _render_customer_section(active_filtered)
    st.divider()

    _render_categories_section(active_filtered)
    st.divider()

    st.markdown("##### Разрез по дисциплинам, разделам, комплектам")

    tab_disc, tab_sect, tab_cx = st.tabs([
        "По дисциплинам",
        "По разделам",
        "По комплектам",
    ])

    with tab_disc:
        df = _load_summary_by_discipline(
            disc_t, sect_t, kit_t, stilobat_only)
        _render_summary_table(df, "Код", "discipline")

    with tab_sect:
        df = _load_summary_by_section(
            disc_t, sect_t, kit_t, stilobat_only)
        _render_summary_table(df, "Раздел", "section")

    with tab_cx:
        df = _load_summary_by_complex(
            disc_t, sect_t, kit_t, stilobat_only)
        _render_summary_table(df, "Комплект", "complex")