# vitro/ui/authors.py
"""
👤 Авторы — аналитика по инженерам.

Вся аналитика синхронизирована с дашбордом через единый источник —
`_load_all_categorized` из deadlines.py.

2 под-вкладки:
  📊 Аналитика авторов — KPI, топ-авторы, качество, сроки, drill-down.
  🔵 Ждут заказчика    — редактор + выгрузка «Выполнено».
"""

import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn, update_category_safe
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly, safe_filename


# ---- Категории замечаний ----
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

CAT_OPTIONS_DISPLAY = [
    f"{CAT_PREFIX[c]} {c}" for c in [CAT_1, CAT_2, CAT_3, CAT_4]
]

CAT_COLORS = {
    "Принято":       "#C6EFCE",
    "Формальное":    "#FFEB9C",
    "Доп.треб.":     "#BDD7EE",
    "Не принято":    "#FFC7CE",
    "Без категории": "#D9D9D9",
}

SHORT_TO_LONG = {
    "Принято": CAT_1,
    "Формальное": CAT_2,
    "Доп.треб.": CAT_3,
    "Не принято": CAT_4,
}


# ---------------------------------------------------------------------------
#  Вспомогательные
# ---------------------------------------------------------------------------
def _strip_prefix(display_value: str) -> str:
    if not display_value:
        return ""
    for prefix in CAT_PREFIX.values():
        if display_value.startswith(prefix):
            return display_value[len(prefix):].strip()
    return display_value.strip()


def _add_prefix(raw_value):
    if not raw_value or pd.isna(raw_value):
        return None
    for cat, prefix in CAT_PREFIX.items():
        if raw_value == cat:
            return f"{prefix} {cat}"
    return raw_value


# ---------------------------------------------------------------------------
#  Единый источник активных
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_active_df() -> pd.DataFrame:
    """Активные замечания из _load_all_categorized (без abandoned)."""
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
def _load_section_options(disciplines: tuple = ()) -> dict[str, str]:
    with get_conn() as conn:
        if disciplines:
            ph = ",".join("?" * len(disciplines))
            rows = conn.execute(f"""
                SELECT DISTINCT section FROM documents
                WHERE section IS NOT NULL AND section <> ''
                  AND discipline IN ({ph}) ORDER BY section
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
            where.append(f"c.discipline IN ({','.join('?' * len(disciplines))})")
            params += list(disciplines)
        if sections:
            ph = ",".join("?" * len(sections))
            where.append(f"""c.code IN (
                SELECT DISTINCT complex FROM documents
                WHERE section IN ({ph}) AND complex IS NOT NULL)""")
            params += list(sections)
        rows = conn.execute(f"""
            SELECT c.code, c.name FROM complexes c
            WHERE {' AND '.join(where)} ORDER BY c.code
        """, tuple(params)).fetchall()
    return {r["code"]: f"{r['code']} — {r['name']}" if r["name"] else r["code"]
            for r in rows}


# ---------------------------------------------------------------------------
#  Фильтрация активных по иерархии
# ---------------------------------------------------------------------------
def _filter_active(df: pd.DataFrame,
                    disciplines: tuple = (),
                    sections: tuple = (),
                    kits: tuple = ()) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if disciplines:
        out = out[out["discipline"].isin(disciplines)]
    if sections:
        out = out[out["section"].isin(sections)]
    if kits:
        out = out[out["complex"].isin(kits)]
    return out


# ---------------------------------------------------------------------------
#  KPI (активные, синхронизировано с дашбордом)
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame):
    if df.empty:
        st.info("Нет данных по авторам с учётом фильтров.")
        return

    total = len(df)
    n_authors = df["author"].nunique()

    cat_counts = (
        df["category"].fillna("Без категории").replace("", "Без категории")
        .value_counts()
    )

    def pct(n):
        return round(n / total * 100, 1) if total else 0.0

    st.markdown("##### 📦 Общий объём")
    st.caption(
        "Считается **только по активным** замечаниям (5 статусов). "
        "Синхронизировано с дашбордом."
    )
    c1, c2 = st.columns(2)
    c1.metric("Авторов", n_authors)
    c2.metric("Активных замечаний", f"{total:,}".replace(",", " "))

    st.markdown("##### 🏷 Категории замечаний")
    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(
        "🟢 Принято/корректное",
        f"{int(cat_counts.get(CAT_1, 0)):,}".replace(",", " "),
        delta=f"{pct(cat_counts.get(CAT_1, 0))}%", delta_color="off",
    )
    c2.metric(
        "🟡 Формальное",
        f"{int(cat_counts.get(CAT_2, 0)):,}".replace(",", " "),
        delta=f"{pct(cat_counts.get(CAT_2, 0))}%", delta_color="off",
    )
    c3.metric(
        "🔵 Доп.требование",
        f"{int(cat_counts.get(CAT_3, 0)):,}".replace(",", " "),
        delta=f"{pct(cat_counts.get(CAT_3, 0))}%", delta_color="off",
    )
    c4.metric(
        "🔴 Не принято",
        f"{int(cat_counts.get(CAT_4, 0)):,}".replace(",", " "),
        delta=f"{pct(cat_counts.get(CAT_4, 0))}%", delta_color="off",
    )
    c5.metric(
        "⚪ Без категории",
        f"{int(cat_counts.get('Без категории', 0)):,}".replace(",", " "),
        delta=f"{pct(cat_counts.get('Без категории', 0))}%",
        delta_color="off",
    )


# ---------------------------------------------------------------------------
#  Агрегация по авторам (активные, из единого источника)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_authors(disciplines: tuple = (), sections: tuple = (),
                   kits: tuple = ()) -> pd.DataFrame:
    """Сводка по авторам — только активные замечания."""
    active = _load_active_df()
    active = _filter_active(active, disciplines, sections, kits)

    if active.empty:
        return pd.DataFrame()

    df = active.copy()
    df["Категория"] = (
        df["category"].fillna("Без категории").replace("", "Без категории")
    )

    rows = []
    for author, sub in df.groupby("author"):
        if not author:
            continue

        total = len(sub)
        cat_counts = sub["Категория"].value_counts()
        cnt_1 = int(cat_counts.get(CAT_1, 0))
        cnt_2 = int(cat_counts.get(CAT_2, 0))
        cnt_3 = int(cat_counts.get(CAT_3, 0))
        cnt_4 = int(cat_counts.get(CAT_4, 0))
        cnt_none = int(cat_counts.get("Без категории", 0))

        rows.append({
            "author": author,
            "total": total,
            "cat_1": cnt_1,
            "cat_2": cnt_2,
            "cat_3": cnt_3,
            "cat_4": cnt_4,
            "cat_none": cnt_none,
            "categorized": total - cnt_none,
            # Разбивка по ответственным
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

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    result["reject_pct"] = result.apply(
        lambda r: round(r["cat_4"] / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)
    result["categorized_pct"] = result.apply(
        lambda r: round(r["categorized"] / r["total"] * 100, 1)
        if r["total"] else 0.0, axis=1)

    return result


# ---------------------------------------------------------------------------
#  Метрики сроков по авторам (только активные)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_author_timing() -> pd.DataFrame:
    """
    Сроки по авторам — только активные замечания.
    Метрики: наше время ответа, ожидание заказчика, хроника, % просрочки.
    """
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    rows = []
    for author, group in active.groupby("author"):
        if not author:
            continue

        # ---- Наше время ответа ----
        with_fix = group[group["fix_date_d"].notna()]
        our_times = []
        for _, r in with_fix.iterrows():
            if r["fix_date_d"] is not None and r["created_d"] is not None:
                try:
                    delta = (r["fix_date_d"] - r["created_d"]).days
                    if delta >= 0:
                        our_times.append(delta)
                except (TypeError, AttributeError):
                    pass
        avg_our = round(sum(our_times) / len(our_times), 1) if our_times else 0

        # ---- Ждут заказчика ----
        waiting = group[group["category_flag"].isin([
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime",
        ])]
        avg_wait = (round(waiting["days_waiting_customer"].mean(), 1)
                     if not waiting.empty else 0)

        # ---- Хроника ----
        chronic = group[
            group["category_flag"] == "waiting_customer_chronic"
        ]
        avg_chronic = (round(chronic["days_waiting_customer"].mean(), 1)
                        if not chronic.empty else 0)

        # ---- Учтено (A/B) ----
        closed_doc = group[
            group["category_flag"] == "closed_by_doc_status"
        ]

        # ---- % реальных просрочек заказчиком ----
        total_waiting = len(waiting) + len(chronic)
        pct_overdue = (
            round(len(waiting[waiting["days_waiting_customer"] > 10])
                  / total_waiting * 100, 1)
            if total_waiting else 0
        )

        # ---- Просрочки АТП ТЛП ----
        overdue_flags = [
            "new_overdue", "in_work_overdue",
            "rejected_overdue", "discussion_overdue",
        ]
        n_ours_overdue = group[
            group["category_flag"].isin(overdue_flags)
        ].shape[0]

        rows.append({
            "author": author,
            "n_total": len(group),
            "n_waiting": len(waiting),
            "n_chronic": len(chronic),
            "n_closed_by_doc": len(closed_doc),
            "n_ours_overdue": n_ours_overdue,
            "avg_our_response_days": avg_our,
            "avg_customer_wait_days": avg_wait,
            "avg_chronic_days": avg_chronic,
            "pct_overdue": pct_overdue,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
#  Разбивка автора по дисциплинам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_author_by_discipline(author: str) -> pd.DataFrame:
    """Разбивка замечаний автора по дисциплинам (активные)."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    sub = active[active["author"] == author].copy()
    if sub.empty:
        return pd.DataFrame()

    sub["Категория"] = (
        sub["category"].fillna("Без категории").replace("", "Без категории")
    )

    rows = []
    for disc, g in sub.groupby("discipline"):
        if not disc:
            continue
        cc = g["Категория"].value_counts()
        rows.append({
            "discipline": disc,
            "n": len(g),
            "cat_1": int(cc.get(CAT_1, 0)),
            "cat_2": int(cc.get(CAT_2, 0)),
            "cat_3": int(cc.get(CAT_3, 0)),
            "cat_4": int(cc.get(CAT_4, 0)),
            "cat_none": int(cc.get("Без категории", 0)),
        })
    return pd.DataFrame(rows).sort_values("n", ascending=False)


# ---------------------------------------------------------------------------
#  Замечания автора (полный список с 8 категориями)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_author_comments(author: str) -> pd.DataFrame:
    """Все замечания автора (включая архив, закрытые — для полноты)."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return pd.DataFrame()

    sub = df[df["author"] == author].copy()
    if sub.empty:
        return pd.DataFrame()

    view = sub[[
        "id", "discipline", "complex", "sheet", "comment",
        "status", "doc_status", "created", "category",
    ]].rename(columns={
        "id": "ID",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "status": "Статус замечания",
        "doc_status": "Статус листа",
        "created": "Создано",
        "category": "Категория",
    })
    return view


# ---------------------------------------------------------------------------
#  Просрочки заказчика по автору (его замечания, мы ответили, он тянет)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_author_overdue(author: str) -> pd.DataFrame:
    """
    Просрочки РАССМОТРЕНИЯ заказчиком по замечаниям автора.

    Берём замечания в статусе «Выполнено», где заказчик не рассмотрел
    ответ > 10 р.д.:
      🟡 10–30 р.д.  — waiting_customer
      🟠 30–90 р.д.  — waiting_customer_overdue
      🔴 >90 р.д.    — waiting_customer_chronic

    Это НЕ наша вина — это вина заказчика.
    """
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    customer_flags = [
        "waiting_customer",
        "waiting_customer_overdue",
        "waiting_customer_chronic",
    ]

    sub = active[
        (active["author"] == author)
        & (active["category_flag"].isin(customer_flags))
    ].copy()

    if sub.empty:
        return pd.DataFrame()

    # Человеческие бакеты
    def _bucket(flag):
        return {
            "waiting_customer": "🟡 Свежие (10–30 р.д.)",
            "waiting_customer_overdue": "🟠 Просроченные (30–90 р.д.)",
            "waiting_customer_chronic": "🔴 Хроника (>90 р.д.)",
        }.get(flag, flag)

    sub["_bucket"] = sub["category_flag"].apply(_bucket)
    sub = sub.sort_values("days_waiting_customer", ascending=False)

    view = sub[[
        "id", "_bucket", "discipline", "complex", "sheet",
        "comment", "status", "fix_date",
        "days_waiting_customer",
    ]].rename(columns={
        "id": "ID",
        "_bucket": "Категория ожидания",
        "discipline": "Дисциплина",
        "complex": "Комплект",
        "sheet": "Лист",
        "comment": "Замечание",
        "status": "Статус",
        "fix_date": "Наш ответ",
        "days_waiting_customer": "Ждём заказчика (р.д.)",
    })
    return view


# ---------------------------------------------------------------------------
#  Топ авторов
# ---------------------------------------------------------------------------
def _render_top_authors(df: pd.DataFrame, limit: int = 20):
    st.markdown(f"### 🏆 Топ-{limit} авторов по активным замечаниям")
    if df.empty:
        st.info("Нет данных.")
        return
    top = df.nlargest(limit, "total").sort_values("total", ascending=True)
    fig = px.bar(
        top, x="total", y="author", orientation="h", text="total",
        labels={"total": "Активных", "author": "Автор"},
        color_discrete_sequence=["#64B5F6"],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(400, 25 * len(top)), yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Авторы_топ", "auth_top")


# ---------------------------------------------------------------------------
#  Качество замечаний
# ---------------------------------------------------------------------------
def _render_quality(df: pd.DataFrame, limit: int = 20):
    st.markdown(f"### 🎯 Качество замечаний — топ-{limit} авторов")
    if df.empty:
        st.info("Нет данных.")
        return
    top = df.nlargest(limit, "total").copy()
    df_long = top.melt(
        id_vars="author",
        value_vars=["cat_1", "cat_2", "cat_3", "cat_4", "cat_none"],
        var_name="cat_key", value_name="Количество")
    mapping = {"cat_1": "Принято", "cat_2": "Формальное",
               "cat_3": "Доп.треб.", "cat_4": "Не принято",
               "cat_none": "Без категории"}
    df_long["Категория"] = df_long["cat_key"].map(mapping)
    order = top.sort_values("total", ascending=False)["author"].tolist()

    fig = px.bar(
        df_long, x="author", y="Количество", color="Категория",
        barmode="stack", color_discrete_map=CAT_COLORS,
        category_orders={"author": order,
                          "Категория": ["Принято", "Формальное",
                                        "Доп.треб.", "Не принято",
                                        "Без категории"]},
        labels={"author": "Автор"},
    )
    fig.update_layout(xaxis_tickangle=-45, height=500, legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Авторы_качество", "auth_quality")

    table = top[[
        "author", "total", "ours", "waiting", "chronic", "closed_doc",
        "cat_1", "cat_2", "cat_3", "cat_4", "cat_none",
        "categorized_pct", "reject_pct",
    ]].rename(columns={
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
        "categorized_pct": "% разбора",
        "reject_pct": "% непринятых",
    }).sort_values("Активных", ascending=False)

    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "% разбора": st.column_config.ProgressColumn(
                "% разбора", min_value=0, max_value=100,
                format="%.1f%%"),
            "% непринятых": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


# ---------------------------------------------------------------------------
#  Сроки по авторам
# ---------------------------------------------------------------------------
def _render_author_timing():
    st.markdown("### ⏸ Сроки рассмотрения по авторам")

    df = _load_author_timing()
    if df.empty:
        st.info("Нет данных по срокам.")
        return

    df = df[df["n_waiting"] + df["n_chronic"] + df["n_ours_overdue"] >= 5].copy()
    if df.empty:
        st.info("Нет данных для отображения.")
        return

    st.caption(
        "**Три разные проблемы:**\n\n"
        "🟠 **Мы медленно отвечаем** — по замечаниям этих авторов "
        "наша команда тратит много времени. Это **наша** проблема.\n\n"
        "🔵 **Заказчик тянет сейчас** — замечания в реальном ожидании "
        "(< 90 р.д.). Это **его текущая** проблема.\n\n"
        "🔴 **Хронические** — висят > 90 р.д."
    )

    st.divider()

    # ---- Блок 1: Мы медленно отвечаем ----
    st.markdown("#### 🟠 Мы медленно отвечаем")
    st.caption("Топ-10 авторов, по чьим замечаниям **АТП ТЛП** "
               "тратит больше всего времени на ответ.")

    our_slow = df.nlargest(10, "avg_our_response_days").sort_values(
        "avg_our_response_days", ascending=True)

    fig_ours = px.bar(
        our_slow,
        x="avg_our_response_days", y="author", orientation="h",
        text="avg_our_response_days",
        labels={"avg_our_response_days": "Дней до нашего ответа",
                "author": ""},
        color="avg_our_response_days",
        color_continuous_scale=["#FFD54F", "#FFB74D", "#E57373"],
    )
    fig_ours.update_traces(texttemplate="%{text:.0f} дн.",
                            textposition="outside")
    fig_ours.add_vline(
        x=10, line_dash="dash", line_color="#2E7D32", line_width=2,
        annotation_text="SLA 10 р.д.",
        annotation_position="top right",
        annotation_font_color="#2E7D32", annotation_font_size=11,
    )
    fig_ours.update_layout(
        height=max(350, 35 * len(our_slow)),
        coloraxis_showscale=False,
        margin=dict(l=180, r=80, t=20, b=40),
    )
    st.plotly_chart(fig_ours, use_container_width=True)
    download_plotly(fig_ours, "Авторы_мы_медленные", "auth_ours",
                    width=1400, height=600)

    over_sla = (our_slow["avg_our_response_days"] > 10).sum()
    if over_sla > 0:
        st.warning(
            f"⚠️ По замечаниям **{over_sla} из 10** авторов мы отвечаем "
            f"дольше **SLA (10 р.д.)**."
        )
    else:
        st.success("✅ По всем топ-авторам мы отвечаем в пределах нормы.")

    st.divider()

    # ---- Блок 2: Заказчик тянет сейчас ----
    st.markdown("#### 🔵 Заказчик тянет сейчас")
    st.caption("Топ-10 авторов, чьи ответы **они держат у себя прямо "
               "сейчас** (< 90 р.д. — реальное ожидание).")

    cust_slow = df[df["n_waiting"] > 0].nlargest(
        10, "avg_customer_wait_days").sort_values(
        "avg_customer_wait_days", ascending=True)

    if cust_slow.empty:
        st.success("🎉 Нет активного ожидания.")
    else:
        fig_cust = px.bar(
            cust_slow,
            x="avg_customer_wait_days", y="author", orientation="h",
            text="avg_customer_wait_days",
            labels={"avg_customer_wait_days": "Дней ожидания",
                    "author": ""},
            color="avg_customer_wait_days",
            color_continuous_scale=["#A5D6A7", "#FFD54F", "#E57373"],
        )
        fig_cust.update_traces(texttemplate="%{text:.0f} дн.",
                                textposition="outside")
        fig_cust.add_vline(
            x=10, line_dash="dash", line_color="#2E7D32", line_width=2,
            annotation_text="SLA 10 р.д.",
            annotation_position="top right",
            annotation_font_color="#2E7D32", annotation_font_size=11,
        )
        fig_cust.update_layout(
            height=max(350, 35 * len(cust_slow)),
            coloraxis_showscale=False,
            margin=dict(l=180, r=80, t=20, b=40),
        )
        st.plotly_chart(fig_cust, use_container_width=True)
        download_plotly(fig_cust, "Авторы_заказчик_тянет", "auth_cust",
                        width=1400, height=600)

        overdue = (cust_slow["avg_customer_wait_days"] > 10).sum()
        if overdue > 0:
            st.error(
                f"🔴 **{overdue} из 10** авторов держат наши ответы "
                f"дольше **SLA**."
            )

    st.divider()

    # ---- Блок 3: Хронические ----
    st.markdown("#### 🔴 Хронические — висят > 90 р.д.")
    st.caption("Эти замечания, скорее всего, уже решены, но статус в "
               "Витрокад не обновлён. Нужно письмо на формальное закрытие.")

    chronic_slow = df[df["n_chronic"] > 0].nlargest(
        10, "avg_chronic_days").sort_values(
        "avg_chronic_days", ascending=True)

    if chronic_slow.empty:
        st.success("🎉 Нет хронических замечаний.")
    else:
        fig_chronic = px.bar(
            chronic_slow,
            x="avg_chronic_days", y="author", orientation="h",
            text="avg_chronic_days",
            labels={"avg_chronic_days": "Дней (хроника)", "author": ""},
            color_discrete_sequence=["#7F0000"],
        )
        fig_chronic.update_traces(texttemplate="%{text:.0f} дн.",
                                    textposition="outside")
        fig_chronic.update_layout(
            height=max(350, 35 * len(chronic_slow)),
            margin=dict(l=180, r=80, t=20, b=40),
        )
        st.plotly_chart(fig_chronic, use_container_width=True)
        download_plotly(fig_chronic, "Авторы_хронические", "auth_chronic",
                        width=1400, height=600)

    st.divider()

    # ---- Сводная таблица ----
    st.markdown("#### 📋 Полная таблица по авторам")

    table = df[[
        "author", "n_total", "n_ours_overdue", "n_waiting", "n_chronic",
        "n_closed_by_doc", "avg_our_response_days",
        "avg_customer_wait_days", "avg_chronic_days", "pct_overdue",
    ]].copy()

    table = table.rename(columns={
        "author": "Автор",
        "n_total": "Активных",
        "n_ours_overdue": "🔴 Просроч. АТП ТЛП",
        "n_waiting": "🔵 Ждут заказ.",
        "n_chronic": "🔴 Хроника",
        "n_closed_by_doc": "🟢 Учтено A/B",
        "avg_our_response_days": "Мы отвечаем (дн.)",
        "avg_customer_wait_days": "Ждём заказчика (дн.)",
        "avg_chronic_days": "Хроника (дн.)",
        "pct_overdue": "% просрочки",
    })

    table["_sort"] = (table["🔴 Просроч. АТП ТЛП"]
                       + table["🔵 Ждут заказ."] + table["🔴 Хроника"])
    table = table.sort_values("_sort", ascending=False).drop(columns=["_sort"])

    st.dataframe(
        table.head(30),
        use_container_width=True, hide_index=True, height=500,
        column_config={
            "Мы отвечаем (дн.)": st.column_config.NumberColumn(
                format="%.1f"),
            "Ждём заказчика (дн.)": st.column_config.NumberColumn(
                format="%.1f"),
            "Хроника (дн.)": st.column_config.NumberColumn(format="%.1f"),
            "% просрочки": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.0f%%"),
        },
    )


# ---------------------------------------------------------------------------
#  Drill-down автора с 2 табами
# ---------------------------------------------------------------------------
def _render_drilldown(df: pd.DataFrame):
    st.markdown("### 🔍 Детали автора")
    if df.empty:
        st.info("Нет данных.")
        return
    authors = sorted(df["author"].dropna().unique())
    sel = st.selectbox(
        "Выберите автора", options=authors,
        key="author_drill",
        placeholder="Начните вводить имя...",
    )
    if not sel:
        return
    row = df[df["author"] == sel].iloc[0]

    # ---- KPI автора ----
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Активных", int(row["total"]))
    c2.metric("🔴 АТП ТЛП", int(row["ours"]))
    c3.metric("🔵 Ждут заказ.", int(row["waiting"]))
    c4.metric("🔴 Хроника", int(row["chronic"]))
    c5.metric("🟢 Учтено A/B", int(row["closed_doc"]))
    c6.metric("% разбора", f"{row['categorized_pct']}%")

    st.divider()

    # ---- 2 под-таба: обзор и просрочки ----
    tab_overview, tab_overdue = st.tabs([
        "📊 Обзор автора",
        "⏰ Просрочки автора",
    ])

    # =====================================================================
    #  Таб 1: Обзор
    # =====================================================================
    with tab_overview:
        col1, col2 = st.columns(2)

        with col1:
            cat_data = pd.DataFrame([
                {"Категория": "Принято", "Количество": int(row["cat_1"])},
                {"Категория": "Формальное", "Количество": int(row["cat_2"])},
                {"Категория": "Доп.треб.", "Количество": int(row["cat_3"])},
                {"Категория": "Не принято", "Количество": int(row["cat_4"])},
                {"Категория": "Без категории",
                 "Количество": int(row["cat_none"])},
            ])
            cat_data = cat_data[cat_data["Количество"] > 0]
            if not cat_data.empty:
                fig = px.pie(
                    cat_data, names="Категория", values="Количество",
                    hole=0.45, color="Категория",
                    color_discrete_map=CAT_COLORS,
                    title="Категории замечаний автора",
                )
                st.plotly_chart(fig, use_container_width=True)
                download_plotly(
                    fig, f"Автор_{safe_filename(sel)}_категории",
                    "auth_drill_pie")

        with col2:
            by_disc = _load_author_by_discipline(sel)
            if not by_disc.empty:
                by_disc["label"] = by_disc["discipline"].apply(
                    lambda c: f"{c} — {discipline_name(c)}")
                fig = px.bar(
                    by_disc.sort_values("n", ascending=True),
                    x="n", y="label", orientation="h", text="n",
                    labels={"n": "Замечаний", "label": ""},
                    color_discrete_sequence=["#9575CD"],
                )
                fig.update_traces(textposition="outside")
                fig.update_layout(height=max(300, 25 * len(by_disc)))
                st.plotly_chart(fig, use_container_width=True)
                download_plotly(
                    fig, f"Автор_{safe_filename(sel)}_дисциплины",
                    "auth_drill_disc")

        st.divider()
        st.markdown(f"**Все замечания автора `{sel}`**")
        st.caption("Последние 500, с разбивкой по статусам и категориям.")

        details = _load_author_comments(sel)
        if details.empty:
            st.info("У автора нет замечаний.")
            return
        st.dataframe(
            details, use_container_width=True, hide_index=True,
            height=500,
            column_config={
                "Замечание": st.column_config.TextColumn(width="large"),
            },
        )

        with st.expander("📥 Выгрузить замечания автора в Excel",
                          expanded=False):
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                details.to_excel(writer, index=False, sheet_name="Замечания")
            buf.seek(0)
            st.download_button(
                "⬇️ Скачать XLSX",
                data=buf.getvalue(),
                file_name=f"Автор_{safe_filename(sel)}_"
                          f"{datetime.now():%Y%m%d}.xlsx",
                mime=("application/vnd.openxmlformats-officedocument"
                      ".spreadsheetml.sheet"),
                use_container_width=True,
                key=f"auth_dl_overview_{safe_filename(sel)}",
            )

    # =====================================================================
    #  Таб 2: Заказчик тянет — просрочки рассмотрения по автору
    # =====================================================================
    with tab_overdue:
        st.markdown(
            f"#### ⏰ Просрочки рассмотрения заказчиком — автор `{sel}`"
        )
        st.caption(
            "Замечания автора в статусе **«Выполнено»**, где **заказчик** "
            "не рассмотрел наш ответ > 10 р.д. Это **его** проблема, "
            "не наша. Разбивка по возрасту ожидания."
        )

        df_overdue = _load_author_overdue(sel)
        if df_overdue.empty:
            st.success("🎉 По этому автору заказчик рассматривает ответы "
                       "в срок.")
            return

        # ---- KPI ----
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Всего ждут > 10 р.д.",
            len(df_overdue),
            help="Замечаний, где заказчик не рассмотрел наш ответ "
                 "дольше 10 р.д.",
        )
        c2.metric(
            "Среднее ожидание",
            f"{int(df_overdue['Ждём заказчика (р.д.)'].mean())} р.д.",
            help="Среднее число рабочих дней ожидания по этим замечаниям.",
        )
        c3.metric(
            "Максимум",
            f"{int(df_overdue['Ждём заказчика (р.д.)'].max())} р.д.",
            help="Самое долгое ожидание среди замечаний автора.",
        )

        st.divider()

        # ---- Разбивка по бакетам ----
        st.markdown("##### 📊 Разбивка по возрасту ожидания")

        bucket_order = [
            "🟡 Свежие (10–30 р.д.)",
            "🟠 Просроченные (30–90 р.д.)",
            "🔴 Хроника (>90 р.д.)",
        ]
        color_map = {
            "🟡 Свежие (10–30 р.д.)":      "#FFD54F",
            "🟠 Просроченные (30–90 р.д.)": "#FFB74D",
            "🔴 Хроника (>90 р.д.)":       "#E57373",
        }

        counts = df_overdue["Категория ожидания"].value_counts()
        bucket_df = pd.DataFrame([
            {"Категория ожидания": b, "Количество": int(counts.get(b, 0))}
            for b in bucket_order
        ])
        bucket_df = bucket_df[bucket_df["Количество"] > 0]

        fig = px.bar(
            bucket_df,
            x="Количество", y="Категория ожидания", orientation="h",
            text="Количество",
            color="Категория ожидания",
            color_discrete_map=color_map,
            category_orders={"Категория ожидания": bucket_order},
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            height=max(200, 50 * len(bucket_df)),
            showlegend=False,
            yaxis_title="",
        )
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(
            fig, f"Автор_{safe_filename(sel)}_ожидание",
            f"auth_wait_{safe_filename(sel)}",
        )

        st.divider()

        # ---- Таблица ----
        st.markdown(f"##### 📋 Список ({len(df_overdue)})")

        st.dataframe(
            df_overdue,
            use_container_width=True, hide_index=True, height=500,
            column_config={
                "Замечание": st.column_config.TextColumn(width="large"),
                "Ждём заказчика (р.д.)": st.column_config.NumberColumn(
                    format="%d"),
            },
        )

        with st.expander("📧 Выгрузить для письма заказчику",
                          expanded=False):
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df_overdue.to_excel(writer, index=False,
                                     sheet_name="Ждут заказчика")
            buf.seek(0)
            st.download_button(
                "⬇️ Скачать XLSX",
                data=buf.getvalue(),
                file_name=f"Автор_{safe_filename(sel)}_ожидание_"
                          f"{datetime.now():%Y%m%d}.xlsx",
                mime=("application/vnd.openxmlformats-officedocument"
                      ".spreadsheetml.sheet"),
                use_container_width=True,
                key=f"auth_dl_wait_{safe_filename(sel)}",
            )


# ---------------------------------------------------------------------------
#  Загрузка ожидающих заказчика
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_waiting_review(disciplines=None, kits=None,
                          sections=None, authors=None) -> pd.DataFrame:
    """Замечания «Выполнено» из единого источника."""
    from vitro.ui.deadlines import _load_all_categorized

    df = _load_all_categorized()
    if df.empty:
        return df

    waiting_flags = [
        "waiting_customer",
        "waiting_customer_overdue",
        "waiting_customer_ontime",
        "waiting_customer_chronic",
        "closed_by_doc_status",
    ]
    df = df[df["category_flag"].isin(waiting_flags)].copy()
    if df.empty:
        return df

    if disciplines:
        df = df[df["discipline"].isin(disciplines)]
    if sections:
        df = df[df["section"].isin(sections)]
    if kits:
        df = df[df["complex"].isin(kits)]
    if authors:
        df = df[df["author"].isin(authors)]     # ← НОВОЕ

    def _bucket(row):
        flag = row["category_flag"]
        if flag == "closed_by_doc_status":
            return "🟢 Учтено (A/B)"
        if flag == "waiting_customer_chronic":
            return "🔴 Хронические (>90 р.д.)"
        if flag == "waiting_customer_overdue":
            return "🟠 Просроченные (30–90 р.д.)"
        if flag == "waiting_customer":
            return "🟡 Свежие (10–30 р.д.)"
        if flag == "waiting_customer_ontime":
            return "🟡 Свежие (в сроке)"
        return "Прочее"

    df = df.copy()
    df["bucket"] = df.apply(_bucket, axis=1)
    df["fix_date"] = df["fix_date"].fillna("")
    df["days_waiting"] = df["days_waiting_customer"]
    return df


# ---------------------------------------------------------------------------
#  Под-вкладка «Ждут заказчика»
# ---------------------------------------------------------------------------
def _render_waiting_review():
    st.markdown("### 🔵 Ответ дан, ожидают рассмотрения заказчика")
    st.caption(
        "Замечания в статусе **«Выполнено»**. АТП ТЛП ответил — ждём "
        "решения заказчика. Категорию можно поставить или изменить."
    )

    # ---------------------------------------------------------------------
    #  Загружаем список авторов (для фильтра)
    # ---------------------------------------------------------------------
    # Загружаем базовый набор (без фильтров) — чтобы получить всех авторов
    from vitro.ui.deadlines import _load_all_categorized
    _all = _load_all_categorized()
    if not _all.empty:
        # Только «Выполнено» — чтобы предлагать авторов, у которых
        # есть замечания в этом статусе
        _waiting_flags_for_authors = [
            "waiting_customer", "waiting_customer_overdue",
            "waiting_customer_ontime", "waiting_customer_chronic",
            "closed_by_doc_status",
        ]
        _authors = sorted(
            _all[_all["category_flag"].isin(_waiting_flags_for_authors)]
            ["author"].dropna().unique().tolist()
        )
    else:
        _authors = []

    # Фильтры
    disc_options = _load_discipline_options()
    with st.expander("🎛 Фильтры", expanded=False):
        c1, c2, c3, c4 = st.columns(4)        # ← было 3, стало 4
        with c1:
            sel_disc_labels = st.multiselect(
                "Дисциплина", options=list(disc_options.values()),
                placeholder="Все дисциплины", key="wait_disc")
            sel_disc = [c for c, l in disc_options.items()
                         if l in sel_disc_labels]
        section_options = _load_section_options(
            tuple(sel_disc) if sel_disc else ())
        with c2:
            if section_options:
                sel_section_labels = st.multiselect(
                    "Раздел", options=list(section_options.values()),
                    placeholder="Все разделы", key="wait_section")
                sel_section = [c for c, l in section_options.items()
                                if l in sel_section_labels]
            else:
                sel_section = []
                st.multiselect("Раздел", options=[],
                                placeholder="Не применимы",
                                disabled=True, key="wait_section_empty")
        kit_options = _load_kit_options(
            tuple(sel_disc) if sel_disc else (),
            tuple(sel_section) if sel_section else ())
        with c3:
            sel_kit_labels = st.multiselect(
                "Комплект", options=list(kit_options.values()),
                placeholder="Все комплекты", key="wait_kit")
            sel_kit = [c for c, l in kit_options.items()
                        if l in sel_kit_labels]
        with c4:
            sel_author_labels = st.multiselect(
                "Автор (заказчик)",
                options=_authors,
                placeholder="Все авторы",
                key="wait_author",
            )
            sel_author = sel_author_labels

    df = _load_waiting_review(
        disciplines=tuple(sel_disc) if sel_disc else None,
        kits=tuple(sel_kit) if sel_kit else None,
        sections=tuple(sel_section) if sel_section else None,
        authors=tuple(sel_author) if sel_author else None,   # ← НОВОЕ
    )

    if df.empty:
        st.success("🎉 Нет замечаний, ожидающих рассмотрения заказчика.")
        return

    # ---- KPI ----
    buckets_order = [
        "🟡 Свежие (в сроке)",
        "🟡 Свежие (10–30 р.д.)",
        "🟠 Просроченные (30–90 р.д.)",
        "🔴 Хронические (>90 р.д.)",
        "🟢 Учтено (A/B)",
    ]
    counts = {b: int((df["bucket"] == b).sum()) for b in buckets_order}

    st.markdown("##### 📊 Классификация")

    # ---------------------------------------------------------------
    #  Блок 1: Активное ожидание заказчика
    # ---------------------------------------------------------------
    n_ontime = counts["🟡 Свежие (в сроке)"]
    n_fresh = counts["🟡 Свежие (10–30 р.д.)"]
    n_overdue = counts["🟠 Просроченные (30–90 р.д.)"]
    n_chronic = counts["🔴 Хронические (>90 р.д.)"]
    n_waiting_total = n_ontime + n_fresh + n_overdue + n_chronic

    st.markdown("**🔵 Заказчик должен рассмотреть**")
    st.caption(
        "Мы ответили (статус «Выполнено»), но заказчик ещё не "
        "рассмотрел. Это **его** зона ответственности."
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "🟢 В сроке (≤10 р.д.)",
        f"{n_ontime:,}".replace(",", " "),
        help="Заказчик ещё в SLA — не пора напоминать.",
    )
    c2.metric(
        "🟡 Свежие (10–30 р.д.)",
        f"{n_fresh:,}".replace(",", " "),
        help="Недавно вышли за SLA, пора напоминать.",
    )
    c3.metric(
        "🟠 Просроченные (30–90 р.д.)",
        f"{n_overdue:,}".replace(",", " "),
        help="Значительное ожидание — нужен письменный запрос.",
    )
    c4.metric(
        "🔴 Хроника (>90 р.д.)",
        f"{n_chronic:,}".replace(",", " "),
        delta="эскалация" if n_chronic > 0 else None,
        delta_color="inverse",
        help="Критическое ожидание — эскалация руководству.",
    )
    c5.metric(
        "📊 Итого заказчик",
        f"{n_waiting_total:,}".replace(",", " "),
        help="Сумма всех замечаний, ожидающих рассмотрения заказчиком.",
    )

    # ---------------------------------------------------------------
    #  Блок 2: Учтено, не закрыто формально (лист A/B)
    # ---------------------------------------------------------------
    # Разделяем учтённые A и B через _load_closed_by_doc()
    from vitro.ui.categories import _load_closed_by_doc as _closed
    closed = _closed()
    n_a = closed.get("a", 0)
    n_b = closed.get("b", 0)
    n_ack_total = n_a + n_b

    st.markdown("**🟢 Учтено (лист A/B), но не закрыто формально**")
    st.caption(
        "Лист получил статус **A** (утверждён) или **B** (к сдаче). "
        "Замечание **фактически принято** — осталось дожать заказчика "
        "на формальное «Закрыто» в Витрокад."
    )
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "🟢 Лист A — утверждён",
        f"{n_a:,}".replace(",", " "),
        help="Лист утверждён заказчиком — только формальность.",
    )
    c2.metric(
        "🟡 Лист B — к сдаче",
        f"{n_b:,}".replace(",", " "),
        help="Лист готов к сдаче. Формально замечание не снято.",
    )
    c3.metric(
        "📊 Итого учтено",
        f"{n_ack_total:,}".replace(",", " "),
        help="Сумма учтённых замечаний — требуется формальное "
             "закрытие в Витрокад.",
    )

    # ---------------------------------------------------------------
    #  Блок 3: Общая сводка
    # ---------------------------------------------------------------
    st.markdown("")
    c_total, _ = st.columns([1, 3])
    c_total.metric(
        "📦 Всего в статусе «Выполнено»",
        f"{n_waiting_total + n_ack_total:,}".replace(",", " "),
        help=f"{n_waiting_total:,} + {n_ack_total:,} — "
             f"все замечания, где АТП ТЛП дал ответ."
        .replace(",", " "),
    )

    st.divider()

    # ---- Топ-10 авторов ----
    st.markdown("##### 👤 Топ-10 авторов, чьи ответы ждут решения")
    top_auth = (df.groupby("author").size()
                   .reset_index(name="Ожидают")
                   .sort_values("Ожидают", ascending=True)
                   .tail(10))
    fig = px.bar(
        top_auth, x="Ожидают", y="author", orientation="h",
        text="Ожидают",
        color_discrete_sequence=["#64B5F6"],
        labels={"author": ""},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=max(300, 30 * len(top_auth)))
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Авторы_ждут_рассмотрения", "auth_wait_rev")

    st.divider()

    # ---- Таблица с редактором ----
    st.markdown(f"##### 📋 Список ({len(df):,})".replace(",", " "))

    df["category"] = df["category"].apply(_add_prefix)

    bucket_priority = {
        "🔴 Хронические (>90 р.д.)": 0,
        "🟠 Просроченные (30–90 р.д.)": 1,
        "🟡 Свежие (10–30 р.д.)": 2,
        "🟡 Свежие (в сроке)": 3,
        "🟢 Учтено (A/B)": 4,
    }
    df["_sort"] = df["bucket"].map(bucket_priority)
    df = df.sort_values(["_sort", "days_waiting"],
                         ascending=[True, False]).drop(columns=["_sort"])

    display_cols = [
        "id", "bucket", "discipline", "complex", "sheet",
        "comment", "author", "fix_date", "days_waiting",
        "category", "category_user", "category_date",
        "category_version",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    view = df[display_cols].copy()

    edited = st.data_editor(
        view,
        column_config={
            "id": st.column_config.NumberColumn(
                "ID", disabled=True, width="small"),
            "bucket": st.column_config.TextColumn(
                "Категория ожидания", disabled=True),
            "discipline": st.column_config.TextColumn(
                "Дисц.", disabled=True, width="small"),
            "complex": st.column_config.TextColumn(
                "Комплект", disabled=True, width="medium"),
            "sheet": st.column_config.TextColumn(
                "Лист", disabled=True, width="medium"),
            "comment": st.column_config.TextColumn(
                "Замечание", disabled=True, width="large"),
            "author": st.column_config.TextColumn(
                "Автор", disabled=True),
            "fix_date": st.column_config.TextColumn(
                "Наш ответ", disabled=True, width="small"),
            "days_waiting": st.column_config.NumberColumn(
                "Ждём (р.д.)", disabled=True, width="small"),
            "category": st.column_config.SelectboxColumn(
                "Категория", options=CAT_OPTIONS_DISPLAY,
                required=False,
                help="Можно поставить или изменить категорию"),
            "category_user": st.column_config.TextColumn(
                "Кто", disabled=True, width="small"),
            "category_date": st.column_config.TextColumn(
                "Когда", disabled=True, width="small"),
            "category_version": None,
        },
        disabled=["id", "bucket", "discipline", "complex", "sheet",
                  "comment", "author", "fix_date", "days_waiting",
                  "category_user", "category_date"],
        hide_index=True,
        use_container_width=True,
        height=600,
        key="waiting_review_editor",
    )

    # ---- Кнопка сохранения ----
    col_save, col_exp = st.columns([1, 1])

    with col_save:
        if st.button("💾 Сохранить изменения", type="primary",
                      use_container_width=True, key="wait_save"):
            user = st.session_state.get("user", "инженер")
            saved, conflicts = 0, []

            for _, row in edited.iterrows():
                orig = df[df["id"] == row["id"]].iloc[0]
                new_cat_display = (None if pd.isna(row["category"])
                                    else str(row["category"]))
                new_cat = (_strip_prefix(new_cat_display)
                            if new_cat_display else None)
                old_cat_display = (None if pd.isna(orig["category"])
                                    else str(orig["category"]))
                old_cat = (_strip_prefix(old_cat_display)
                            if old_cat_display else None)

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
                st.warning("⚠️ Конфликты:")
                for c in conflicts[:20]:
                    st.write(f"- {c}")

    # ---- Экспорт с выбором среза ----
    with col_exp:
        export_choice = st.radio(
            "Какой срез выгрузить:",
            options=[
                "Всё «Выполнено»",
                "🔵 Ждут заказчика",
                "🔴 Только хроника",
                "🟢 Учтено (A/B)",
            ],
            horizontal=False,
            key="wait_export_choice",
        )

        if export_choice == "Всё «Выполнено»":
            export_df = df
        elif export_choice == "🔵 Ждут заказчика":
            export_df = df[df["bucket"].isin([
                "🟡 Свежие (10–30 р.д.)",
                "🟠 Просроченные (30–90 р.д.)",
                "🟡 Свежие (в сроке)",
            ])]
        elif export_choice == "🔴 Только хроника":
            export_df = df[df["bucket"] == "🔴 Хронические (>90 р.д.)"]
        else:
            export_df = df[df["bucket"] == "🟢 Учтено (A/B)"]

        buf = io.BytesIO()
        export = export_df[display_cols].copy()
        if "category" in export.columns:
            export["category"] = export["category"].apply(
                lambda x: _strip_prefix(str(x)) if x else x)
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export.to_excel(writer, index=False, sheet_name="Выполнено")
        buf.seek(0)
        st.download_button(
            "📥 Скачать Excel",
            data=buf.getvalue(),
            file_name=(f"Выполнено_"
                       f"{export_choice.replace(' ', '_').replace('«','').
                          replace('»','').replace('(','').replace(')','')}_"
                       f"{datetime.now():%Y%m%d}.xlsx"),
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
            key="wait_download_btn",
        )
        st.caption(f"В выгрузке: **{len(export_df):,}** строк"
                   .replace(",", " "))


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def _render_analytics_tab():
    disc_options = _load_discipline_options()

    c1, c2, c3 = st.columns(3)
    with c1:
        sel_disc_labels = st.multiselect(
            "Дисциплина", options=list(disc_options.values()),
            placeholder="Все дисциплины", key="authors_disc")
        sel_disc = [c for c, l in disc_options.items()
                     if l in sel_disc_labels]

    section_options = _load_section_options(
        tuple(sel_disc) if sel_disc else ())

    with c2:
        if section_options:
            sel_section_labels = st.multiselect(
                "Раздел", options=list(section_options.values()),
                placeholder="Все разделы", key="authors_section")
            sel_section = [c for c, l in section_options.items()
                            if l in sel_section_labels]
        else:
            sel_section = []
            st.multiselect("Раздел", options=[],
                            placeholder="Разделы не применимы",
                            disabled=True, key="authors_section_empty")

    kit_options = _load_kit_options(
        tuple(sel_disc) if sel_disc else (),
        tuple(sel_section) if sel_section else ())

    with c3:
        sel_kit_labels = st.multiselect(
            "Комплект", options=list(kit_options.values()),
            placeholder="Все комплекты", key="authors_kit")
        sel_kit = [c for c, l in kit_options.items()
                    if l in sel_kit_labels]

    disc_t = tuple(sel_disc) if sel_disc else ()
    sect_t = tuple(sel_section) if sel_section else ()
    kit_t = tuple(sel_kit) if sel_kit else ()

    # KPI
    active = _load_active_df()
    active_filtered = _filter_active(active, disc_t, sect_t, kit_t)
    _render_kpi(active_filtered)

    df = _load_authors(disc_t, sect_t, kit_t)
    if df.empty:
        st.info("Нет данных по авторам с учётом фильтров.")
        return

    st.divider()
    limit = st.slider("Сколько авторов показать в топе",
                      min_value=5, max_value=50, value=20, step=5,
                      key="authors_limit")

    _render_top_authors(df, limit)
    st.divider()
    _render_quality(df, limit)
    st.divider()
    _render_author_timing()
    st.divider()
    _render_drilldown(df)


def render():
    st.header("👤 Авторы замечаний")
    st.caption(
        "Аналитика по инженерам заказчика: сколько выдают, "
        "как быстро рассматривают наши ответы."
    )

    tab_analytics, tab_waiting = st.tabs([
        "📊 Аналитика авторов",
        "🔵 Ждут заказчика",
    ])

    with tab_analytics:
        _render_analytics_tab()
    with tab_waiting:
        _render_waiting_review()