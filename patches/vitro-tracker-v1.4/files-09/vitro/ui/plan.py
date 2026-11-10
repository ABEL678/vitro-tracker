# vitro/ui/plan.py
"""
📅 План выдачи комплектов — планирование выдачи РД Заказчику.

Формирует план выдачи ВСЕХ комплектов (загруженных в Витро и планируемых
к загрузке) на основании вводных данных:
  - «Ведомость основных комплектов» (Excel) — шифр, наименование, статус,
    срок готовности.
  - «Ведомости чертежей каждого комплекта» (Excel) — номер листа,
    наименование, плановая дата. Поддерживаются как шаблон, так и реальные
    регистры «Комплекты чертежей» (ШИФР / Тип чертежа / Номер листа /
    Название листа / Шифр листа, шапки комплектов, приложения без шифра,
    латинские двойники в шифрах).

Структура вкладки (сверху вниз):
  0. 📥 Вводные данные — импорт ведомостей Excel (свёрнут, без шаблонов).
  1. Фильтры — единый стандарт (как в «Сводке по проекту»).
  2. 📆 Период «с / по» — сразу под фильтрами; применяется к сводной
     таблице (по «Сроку готовности» комплекта) и к детальному плану
     по листам (по «Плановой дате» листа).
  3. 📋 Общая таблица (сводная): группировка «Дисциплина → Раздел»
     строками-заголовками (как в Сводке), внутри групп — комплекты;
     колонки: шифр, наименование, общий статус, листов в Витро,
     листов (план), срок готовности (редактируемый); внизу — строка
     «ИТОГО» с суммами. Единственная таблица комплектов: детальная
     таблица по комплектам удалена как дублирующая сводную (патч 09).
  4. Бублики: «Комплектов всего/план» и «Листов всего/план» — по всему
     отфильтрованному отбору (без периода). «Листов всего» =
     max(листов по плану, листов в Витро) — листы комплектов, которых
     нет в ведомостях чертежей, и превышения плана не теряются
     (патч 09); при превышении показывается «сверх плана».
  5. Детальный план по листам комплекта — позиции plan_drawings:
     дисциплина, раздел, комплект, номер и наименование листа,
     плановая дата, фактическая дата (из Витро) и статус выдачи
     («Выдан» — есть фактическая дата, «Планируется» — нет).
  6. 📥 Выгрузка в Excel — лист «Листы» (таблица выше) + «Параметры
     выгрузки», с учётом фильтров и выбранного периода.

Данные плана хранятся в SQLite: plan_kits, plan_drawings (sqlite_db.py).
"""

from datetime import date, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import (
    get_conn,
    load_plan_kits,
    load_plan_drawings,
    upsert_plan_kits,
    replace_plan_drawings_for_kits,
    update_plan_due_dates,
    clear_plan,
    log_event,
)
from vitro._cache import versioned_cache
from vitro.ui._auth import ensure_user
from vitro.ui._filters import (
    _extract_2nd_block,
    _extract_4th_block,
    load_kit_options as _load_kit_options,
    render_standard_filters,
)
from vitro.plan_xlsx import (
    MIME_XLSX,
    build_plan_export,
    parse_drawings_workbook,
    parse_kits_workbook,
)

# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
KIT_STATUS_OPTIONS = [
    "Загружен в Витро",
    "Загружается (частично)",
    "План (не загружен)",
    "Только в Витро",
]

KIT_STATUS_COLORS = {
    "Загружен в Витро":        "#2ca02c",
    "Загружается (частично)":  "#ffdd57",
    "План (не загружен)":      "#9467bd",
    "Только в Витро":          "#1f77b4",
}

# Колонки сводной таблицы (требование ревизии: без «Дисциплина»/«Раздел» —
# они стали заголовками групп).
OVERVIEW_COLUMNS = [
    "Шифр комплекта", "Наименование комплекта", "Общий статус",
    "Листов в Витро", "Листов (план)", "Срок готовности",
]

# Детальный план по листам комплекта — единственная детальная таблица
# (таблица А по комплектам удалена в патче 09: дублировала сводную).
SHEET_PLAN_COLUMNS = [
    "Дисциплина", "Раздел", "Комплект(ы)", "Номер листа",
    "Наименование листа", "Плановая дата", "Фактическая дата",
    "Статус выдачи",
]

# Значения колонки «Статус выдачи» (таблица Б).
STATUS_ISSUED = "Выдан"          # фактическая дата в Витро есть
STATUS_PLANNED = "Планируется"   # фактической даты нет

# Служебные префиксы строк сводной таблицы (не комплекты — не сохраняются).
_GROUP_MARK = "📁"
_TOTAL_MARK = "ИТОГО"

MAX_DETAIL_ROWS = 2000  # строки детальных таблиц сверх лимита — в Excel


# ---------------------------------------------------------------------------
#  Данные Витро
# ---------------------------------------------------------------------------
@versioned_cache(600)
def _vitro_sheet_counts() -> dict[str, int]:
    """{шифр комплекта: количество листов в Витро}."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT complex, COUNT(*) AS n
            FROM documents
            WHERE complex IS NOT NULL
            GROUP BY complex
        """).fetchall()
    return {r["complex"]: int(r["n"]) for r in rows if r["complex"]}


@versioned_cache(600)
def _vitro_kit_meta() -> dict[str, dict]:
    """{шифр: {name, discipline, section}} — справочник комплектов Витро."""
    meta: dict[str, dict] = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT code, name, discipline FROM complexes"
        ).fetchall():
            meta[r["code"]] = {
                "name": r["name"] or "",
                "discipline": r["discipline"] or "",
                "section": "",
            }
        for r in conn.execute("""
            SELECT complex, discipline, section
            FROM documents WHERE complex IS NOT NULL
        """).fetchall():
            code = r["complex"]
            if code not in meta:
                meta[code] = {
                    "name": "",
                    "discipline": r["discipline"] or "",
                    "section": r["section"] or "",
                }
            else:
                if not meta[code]["discipline"]:
                    meta[code]["discipline"] = r["discipline"] or ""
                if not meta[code]["section"]:
                    meta[code]["section"] = r["section"] or ""
    return meta


@versioned_cache(600)
def _vitro_sheet_dates() -> dict[tuple[str, str], str]:
    """
    {(шифр комплекта, номер листа): дата статуса в Витро} — фактическая
    дата загрузки листа (MAX по ревизиям). Для колонки «Фактическая дата»
    таблицы Б.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT complex, sheet_number, MAX(status_date) AS d
            FROM documents
            WHERE complex IS NOT NULL
              AND TRIM(COALESCE(sheet_number, '')) != ''
              AND status_date IS NOT NULL
              AND TRIM(COALESCE(status_date, '')) != ''
            GROUP BY complex, sheet_number
        """).fetchall()
    return {
        (r["complex"], str(r["sheet_number"]).strip()): str(r["d"])
        for r in rows if r["d"]
    }


def _iso_to_date(v) -> date | None:
    """'YYYY-MM-DD[...]' / datetime.date → date; иначе None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        if v is not None and pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
#  Общая (сводная) таблица
# ---------------------------------------------------------------------------
def _build_overview(include_vitro_only: bool) -> pd.DataFrame:
    """
    Одна строка = один комплект: план (ведомости) + Витро.
    Общий статус вычисляется: Загружен в Витро / Загружается (частично) /
    План (не загружен) / Только в Витро.
    """
    plan = load_plan_kits()
    drawings = load_plan_drawings()
    vitro_counts = _vitro_sheet_counts()
    plan_qty = {}
    if not drawings.empty:
        plan_qty = drawings.groupby("kit_code")["qty"].sum().to_dict()

    rows = []
    if not plan.empty:
        for _, r in plan.iterrows():
            code = r["code"]
            in_vitro = code in vitro_counts
            sheets_vitro = vitro_counts.get(code, 0)
            sheets_plan = int(plan_qty.get(code, 0))
            if in_vitro and sheets_plan > 0 and sheets_vitro < sheets_plan:
                status = "Загружается (частично)"
            elif in_vitro:
                status = "Загружен в Витро"
            else:
                status = "План (не загружен)"
            rows.append({
                "code": code,
                "name": r["name"] or "",
                "discipline": r["discipline"] or "",
                "section": r["section"] or "",
                "plan_status": r["plan_status"] or "",
                "due_date": r["due_date"] or None,
                "sheets_vitro": sheets_vitro,
                "sheets_plan": sheets_plan,
                "status": status,
                "in_vitro": in_vitro,
                "updated_at": r["updated_at"] or "",
                "updated_by": r["updated_by"] or "",
            })

    if include_vitro_only:
        meta = _vitro_kit_meta()
        for code in sorted(vitro_counts):
            if code in set(r["code"] for r in rows):
                continue
            info = meta.get(code, {})
            rows.append({
                "code": code,
                "name": info.get("name", ""),
                "discipline": info.get("discipline", ""),
                "section": info.get("section", ""),
                "plan_status": "",
                "due_date": None,
                "sheets_vitro": vitro_counts[code],
                "sheets_plan": 0,
                "status": "Только в Витро",
                "in_vitro": True,
                "updated_at": "",
                "updated_by": "",
            })

    if not rows:
        return pd.DataFrame(columns=["code"])

    df = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    return df


def _overview_to_grouped_display(df: pd.DataFrame
                                 ) -> tuple[pd.DataFrame, set]:
    """
    Внутренний DataFrame → сводная таблица с группировкой
    «Дисциплина → Раздел» (как в Сводке по проекту):

        📁 01_АР · Подземная часть        (заголовок группы + суммы)
        АТ-РД-АР1-С-00   Архитектура …    (комплекты группы)
        …
        ИТОГО                             (суммы по всему отбору)

    Колонки — только OVERVIEW_COLUMNS («Дисциплина»/«Раздел» стали
    заголовками групп). Срок готовности — тип date (для DateColumn).

    Возвращает (display_df, valid_codes) — valid_codes нужен
    _save_due_dates, чтобы служебные строки (📁… / ИТОГО) не попадали
    в UPDATE.
    """
    if df.empty:
        return pd.DataFrame(columns=OVERVIEW_COLUMNS), set()

    def _sort_key(val: str) -> tuple[int, str]:
        v = str(val or "")
        return (1, "") if v == "" else (0, v)

    groups: list[tuple[str, str, pd.DataFrame]] = []
    for (disc, sect), sub in df.groupby(
            ["discipline", "section"], dropna=False, sort=True):
        groups.append((str(disc or ""), str(sect or ""), sub))
    groups.sort(key=lambda g: (_sort_key(g[0]), _sort_key(g[1])))

    out_rows = []
    valid_codes: set = set()
    sum_vitro = sum_plan = 0

    for disc, sect, sub in groups:
        disc_label = disc if disc else "— без дисциплины"
        sect_label = sect if sect else "— без раздела"
        out_rows.append({
            "Шифр комплекта":
                f"{_GROUP_MARK} {disc_label} · {sect_label}",
            "Наименование комплекта": f"комплектов: {len(sub)}",
            "Общий статус": "",
            "Листов в Витро": int(sub["sheets_vitro"].astype(int).sum()),
            "Листов (план)": int(sub["sheets_plan"].astype(int).sum()),
            "Срок готовности": None,
        })
        for _, r in sub.iterrows():
            valid_codes.add(r["code"])
            sum_vitro += int(r["sheets_vitro"])
            sum_plan += int(r["sheets_plan"])
            out_rows.append({
                "Шифр комплекта": r["code"],
                "Наименование комплекта": r["name"] or "",
                "Общий статус": r["status"] or "",
                "Листов в Витро": int(r["sheets_vitro"]),
                "Листов (план)": int(r["sheets_plan"]),
                "Срок готовности": _iso_to_date(r["due_date"]),
            })

    out_rows.append({
        "Шифр комплекта": _TOTAL_MARK,
        "Наименование комплекта": f"всего комплектов: {len(valid_codes)}",
        "Общий статус": "",
        "Листов в Витро": sum_vitro,
        "Листов (план)": sum_plan,
        "Срок готовности": None,
    })

    return pd.DataFrame(out_rows, columns=OVERVIEW_COLUMNS), valid_codes


def _norm_editor_date(v):
    """Нормализация даты из data_editor для сравнения:
    None / NaT / NaN → None, datetime → date."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date()
    return v


def _current_user() -> str:
    """Имя текущего пользователя (отдельная функция — для тестов)."""
    return st.session_state.get("user", "")


def _save_due_dates(display_before: pd.DataFrame,
                    display_after: pd.DataFrame,
                    valid_codes: set) -> int:
    """Разница сроков готовности → UPDATE в БД. Строки-заголовки групп
    (📁…) и «ИТОГО» игнорируются. None/NaT считаются одинаковыми —
    иначе незатронутые пустые ячейки дают ложные отличия и вкладка
    зацикливается на сохранении (см. патч 07). Возвращает число строк
    (или -1, если требуется имя пользователя)."""
    changes: dict[str, str] = {}
    for i in range(len(display_after)):
        code = display_after.iloc[i]["Шифр комплекта"]
        if code not in valid_codes:
            continue  # служебная строка (группа / ИТОГО) — не комплект
        old = _norm_editor_date(display_before.iloc[i]["Срок готовности"])
        new = _norm_editor_date(display_after.iloc[i]["Срок готовности"])
        if old != new:
            changes[code] = None if new is None else new.isoformat()
    if not changes:
        return 0
    user = _current_user()
    if not user or user == "инженер":
        return -1  # требуется имя пользователя
    return update_plan_due_dates(changes, user)


def _render_overview_table(overview_filtered: pd.DataFrame,
                           date_from=None, date_to=None) -> None:
    """Секция 3. Сводная таблица комплектов: группировка по
    «Дисциплина → Раздел», строка «ИТОГО», редактируемый срок.
    Период «с/по» фильтрует комплекты по «Сроку готовности»
    (патч 09)."""
    st.markdown("##### 📋 Общая таблица комплектов (сводная)")

    overview_period = _apply_period_to_overview(
        overview_filtered, date_from, date_to)

    if overview_period.empty:
        if overview_filtered.empty:
            st.info(
                "Нет комплектов по выбранным фильтрам. Разверните блок "
                "«📥 Вводные данные» и загрузите ведомости Excel."
            )
        else:
            st.info(
                "Нет комплектов со сроком готовности в выбранном периоде "
                f"({_period_label(date_from, date_to)}). Комплекты без "
                "срока при активном периоде скрываются."
            )
        return

    display_before, valid_codes = _overview_to_grouped_display(
        overview_period)

    edited = st.data_editor(
        display_before,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=min(max(len(display_before) * 42 + 40, 300), 700),
        column_config={
            "Шифр комплекта": st.column_config.TextColumn(width="medium"),
            "Наименование комплекта": st.column_config.TextColumn(
                width="large"),
            "Общий статус": st.column_config.TextColumn(width="small"),
            "Листов в Витро": st.column_config.NumberColumn(width="small"),
            "Листов (план)": st.column_config.NumberColumn(width="small"),
            "Срок готовности": st.column_config.DateColumn(
                "Срок готовности",
                format="DD.MM.YYYY",
                width="small",
                help="Редактируется в строках комплектов — изменения "
                     "сохраняются автоматически.",
            ),
        },
        disabled=[
            "Шифр комплекта", "Наименование комплекта", "Общий статус",
            "Листов в Витро", "Листов (план)",
        ],
        key="plan_overview_editor",
    )

    n = _save_due_dates(display_before, edited, valid_codes)
    if n > 0:
        # БЕЗ st.rerun(): повторный реран сразу после сохранения вызывал
        # зацикливание вкладки (см. патч 07). Значения уже отображаются
        # в редакторе, БД обновлена — остальное подтянется при следующем
        # естественном реране.
        st.toast(f"Срок готовности обновлён: {n} компл.")
    elif n == -1:
        st.warning(
            "✏️ Изменены сроки готовности. Введите имя, чтобы сохранить: "
            "нажмите кнопку ниже."
        )
        if st.button("💾 Сохранить сроки готовности", type="primary",
                     key="plan_save_due_btn"):
            user = ensure_user()
            if user:
                changed = _save_due_dates(display_before, edited, valid_codes)
                if changed > 0:
                    st.toast(f"Срок готовности сохранён: {changed} компл.")
                st.rerun()

    st.caption(
        f"Комплектов: **{len(overview_period)}** · "
        f"листов в Витро: **{int(overview_period['sheets_vitro'].sum())}** · "
        f"листов (план): **{int(overview_period['sheets_plan'].sum())}**. "
        "Группировка: Дисциплина → Раздел (строки «📁…» — заголовки групп, "
        "«ИТОГО» — суммы по отбору). Срок готовности редактируется "
        "в строках комплектов."
    )
    if date_from or date_to:
        st.caption(
            f"Учтён период: **{_period_label(date_from, date_to)}** "
            "(по «Сроку готовности»)."
        )


# ---------------------------------------------------------------------------
#  Фильтры (единый стандарт + статус комплекта)
# ---------------------------------------------------------------------------
def _plan_kit_options_fn(disc, sect, block_codes) -> dict[str, str]:
    """
    Комплекты для каскада: справочник Витро + комплекты из плана
    (их ещё нет в Витро, но они есть в ведомостях).
    """
    base = _load_kit_options(disc, sect, block_codes)
    plan = load_plan_kits()
    if plan.empty:
        return base
    for _, r in plan.iterrows():
        code = r["code"]
        if disc and (r["discipline"] or "") not in disc:
            continue
        if sect and (r["section"] or "") not in sect:
            continue
        if block_codes and _extract_4th_block(code) not in block_codes:
            continue
        if code not in base:
            base[code] = f"{code} — {r['name']}" if r["name"] else code
    return dict(sorted(base.items()))


def _render_filters() -> dict:
    """
    Единый стандарт фильтров (как в Сводке) + фильтр «Статус комплекта»
    и «Только с датами» в 3-м ряду.
    """
    def _plan_extra_row():
        c1, c2 = st.columns(2)
        with c1:
            kit_status = st.multiselect(
                "Статус комплекта",
                options=KIT_STATUS_OPTIONS,
                placeholder="Все",
                key="plan_kit_status",
                help="«План (не загружен)» — комплект ещё не загружен "
                     "в Витро. «Только в Витро» — нет в ведомостях.",
            )
        with c2:
            only_dated = st.checkbox(
                "Только позиции со сроками",
                value=False,
                key="plan_only_dated",
                help="Скрыть позиции без срока готовности и без плановой даты.",
            )
        return {
            "kit_status": tuple(kit_status),
            "only_dated": bool(only_dated),
        }

    return render_standard_filters(
        key_prefix="plan_",
        expander_label="Фильтры",
        expanded=True,
        show_remark_status=False,
        show_sheet_status=False,
        kit_options_fn=_plan_kit_options_fn,
        extra_row=_plan_extra_row,
    )


def _render_period_block() -> tuple[date | None, date | None]:
    """
    Секция 2. Период «с / по» — сразу под блоком фильтров, до общей
    таблицы. Применяется к сводной таблице (по «Сроку готовности»)
    и к детальному плану по листам (по «Плановой дате»).
    """
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        date_from = st.date_input(
            "Период: с",
            value=None,
            min_value=date(2020, 1, 1),
            max_value=date(2100, 12, 31),
            key="plan_date_from",
            help="Период применяется к сводной таблице и к плану по "
                 "листам: у комплекта — срок готовности, у листа — "
                 "плановая дата (или срок комплекта, если у листа даты "
                 "нет). Бублики от периода не зависят.",
        )
    with c2:
        date_to = st.date_input(
            "по",
            value=None,
            min_value=date(2020, 1, 1),
            max_value=date(2100, 12, 31),
            key="plan_date_to",
        )
    with c3:
        st.caption(
            "Применяется к сводной таблице (по «Сроку готовности» "
            "комплекта) и к плану по листам (по «Плановой дате» листа). "
            "Пустые даты при активном периоде скрываются. Бублики "
            "от периода не зависят."
        )
    return date_from, date_to


def _apply_overview_filters(overview: pd.DataFrame, flt: dict) -> pd.DataFrame:
    """Стандартные фильтры + статус комплекта → к общей таблице."""
    if overview.empty:
        return overview
    out = overview
    if flt["disc"]:
        out = out[out["discipline"].isin(flt["disc"])]
    if flt["section"]:
        out = out[out["section"].isin(flt["section"])]
    if flt["kits"]:
        out = out[out["code"].isin(flt["kits"])]
    if flt["block_codes"]:
        blocks = out["code"].apply(_extract_4th_block)
        out = out[blocks.isin(flt["block_codes"])]
    if flt["marka"]:
        m = flt["marka"].strip().upper()
        if m:
            out = out[out["code"].apply(_extract_2nd_block) == m]
    kit_status = flt["extra"].get("kit_status", ())
    if kit_status:
        out = out[out["status"].isin(kit_status)]
    return out


def _apply_period_to_overview(df: pd.DataFrame,
                              date_from, date_to) -> pd.DataFrame:
    """Период «с/по» → фильтр сводной таблицы по «Сроку готовности».

    При активном периоде остаются только комплекты, чей срок попадает
    в диапазон (границы включительно); комплекты без срока скрываются —
    иначе «ИТОГО» считал бы позиции вне периода (патч 09).
    """
    if df.empty or not (date_from or date_to):
        return df
    out = df[df["due_date"].notna()]
    if out.empty:
        return out
    iso = out["due_date"].astype(str).str.slice(0, 10)
    if date_from:
        out = out[iso >= date_from.isoformat()]
    if date_to:
        out = out[iso <= date_to.isoformat()]
    return out


def _plan_totals(overview_filtered: pd.DataFrame) -> dict:
    """Сводные числа для бубликов — по всему отфильтрованному отбору
    (без учёта периода).

    Листов всего = max(сумма листов по плану, сумма листов в Витро):
    листы комплектов, которых нет в ведомостях чертежей («Только в
    Витро»), и листы сверх плана (повторы, извещения) не теряются —
    иначе бублик показывал меньше, чем Сводка по проекту (патч 09:
    было 9 142 вместо 12 880).

    Возвращает словарь:
      kits_total / kits_loaded / kits_plan — комплектов всего, в Витро,
                                             план к загрузке;
      sheets_total / sheets_vitro / sheets_plan — листов всего (max),
                                             в Витро, по плану;
      sheets_excess — листов сверх плана (Витро − план, ≥ 0);
      sheets_remaining — осталось по плану (план − Витро, ≥ 0);
      sheets_done — выдано (total − remaining; при преимуществе плана
                    совпадает с «в Витро»)."""
    if overview_filtered.empty:
        return {k: 0 for k in (
            "kits_total", "kits_loaded", "kits_plan", "sheets_total",
            "sheets_vitro", "sheets_plan", "sheets_excess",
            "sheets_remaining", "sheets_done")}
    n_total = len(overview_filtered)
    n_loaded = int(overview_filtered["in_vitro"].astype(bool).sum())
    sheets_vitro = int(overview_filtered["sheets_vitro"].astype(int).sum())
    sheets_plan = int(overview_filtered["sheets_plan"].astype(int).sum())
    sheets_total = max(sheets_vitro, sheets_plan)
    sheets_excess = max(0, sheets_vitro - sheets_plan)
    sheets_remaining = max(0, sheets_plan - sheets_vitro)
    return {
        "kits_total": n_total,
        "kits_loaded": n_loaded,
        "kits_plan": n_total - n_loaded,
        "sheets_total": sheets_total,
        "sheets_vitro": sheets_vitro,
        "sheets_plan": sheets_plan,
        "sheets_excess": sheets_excess,
        "sheets_remaining": sheets_remaining,
        "sheets_done": sheets_total - sheets_remaining,
    }


# ---------------------------------------------------------------------------
#  Бублики
# ---------------------------------------------------------------------------
def _donut(fig_data: pd.DataFrame, names_col: str, values_col: str,
           key: str, center_total: str) -> None:
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
        color_discrete_map=KIT_STATUS_COLORS,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent",
        textfont_size=11,
    )
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=11)),
        margin=dict(l=5, r=5, t=5, b=5),
        height=220,
        annotations=[dict(
            text=center_total,
            showarrow=False,
            font=dict(size=16, color="#1F4E78"),
            x=0.5, y=0.5,
        )],
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _render_donuts(overview_filtered: pd.DataFrame) -> tuple[int, int]:
    """Секция 4. Бублики «Комплектов всего/план» и «Листов всего/план».

    Числа — из _plan_totals: по всему отфильтрованному отбору
    (без периода), включая комплекты «Только в Витро». «Листов всего»
    = max(план, Витро) — см. патч 09.
    """
    st.markdown("##### 🎯 Комплектов и листов: всего / план")

    totals = _plan_totals(overview_filtered)
    if totals["kits_total"] == 0:
        st.info("Нет комплектов по выбранным фильтрам.")
        return (0, 0)

    # ---- КОМПЛЕКТЫ: все отфильтрованные, включая «Только в Витро» ----
    n_total = totals["kits_total"]
    n_loaded = totals["kits_loaded"]
    n_plan = totals["kits_plan"]

    kits_data = pd.DataFrame([
        {"Группа": "Загружено в Витро", "Количество": n_loaded},
        {"Группа": "План к загрузке", "Количество": n_plan},
    ])
    kits_data = kits_data[kits_data["Количество"] > 0]

    # ---- ЛИСТЫ: «всего» = max(план ведомостей, фактически в Витро) ----
    # Листы комплектов без ведомостей чертежей и превышения плана
    # (повторы, извещения) не теряются: при преимуществе Витро показываем
    # «сверх плана», при преимуществе плана — «осталось по плану».
    sheets_total = totals["sheets_total"]
    sheets_done = totals["sheets_done"]
    sheets_remaining = totals["sheets_remaining"]
    sheets_excess = totals["sheets_excess"]

    sheets_data = pd.DataFrame([
        {"Группа": "Выдано (в Витро)", "Количество": sheets_done},
        {"Группа": "Осталось по плану", "Количество": sheets_remaining},
    ])
    sheets_data = sheets_data[sheets_data["Количество"] > 0]

    col1, col2 = st.columns(2)
    with col1:
        st.caption(
            f"Комплектов всего: **{n_total}** · "
            f"загружено **{n_loaded}** · план **{n_plan}**"
        )
        _donut(kits_data, "Группа", "Количество", "plan_pie_kits",
               center_total=f"{n_total}")
    with col2:
        cap = (f"Листов всего: **{sheets_total}** · "
               f"в Витро: **{totals['sheets_vitro']}** · "
               f"в плане: **{totals['sheets_plan']}**")
        if sheets_excess:
            cap += f" · сверх плана: **{sheets_excess}**"
        st.caption(cap)
        _donut(sheets_data, "Группа", "Количество", "plan_pie_sheets",
               center_total=f"{sheets_total}")

    if sheets_excess:
        st.caption(
            f"ℹ️ В Витро на **{sheets_excess}** лист(ов) больше планового "
            "количества (комплекты без ведомостей чертежей, повторы, "
            "извещения, приложения) — они включены в «Листов всего», "
            "чтобы сумма совпадала с Сводкой по проекту."
        )
    return (n_total, sheets_total)


# ---------------------------------------------------------------------------
#  Таблица Б. Детальный план по листам комплекта
# ---------------------------------------------------------------------------
def _build_sheet_plan_table(overview_filtered: pd.DataFrame,
                            flt: dict,
                            date_from, date_to) -> pd.DataFrame:
    """
    Таблица Б: позиции листов (plan_drawings) комплектов, прошедших
    фильтры. Дата позиции = плановая дата листа, а если её нет —
    срок готовности комплекта. «Фактическая дата» — дата статуса листа
    в Витро (если лист загружен; MAX по ревизиям).
    """
    if overview_filtered.empty:
        return pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

    drawings = load_plan_drawings()
    if drawings.empty:
        return pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

    ov = overview_filtered[[
        "code", "status", "due_date", "discipline", "section",
    ]].rename(columns={"discipline": "kit_discipline",
                       "section": "kit_section"})
    df = drawings.merge(
        ov, left_on="kit_code", right_on="code", how="inner",
    )
    df = df.rename(columns={"kit_code": "Комплект(ы)",
                            "title": "Наименование листа"})
    df = df.drop(columns=["code"])

    # Дисциплина/раздел: значение строки листа, а если пусто — комплекта.
    row_disc = df["discipline"].fillna("")
    df["Дисциплина"] = row_disc.where(row_disc != "", df["kit_discipline"]
                                      .fillna(""))
    row_sect = df["section"].fillna("")
    df["Раздел"] = row_sect.where(row_sect != "", df["kit_section"].fillna(""))

    # ---- Стандартные фильтры ----
    if flt["disc"]:
        df = df[df["Дисциплина"].isin(flt["disc"])]
    if flt["section"]:
        df = df[df["Раздел"].isin(flt["section"])]
    if flt["kits"]:
        df = df[df["Комплект(ы)"].isin(flt["kits"])]
    if flt["block_codes"]:
        blocks = df["Комплект(ы)"].apply(_extract_4th_block)
        df = df[blocks.isin(flt["block_codes"])]
    if flt["marka"]:
        m = flt["marka"].strip().upper()
        if m:
            df = df[df["Комплект(ы)"].apply(_extract_2nd_block) == m]
    kit_status = flt["extra"].get("kit_status", ())
    if kit_status:
        df = df[df["status"].isin(kit_status)]

    # ---- Дата позиции: плановая дата листа или срок комплекта ----
    df["_date"] = df["planned_date"].where(
        df["planned_date"].notna() & (df["planned_date"] != ""),
        df["due_date"],
    )

    only_dated = flt["extra"].get("only_dated", False)
    if only_dated:
        df = df[df["_date"].notna()]

    # ---- Период ----
    if date_from or date_to:
        df = df[df["_date"].notna()]
        if date_from:
            iso = df["_date"].astype(str).str.slice(0, 10)
            df = df[iso >= date_from.isoformat()]
        if date_to:
            iso = df["_date"].astype(str).str.slice(0, 10)
            df = df[iso <= date_to.isoformat()]

    if df.empty:
        return pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

    # ---- Фактическая дата из Витро ----
    vitro_dates = _vitro_sheet_dates()

    def _fact_date(row) -> date | None:
        if pd.isna(row["sheet_no"]) or not str(row["sheet_no"]).strip():
            return None
        d = vitro_dates.get(
            (row["Комплект(ы)"], str(row["sheet_no"]).strip()))
        return _iso_to_date(d) if d else None

    fact = df.apply(_fact_date, axis=1)

    def _issue_status(d) -> str:
        """«Выдан», если лист загружен в Витро (есть фактическая дата),
        иначе «Планируется» (патч 09)."""
        if d is None:
            return STATUS_PLANNED
        try:
            if pd.isna(d):
                return STATUS_PLANNED
        except (TypeError, ValueError):
            pass
        return STATUS_ISSUED

    out = pd.DataFrame({
        "Дисциплина": df["Дисциплина"],
        "Раздел": df["Раздел"],
        "Комплект(ы)": df["Комплект(ы)"],
        "Номер листа": df["sheet_no"].fillna(""),
        "Наименование листа": df["Наименование листа"].fillna(""),
        "Плановая дата": df["_date"].apply(_iso_to_date),
        "Фактическая дата": fact,
        "Статус выдачи": fact.map(_issue_status),
    })
    out = out.sort_values(
        ["Дисциплина", "Раздел", "Комплект(ы)", "Номер листа"],
    ).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
#  Секции 5–6: детальные таблицы + выгрузка в Excel
# ---------------------------------------------------------------------------
def _period_label(date_from, date_to) -> str:
    """Человекочитаемая подпись периода для выгрузки и подписей."""
    if date_from or date_to:
        return (f"{date_from.strftime('%d.%m.%Y') if date_from else '…'} — "
                f"{date_to.strftime('%d.%m.%Y') if date_to else '…'}")
    return "все даты"


def _render_export_button(sheets_table: pd.DataFrame,
                          period_label: str) -> None:
    """Плашка выгрузки: Excel с листом «Листы» (таблица Б) и служебным
    листом «Параметры выгрузки», с учётом фильтров и периода
    (патч 09: лист «Комплекты» удалён вместе с таблицей А)."""
    export_bytes = build_plan_export(
        sheets_table, period_label=period_label)
    st.download_button(
        "📥 Выгрузить в Excel: лист «Листы»",
        data=export_bytes,
        file_name=f"План_выдачи_листы_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime=MIME_XLSX,
        use_container_width=False,
        key="plan_export_excel",
        help="Файл повторяет таблицу «Детальный план по листам "
             "комплекта» с учётом фильтров и периода; второй лист — "
             "параметры формирования.",
    )
    st.caption(f"Период выгрузки: **{period_label}**.")


def _render_sheet_plan_table(sheets_table: pd.DataFrame,
                             period_label: str) -> None:
    """Детальный план по листам комплекта (plan_drawings) —
    единственная детальная таблица (патч 09)."""
    st.markdown("##### 🗓 Детальный план по листам комплекта")

    if sheets_table.empty:
        st.info(
            "Нет позиций листов по выбранным фильтрам/периоду. "
            "Загрузите ведомости чертежей в блоке «📥 Вводные данные»."
        )
        return

    n = len(sheets_table)
    st.caption(f"Строк: **{n}** · период: **{period_label}**")
    if n > MAX_DETAIL_ROWS:
        st.caption(
            f"Показаны первые **{MAX_DETAIL_ROWS}** строк из **{n}** — "
            "полный объём в Excel-выгрузке."
        )

    shown = sheets_table.head(MAX_DETAIL_ROWS)
    st.dataframe(
        shown,
        use_container_width=True,
        hide_index=True,
        height=min(max(len(shown) * 36 + 40, 300), 700),
        column_config={
            "Дисциплина": st.column_config.TextColumn(width="small"),
            "Раздел": st.column_config.TextColumn(width="small"),
            "Комплект(ы)": st.column_config.TextColumn(width="medium"),
            "Номер листа": st.column_config.TextColumn(width="small"),
            "Наименование листа": st.column_config.TextColumn(width="large"),
            "Плановая дата": st.column_config.DateColumn(
                format="DD.MM.YYYY", width="small"),
            "Фактическая дата": st.column_config.DateColumn(
                "Фактическая дата",
                format="DD.MM.YYYY", width="small",
                help="Дата статуса листа в Витро (если лист загружен).",
            ),
            "Статус выдачи": st.column_config.TextColumn(
                width="small",
                help="«Выдан» — лист загружен в Витро (есть фактическая "
                     "дата); «Планируется» — ещё не загружен.",
            ),
        },
    )


# ---------------------------------------------------------------------------
#  Секция 0. Импорт ведомостей
# ---------------------------------------------------------------------------
def _render_import_block() -> None:
    with st.expander("📥 Вводные данные: ведомости (Excel)", expanded=False):
        st.caption(
            "Загрузите **Ведомость основных комплектов** и **ведомости "
            "чертежей** в формате Excel. Колонки распознаются по названиям "
            "(«Шифр комплекта», «Номер листа», «Название листа», «Шифр "
            "листа» и т. п.) — порядок и лишние колонки не мешают; подходят "
            "и реальные регистры «Комплекты чертежей» с шапками комплектов "
            "и приложениями (СО/ВР/П). Повторная загрузка ведомости "
            "комплектов обновляет шифры; повторная загрузка ведомости "
            "чертежей **заменяет** чертежи указанных в ней комплектов."
        )

        c1, c2 = st.columns(2)

        with c1:
            up_kits = st.file_uploader(
                "Ведомость основных комплектов (Excel)",
                type=["xlsx", "xlsm", "xltx"],
                accept_multiple_files=True,
                key="plan_up_kits",
            )

        with c2:
            up_drawings = st.file_uploader(
                "Ведомости чертежей комплектов (Excel)",
                type=["xlsx", "xlsm", "xltx"],
                accept_multiple_files=True,
                key="plan_up_drawings",
            )
            default_kit = st.text_input(
                "Шифр комплекта (если в файлах нет колонки «Шифр»)",
                placeholder="например: АТ-РД-КЖ1-1-00",
                key="plan_default_kit",
                help="Если ведомость чертежей сделана для одного комплекта "
                     "без колонки шифра — укажите его здесь. Можно вводить "
                     "несколько через запятую (лист = шифр по порядку).",
            )

        # ---- Разбор ведомости комплектов ----
        kits_rows: list[dict] = []
        kits_warnings: list[str] = []
        if up_kits:
            for f in up_kits:
                r, w = parse_kits_workbook(f.getvalue(), f.name)
                kits_rows += r
                kits_warnings += w
            if kits_rows:
                st.markdown(
                    f"**Ведомость комплектов:** распознано строк — "
                    f"**{len(kits_rows)}**")
                df_kits = pd.DataFrame(kits_rows).head(300)
                st.dataframe(
                    df_kits.rename(columns={
                        "code": "Шифр комплекта",
                        "name": "Наименование комплекта",
                        "discipline": "Дисциплина",
                        "section": "Раздел",
                        "plan_status": "Статус",
                        "due_date": "Срок готовности",
                        "due_comment": "Пояснение",
                        "updated_at": "Обновлён",
                        "updated_by": "Кем",
                    }),
                    use_container_width=True, hide_index=True, height=200)
                if st.button("💾 Сохранить комплекты в план",
                             type="primary", key="plan_save_kits"):
                    user = ensure_user()
                    if user:
                        try:
                            n = upsert_plan_kits(kits_rows, user)
                            st.success(f"✅ Сохранено комплектов: {n}")
                            log_event("plan_import",
                                      f"Ведомость комплектов: {n} строк "
                                      f"(файлов: {len(up_kits)})", rows=n)
                            st.rerun()
                        except Exception as e:
                            st.error(f"ОШИБКА БД при сохранении комплектов: {e}")
                            st.stop()
            else:
                st.info("Строки комплектов не распознаны.")

        # ---- Разбор ведомостей чертежей ----
        drawings_rows: list[dict] = []
        drawings_warnings: list[str] = []
        drawings_kits: dict[str, str] = {}
        if up_drawings:
            # Поддержка «один файл = один комплект»: если файлов несколько
            # и колонки шифра нет, применяется шифр по порядку файлов.
            default_codes = [c.strip().upper() for c in
                             (default_kit or "").split(",") if c.strip()]
            for idx, f in enumerate(up_drawings):
                dk = default_codes[idx] if idx < len(default_codes) else \
                    (default_codes[0] if len(default_codes) == 1 else "")
                r, w, k = parse_drawings_workbook(f.getvalue(), f.name, dk)
                drawings_rows += r
                drawings_warnings += w
                for code, kname in k.items():
                    # название берём из шапки комплекта; пустое не затираем
                    if kname and not drawings_kits.get(code):
                        drawings_kits[code] = kname
                    drawings_kits.setdefault(code, drawings_kits.get(code, ""))
            if drawings_rows:
                n_drw = sum(int(r["qty"]) for r in drawings_rows)
                st.markdown(
                    f"**Ведомости чертежей:** распознано листов — "
                    f"**{len(drawings_rows)}** ({n_drw} чертежей), "
                    f"комплектов — **{len({r['kit_code'] for r in drawings_rows})}**")
                df_drw = pd.DataFrame(drawings_rows).head(300)
                if "qty" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"qty": "Кол-во"})
                if "sheet_no" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"sheet_no": "Номер листа"})
                if "title" in df_drw.columns:
                    df_drw = df_drw.rename(columns={"title": "Наименование чертежа"})
                st.dataframe(
                    df_drw.rename(columns={
                        "kit_code": "Шифр комплекта",
                        "planned_date": "Плановая дата",
                        "discipline": "Дисциплина",
                        "section": "Раздел",
                        "source_file": "Файл",
                    }),
                    use_container_width=True, hide_index=True, height=200)
                if st.button("💾 Сохранить чертежи в план",
                             type="primary", key="plan_save_drawings"):
                    user = ensure_user()
                    if user:
                        try:
                            # Названия комплектов из шапок регистров —
                            # в план (пустые существующие поля заполняются)
                            if drawings_kits:
                                upsert_plan_kits(
                                    [{"code": c, "name": n} for c, n
                                     in drawings_kits.items()], user)
                            n = replace_plan_drawings_for_kits(drawings_rows, user)
                            n_kits = len({r["kit_code"] for r in drawings_rows})
                            st.success(f"✅ Сохранено листов: {n} "
                                       f"({n_kits} компл.)")
                            log_event("plan_import",
                                      f"Ведомости чертежей: {n} листов "
                                      f"(файлов: {len(up_drawings)})", rows=n)
                            st.rerun()
                        except Exception as e:
                            st.error(f"ОШИБКА БД при сохранении чертежей: {e}")
                            st.stop()
            else:
                st.info("Строки чертежей не распознаны.")

        # ---- Предупреждения разбора ----
        all_warnings = kits_warnings + drawings_warnings
        if all_warnings:
            with st.expander(
                    f"⚠️ Замечания по файлам ({len(all_warnings)})",
                    expanded=False):
                for w in all_warnings[:50]:
                    st.write(f"- {w}")

        # ---- Опасная зона ----
        st.divider()
        confirm = st.text_input(
            "Очистить план целиком: введите DELETE",
            key="plan_confirm_clear",
            help="Удалит все комплекты и чертежи плана. Данные Витро "
                 "не затрагиваются.",
        )
        if st.button("🗑 Очистить план", key="plan_clear_btn",
                     disabled=confirm != "DELETE"):
            user = ensure_user()
            if user:
                clear_plan()
                log_event("plan_clear",
                          f"План выдачи очищен. Пользователь: {user}")
                st.success("План очищен.")
                st.rerun()


# ===========================================================================
#  Точка входа
# ===========================================================================
def render():
    st.header("📅 План выдачи комплектов")
    st.caption(
        "План выдачи всех комплектов РД: загруженных в Витро и "
        "планируемых к загрузке — на основании ведомостей Excel. "
        "Данные плана общие для всех пользователей приложения."
    )

    # ---- 0. Вводные данные ----
    _render_import_block()

    st.divider()

    # ---- 1. Фильтры (единый стандарт) ----
    flt = _render_filters()

    # ---- 2. Период «с / по» — сразу под фильтрами, до общей таблицы ----
    date_from, date_to = _render_period_block()

    include_vitro_only = st.checkbox(
        "Включить комплекты, загруженные в Витро, но отсутствующие "
        "в ведомостях",
        value=True, key="plan_include_vitro_only",
        help="«Только в Витро» — комплекты есть в базе Витро, но их нет "
             "в загруженных ведомостях. Срок готовности для них можно "
             "назначить в сводной таблице.",
    )

    overview_all = _build_overview(include_vitro_only)
    overview_filtered = _apply_overview_filters(overview_all, flt)

    st.divider()

    # ---- 3. Общая таблица (сводная, с группировкой и ИТОГО) ----
    # Период применяется и к сводной таблице (по «Сроку готовности»).
    _render_overview_table(overview_filtered, date_from, date_to)

    st.divider()

    # ---- 4. Бублики (весь отбор, без периода) ----
    _render_donuts(overview_filtered)

    st.divider()

    # ---- 5–6. Детальный план по листам + выгрузка в Excel ----
    # (Таблица А по комплектам удалена — дублировала сводную, патч 09.)
    sheets_table = _build_sheet_plan_table(
        overview_filtered, flt, date_from, date_to)

    period_label = _period_label(date_from, date_to)

    _render_export_button(sheets_table, period_label)
    _render_sheet_plan_table(sheets_table, period_label)
