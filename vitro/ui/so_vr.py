"""
📑 СО и ВОР — листы СО и ВР (патч 22.22).

Витрина статусов листов СО/ВР из таблицы documents_sovr.
Две подвкладки: основная (таблица, бублики) и сервисная (заглушка).

Структура вкладки:
  1. Фильтры — стандартные (Дисциплина/Раздел/Комплект) + статус листа + период.
  2. Бублики — СО и ВР (статусы + загружено/план) — всегда оба блока.
  3. Сводная таблица — строка = лист (complex + sheet_number).
  4. Экспорт в Excel.
  5. Сервисная подвкладка — заглушка (патч 22.23).
"""

from datetime import date, datetime

import io
import pandas as pd
import plotly.express as px
import streamlit as st
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from vitro._cache import versioned_cache
from vitro.sqlite_db import get_conn, load_sovr_table
from vitro.sheet_kind import normalize_sheet_number
from vitro.time_utils import fmt_msk

# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
SHEET_STATUS_COLORS = {
    "Статус A — утверждён": "#2ca02c",
    "Статус B — к сдаче":   "#ffdd57",
    "Статус C — в работе":  "#ff7f0e",
    "Другие статусы":       "#9467bd",
}

SHEET_STATUS_COLORS_LOADED = {
    "Загружено": "#2ca02c",
}

STATUS_OPTIONS = ["Не загружен", "A", "B", "C", "Другие"]

# ---------------------------------------------------------------------------
#  Кэшированные данные
# ---------------------------------------------------------------------------
@versioned_cache(3600)
def _load_sovr_data() -> pd.DataFrame:
    """Все строки витрины СО/ВР."""
    return load_sovr_table()


@versioned_cache(600)
def _load_sheets_sovr_template_count() -> int:
    """Количество строк в таблице-каркасе sheets_sovr_template."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sheets_sovr_template"
        ).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
#  Утилиты
# ---------------------------------------------------------------------------
def _fmt_date(v) -> str:
    """Формат даты ДД.ММ.ГГГГ."""
    if v is None or str(v).strip() == "":
        return "—"
    try:
        dt = datetime.fromisoformat(str(v)[:10])
        return dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(v)


def _fmt_num(n) -> str:
    """Число с пробелами-разделителями тысяч."""
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _classify_status(status: str) -> str:
    """Классифицирует статус листа: A / B / C / Другие."""
    s = (status or "").strip().upper()
    if s == "A":
        return "Статус A — утверждён"
    if s == "B":
        return "Статус B — к сдаче"
    if s == "C":
        return "Статус C — в работе"
    return "Другие статусы"


# ---------------------------------------------------------------------------
#  Фильтры
# ---------------------------------------------------------------------------
def _render_filters() -> dict:
    """Фильтры вкладки СО и ВОР: стандартные + статус + период."""
    from vitro.ui._filters import render_standard_filters

    def _extra_row():
        """Ряд 3: статус листа + период."""
        status = st.multiselect(
            "Статус листа",
            options=STATUS_OPTIONS,
            placeholder="Все",
            key="sovr_status",
        )
        st.caption(
            "Период по дате выдачи (send_date). "
            "Пустые даты исключаются при заданном периоде."
        )
        c1, c2 = st.columns(2)
        with c1:
            date_from = st.date_input(
                "Период: с",
                value=None,
                min_value=date(2020, 1, 1),
                max_value=date(2100, 12, 31),
                key="sovr_date_from",
            )
        with c2:
            date_to = st.date_input(
                "по",
                value=None,
                min_value=date(2020, 1, 1),
                max_value=date(2100, 12, 31),
                key="sovr_date_to",
            )
        return {
            "status": tuple(status),
            "date_from": date_from,
            "date_to": date_to,
        }

    std = render_standard_filters(
        key_prefix="sovr_",
        show_remark_status=False,
        show_sheet_status=False,
        extra_row=_extra_row,
    )

    return {
        "kits": std["kits"],
        "block_codes": std["block_codes"],
        "status": std["extra"].get("status", ()),
        "date_from": std["extra"].get("date_from"),
        "date_to": std["extra"].get("date_to"),
    }


def _apply_filters(df: pd.DataFrame, flt: dict) -> pd.DataFrame:
    """Применяет фильтры к DataFrame витрины."""
    if df.empty:
        return df

    out = df.copy()

    # Комплект (complex)
    kits = flt.get("kits", ())
    if kits:
        out = out[out["complex"].isin(kits)]

    # Расположение (4-й блок complex)
    block_codes = flt.get("block_codes", ())
    if block_codes:
        def _get_4th_block(val):
            if not val:
                return ""
            parts = str(val).split("-")
            return parts[3].strip().upper() if len(parts) >= 4 else ""
        out = out[out["complex"].apply(_get_4th_block).isin(block_codes)]

    # Статус листа
    if flt["status"]:
        statuses = flt["status"]
        if "Не загружен" in statuses:
            # "Не загружен" — это когда нет записи в витрине
            # В контексте витрины это пустая выборка (пока нет перечня)
            # Остальные статусы — обычные
            other_statuses = [s for s in statuses if s != "Не загружен"]
            if not other_statuses:
                return pd.DataFrame(columns=out.columns)
            out = out[out["status"].isin(other_statuses)]
        else:
            out = out[out["status"].isin(statuses)]

    # Период по send_date
    date_from = flt.get("date_from")
    date_to = flt.get("date_to")
    if date_from or date_to:
        # Исключаем пустые send_date
        out = out[out["send_date"].notna() & (out["send_date"].astype(str).str.strip() != "")]
        if not out.empty:
            iso = out["send_date"].astype(str).str.slice(0, 10)
            if date_from:
                out = out[iso >= date_from.isoformat()]
            if date_to:
                out = out[iso <= date_to.isoformat()]

    return out


# ---------------------------------------------------------------------------
#  Бублики
# ---------------------------------------------------------------------------
def _donut(fig_data: pd.DataFrame, names_col: str, values_col: str,
           key: str, center_total: str,
           color_map: dict | None = None) -> None:
    """Бублик в стиле Сводки: без легенды, проценты внутри."""
    if fig_data.empty or int(fig_data[values_col].sum()) == 0:
        st.caption("Нет данных для диаграммы.")
        return
    fig = px.pie(
        fig_data,
        names=names_col,
        values=values_col,
        hole=0.45,
        color=names_col,
        color_discrete_map=color_map or {},
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent",
        textfont_size=11,
    )
    fig.update_layout(
        showlegend=False,
        margin=dict(l=5, r=5, t=5, b=5),
        height=200,
        annotations=[dict(
            text=center_total,
            showarrow=False,
            font=dict(size=16, color="#1F4E78"),
            x=0.5, y=0.5,
        )],
    )
    st.plotly_chart(fig, width='stretch', key=key)


def _render_sovr_donuts(df: pd.DataFrame) -> None:
    """Бублики для СО и ВР — всегда оба блока."""
    for label, sn in [("СО", "СО"), ("ВР", "ВР")]:

        st.markdown(f"##### 🍩 {label}")

        # --- Бублик «Статусы» ---
        status_counts = df[df["sheet_number"] == sn].copy()
        if not status_counts.empty:
            status_counts["_status_bucket"] = status_counts["status"].apply(_classify_status)
            status_data = (
                status_counts.groupby("_status_bucket")
                .size()
                .reset_index(name="Количество")
            )
        else:
            status_data = pd.DataFrame(columns=["Группа", "Количество"])

        total_status = int(status_data["Количество"].sum()) if not status_data.empty else 0
        status_data = status_data[status_data["Количество"] > 0]

        st.markdown("###### Статусы")
        _donut(status_data, "Группа", "Количество",
               f"sovr_donut_{label.lower()}_status",
               center_total=_fmt_num(total_status),
               color_map=SHEET_STATUS_COLORS)

        # --- Бублик «Загружено / План» ---
        st.markdown("###### Загружено / План")
        loaded_count = total_status  # в витрине = загружено

        template_count = _load_sheets_sovr_template_count()

        if template_count == 0 and loaded_count > 0:
            # Перечень не загружен — показываем только фактическое «Загружено»
            st.caption(
                f"📦 Перечень не загружен — план не рассчитывается. "
                f"Загружено: **{loaded_count}** лист(ов)."
            )
            loaded_data = pd.DataFrame([{
                "Группа": "Загружено",
                "Количество": loaded_count,
            }])
            _donut(loaded_data, "Группа", "Количество",
                   f"sovr_donut_{label.lower()}_loaded",
                   center_total=_fmt_num(loaded_count),
                   color_map=SHEET_STATUS_COLORS_LOADED)
        elif template_count == 0:
            st.caption("Перечень не загружен — нет данных.")
        else:
            # Перечень загружен — считаем план
            plan_count = template_count
            st.markdown(
                f"Загружено: **{loaded_count}** · "
                f"План: **{plan_count}**"
            )
            loaded_data = pd.DataFrame([
                {"Группа": "Загружено", "Количество": loaded_count},
                {"Группа": "План к загрузке", "Количество": plan_count},
            ])
            loaded_data = loaded_data[loaded_data["Количество"] > 0]
            _donut(loaded_data, "Группа", "Количество",
                   f"sovr_donut_{label.lower()}_loaded",
                   center_total=_fmt_num(loaded_count + plan_count),
                   color_map={
                       "Загружено": SHEET_STATUS_COLORS_LOADED["Загружено"],
                       "План к загрузке": "#9467bd",
                   })


# ---------------------------------------------------------------------------
#  Сводная таблица
# ---------------------------------------------------------------------------
def _build_sovr_table(df: pd.DataFrame, flt: dict) -> pd.DataFrame:
    """Сборка сводной таблицы: строка = лист."""
    if df.empty:
        return pd.DataFrame()

    out = df.copy()

    # Группируем по (complex, sheet_number) — берём первую строку (уже дедуп)
    groups = out.groupby(["complex", "sheet_number"])

    rows = []
    for (complex_code, sheet_no), group in groups:
        row = group.iloc[0]
        rows.append({
            "complex": complex_code,
            "sheet_number": sheet_no,
            "status": row["status"],
            "send_date": row["send_date"],
            "status_date": row["status_date"],
            "workflow_id": row["workflow_id"],
            "revision": row["revision"],
            "name": row["name"],
            "doc_id": row["doc_id"],
        })

    result = pd.DataFrame(rows)

    # Сортировка
    result = result.sort_values(["complex", "sheet_number"]).reset_index(drop=True)
    return result


def _render_sovr_table(df: pd.DataFrame, flt: dict) -> None:
    """Отображение сводной таблицы."""
    table = _build_sovr_table(df, flt)

    if table.empty:
        st.info(
            "Нет данных по выбранным фильтрам. "
            "Если выбран «Не загружен» — таблица пустая (перечень не загружен)."
        )
        return

    # Разделяем на СО и ВР
    so_df = table[table["sheet_number"] == "СО"]
    vr_df = table[table["sheet_number"] == "ВР"]

    # --- Таблица СО ---
    if not so_df.empty:
        st.markdown("###### 📑 СО")
        so_display = so_df[["complex", "sheet_number", "name", "revision",
                            "status", "send_date", "status_date", "workflow_id"]].copy()
        so_display.columns = [
            "Комплект", "Номер листа", "Наименование", "Ревизия",
            "Статус", "Дата выдачи", "Дата получения статуса", "№ процесса",
        ]
        # Форматируем даты
        so_display["Дата выдачи"] = so_display["Дата выдачи"].apply(_fmt_date)
        so_display["Дата получения статуса"] = so_display["Дата получения статуса"].apply(_fmt_date)

        st.dataframe(
            so_display,
            width='stretch',
            hide_index=True,
            height=min(max(len(so_display) * 36 + 40, 200), 600),
            column_config={
                "Комплект": st.column_config.TextColumn(width="medium"),
                "Номер листа": st.column_config.TextColumn(width="small"),
                "Наименование": st.column_config.TextColumn(width="large"),
                "Ревизия": st.column_config.TextColumn(width="small"),
                "Статус": st.column_config.TextColumn(width="small"),
                "Дата выдачи": st.column_config.TextColumn(width="small"),
                "Дата получения статуса": st.column_config.TextColumn(width="small"),
                "№ процесса": st.column_config.TextColumn(width="small"),
            },
        )

    # --- Таблица ВР ---
    if not vr_df.empty:
        st.markdown("###### 📑 ВР")
        vr_display = vr_df[["complex", "sheet_number", "name", "revision",
                            "status", "send_date", "status_date", "workflow_id"]].copy()
        vr_display.columns = [
            "Комплект", "Номер листа", "Наименование", "Ревизия",
            "Статус", "Дата выдачи", "Дата получения статуса", "№ процесса",
        ]
        vr_display["Дата выдачи"] = vr_display["Дата выдачи"].apply(_fmt_date)
        vr_display["Дата получения статуса"] = vr_display["Дата получения статуса"].apply(_fmt_date)

        st.dataframe(
            vr_display,
            width='stretch',
            hide_index=True,
            height=min(max(len(vr_display) * 36 + 40, 200), 600),
            column_config={
                "Комплект": st.column_config.TextColumn(width="medium"),
                "Номер листа": st.column_config.TextColumn(width="small"),
                "Наименование": st.column_config.TextColumn(width="large"),
                "Ревизия": st.column_config.TextColumn(width="small"),
                "Статус": st.column_config.TextColumn(width="small"),
                "Дата выдачи": st.column_config.TextColumn(width="small"),
                "Дата получения статуса": st.column_config.TextColumn(width="small"),
                "№ процесса": st.column_config.TextColumn(width="small"),
            },
        )

    # Итого
    total = len(table)
    so_count = len(so_df)
    vr_count = len(vr_df)
    st.caption(
        f"Листов всего: **{total}** · СО: **{so_count}** · ВР: **{vr_count}**"
    )


# ---------------------------------------------------------------------------
#  Экспорт в Excel
# ---------------------------------------------------------------------------
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP = Alignment(wrap_text=True, vertical="top")


def _export_sovr_excel(df: pd.DataFrame, flt: dict) -> bytes:
    """Сводная таблица СО/ВР → Excel с форматированием."""
    from openpyxl import Workbook

    wb = Workbook()

    # Лист СО
    ws_so = wb.active
    ws_so.title = "СО"
    headers_so = ["Комплект", "Номер листа", "Наименование", "Ревизия",
                  "Статус", "Дата выдачи", "Дата получения статуса", "№ процесса"]
    for j, h in enumerate(headers_so, start=1):
        cell = ws_so.cell(row=1, column=j, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    so_df = df[df["sheet_number"] == "СО"] if not df.empty else pd.DataFrame()
    if not so_df.empty:
        for i, (_, row) in enumerate(so_df.iterrows(), start=2):
            for j, col_name in enumerate(["complex", "sheet_number", "name",
                                          "revision", "status", "send_date",
                                          "status_date", "workflow_id"], start=1):
                val = row[col_name]
                cell = ws_so.cell(row=i, column=j, value=val)
                cell.border = _BORDER
                cell.alignment = _WRAP
                # Форматируем даты
                if col_name in ("send_date", "status_date"):
                    cell.value = _fmt_date(val)

    # Ширина колонок
    for j in range(1, len(headers_so) + 1):
        ws_so.column_dimensions[get_column_letter(j)].width = 20

    ws_so.freeze_panes = "A2"
    if ws_so.max_row >= 2:
        ws_so.auto_filter.ref = f"A1:{get_column_letter(len(headers_so))}{ws_so.max_row}"

    # Лист ВР
    ws_vr = wb.create_sheet(title="ВР")
    headers_vr = headers_so
    for j, h in enumerate(headers_vr, start=1):
        cell = ws_vr.cell(row=1, column=j, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    vr_df = df[df["sheet_number"] == "ВР"] if not df.empty else pd.DataFrame()
    if not vr_df.empty:
        for i, (_, row) in enumerate(vr_df.iterrows(), start=2):
            for j, col_name in enumerate(["complex", "sheet_number", "name",
                                          "revision", "status", "send_date",
                                          "status_date", "workflow_id"], start=1):
                val = row[col_name]
                cell = ws_vr.cell(row=i, column=j, value=val)
                cell.border = _BORDER
                cell.alignment = _WRAP
                if col_name in ("send_date", "status_date"):
                    cell.value = _fmt_date(val)

    for j in range(1, len(headers_vr) + 1):
        ws_vr.column_dimensions[get_column_letter(j)].width = 20

    ws_vr.freeze_panes = "A2"
    if ws_vr.max_row >= 2:
        ws_vr.auto_filter.ref = f"A1:{get_column_letter(len(headers_vr))}{ws_vr.max_row}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Рендер
# ---------------------------------------------------------------------------
def render() -> None:
    """Основной рендер вкладки СО и ВОР."""
    flt = _render_filters()

    # Загрузка данных
    df = _load_sovr_data()

    # Применяем фильтры
    df_filtered = _apply_filters(df, flt)

    # Бублики (оба блока всегда)
    _render_sovr_donuts(df_filtered)

    # Сводная таблица
    _render_sovr_table(df_filtered, flt)

    # Экспорт
    if not df_filtered.empty:
        excel_bytes = _export_sovr_excel(df_filtered, flt)
        st.download_button(
            "📥 Выгрузить в Excel",
            data=excel_bytes,
            file_name=f"СО_и_ВР_{datetime.now():%Y%m%d_%H%M}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='content',
            key="sovr_export_excel",
            help="Выгрузка витрины статусов СО/ВР в Excel.",
        )


def render_service() -> None:
    """Сервисная подвкладка — заглушка (патч 22.23)."""
    st.info("⚙️ Сервисная подвкладка в разработке (патч 22.23).")
    st.caption(
        "Здесь будет функционал обновления статусов СО/ВР из Витро, "
        "загрузки Excel-перечня и управления данными."
    )
