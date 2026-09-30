# vitro/ui/sheets.py
"""
📄 Листы — контроль статусов листов РД.

Вкладка отвечает на вопросы:
  - Сколько листов в A / B / C / И / прочих?
  - Какой % утверждён (A+B) — общий прогресс проекта?
  - Где листы-блокеры (в C с активными замечаниями)?
  - Какие комплекты в зоне риска (мало A+B, много активных)?
  - Как идёт тренд A/B по неделям?

Замечания берутся из `_load_all_categorized` (единый источник).
Листы — из `documents`.
"""

import io
import time
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.disciplines import discipline_name
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Универсальный парсер даты
# ---------------------------------------------------------------------------
def _parse_date_safe(value) -> date | None:
    """
    Парсит дату из любого формата, который встречается в БД.
    Возвращает `date` или None.

    Поддерживает:
      - YYYY-MM-DD
      - YYYY-MM-DD HH:MM:SS
      - YYYY/MM/DD
      - DD.MM.YYYY
      - DD.MM.YY
      - ISO 8601 с T
      - datetime / date (уже распарсенные pandas)
    """
    if value is None:
        return None

    # Уже date / datetime
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    s = str(value).strip()
    if not s or s.lower() in ("none", "nat", "nan", ""):
        return None

    # Отрезаем время
    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]

    # Пробуем форматы
    for fmt in (
        "%Y-%m-%d",       # 2026-09-29
        "%Y/%m/%d",       # 2026/09/29
        "%d.%m.%Y",       # 29.09.2026
        "%d.%m.%y",       # 29.09.26
        "%d-%m-%Y",       # 29-09-2026
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # pandas fallback
    try:
        parsed = pd.to_datetime(s, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Единый источник активных замечаний
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


# ---------------------------------------------------------------------------
#  Группировка статусов
# ---------------------------------------------------------------------------
def _normalize_status(raw) -> str:
    """Приводит сырой статус к группе: A / B / C / И / Прочие."""
    if raw is None:
        return "Прочие"
    s = str(raw).strip().upper()
    if s == "A":
        return "A"
    if s == "B":
        return "B"
    if s == "C":
        return "C"
    if s == "И":
        return "И"
    return "Прочие"

# ---------------------------------------------------------------------------
#  Загрузка всех листов
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_all_sheets() -> pd.DataFrame:
    """
    Возвращает все листы, агрегированные по нормализованному leaf.

    Использует `clean_leaf_key` из vitro.text_utils, потому что
    этот запрос идёт НАПРЯМУЮ к таблице documents (не через deadlines).
    """

    t0 = time.time()

    from vitro.text_utils import clean_leaf_key

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                id, leaf, discipline, section, complex,
                status, revision, name, status_date,
                sheet_number
            FROM documents
        """).fetchall()

    today = date.today()

    by_key = {}
    for r in rows:
        raw_leaf = r["leaf"]
        if not raw_leaf:
            continue

        key = clean_leaf_key(raw_leaf)

        current = by_key.get(key)
        if current is None:
            by_key[key] = _row_to_dict(r)
            continue

        # Сравниваем по status_date, fallback на id
        cur_sd = _parse_date_safe(current.get("status_date_str"))
        new_sd = _parse_date_safe(r["status_date"])

        if new_sd and cur_sd and new_sd > cur_sd:
            by_key[key] = _row_to_dict(r)
        elif new_sd and not cur_sd:
            by_key[key] = _row_to_dict(r)
        elif not new_sd and not cur_sd and r["id"] > current["id"]:
            by_key[key] = _row_to_dict(r)
        elif new_sd and cur_sd and new_sd == cur_sd and r["id"] > current["id"]:
            by_key[key] = _row_to_dict(r)

    result = []
    for key, data in by_key.items():
        sd = _parse_date_safe(data["status_date_str"])
        days_in_status = (today - sd).days if sd else None

        result.append({
            "id": data["id"],
            "leaf": key,                            # уже нормализованный
            "leaf_raw": data["raw_leaf"],           # оригинал
            "discipline": data["discipline"],
            "section": data["section"],
            "complex": data["complex"],
            "status_raw": data["status_raw"],
            "status_group": _normalize_status(data["status_raw"]),
            "revision": data["revision"],
            "name": data["name"],
            "status_date": sd,
            "status_days": days_in_status,
            "sheet_number": data["sheet_number"],
        })

    t1 = time.time()
    print(f"[TIMING] _load_all_sheets: {t1 - t0:.1f} сек")
    return pd.DataFrame(result)


def _row_to_dict(r) -> dict:
    """Преобразует sqlite3.Row в dict с предопределёнными полями."""
    return {
        "id": r["id"],
        "raw_leaf": r["leaf"],
        "discipline": r["discipline"],
        "section": r["section"],
        "complex": r["complex"],
        "status_raw": r["status"],
        "revision": r["revision"],
        "name": r["name"],
        "status_date_str": r["status_date"],
        "sheet_number": r["sheet_number"],
    }


# ---------------------------------------------------------------------------
#  Активные замечания по листам
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_active_by_sheet() -> pd.DataFrame:
    """Активные замечания, сгруппированные по листу."""
    active = _load_active_df()
    if active.empty:
        return pd.DataFrame()

    df = active.copy()

    overdue_flags = [
        "new_overdue", "in_work_overdue",
        "rejected_overdue", "discussion_overdue",
    ]

    rows = []
    for leaf, sub in df.groupby("sheet"):
        rows.append({
            "leaf": leaf,             # ← колонка "leaf", НЕ "leaf_key"
            "complex": sub["complex"].iloc[0] if len(sub) else None,
            "discipline": sub["discipline"].iloc[0] if len(sub) else None,
            "active_total": len(sub),
            "ours_overdue": sub[
                sub["category_flag"].isin(overdue_flags)
            ].shape[0],
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
#  KPI
# ---------------------------------------------------------------------------
def _render_kpi(df: pd.DataFrame) -> bool:
    if df.empty:
        st.warning("Нет данных по листам.")
        return False

    total = len(df)
    counts = df["status_group"].value_counts()

    n_a = int(counts.get("A", 0))
    n_b = int(counts.get("B", 0))
    n_c = int(counts.get("C", 0))
    n_i = int(counts.get("И", 0))
    n_other = int(counts.get("Прочие", 0))

    # % A+B = (A+B) / (Всего − И − Прочие)
    # Из «Прочих» исключаем только Аннулировано — но у нас
    # в группе «Прочие» смешаны все. Для знаменателя считаем
    # «рабочие листы» = Всего − И − Аннулировано.
    # Так как Аннулировано у нас в «Прочих», найдём его явно.
    annulled = int((df["status_raw"].astype(str).str.strip().str.lower()
                     .isin(["аннулировано", "annulled"])).sum())
    working = total - n_i - annulled
    pct_ab = round((n_a + n_b) / working * 100, 1) if working > 0 else 0.0

    st.markdown("##### 📦 Всего листов РД")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Всего", f"{total:,}".replace(",", " "))
    c2.metric(
        "🟢 A — утверждён",
        f"{n_a:,}".replace(",", " "),
        help="Лист утверждён заказчиком. Работа завершена.",
    )
    c3.metric(
        "🟢 B — к сдаче",
        f"{n_b:,}".replace(",", " "),
        help="Лист готов к сдаче. Формально ещё не утверждён.",
    )
    c4.metric(
        "🚧 C — в работе",
        f"{n_c:,}".replace(",", " "),
        help="Лист в работе, есть активные замечания.",
    )
    c5.metric(
        "ℹ️ И — информационный",
        f"{n_i:,}".replace(",", " "),
        help="Информационный лист — не требует утверждения.",
    )
    c6.metric(
        "⚪ Прочие",
        f"{n_other:,}".replace(",", " "),
        help="Аннулированные, размещённые, без статуса.",
    )

    # ---- Прогресс A+B ----
    st.markdown("##### 📈 Прогресс утверждения")
    st.caption(
        "**% A+B** — доля утверждённых и готовых к сдаче листов "
        "среди рабочих. Из знаменателя исключены информационные "
        "и аннулированные листы."
    )

    cc1, cc2 = st.columns([1, 3])
    cc1.metric(
        "Утверждено (A+B)",
        f"{pct_ab}%",
        help=f"{n_a + n_b:,} из {working:,} рабочих листов"
             .replace(",", " "),
    )
    with cc2:
        st.progress(min(pct_ab / 100, 1.0))

    return True


# ---------------------------------------------------------------------------
#  По дисциплинам
# ---------------------------------------------------------------------------
def _render_by_discipline(df: pd.DataFrame):
    st.markdown("### 🏷 Статусы листов по дисциплинам")

    if df.empty:
        st.info("Нет данных.")
        return

    rows = []
    for disc, sub in df.groupby("discipline"):
        if not disc:
            continue

        total = len(sub)
        a = int((sub["status_group"] == "A").sum())
        b = int((sub["status_group"] == "B").sum())
        c = int((sub["status_group"] == "C").sum())
        i = int((sub["status_group"] == "И").sum())
        other = int((sub["status_group"] == "Прочие").sum())

        annulled = int(
            sub["status_raw"].astype(str).str.strip().str.lower()
            .isin(["аннулировано", "annulled"]).sum()
        )
        working = total - i - annulled
        pct_ab = round((a + b) / working * 100, 1) if working > 0 else 0.0
        pct_c = round(c / working * 100, 1) if working > 0 else 0.0

        rows.append({
            "Код": disc,
            "Наименование": discipline_name(disc),
            "Всего": total,
            "A": a,
            "B": b,
            "C": c,
            "И": i,
            "Прочие": other,
            "% A+B": pct_ab,
            "% C": pct_c,
        })

    result = pd.DataFrame(rows).sort_values("% A+B", ascending=True)

    st.dataframe(
        result, use_container_width=True, hide_index=True,
        column_config={
            "% A+B": st.column_config.ProgressColumn(
                "% A+B", min_value=0, max_value=100, format="%.1f%%"),
            "% C": st.column_config.ProgressColumn(
                "% C", min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    # ---- Стек-бар по дисциплинам ----
    chart = result.sort_values("% A+B", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=chart["Код"], x=chart["A"], name="🟢 A",
        orientation="h", marker=dict(color="#2E7D32"),
    ))
    fig.add_trace(go.Bar(
        y=chart["Код"], x=chart["B"], name="🟢 B",
        orientation="h", marker=dict(color="#A5D6A7"),
    ))
    fig.add_trace(go.Bar(
        y=chart["Код"], x=chart["C"], name="🚧 C",
        orientation="h", marker=dict(color="#E57373"),
    ))
    fig.add_trace(go.Bar(
        y=chart["Код"], x=chart["И"], name="ℹ️ И",
        orientation="h", marker=dict(color="#BDBDBD"),
    ))
    fig.add_trace(go.Bar(
        y=chart["Код"], x=chart["Прочие"], name="⚪ Прочие",
        orientation="h", marker=dict(color="#EEEEEE"),
    ))

    fig.update_layout(
        barmode="stack",
        height=max(400, 35 * len(chart)),
        xaxis_title="Листов",
        yaxis_title="",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Листы_по_дисциплинам", "sh_disc_chart",
                    width=1400, height=600)


# ---------------------------------------------------------------------------
#  Тренд A/B по неделям
# ---------------------------------------------------------------------------
def _render_weekly_trend(df: pd.DataFrame):
    st.markdown("### 📊 Тренд утверждения A/B по неделям")
    st.caption(
        "Показывает, сколько листов переходило в статус **A** или **B** "
        "каждую неделю. Даты берутся из даты присвоения текущего статуса."
    )

    sub = df[df["status_group"].isin(["A", "B"]) & df["status_date"].notna()].copy()
    if sub.empty:
        st.info("Нет данных по датам статусов листов A/B.")
        return

    # Группируем по неделям (ISO-неделя)
    sub["_week"] = sub["status_date"].apply(
        lambda d: d - timedelta(days=d.weekday())
    )

    weekly = (sub.groupby(["_week", "status_group"]).size()
                 .reset_index(name="Листов"))

    # Стек-бар
    fig = px.bar(
        weekly, x="_week", y="Листов", color="status_group",
        barmode="stack",
        color_discrete_map={"A": "#2E7D32", "B": "#A5D6A7"},
        labels={"_week": "Неделя", "status_group": "Статус",
                "Листов": "Листов"},
        title="Новые листы A/B по неделям",
    )
    fig.update_layout(
        height=400,
        xaxis_tickangle=-45,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    download_plotly(fig, "Листы_тренд_AB", "sh_trend")

    # Накопительный график
    weekly_cum = weekly.pivot(
        index="_week", columns="status_group", values="Листов"
    ).fillna(0).sort_index()
    weekly_cum["A_cum"] = weekly_cum.get("A", pd.Series()).cumsum()
    weekly_cum["B_cum"] = weekly_cum.get("B", pd.Series()).cumsum()

    fig2 = go.Figure()
    if "A_cum" in weekly_cum.columns:
        fig2.add_trace(go.Scatter(
            x=weekly_cum.index, y=weekly_cum["A_cum"],
            mode="lines+markers", name="🟢 A (накопительно)",
            line=dict(color="#2E7D32", width=3),
        ))
    if "B_cum" in weekly_cum.columns:
        fig2.add_trace(go.Scatter(
            x=weekly_cum.index, y=weekly_cum["B_cum"],
            mode="lines+markers", name="🟢 B (накопительно)",
            line=dict(color="#A5D6A7", width=3),
        ))
    fig2.update_layout(
        title="Накопительный рост листов A/B",
        height=400,
        xaxis_title="Неделя",
        yaxis_title="Листов",
        xaxis_tickangle=-45,
    )
    st.plotly_chart(fig2, use_container_width=True)
    download_plotly(fig2, "Листы_тренд_AB_cum", "sh_trend_cum")


# ---------------------------------------------------------------------------
#  Листы-блокеры (C + активные)
# ---------------------------------------------------------------------------
def _render_blockers(df: pd.DataFrame):
    st.markdown("### 🚧 Листы-блокеры")
    st.caption(
        "Листы в статусе **C**, по которым есть **активные замечания**. "
        "Это места, где работа реально стоит — нужно двигать."
    )

    if df.empty:
        st.info("Нет данных.")
        return

    # Листы C
    c_sheets = df[df["status_group"] == "C"].copy()
    if c_sheets.empty:
        st.success("🎉 Нет листов в статусе C.")
        return

    # Прикрепляем активные замечания
    active_by_sheet = _load_active_by_sheet()

    if active_by_sheet.empty:
        st.info("Нет активных замечаний по листам.")
        return

    # `leaf` уже нормализован в _load_all_sheets — просто merge
    blocked = c_sheets.merge(
        active_by_sheet[["leaf", "active_total", "ours_overdue"]],
        on="leaf", how="inner",
    )

    if blocked.empty:
        st.success("🎉 Нет листов в C с активными замечаниями.")
        return

    blocked = blocked.sort_values(
        ["ours_overdue", "active_total", "status_days"],
        ascending=[False, False, False],
    )

    # ---- KPI ----
    c1, c2, c3 = st.columns(3)
    c1.metric("Листов-блокеров", f"{len(blocked):,}".replace(",", " "))
    c2.metric(
        "С просрочками АТП ТЛП",
        f"{int((blocked['ours_overdue'] > 0).sum()):,}"
        .replace(",", " "),
    )
    c3.metric(
        "Средний возраст в C",
        f"{int(blocked['status_days'].dropna().mean())} дн."
        if blocked["status_days"].notna().any() else "—",
    )

    # ---- Топ-15 комплектов ----
    top_cx = (blocked.groupby("complex").size()
                 .reset_index(name="Блокеров")
                 .sort_values("Блокеров", ascending=True)
                 .tail(15))

    if not top_cx.empty:
        st.markdown("##### 🏗 Топ-15 комплектов по числу листов-блокеров")
        fig = px.bar(
            top_cx, x="Блокеров", y="complex", orientation="h",
            text="Блокеров",
            color_discrete_sequence=["#E57373"],
            labels={"complex": ""},
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(height=max(350, 25 * len(top_cx)))
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Листы_блокеры_комплекты", "sh_block_cx")

    # ---- Таблица ----
    st.markdown(f"##### 📋 Список листов-блокеров ({len(blocked):,})"
                .replace(",", " "))

    view = blocked[[
        "leaf", "complex", "discipline", "name",
        "status_days", "active_total", "ours_overdue",
    ]].rename(columns={
        "leaf": "Лист",
        "complex": "Комплект",
        "discipline": "Дисциплина",
        "name": "Название листа",
        "status_days": "Дней в C",
        "active_total": "Активных замечаний",
        "ours_overdue": "Просрочек АТП ТЛП",
    })

    st.dataframe(
        view.head(500), use_container_width=True, hide_index=True,
        height=500,
        column_config={
            "Название листа": st.column_config.TextColumn(width="large"),
            "Дней в C": st.column_config.NumberColumn(format="%d"),
            "Активных замечаний": st.column_config.NumberColumn(format="%d"),
            "Просрочек АТП ТЛП": st.column_config.NumberColumn(format="%d"),
        },
    )
    if len(view) > 500:
        st.caption(f"Показаны первые 500 из {len(view):,}."
                   .replace(",", " "))

    # =====================================================================
    #  Выгрузка в Excel
    # =====================================================================
    with st.expander("📥 Выгрузить список листов-блокеров в Excel",
                     expanded=False):
        export = view.copy()

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export.to_excel(writer, index=False,
                            sheet_name="Листы-блокеры")

            ws = writer.sheets["Листы-блокеры"]

            # Фиксация шапки
            ws.freeze_panes = "A2"

            # Автофильтр
            max_col_letter = _excel_col_letter(len(export.columns))
            ws.auto_filter.ref = (
                f"A1:{max_col_letter}{len(export) + 1}"
            )

            # Автоширина
            for col_idx, col_name in enumerate(export.columns, start=1):
                col_letter = _excel_col_letter(col_idx)
                max_len = max(
                    len(str(col_name)),
                    export[col_name].astype(str).str.len().max()
                    if len(export) else 0,
                )
                ws.column_dimensions[col_letter].width = min(
                    max_len + 2, 60)

            # Жирная шапка с заливкой
            from openpyxl.styles import Font, PatternFill, Alignment
            bold = Font(bold=True)
            header_fill = PatternFill(
                start_color="FFE0B2", end_color="FFE0B2",
                fill_type="solid",
            )
            for col_idx in range(1, len(export.columns) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = bold
                cell.fill = header_fill
                cell.alignment = Alignment(
                    horizontal="center", vertical="center",
                    wrap_text=True,
                )

            # Высота шапки — 2 строки
            ws.row_dimensions[1].height = 30

        buf.seek(0)

        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Листы_блокеры_{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
            key="sh_blockers_dl",
        )
        st.caption(
            f"В выгрузке: **{len(export):,}** листов-блокеров. "
            f"Шапка зафиксирована, автофильтр включён."
            .replace(",", " ")
        )


# ---------------------------------------------------------------------------
#  Комплекты в зоне риска (2 топа)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_complex_stats(df_sheets: pd.DataFrame) -> pd.DataFrame:
    """
    Сводка по комплектам: листы по группам + активные замечания + просрочки.
    """
    # Листы
    sheet_stats = []
    for cx, sub in df_sheets.groupby("complex"):
        if not cx:
            continue

        total = len(sub)
        a = int((sub["status_group"] == "A").sum())
        b = int((sub["status_group"] == "B").sum())
        c = int((sub["status_group"] == "C").sum())
        i = int((sub["status_group"] == "И").sum())
        annulled = int(
            sub["status_raw"].astype(str).str.strip().str.lower()
            .isin(["аннулировано", "annulled"]).sum()
        )
        working = total - i - annulled
        pct_ab = round((a + b) / working * 100, 1) if working > 0 else 0.0

        sheet_stats.append({
            "complex": cx,
            "discipline": sub["discipline"].iloc[0] if len(sub) else None,
            "docs_total": total,
            "doc_a": a,
            "doc_b": b,
            "doc_c": c,
            "doc_i": i,
            "doc_other": total - a - b - c - i,
            "pct_ab": pct_ab,
        })

    df_cx = pd.DataFrame(sheet_stats)

    # Активные замечания
    active = _load_active_df()
    if not active.empty:
        overdue_flags = [
            "new_overdue", "in_work_overdue",
            "rejected_overdue", "discussion_overdue",
        ]

        rows = []
        for cx, sub in active.groupby("complex"):
            rows.append({
                "complex": cx,
                "active_total": len(sub),
                "ours_overdue": sub[
                    sub["category_flag"].isin(overdue_flags)
                ].shape[0],
            })
        df_active = pd.DataFrame(rows)
        df_cx = df_cx.merge(df_active, on="complex", how="left").fillna(0)
    else:
        df_cx["active_total"] = 0
        df_cx["ours_overdue"] = 0

    return df_cx


def _render_risk_complexes(df_sheets: pd.DataFrame):
    st.markdown("### ⚠️ Комплекты в зоне риска")

    df_cx = _load_complex_stats(df_sheets)
    if df_cx.empty:
        st.info("Нет данных по комплектам.")
        return

    col1, col2 = st.columns(2)

    # ---- Топ-15: низкий % A+B + много активных ----
    with col1:
        st.markdown("##### 🎯 Мало A+B + много замечаний")
        st.caption("Комплекты с % A+B < 30% и активных ≥ 20.")

        sub1 = df_cx[
            (df_cx["pct_ab"] < 30) & (df_cx["active_total"] >= 20)
        ].nlargest(15, "active_total")

        if sub1.empty:
            st.success("Нет таких комплектов.")
        else:
            sub1 = sub1.sort_values("active_total", ascending=True)
            fig = px.bar(
                sub1, x="active_total", y="complex", orientation="h",
                text="active_total",
                color="pct_ab",
                color_continuous_scale=["#7F0000", "#E57373", "#FFD54F"],
                labels={"active_total": "Активных",
                        "complex": "",
                        "pct_ab": "% A+B"},
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                height=max(400, 28 * len(sub1)),
                coloraxis_showscale=True,
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Листы_риск_мало_AB", "sh_risk_ab",
                            width=1200, height=600)

    # ---- Топ-15: низкий % A+B + много просрочек АТП ТЛП ----
    with col2:
        st.markdown("##### 🚨 Мало A+B + просрочки АТП ТЛП")
        st.caption("Комплекты с % A+B < 50% и просрочек ≥ 10.")

        sub2 = df_cx[
            (df_cx["pct_ab"] < 50) & (df_cx["ours_overdue"] >= 10)
        ].nlargest(15, "ours_overdue")

        if sub2.empty:
            st.success("Нет таких комплектов.")
        else:
            sub2 = sub2.sort_values("ours_overdue", ascending=True)
            fig = px.bar(
                sub2, x="ours_overdue", y="complex", orientation="h",
                text="ours_overdue",
                color="pct_ab",
                color_continuous_scale=["#7F0000", "#E57373", "#FFD54F"],
                labels={"ours_overdue": "Просрочек",
                        "complex": "",
                        "pct_ab": "% A+B"},
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                height=max(400, 28 * len(sub2)),
                coloraxis_showscale=True,
            )
            st.plotly_chart(fig, use_container_width=True)
            download_plotly(fig, "Листы_риск_просрочки", "sh_risk_ov",
                            width=1200, height=600)


# ---------------------------------------------------------------------------
#  Drill-down: комплект / лист
# ---------------------------------------------------------------------------
def _load_sheet_comments(leaf: str) -> pd.DataFrame:
    """Все замечания по листу (из _load_all_categorized)."""
    from vitro.ui.deadlines import _load_all_categorized
    df = _load_all_categorized()
    if df.empty:
        return pd.DataFrame()

    # `sheet` уже нормализован в deadlines._load_all_categorized
    sub = df[df["sheet"] == leaf].copy()
    if sub.empty:
        return pd.DataFrame()

    return sub[[
        "id", "comment", "status", "author", "created",
        "fix_date", "category", "category_flag",
    ]].rename(columns={
        "id": "ID",
        "comment": "Замечание",
        "status": "Статус",
        "author": "Автор",
        "created": "Создано",
        "fix_date": "Наш ответ",
        "category": "Категория",
        "category_flag": "Категория (флаг)",
    })

def _render_drilldown(df_sheets: pd.DataFrame):
    st.markdown("### 🔍 Детали")

    tab_cx, tab_leaf = st.tabs(["🏗 По комплекту", "📄 По листу"])

    # =====================================================================
    #  По комплекту (с каскадными фильтрами)
    # =====================================================================
    with tab_cx:
        # ---- Каскадные фильтры: дисциплина → раздел → комплект ----
        c1, c2, c3 = st.columns(3)

        # Уникальные дисциплины из df_sheets
        disciplines = sorted(
            df_sheets["discipline"].dropna().unique().tolist()
        )

        with c1:
            sel_disc_labels = st.multiselect(
                "Дисциплина",
                options=disciplines,
                format_func=lambda c: f"{c} — {discipline_name(c)}",
                placeholder="Все дисциплины",
                key="sh_drill_disc",
            )
            sel_disc = sel_disc_labels

        # Разделы — только из выбранных дисциплин (или все)
        if sel_disc:
            sections_pool = df_sheets[
                df_sheets["discipline"].isin(sel_disc)
            ]
        else:
            sections_pool = df_sheets

        sections = sorted(
            sections_pool["section"].dropna().unique().tolist()
        )
        sections = [s for s in sections if s]  # убираем пустые

        with c2:
            sel_sections = st.multiselect(
                "Раздел",
                options=sections,
                placeholder="Все разделы",
                key="sh_drill_section",
            )

        # Комплекты — только из выбранных дисциплин/разделов
        complexes_pool = sections_pool
        if sel_sections:
            complexes_pool = complexes_pool[
                complexes_pool["section"].isin(sel_sections)
            ]

        complexes = sorted(
            complexes_pool["complex"].dropna().unique().tolist()
        )

        with c3:
            sel = st.selectbox(
                "Комплект",
                options=[""] + complexes,
                format_func=lambda x: x or "— выберите комплект —",
                key="sh_drill_cx",
                placeholder="Начните вводить шифр...",
            )

        if not sel:
            st.info(
                "💡 Выберите комплект в фильтре выше. "
                "Можно сузить список через дисциплину и раздел."
            )
        else:
            sub = df_sheets[df_sheets["complex"] == sel]
            _render_complex_details(sel, sub)

    # =====================================================================
    #  По листу (с каскадными фильтрами: дисциплина → раздел → комплект → лист)
    # =====================================================================
    with tab_leaf:
        # ---- Уровень 1: Дисциплина ----
        disciplines_leaf = sorted(
            df_sheets["discipline"].dropna().unique().tolist()
        )

        c1, c2, c3 = st.columns(3)

        with c1:
            sel_disc_leaf = st.multiselect(
                "Дисциплина",
                options=disciplines_leaf,
                format_func=lambda c: f"{c} — {discipline_name(c)}",
                placeholder="Все дисциплины",
                key="sh_leaf_disc",
            )

        # ---- Уровень 2: Раздел (зависит от дисциплины) ----
        if sel_disc_leaf:
            pool_after_disc = df_sheets[
                df_sheets["discipline"].isin(sel_disc_leaf)
            ]
        else:
            pool_after_disc = df_sheets

        sections_leaf = sorted([
            s for s in pool_after_disc["section"].dropna().unique().tolist()
            if s
        ])

        with c2:
            if sections_leaf:
                sel_section_leaf = st.multiselect(
                    "Раздел",
                    options=sections_leaf,
                    placeholder="Все разделы",
                    key="sh_leaf_section",
                )
            else:
                sel_section_leaf = []
                st.multiselect(
                    "Раздел",
                    options=[],
                    placeholder="Не применимы",
                    disabled=True,
                    key="sh_leaf_section_empty",
                )

        # ---- Уровень 3: Комплект (зависит от дисциплины/раздела) ----
        pool_after_sect = pool_after_disc
        if sel_section_leaf:
            pool_after_sect = pool_after_sect[
                pool_after_sect["section"].isin(sel_section_leaf)
            ]

        complexes_leaf = sorted(
            pool_after_sect["complex"].dropna().unique().tolist()
        )

        with c3:
            sel_cx_for_leaf = st.selectbox(
                "Комплект",
                options=[""] + complexes_leaf,
                format_func=lambda x: x or "— выберите комплект —",
                key="sh_leaf_cx",
                placeholder="Начните вводить шифр...",
            )

        if not sel_cx_for_leaf:
            st.info(
                "💡 Выберите комплект — потом появится список листов. "
                "Можно сузить список через дисциплину и раздел."
            )
            return

        # ---- Уровень 4: Статус листа (дополнительный фильтр) ----
        leaves_pool = df_sheets[
            df_sheets["complex"] == sel_cx_for_leaf
            ]

        # Список статусов, которые есть в этом комплекте
        available_statuses = sorted(
            leaves_pool["status_group"].dropna().unique().tolist()
        )
        # Сортируем по порядку: A, B, C, И, Прочие
        status_order = {"A": 0, "B": 1, "C": 2, "И": 3, "Прочие": 4}
        available_statuses = sorted(
            available_statuses,
            key=lambda x: status_order.get(x, 99),
        )

        c_status, c_search = st.columns([2, 2])
        with c_status:
            sel_status_leaf = st.multiselect(
                "Статус листа",
                options=available_statuses,
                default=available_statuses,  # по умолчанию все
                placeholder="Все статусы",
                key="sh_leaf_status",
                help="Фильтр по группе статуса: A / B / C / И / Прочие.",
            )

        # Применяем фильтр по статусу
        if sel_status_leaf:
            leaves_pool = leaves_pool[
                leaves_pool["status_group"].isin(sel_status_leaf)
            ]

        leaves = sorted(
            leaves_pool["leaf"].dropna().unique().tolist()
        )

        with c_search:
            sel_leaf = st.selectbox(
                "Лист",
                options=[""] + leaves,
                format_func=lambda x: x or "— выберите лист —",
                key="sh_leaf_select",
                placeholder="Начните вводить...",
            )

        if not leaves:
            st.warning(
                "⚠️ По выбранным фильтрам листов нет. "
                "Попробуйте расширить статус или выбрать другой комплект."
            )
            return

        if sel_leaf:
            _render_leaf_details(sel_leaf, df_sheets)


def _render_complex_details(complex_code: str, sub: pd.DataFrame):
    """KPI + список листов по комплекту."""
    total = len(sub)
    a = int((sub["status_group"] == "A").sum())
    b = int((sub["status_group"] == "B").sum())
    c = int((sub["status_group"] == "C").sum())
    i = int((sub["status_group"] == "И").sum())

    annulled = int(
        sub["status_raw"].astype(str).str.strip().str.lower()
        .isin(["аннулировано", "annulled"]).sum()
    )
    working = total - i - annulled
    pct_ab = round((a + b) / working * 100, 1) if working > 0 else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Всего листов", total)
    c2.metric("A", a)
    c3.metric("B", b)
    c4.metric("C", c)
    c5.metric("И", i)
    c6.metric("% A+B", f"{pct_ab}%")

    # Таблица листов
    st.markdown(f"##### 📋 Листы комплекта `{complex_code}`")

    view = sub[[
        "leaf", "name", "discipline", "status_raw",
        "status_days", "revision",
    ]].rename(columns={
        "leaf": "Лист",
        "name": "Название",
        "discipline": "Дисциплина",
        "status_raw": "Статус",
        "status_days": "Дней в статусе",
        "revision": "Ревизия",
    }).sort_values("Дней в статусе", ascending=False, na_position="last")

    st.dataframe(
        view, use_container_width=True, hide_index=True, height=500,
        column_config={
            "Название": st.column_config.TextColumn(width="large"),
            "Дней в статусе": st.column_config.NumberColumn(format="%d"),
        },
    )


# ---------------------------------------------------------------------------
#  Drill-down: детали листа
# ---------------------------------------------------------------------------
def _render_leaf_details(leaf: str, df_sheets: pd.DataFrame):
    """KPI + все замечания по листу."""
    # `leaf` уже нормализован — просто ищем совпадение
    row = df_sheets[df_sheets["leaf"] == leaf]
    if row.empty:
        st.info("Лист не найден.")
        return
    row = row.iloc[0]

    # =====================================================================
    #  KPI листа
    # =====================================================================
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Статус",
        row["status_raw"] or "—",
        help="Текущий статус листа в Витрокад.",
    )
    c2.metric(
        "Дней в статусе",
        f"{row['status_days']}"
        if pd.notna(row["status_days"]) else "—",
        help="Сколько дней лист находится в текущем статусе.",
    )
    c3.metric(
        "Ревизия",
        row["revision"] or "—",
        help="Номер текущей ревизии листа.",
    )
    c4.metric(
        "Комплект",
        row["complex"] or "—",
        help="Шифр комплекта, к которому относится лист.",
    )

    st.caption(
        f"**{row['name'] or leaf}** · "
        f"Дисциплина: **{row['discipline']}** · "
        f"Раздел: **{row['section'] or '—'}**"
    )

    st.divider()

    # =====================================================================
    #  Список замечаний по листу
    # =====================================================================
    st.markdown(f"##### 📋 Замечания по листу `{leaf}`")

    comments = _load_sheet_comments(leaf)
    if comments.empty:
        st.info("У листа нет замечаний.")
        return

    # Мини-KPI по замечаниям
    c1, c2, c3 = st.columns(3)
    c1.metric("Всего замечаний", len(comments))
    if "status" in comments.columns:
        active_mask = comments["status"].isin(
            ["Новое", "Принято в работу", "Не принято",
             "К обсуждению", "Выполнено"]
        )
        c2.metric(
            "Активных",
            int(active_mask.sum()),
            help="Замечания в 5 активных статусах (не закрытые "
                 "и не аннулированные).",
        )
        closed_mask = comments["status"] == "Закрыто"
        c3.metric("Закрыто", int(closed_mask.sum()))

    st.dataframe(
        comments, use_container_width=True, hide_index=True, height=500,
        column_config={
            "Замечание": st.column_config.TextColumn(width="large"),
        },
    )

    # =====================================================================
    #  Выгрузка в Excel
    # =====================================================================
    with st.expander("📥 Выгрузить замечания листа в Excel",
                     expanded=False):
        # Готовим данные для Excel
        export = comments.copy()

        # Человеческие названия колонок
        rename_map = {
            "ID": "ID",
            "comment": "Замечание",
            "status": "Статус замечания",
            "author": "Автор замечания",
            "created": "Создано",
            "fix_date": "Наш ответ",
            "category": "Категория",
            "category_flag": "Категория (флаг)",
        }
        # Переименовываем только те, что есть
        rename_existing = {
            k: v for k, v in rename_map.items() if k in export.columns
        }
        export = export.rename(columns=rename_existing)

        # Записываем в XLSX
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export.to_excel(writer, index=False, sheet_name="Замечания")

            # Автоширина
            ws = writer.sheets["Замечания"]
            for col_idx, col_name in enumerate(export.columns, start=1):
                col_letter = _excel_col_letter(col_idx)
                max_len = max(
                    len(str(col_name)),
                    export[col_name].astype(str).str.len().max()
                    if len(export) else 0,
                )
                ws.column_dimensions[col_letter].width = min(
                    max_len + 2, 60)

            # Фиксация шапки
            ws.freeze_panes = "A2"

            # Автофильтр
            max_col_letter = _excel_col_letter(len(export.columns))
            ws.auto_filter.ref = (
                f"A1:{max_col_letter}{len(export) + 1}"
            )

        buf.seek(0)

        # Безопасное имя файла (без запрещённых символов)
        safe_leaf = (
            leaf.replace("/", "_").replace("\\", "_")
            .replace(":", "_").replace("*", "_")
            .replace("?", "_").replace('"', "_")
            .replace("<", "_").replace(">", "_")
            .replace("|", "_")
        ) or "leaf"

        st.download_button(
            "⬇️ Скачать XLSX",
            data=buf.getvalue(),
            file_name=f"Замечания_листа_{safe_leaf}_"
                      f"{datetime.now():%Y%m%d}.xlsx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
            use_container_width=True,
            key=f"sheet_dl_{safe_leaf}",
        )
        st.caption(
            f"В выгрузке: **{len(export)}** замечаний по листу."
            .replace(",", " ")
        )


def _excel_col_letter(col_idx: int) -> str:
    """
    Преобразует 1-based индекс колонки в букву Excel:
      1 → A, 26 → Z, 27 → AA, 28 → AB и т. д.
    """
    result = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        result = chr(65 + rem) + result
    return result


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("📄 Листы")
    st.caption(
        "Контроль статусов листов РД: сколько утверждено, где блокеры, "
        "какие комплекты в зоне риска. Замечания — из единого источника, "
        "синхронизировано с дашбордом."
    )

    # Кнопка «Обновить» убрана — данные из кэша (TTL 1 час).

    # ---- Фильтр по дисциплине ----
    disc_options = _load_discipline_options()
    c_disc, _ = st.columns([2, 3])
    with c_disc:
        sel_disc_labels = st.multiselect(
            "Дисциплина",
            options=list(disc_options.values()),
            placeholder="Все дисциплины",
            key="sh_disc",
        )
        sel_disc = [code for code, label in disc_options.items()
                    if label in sel_disc_labels]

    with st.spinner("Загрузка данных по листам..."):
        df = _load_all_sheets()

    if df.empty:
        st.warning("Нет данных по листам. Запустите синхронизацию.")
        return

    # Применяем фильтр
    if sel_disc:
        df = df[df["discipline"].isin(sel_disc)].copy()
        if df.empty:
            st.info("Нет листов по заданным фильтрам.")
            return

    _render_kpi(df)

    st.divider()
    _render_by_discipline(df)

    st.divider()
    _render_weekly_trend(df)

    st.divider()
    _render_blockers(df)

    st.divider()
    _render_risk_complexes(df)

    st.divider()
    _render_drilldown(df)