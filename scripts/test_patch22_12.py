"""Тесты патча 22.12 — подвкладка «Готовы к переводу в А».

Блоки проверок:
  0. SQL-логика: лист B/C + все замечания "Закрыто"/"Аннулировано" → включён.
  1. Пограничные случаи: лист без замечаний, лист с незакрытым замечанием,
     лист со статусом A, лист с миксом статусов.
  2. Фильтры: дисциплина, раздел, комплект, блок, марка.
  3. Регрессия: существующие подвкладки не сломаны,
     _load_all_categorized не затронут, _load_customer_a_annulled не затронут.

СТОП-ЛИСТ: реальная data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_12.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_12_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import pandas as pd  # noqa: E402

import vitro.sqlite_db as sqlite_db  # noqa: E402
import vitro.ui.authors as authors  # noqa: E402
from vitro.ui.authors import (  # noqa: E402
    _load_ready_for_a,
    _render_ready_for_a,
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


# ---------------------------------------------------------------------------
#  Подготовка БД
# ---------------------------------------------------------------------------
def seed_db() -> None:
    """Очищаем БД для чистого теста + сбрасываем кэш."""
    try:
        _load_ready_for_a.clear()
    except Exception:
        pass
    try:
        authors._load_closed_comments_for_doc.clear()
    except Exception:
        pass
    with sqlite_db.get_conn() as conn:
        conn.execute("DELETE FROM comments")
        conn.execute("DELETE FROM documents")


def add_document(doc_id, leaf, complex_code, status, sheet_number="1",
                 discipline=""):
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, leaf, complex, status, sheet_number, "
            "discipline) VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, leaf, complex_code, status, sheet_number, discipline),
        )


def add_comment(comment_id, doc_id, status, created="2026-01-01T10:00:00"):
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO comments (id, doc_id, status, created) VALUES (?, ?, ?, ?)",
            (comment_id, doc_id, status, created),
        )


KIT_B = "АТ-РД-КЖ1-1-00"   # статус B
KIT_C = "АТ-РД-АР2-2-00"   # статус C
KIT_A = "АТ-РД-ОВ3-3-00"   # статус A (не должен попадать)
KIT_ABSENT = "АТ-РД-ХХ9-9-99"  # отсутствует в БД


# ===========================================================================
#  БЛОК 0 — SQL-логика: включение
# ===========================================================================
def block0():
    # Лист B + все замечания "Закрыто" → включён
    seed_db()
    add_document(1, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(1, 1, "Закрыто", "2026-01-01T10:00:00")
    add_comment(2, 1, "Закрыто", "2026-02-15T14:00:00")

    df = _load_ready_for_a()
    check("0: лист B + все 'Закрыто' → включён",
          KIT_B in df["complex"].values,
          f"complex={list(df['complex'])}")
    if KIT_B in df["complex"].values:
        row = df[df["complex"] == KIT_B].iloc[0]
        check("0: n_total == 2", int(row["n_total"]) == 2,
              f"n_total={row['n_total']}")
        check("0: n_closed == 2", int(row["n_closed"]) == 2,
              f"n_closed={row['n_closed']}")

    # Лист C + все замечания "Аннулировано" → включён
    seed_db()
    add_document(2, f"{KIT_C}-1.PDF", KIT_C, "C")
    add_comment(3, 2, "Аннулировано", "2026-03-10T09:00:00")

    df = _load_ready_for_a()
    check("0: лист C + все 'Аннулировано' → включён",
          KIT_C in df["complex"].values,
          f"complex={list(df['complex'])}")
    if KIT_C in df["complex"].values:
        row = df[df["complex"] == KIT_C].iloc[0]
        check("0: n_total == 1", int(row["n_total"]) == 1)
        check("0: n_closed == 1", int(row["n_closed"]) == 1)

    # Микс "Закрыто" + "Аннулировано" → включён
    seed_db()
    add_document(3, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(4, 3, "Закрыто", "2026-04-01T10:00:00")
    add_comment(5, 3, "Аннулировано", "2026-05-15T11:00:00")

    df = _load_ready_for_a()
    check("0: микс 'Закрыто' + 'Аннулировано' → включён",
          KIT_B in df["complex"].values)


# ===========================================================================
#  БЛОК 1 — Пограничные случаи
# ===========================================================================
def block1():
    # Лист B + есть "Новое" → НЕ включён
    seed_db()
    add_document(1, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(1, 1, "Новое", "2026-01-01T10:00:00")

    df = _load_ready_for_a()
    check("1: лист B + 'Новое' → НЕ включён",
          KIT_B not in df["complex"].values,
          f"complex={list(df['complex'])}")

    # Лист B + микс "Закрыто" + "Новое" → НЕ включён
    seed_db()
    add_document(2, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(2, 2, "Закрыто", "2026-01-01T10:00:00")
    add_comment(3, 2, "Новое", "2026-02-01T10:00:00")

    df = _load_ready_for_a()
    check("1: лист B + микс 'Закрыто'+'Новое' → НЕ включён",
          KIT_B not in df["complex"].values)

    # Лист A + все замечания "Закрыто" → НЕ включён
    seed_db()
    add_document(4, f"{KIT_A}-1.PDF", KIT_A, "A")
    add_comment(4, 4, "Закрыто", "2026-01-01T10:00:00")

    df = _load_ready_for_a()
    check("1: лист A + все 'Закрыто' → НЕ включён",
          KIT_A not in df["complex"].values)

    # Лист B + нет замечаний → НЕ включён
    seed_db()
    add_document(5, f"{KIT_B}-1.PDF", KIT_B, "B")

    df = _load_ready_for_a()
    check("1: лист B + нет замечаний → НЕ включён",
          KIT_B not in df["complex"].values)

    # Лист B + 1 замечание "Закрыто" → включён
    seed_db()
    add_document(6, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(5, 6, "Закрыто", "2026-01-01T10:00:00")

    df = _load_ready_for_a()
    check("1: лист B + 1 замечание 'Закрыто' → включён",
          KIT_B in df["complex"].values)

    # Отсутствующий комплект → НЕ включён
    df = _load_ready_for_a()
    check("1: отсутствующий комплект → НЕ включён",
          KIT_ABSENT not in df["complex"].values)

    # Лист B + "Принято в работу" → НЕ включён
    seed_db()
    add_document(7, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(6, 7, "Принято в работу", "2026-01-01T10:00:00")

    df = _load_ready_for_a()
    check("1: лист B + 'Принято в работу' → НЕ включён",
          KIT_B not in df["complex"].values)

    # Лист C + "Выполнено" → НЕ включён
    seed_db()
    add_document(8, f"{KIT_C}-1.PDF", KIT_C, "C")
    add_comment(7, 8, "Выполнено", "2026-01-01T10:00:00")

    df = _load_ready_for_a()
    check("1: лист C + 'Выполнено' → НЕ включён",
          KIT_C not in df["complex"].values)


# ===========================================================================
#  БЛОК 2 — Фильтры
# ===========================================================================
def block2():
    # Создаём несколько комплектов с разными дисциплинами
    kit1 = "АТ-РД-КЖ1-1-00"  # discipline: КЖ (из документа)
    kit2 = "АТ-РД-КЖ2-2-00"  # discipline: КЖ
    kit3 = "АТ-РД-АР1-1-00"  # discipline: АР

    seed_db()
    add_document(1, f"{kit1}-1.PDF", kit1, "B", discipline="КЖ")
    add_comment(1, 1, "Закрыто", "2026-01-01T10:00:00")

    add_document(2, f"{kit2}-1.PDF", kit2, "C", discipline="КЖ")
    add_comment(2, 2, "Закрыто", "2026-02-01T10:00:00")

    add_document(3, f"{kit3}-1.PDF", kit3, "B", discipline="АР")
    add_comment(3, 3, "Аннулировано", "2026-03-01T10:00:00")

    # Фильтр по дисциплине
    df = _load_ready_for_a(disciplines=("КЖ",))
    check("2: фильтр по дисциплине 'КЖ' → 2 комплекта",
          len(df) == 2, f"len={len(df)}")
    check("2: фильтр по дисциплине 'КЖ' → kit1 и kit2",
          set(df["complex"]) == {kit1, kit2},
          f"complex={list(df['complex'])}")

    # Фильтр по комплекту
    df = _load_ready_for_a(kits=(kit1,))
    check("2: фильтр по комплекту kit1 → 1 комплект",
          len(df) == 1, f"len={len(df)}")
    check("2: фильтр по комплекту kit1 → только kit1",
          df["complex"].iloc[0] == kit1)

    # Фильтр по блоку (4-й блок шифра)
    # kit1 = АТ-РД-КЖ1-1-00 → блок 1
    # kit2 = АТ-РД-КЖ2-2-00 → блок 2
    # kit3 = АТ-РД-АР1-1-00 → блок 1
    df = _load_ready_for_a(block_codes=("1",))
    check("2: фильтр по блоку '1' → 2 комплекта (kit1, kit3)",
          len(df) == 2, f"len={len(df)}, complex={list(df['complex'])}")
    check("2: фильтр по блоку '1' → kit1 и kit3",
          set(df["complex"]) == {kit1, kit3})

    df = _load_ready_for_a(block_codes=("2",))
    check("2: фильтр по блоку '2' → 1 комплект (kit2)",
          len(df) == 1, f"len={len(df)}")
    check("2: фильтр по блоку '2' → только kit2",
          df["complex"].iloc[0] == kit2)

    # Фильтр по марке (2-й блок шифра)
    # kit1 = КЖ1, kit2 = КЖ2, kit3 = АР1
    df = _load_ready_for_a(marka="КЖ1")
    check("2: фильтр по марке 'КЖ1' → 1 комплект (kit1)",
          len(df) == 1, f"len={len(df)}")
    check("2: фильтр по марке 'КЖ1' → только kit1",
          df["complex"].iloc[0] == kit1)

    # Комбинация фильтров: дисциплина КЖ + блок 1
    df = _load_ready_for_a(disciplines=("КЖ",), block_codes=("1",))
    check("2: комбинация КЖ+блок1 → 1 комплект (kit1)",
          len(df) == 1, f"len={len(df)}")
    check("2: комбинация КЖ+блок1 → только kit1",
          df["complex"].iloc[0] == kit1)


# ===========================================================================
#  БЛОК 3 — Регрессия
# ===========================================================================
def block3():
    # Проверяем, что _load_all_categorized не сломан
    from vitro.ui.authors import _load_all_categorized  # noqa: E402
    _load_all_categorized.clear()
    df = _load_all_categorized()
    check("3: _load_all_categorized возвращает DataFrame",
          isinstance(df, pd.DataFrame))

    # Проверяем, что _load_customer_a_annulled не сломан
    from vitro.ui.authors import _load_customer_a_annulled  # noqa: E402
    _load_customer_a_annulled.clear()
    df_ca = _load_customer_a_annulled()
    check("3: _load_customer_a_annulled возвращает DataFrame",
          isinstance(df_ca, pd.DataFrame))

    # Проверяем, что _load_ready_for_a не мутирует аргументы
    seed_db()
    add_document(1, f"{KIT_B}-1.PDF", KIT_B, "B")
    add_comment(1, 1, "Закрыто", "2026-01-01T10:00:00")

    df_before = _load_ready_for_a()
    df_after = _load_ready_for_a()
    check("3: повторный вызов возвращает тот же результат",
          len(df_before) == len(df_after),
          f"before={len(df_before)}, after={len(df_after)}")


# ===========================================================================
#  БЛОК 4 — Развёртка до листов, пробелы, реестр
# ===========================================================================
def block4():
    # (а) два листа одного комплекта, оба со всеми закрытыми → 2 строки
    seed_db()
    add_document(1, f"{KIT_B}-1.PDF", KIT_B, "B", discipline="КЖ")
    add_comment(1, 1, "Закрыто", "2026-01-01T10:00:00")
    add_document(2, f"{KIT_B}-2.PDF", KIT_B, "B", discipline="КЖ")
    add_comment(2, 2, "Аннулировано", "2026-02-01T10:00:00")

    df = _load_ready_for_a()
    check("4а: два листа одного комплекта → 2 строки",
          len(df) == 2, f"len={len(df)}")
    check("4а: строки — разные документы (doc_id)",
          set(df["doc_id"]) == {1, 2}, f"doc_id={list(df['doc_id'])}")
    check("4а: колонка комплекта сохранена",
          set(df["complex"]) == {KIT_B})

    # (б) хвостовой пробел в статусе замечания → лист попадает в отбор
    seed_db()
    add_document(3, f"{KIT_B}-3.PDF", KIT_B, "B", discipline="КЖ")
    add_comment(3, 3, "Закрыто ", "2026-03-01T10:00:00")

    df = _load_ready_for_a()
    check("4б: 'Закрыто ' (хвостовой пробел) → лист в отборе",
          KIT_B in df["complex"].values, f"complex={list(df['complex'])}")

    # Колонка «Название» берётся из documents.name
    seed_db()
    with sqlite_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, leaf, complex, status, sheet_number, "
            "discipline, name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (4, f"{KIT_B}-4.PDF", KIT_B, "B", "1", "КЖ", "Название листа X"),
        )
    add_comment(4, 4, "Закрыто", "2026-04-01T10:00:00")
    df = _load_ready_for_a()
    check("4б: sheet_name = documents.name",
          len(df) == 1 and df["sheet_name"].iloc[0] == "Название листа X",
          f"sheet_name={list(df['sheet_name'])}")

    # (в) реестр: лист с «Закрыто» и «Аннулировано» → обе строки видны
    seed_db()
    add_document(5, f"{KIT_B}-5.PDF", KIT_B, "B", discipline="КЖ")
    add_comment(5, 5, "Закрыто", "2026-05-01T10:00:00")
    add_comment(6, 5, "Аннулировано", "2026-05-02T10:00:00")

    reg = authors._load_closed_comments_for_doc(5)
    check("4в: реестр — 2 строки (Закрыто + Аннулировано)",
          len(reg) == 2, f"len={len(reg)}")
    check("4в: реестр — статусы присутствуют",
          set(reg["comment_status"]) == {"Закрыто", "Аннулировано"},
          f"статусы={list(reg['comment_status'])}")
    check("4в: реестр — есть колонка текста и даты",
          {"comment_text", "changed_at"}.issubset(reg.columns))


# ===========================================================================
#  Запуск всех блоков
# ===========================================================================
block0()
block1()
block2()
block3()
block4()

# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.12 пройдены.")
