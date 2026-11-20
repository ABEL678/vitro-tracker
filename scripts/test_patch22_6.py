# scripts/test_patch22_6.py
"""Тесты патча 22.6 — защита сводки «План выдачи» от текста в qty
и контроль версии приложения.

Проблема (из HANDOFF): в vitro/ui/plan.py, _build_overview,
    drawings.groupby("kit_code")["qty"].sum()
падает с TypeError: can only concatenate str (not "int") to str,
если в plan_drawings.qty окажется ТЕКСТ. SQLite не проверяет типы
INTEGER-колонок: текстовое значение можно записать напрямую, а
весь столбец после read_sql становится object — pandas складывает
строки конкатенацией и упирается в число.

Фикс: перед группировкой колонка приводится к числам
pd.to_numeric(..., errors="coerce") — нечисловой текст становится
NaN и считается так же, как NULL (план 0), а числовой текст
(«3» из БД) — числом 3.

Блоки проверок:
  0. Версия приложения — APP_VERSION = "1.5 от 19.11.2026",
     футер app.py берёт её из конфига (не хардкод).
  1. qty = только текст → сводка не падает, плановых листов 0.
  2. qty смешанный (числа + текст + NULL) → сумма только чисел.
  3. Число текстом («3») ведёт себя как число (coerce приводит его
     к 3) — фантомного «плана 0» не появляется.
  4. Текст в qty не ломает детальную таблицу и итоги (бублики).
  5. Регресс 22.5: qty = NULL по-прежнему считается как 0.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_6.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* (СТОП-ЛИСТ патчей 21/22) ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_6_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.config import APP_VERSION  # noqa: E402
from vitro.ui.plan import (  # noqa: E402
    SHEET_PLAN_COLUMNS,
    _build_overview,
    _build_sheet_plan_table,
    _plan_totals,
)

sqlite_db.init_db()

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
    if not cond:
        FAILED.append(name)


FLT = {
    "disc": [], "section": [], "kits": [], "block_codes": [],
    "marka": "", "extra": {"kit_status": (), "only_dated": False},
}


def reset_plan() -> None:
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM plan_drawings")
        conn.execute("DELETE FROM plan_kits")


def add_kit(code: str, name: str = "", section: str = "Раздел 1") -> None:
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO plan_kits (code, name, discipline, section, "
            "plan_status) VALUES (?, ?, ?, ?, ?)",
            (code, name or f"Комплект {code}", "КЖ", section, "План"),
        )


def add_drawing(kit_code: str, sheet_no: str, qty) -> None:
    """qty пишется как есть — SQLite не проверяет тип INTEGER-колонки."""
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO plan_drawings (kit_code, sheet_no, title, "
            "discipline, section, qty, planned_date) VALUES (?,?,?,?,?,?,?)",
            (kit_code, sheet_no, f"Лист {sheet_no}", "", "", qty, None),
        )


def plan_row(code: str) -> pd.Series | None:
    ov = _build_overview(True)
    hit = ov[ov["code"] == code]
    return None if hit.empty else hit.iloc[0]


# ---------------------------------------------------------------------------
#  БЛОК 0 — версия приложения
# ---------------------------------------------------------------------------
check("версия: APP_VERSION = «1.5 от 19.11.2026»",
      APP_VERSION == "1.5 от 19.11.2026", f"получено {APP_VERSION!r}")

_app_src = (ROOT / "app.py").read_text(encoding="utf-8")
check("версия: футер app.py импортирует APP_VERSION из конфига",
      "from vitro.config import APP_VERSION" in _app_src)
check("версия: в футере нет захардкоженной старой версии",
      "1.4 от 12.11.2026" not in _app_src)


# ---------------------------------------------------------------------------
#  БЛОК 1 — qty = только текст: сводка не падает
# ---------------------------------------------------------------------------
KIT_TXT = "АТ-РД-КЖ1-1-00"
reset_plan()
add_kit(KIT_TXT, "Комплект с текстом в qty")
add_drawing(KIT_TXT, "1", "два")
add_drawing(KIT_TXT, "2", "n/a")
add_drawing(KIT_TXT, "3", "")

try:
    row = plan_row(KIT_TXT)
    crashed = None
except Exception as exc:  # noqa: BLE001 — ловим именно TypeError конкатенации
    row = None
    crashed = exc

check("текст: сводка не падает на текстовом qty",
      crashed is None, f"{type(crashed).__name__}: {crashed}"
      if crashed else "")
check("текст: комплект попал в сводку", row is not None)
check("текст: текстовый qty считается как отсутствие плана (0)",
      row is not None and int(row["sheets_plan"]) == 0,
      f"получено {None if row is None else row['sheets_plan']!r}")


# ---------------------------------------------------------------------------
#  БЛОК 2 — смешанные значения: числа + текст + NULL
# ---------------------------------------------------------------------------
KIT_MIX = "АТ-РД-КЖ2-1-00"
reset_plan()
add_kit(KIT_MIX, "Комплект со смешанным qty")
add_drawing(KIT_MIX, "1", 2)          # число
add_drawing(KIT_MIX, "2", "мусор")    # текст → NaN
add_drawing(KIT_MIX, "3", None)       # NULL → NaN
add_drawing(KIT_MIX, "4", 3)          # число
add_drawing(KIT_MIX, "5", "1")        # число текстом → 1

try:
    row_mix = plan_row(KIT_MIX)
    crashed_mix = None
except Exception as exc:  # noqa: BLE001
    row_mix = None
    crashed_mix = exc

check("смешанные: сводка не падает",
      crashed_mix is None, f"{type(crashed_mix).__name__}: {crashed_mix}"
      if crashed_mix else "")
check("смешанные: сумма = только числа (2 + 3 + 1 = 6)",
      row_mix is not None and int(row_mix["sheets_plan"]) == 6,
      f"получено {None if row_mix is None else row_mix['sheets_plan']!r}")
check("смешанные: sheets_plan — целое, не NaN/float",
      row_mix is not None
      and isinstance(int(row_mix["sheets_plan"]), int)
      and row_mix["sheets_plan"] == int(row_mix["sheets_plan"]),
      f"получено {None if row_mix is None else row_mix['sheets_plan']!r}")


# ---------------------------------------------------------------------------
#  БЛОК 3 — «число текстом» ведёт себя как число
# ---------------------------------------------------------------------------
KIT_NUMTXT = "АТ-РД-КЖ3-1-00"
reset_plan()
add_kit(KIT_NUMTXT, "Комплект с числом текстом")
add_drawing(KIT_NUMTXT, "1", "4")

row_numtxt = plan_row(KIT_NUMTXT)
check("число текстом: «4» считается числом (4, не 0)",
      row_numtxt is not None and int(row_numtxt["sheets_plan"]) == 4,
      f"получено {None if row_numtxt is None else row_numtxt['sheets_plan']!r}")


# ---------------------------------------------------------------------------
#  БЛОК 4 — текст в qty не ломает детальную таблицу и итоги
# ---------------------------------------------------------------------------
KIT_DETAIL = "АТ-РД-КЖ4-1-00"
reset_plan()
add_kit(KIT_DETAIL, "Комплект для детальной таблицы")
add_drawing(KIT_DETAIL, "1", "план")
add_drawing(KIT_DETAIL, "2", 2)
add_drawing(KIT_DETAIL, "3", None)

try:
    overview = _build_overview(True)
    detail = _build_sheet_plan_table(overview, FLT, None, None)
    crashed_detail = None
except Exception as exc:  # noqa: BLE001
    overview = pd.DataFrame()
    detail = pd.DataFrame(columns=SHEET_PLAN_COLUMNS)
    crashed_detail = exc

check("детальная: текст в qty не ломает сборку таблицы",
      crashed_detail is None, f"{type(crashed_detail).__name__}: "
      f"{crashed_detail}" if crashed_detail else "")
check("детальная: все 3 позиции на месте",
      len(detail) == 3, f"строк={len(detail)}")

try:
    totals = _plan_totals(overview)
    crashed_totals = None
except Exception as exc:  # noqa: BLE001
    totals = {}
    crashed_totals = exc

check("итоги: бублики считаются при тексте в qty",
      crashed_totals is None, f"{type(crashed_totals).__name__}: "
      f"{crashed_totals}" if crashed_totals else "")
check("итоги: плановых листов = 2 (только число)",
      totals.get("sheets_plan") == 2,
      f"получено {totals.get('sheets_plan')!r}")
check("итоги: листов всего = max(Витро, план) без падения",
      totals.get("sheets_total") == max(totals.get("sheets_vitro", 0), 2),
      f"получено {totals.get('sheets_total')!r}")


# ---------------------------------------------------------------------------
#  БЛОК 5 — регресс 22.5: NULL qty по-прежнему 0
# ---------------------------------------------------------------------------
KIT_NULL = "АТ-РД-КЖ5-1-00"
reset_plan()
add_kit(KIT_NULL, "Комплект без qty")
add_drawing(KIT_NULL, "1", None)
add_drawing(KIT_NULL, "2", None)

row_null = plan_row(KIT_NULL)
check("NULL: плановых листов 0 (поведение 22.5 сохранено)",
      row_null is not None and int(row_null["sheets_plan"]) == 0,
      f"получено {None if row_null is None else row_null['sheets_plan']!r}")

# Пустой план (нет ни комплектов, ни чертежей) — без падения.
reset_plan()
try:
    overview_empty = _build_overview(True)
    crashed_empty = None
except Exception as exc:  # noqa: BLE001
    overview_empty = pd.DataFrame()
    crashed_empty = exc
check("пусто: пустой план не ломает сводку",
      crashed_empty is None and overview_empty.empty,
      f"{type(crashed_empty).__name__}: {crashed_empty}"
      if crashed_empty else f"строк={len(overview_empty)}")


# ---------------------------------------------------------------------------
#  БЛОК 6 — нечисловые крайности в qty
# ---------------------------------------------------------------------------
# float('inf') — тоже «не количество»: int(inf) даёт OverflowError,
# поэтому бесконечность трактуется как отсутствие плана.
KIT_INF = "АТ-РД-КЖ6-1-00"
reset_plan()
add_kit(KIT_INF, "Комплект с бесконечностью")
add_drawing(KIT_INF, "1", float("inf"))
add_drawing(KIT_INF, "2", 4)

try:
    row_inf = plan_row(KIT_INF)
    crashed_inf = None
except Exception as exc:  # noqa: BLE001
    row_inf = None
    crashed_inf = exc

check("крайности: inf в qty не роняет сводку",
      crashed_inf is None, f"{type(crashed_inf).__name__}: {crashed_inf}"
      if crashed_inf else "")
check("крайности: inf считается как отсутствие плана (остаётся 4)",
      row_inf is not None and int(row_inf["sheets_plan"]) == 4,
      f"получено {None if row_inf is None else row_inf['sheets_plan']!r}")

# Дробный текст с запятой («3,5») — не целое количество: как отсутствие.
KIT_FRAC = "АТ-РД-КЖ7-1-00"
reset_plan()
add_kit(KIT_FRAC, "Комплект с дробным текстом")
add_drawing(KIT_FRAC, "1", "3,5")
row_frac = plan_row(KIT_FRAC)
check("крайности: «3,5» — не падение и не дробь в плане",
      row_frac is not None and int(row_frac["sheets_plan"]) == 0,
      f"получено {None if row_frac is None else row_frac['sheets_plan']!r}")


# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.6 пройдены.")
