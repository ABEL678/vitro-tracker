# scripts/test_patch22_5.py
"""Тесты патча 22.5 — падение «План выдачи» с pyarrow ArrowInvalid.

Баг с сервера:
    pyarrow.lib.ArrowInvalid: Could not convert '' with type str:
    tried to convert to int64 — Conversion failed for column Строк
    with type object

Причина: в журнале загрузок (vitro/ui/plan.py, _render_import_log)
пустое число строк подставлялось как "" — строка, а колонка
рендерится st.column_config.NumberColumn → Arrow пытается привести
object-колонку к int64 и падает. Пустое значение возникает законно:
log_event("plan_clear", …) вызывается без rows (по умолчанию 0),
а `if r["rows"]` для 0 ложно — как и для NULL.

Блок 1: _build_import_log_rows — "" больше не попадает в «Строк»;
        колонка приводится к nullable Int64 (не 42.0); Arrow-конверсия
        проходит на всех сочетаниях (есть числа / только пустые /
        только 0 / пустая выборка).
Блок 2: детальная таблица плана (_build_sheet_plan_table) —
        позиция БЕЗ планового количества (qty = NULL) не ломает
        сборку и не теряет строку; сводка по комплектам считает
        такие позиции как 0 плановых листов.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_5.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* (СТОП-ЛИСТ патчей 21/22) ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_5_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import pyarrow as pa  # noqa: E402
import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.ui.plan import (  # noqa: E402
    SHEET_PLAN_COLUMNS,
    STATUS_PLANNED,
    _build_import_log_rows,
    _build_overview,
    _build_sheet_plan_table,
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


def log_rows(*specs):
    """specs: (timestamp, event, message, rows, status) → sqlite3.Row-подобные."""
    return [
        {"timestamp": ts, "event": ev, "message": msg,
         "rows": rw, "status": st}
        for ts, ev, msg, rw, st in specs
    ]


# ---------------------------------------------------------------------------
#  БЛОК 1 — журнал загрузок: никаких "" в колонке «Строк»
# ---------------------------------------------------------------------------
# Ровно серверный случай: загрузка ведомости + очистка плана (rows=0)
# + предупреждение парсера (rows=0) + NULL из БД.
rows = log_rows(
    ("2026-11-19T18:40:00", "plan_import", "Ведомость чертежей: 12 листов",
     12, "OK"),
    ("2026-11-19T18:41:00", "plan_clear", "План выдачи очищен", 0, "OK"),
    ("2026-11-19T18:42:00", "plan_import", "Замечания разбора (комплекты)",
     0, "WARN"),
    ("2026-11-19T18:43:00", "plan_import", "Ведомость без числа строк",
     None, "OK"),
)
df = _build_import_log_rows(rows)

check("журнал: 4 записи не потеряны", len(df) == 4, f"получено {len(df)}")
check("журнал: колонка «Строк» — не object (Arrow не упадёт)",
      df["Строк"].dtype == pd.Int64Dtype() or
      str(df["Строк"].dtype) == "Int64",
      f"dtype={df['Строк'].dtype}")
check("журнал: в «Строк» нет пустых строк",
      not any(isinstance(v, str) for v in df["Строк"].tolist()),
      f"значения={df['Строк'].tolist()}")
check("журнал: пустое значение — пропуск (NA), а не «»",
      pd.isna(df["Строк"].iloc[1]) and pd.isna(df["Строк"].iloc[2])
      and pd.isna(df["Строк"].iloc[3]),
      f"значения={df['Строк'].tolist()}")
check("журнал: число строк сохранено (12, не 12.0)",
      df["Строк"].iloc[0] == 12, f"получено {df['Строк'].iloc[0]!r}")
check("журнал: «Очистка плана» распознана",
      df["Событие"].iloc[1] == "Очистка плана",
      f"получено «{df['Событие'].iloc[1]}»")
check("журнал: предупреждение → статус WARN", df["Статус"].iloc[2] == "WARN")

# NULL timestamp → пустая строка даты (проверяем на отдельной записи:
# в наборе выше у всех записей дата задана).
df_null_ts = _build_import_log_rows(log_rows(
    (None, "plan_import", "Без даты", 3, "OK"),
))
check("журнал: NULL timestamp → пустая строка даты",
      df_null_ts["Дата (МСК)"].iloc[0] == "",
      f"получено {df_null_ts['Дата (МСК)'].iloc[0]!r}")

# Главная проверка: Arrow-конверсия проходит (до фикса здесь был
# ArrowInvalid «Could not convert '' with type str: tried to convert
# to int64 … for column Строк with type object»).
try:
    table = pa.Table.from_pandas(df)
    check("журнал: Arrow-конверсия проходит (баг не воспроизводится)",
          True)
    check("журнал: в Arrow «Строк» — целое",
          pa.types.is_integer(table.schema.field("Строк").type),
          f"тип={table.schema.field('Строк').type}")
except Exception as exc:  # noqa: BLE001 — ловим именно падение Arrow
    check("журнал: Arrow-конверсия проходит (баг не воспроизводится)",
          False, f"{type(exc).__name__}: {exc}")
    table = None

# Все числа (без пропусков) — тоже Int64, не 12.0
df_only_nums = _build_import_log_rows(log_rows(
    ("2026-11-19T18:40:00", "plan_import", "Ведомость: 5", 5, "OK"),
    ("2026-11-19T18:41:00", "plan_import", "Ведомость: 7", 7, "OK"),
))
check("журнал: только числа → Int64 (нет 5.0)",
      str(df_only_nums["Строк"].dtype) == "Int64"
      and df_only_nums["Строк"].tolist() == [5, 7],
      f"dtype={df_only_nums['Строк'].dtype}, "
      f"значения={df_only_nums['Строк'].tolist()}")

# Только пустые значения — Arrow не должен падать и здесь
df_all_empty = _build_import_log_rows(log_rows(
    ("2026-11-19T18:40:00", "plan_clear", "Очистка", 0, "OK"),
    ("2026-11-19T18:41:00", "plan_clear", "Очистка", None, "OK"),
))
try:
    pa.Table.from_pandas(df_all_empty)
    check("журнал: только пустые «Строк» → Arrow проходит", True)
except Exception as exc:  # noqa: BLE001
    check("журнал: только пустые «Строк» → Arrow проходит", False,
          f"{type(exc).__name__}: {exc}")

# Пустая выборка — колонки на месте, без падения
df_empty = _build_import_log_rows([])
check("журнал: пустая выборка → пустой DataFrame",
      df_empty.empty)
check("журнал: пустая выборка сохраняет колонки",
      list(df_empty.columns) == ["Дата (МСК)", "Событие", "Строк",
                                 "Статус", "Сообщение"],
      f"колонки={list(df_empty.columns)}")
try:
    pa.Table.from_pandas(df_empty)
    check("журнал: пустая выборка → Arrow проходит", True)
except Exception as exc:  # noqa: BLE001
    check("журнал: пустая выборка → Arrow проходит", False,
          f"{type(exc).__name__}: {exc}")

# SQLite не проверяет типы INTEGER-колонок, поэтому в log.rows может
# оказаться текст или дробь. Журнал диагностический: одна кривая
# запись не должна валить вкладку — значение становится пропуском.
df_text = _build_import_log_rows(log_rows(
    ("2026-11-19T18:40:00", "plan_import", "«12» текстом", "12", "OK"),
    ("2026-11-19T18:41:00", "plan_import", "мусор", "n/a", "OK"),
    ("2026-11-19T18:42:00", "plan_import", "дробное", 12.7, "OK"),
))
check("журнал: число текстом распознано (12)",
      df_text["Строк"].iloc[0] == 12,
      f"получено {df_text['Строк'].iloc[0]!r}")
check("журнал: нечисловой log.rows → пропуск (не падение)",
      pd.isna(df_text["Строк"].iloc[1]),
      f"получено {df_text['Строк'].iloc[1]!r}")
check("журнал: дробный log.rows → пропуск (число строк целое)",
      pd.isna(df_text["Строк"].iloc[2]),
      f"получено {df_text['Строк'].iloc[2]!r}")
try:
    pa.Table.from_pandas(df_text)
    check("журнал: текст/дробь в «Строк» → Arrow проходит", True)
except Exception as exc:  # noqa: BLE001
    check("журнал: текст/дробь в «Строк» → Arrow проходит", False,
          f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
#  БЛОК 2 — детальная таблица: позиция БЕЗ планового количества
# ---------------------------------------------------------------------------
KIT = "АТ-РД-КЖ1-1-00"

with sqlite_db.get_conn() as conn:
    conn.execute("DELETE FROM plan_drawings")
    conn.execute("DELETE FROM plan_kits")
    conn.execute(
        "INSERT INTO plan_kits (code, name, discipline, section, "
        "plan_status) VALUES (?, ?, ?, ?, ?)",
        (KIT, "Комплект КЖ1", "КЖ", "Раздел 1", "План"),
    )
    # Позиция 1 — БЕЗ планового количества (qty = NULL): именно она
    # валит сервер, если её попытаться привести к целому.
    conn.execute(
        "INSERT INTO plan_drawings (kit_code, sheet_no, title, "
        "discipline, section, qty, planned_date) VALUES (?,?,?,?,?,?,?)",
        (KIT, "1", "Лист без количества", "", "", None, None),
    )
    # Позиция 2 — обычная, с количеством и плановой датой.
    conn.execute(
        "INSERT INTO plan_drawings (kit_code, sheet_no, title, "
        "discipline, section, qty, planned_date) VALUES (?,?,?,?,?,?,?)",
        (KIT, "2", "Лист с количеством", "", "", 3, "2026-12-01"),
    )

FLT = {
    "disc": [], "section": [], "kits": [], "block_codes": [],
    "marka": "", "extra": {"kit_status": (), "only_dated": False},
}

overview = _build_overview(True)
check("детальная: сводка по комплектам собрана", len(overview) == 1,
      f"строк={len(overview)}")
check("детальная: NULL qty не ломает сводку (план = 3, не падение)",
      int(overview["sheets_plan"].iloc[0]) == 3,
      f"получено {overview['sheets_plan'].iloc[0]!r}")

try:
    detail = _build_sheet_plan_table(overview, FLT, None, None)
    check("детальная: сборка таблицы не падает на qty = NULL", True)
except Exception as exc:  # noqa: BLE001
    check("детальная: сборка таблицы не падает на qty = NULL", False,
          f"{type(exc).__name__}: {exc}")
    detail = pd.DataFrame()

check("детальная: обе позиции на месте (NULL qty не теряет строку)",
      len(detail) == 2, f"строк={len(detail)}")
check("детальная: позиция без количества присутствует",
      "1" in detail["Номер листа"].tolist(),
      f"номера={detail['Номер листа'].tolist()}")
check("детальная: наименование позиции без qty сохранено",
      "Лист без количества" in detail["Наименование листа"].tolist())

# Колонки таблицы не содержат типов, ломающих Arrow.
# pandas 3.0 отдаёт строковые колонки как dtype `str` (не object),
# поэтому проверяем «не число», а не конкретное имя dtype.
for col in ("Комплект(ы)", "Номер листа", "Наименование листа"):
    series = detail[col]
    check(f"детальная: «{col}» — строковая колонка (Arrow-совместима)",
          not pd.api.types.is_numeric_dtype(series),
          f"dtype={series.dtype}")

try:
    pa.Table.from_pandas(detail)
    check("детальная: Arrow-конверсия таблицы проходит", True)
except Exception as exc:  # noqa: BLE001
    check("детальная: Arrow-конверсия таблицы проходит", False,
          f"{type(exc).__name__}: {exc}")

# «Плановая дата» у позиции без даты — пропуск, не "".
check("детальная: нет плановой даты → пропуск (None/NaT)",
      pd.isna(detail.loc[detail["Номер листа"] == "1", "Плановая дата"]
              .iloc[0]))
check("детальная: плановая дата второй позиции на месте",
      str(detail.loc[detail["Номер листа"] == "2", "Плановая дата"]
          .iloc[0]) == "2026-12-01",
      f"получено {detail.loc[detail['Номер листа'] == '2', 'Плановая дата'].iloc[0]!r}")

# Позиции БЕЗ планового количества могут занимать весь комплект
# (например, ведомость без колонки «Кол-во»): сводка даёт 0 плановых
# листов, а детальная таблица не теряет ни одной позиции и не падает
# на Arrow — ни при рендере, ни при выгрузке в Excel.
KIT_NOQTY = "АТ-РД-КЖ2-1-00"
with sqlite_db.get_conn() as conn:
    conn.execute(
        "INSERT INTO plan_kits (code, name, discipline, section, "
        "plan_status) VALUES (?, ?, ?, ?, ?)",
        (KIT_NOQTY, "Комплект КЖ2", "КЖ", "Раздел 2", "План"),
    )
    for no in ("1", "2", "3"):
        conn.execute(
            "INSERT INTO plan_drawings (kit_code, sheet_no, title, "
            "discipline, section, qty, planned_date) "
            "VALUES (?,?,?,?,?,?,?)",
            (KIT_NOQTY, no, f"Лист {no} без количества", "", "", None, None),
        )

overview_noqty = _build_overview(True)
row_noqty = overview_noqty[overview_noqty["code"] == KIT_NOQTY]
check("детальная: комплект только с NULL qty попал в сводку",
      len(row_noqty) == 1, f"строк={len(row_noqty)}")
check("детальная: только NULL qty → плановых листов 0 (не падение)",
      len(row_noqty) == 1 and int(row_noqty["sheets_plan"].iloc[0]) == 0,
      f"получено {row_noqty['sheets_plan'].tolist()!r}")

try:
    detail_noqty = _build_sheet_plan_table(overview_noqty, FLT, None, None)
    detail_noqty = detail_noqty[
        detail_noqty["Комплект(ы)"] == KIT_NOQTY].reset_index(drop=True)
    check("детальная: сборка таблицы не падает, когда qty = NULL везде",
          True)
except Exception as exc:  # noqa: BLE001
    check("детальная: сборка таблицы не падает, когда qty = NULL везде",
          False, f"{type(exc).__name__}: {exc}")
    detail_noqty = pd.DataFrame(columns=SHEET_PLAN_COLUMNS)

check("детальная: все 3 позиции без qty сохранены (ни одна не потеряна)",
      len(detail_noqty) == 3, f"строк={len(detail_noqty)}")
check("детальная: номера листов комплекта без qty на месте",
      sorted(detail_noqty["Номер листа"].tolist()) == ["1", "2", "3"],
      f"номера={detail_noqty['Номер листа'].tolist()}")
check("детальная: у позиций без qty нет плановой даты (пропуск)",
      bool(detail_noqty["Плановая дата"].isna().all()),
      f"значения={detail_noqty['Плановая дата'].tolist()}")
check("детальная: у позиций без qty статус «Планируется»",
      set(detail_noqty["Статус выдачи"].tolist()) == {STATUS_PLANNED},
      f"статусы={detail_noqty['Статус выдачи'].tolist()}")
try:
    pa.Table.from_pandas(detail_noqty)
    check("детальная: Arrow-конверсия таблицы без qty проходит", True)
    check("детальная: в Arrow «Плановая дата» — не строка",
          not pa.types.is_string(
              pa.Table.from_pandas(detail_noqty).schema
              .field("Плановая дата").type),
          f"тип={pa.Table.from_pandas(detail_noqty).schema.field('Плановая дата').type}")
except Exception as exc:  # noqa: BLE001
    check("детальная: Arrow-конверсия таблицы без qty проходит", False,
          f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.5 пройдены.")
