# test_patch11.py — тесты глобальной дедупликации листов (PATCH-11)
"""
Проверяет (PATCH-11) единый стандарт «один уникальный лист = одна строка»:

  1. vitro/sheets.py: sheet_key / dedup_document_rows / dedup_documents /
     sql_count_sheets — ревизии и повторные выгрузки склеиваются,
     остаётся последняя ревизия (MAX(status_date), затем MAX(id));
     пустой sheet_number → fallback по id; разные комплекты не склеиваются.
  2. sqlite_db.db_stats: ключ "sheets" = уникальные листы,
     "documents" = строки (файлы).
  3. Сводка по проекту: _load_documents возвращает уникальные листы;
     бублик «Листов всего» и разрез считают листы, а не файлы
     (сценарий «8 файлов → 5 листов» — аналог «52 против 44»).
  4. Экспорт: _load_discipline_summary / _load_complex_summary /
     _load_section_summary — колонка «Листов» = уникальные листы,
     статусы A/B/C — по последней ревизии; замечания НЕ теряются
     (замечание у старой ревизии продолжает считаться).
  5. xlsx_builder.compute_stats — «Всего листов» по уникальным листам.
  6. Поиск: замечание, привязанное к старой ревизии, находится
     (дедупликация листов не прячет замечания).

Запуск: python3 scripts/test_patch11.py
"""
import os
import sys

REPO = "/home/z/my-project/vitro-tracker"
sys.path.insert(0, REPO)
os.environ["SQLITE_PATH"] = "data/vitro_test11.db"
os.chdir(REPO)

import pandas as pd  # noqa: E402

import vitro.sqlite_db as db  # noqa: E402
from vitro.sheets import (  # noqa: E402
    dedup_document_rows,
    dedup_documents,
    sheet_key,
    sql_count_sheets,
)

DB_FILE = os.path.join(REPO, "data", "vitro_test11.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
db.init_db()

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------------
# Фикстура: 8 файлов → 5 уникальных листов
# ------------------------------------------------------------------
def doc(i, cx, disc, sn, status, sd, section="Корпус 1"):
    return dict(
        id=i, leaf=f"FILE_{i}.pdf", discipline=disc, section=section,
        complex=cx, status=status, revision="1", dir_ref="",
        name=f"Лист {sn or i}", status_date=sd, sheet_number=sn,
        vitro_guid=f"g{i}", vitro_version=1,
    )


K1 = "АТ-РД-АР1-С-00"
K2 = "АТ-РД-КМ1-С-00"

docs = [
    # Лист 10.01 — три ревизии C → B → A; остаётся последняя (A)
    doc(1, K1, "АР", "10.01", "C", "2025-01-10"),
    doc(2, K1, "АР", "10.01", "B", "2025-02-10"),
    doc(3, K1, "АР", "10.01", "A", "2025-03-10"),
    # Лист 10.02 — повторная выгрузка той же ревизии (та же дата):
    # остаётся строка с бОльшим id
    doc(4, K1, "АР", "10.02", "B", "2025-02-11"),
    doc(5, K1, "АР", "10.02", "B", "2025-02-11"),
    # Листы без номера (sheet_number пустой) — два самостоятельных листа
    doc(6, K1, "АР", "", "C", "2025-01-15"),
    doc(7, K1, "АР", None, "C", "2025-01-16"),
    # Тот же номер 10.01 в ДРУГОМ комплекте — отдельный лист
    doc(8, K2, "КМ", "10.01", "A", "2025-04-01"),
]

comments = [
    # Замечание привязано к СТАРОЙ ревизии листа 10.01 (doc_id=1):
    # после дедупликации ревизия исчезает из подсчёта листов,
    # но замечание обязано остаться во всех замечаниевых счётчиках.
    dict(id=101, doc_id=1, comment="Тест-замечание к старой ревизии",
         status="Новое", author="Инженер", created="2025-02-01 10:00:00",
         fix_date=None),
]

n_docs, n_comments = db.save_all(docs, comments, {d["id"] for d in docs})
check("fixture_saved", (n_docs, n_comments) == (8, 1),
      f"docs={n_docs}, comments={n_comments}")

# ------------------------------------------------------------------
# 1. sheets.py — ключ и дедупликация
# ------------------------------------------------------------------
check("key_sheet", sheet_key(docs[0]) == ("sheet", K1, "10.01"))
check("key_row_fallback", sheet_key(docs[6]) == ("row", 7))

kept = dedup_document_rows(docs)
# 5 = 10.01 + 10.02 + два листа без номера + 10.01 в K2
check("dedup_count", len(kept) == 5, f"kept={len(kept)}, ожидалось 5")
by_id = {r["id"]: r for r in kept}

# 10.01 — последняя ревизия (дата 2025-03-10)
check("latest_by_date", 3 in by_id and 1 not in by_id and 2 not in by_id,
      "лист 10.01: осталась ревизия id=3 (A)")
# Повторная выгрузка (та же дата) — остаётся бОльший id
check("latest_by_id", 5 in by_id and 4 not in by_id,
      "лист 10.02: осталась строка id=5")
# Пустой sheet_number — обе строки остаются
check("empty_sn_kept", 6 in by_id and 7 in by_id, "строки без номера не склеены")
# Другой комплект — не склеился с K1
check("cross_complex_kept", 8 in by_id, "K2 10.01 — отдельный лист")
# Статус оставленной ревизии
check("latest_status", by_id[3]["status"] == "A", "статус последней ревизии")
# Порядок результата — по id
check("sorted_by_id", [r["id"] for r in kept] == sorted(r["id"] for r in kept))

df_kept = dedup_documents(pd.DataFrame(docs))
check("df_wrapper_len", len(df_kept) == 5)
check("df_wrapper_cols", list(df_kept.columns) == list(pd.DataFrame(docs).columns))
check("df_empty_safe", len(dedup_documents(pd.DataFrame(
    [dict(id=9, complex="X", sheet_number=None)]))) == 1)
check("df_no_column", len(dedup_documents(pd.DataFrame(
    [dict(id=1, complex="X")]))) == 1)  # нет sheet_number → без изменений

# ------------------------------------------------------------------
# 2. db_stats: sheets vs documents
# ------------------------------------------------------------------
stats = db.db_stats()
check("db_stats_documents", stats["documents"] == 8, f"={stats['documents']}")
check("db_stats_sheets", stats["sheets"] == 5, f"={stats['sheets']}")

with db.get_conn() as conn:
    per_cx = {
        r["complex"]: r["n"]
        for r in conn.execute(
            f"SELECT d.complex AS complex, {sql_count_sheets('d')} AS n "
            "FROM documents AS d GROUP BY d.complex"
        )
    }
check("sql_per_complex", per_cx.get(K1) == 4 and per_cx.get(K2) == 1,
      f"K1={per_cx.get(K1)}, K2={per_cx.get(K2)}")

# ------------------------------------------------------------------
# 3. Сводка по проекту: _load_documents → уникальные листы
# ------------------------------------------------------------------
import vitro.ui.summary as summary  # noqa: E402

docs_df = summary._load_documents()
check("summary_docs_deduped", len(docs_df) == 5, f"={len(docs_df)}")
st_u = docs_df["status"].fillna("").astype(str).str.strip().str.upper()
check("summary_statuses", (int((st_u == "A").sum()) == 2
                           and int((st_u == "B").sum()) == 1
                           and int((st_u == "C").sum()) == 2),
      f"A={(st_u == 'A').sum()}, B={(st_u == 'B').sum()}, "
      f"C={(st_u == 'C').sum()}")

# Разрез по дисциплинам: АР = 4 листа (K1), КМ = 1 лист (K2)
rows = summary._load_summary_by_discipline()
ar = rows[rows["Код"] == "АР"].iloc[0]
km = rows[rows["Код"] == "КМ"].iloc[0]
check("summary_disc_docs", int(ar["docs_total"]) == 4,
      f"АР docs_total={ar['docs_total']}")
check("summary_disc_a", int(ar["doc_a"]) == 1, "АР: A=1 (10.01 после ревизий)")
check("summary_disc_km", int(km["docs_total"]) == 1,
      f"КМ docs_total={km['docs_total']}")

# ------------------------------------------------------------------
# 4. Экспорт: «Листов» = уникальные листы, замечания не теряются
# ------------------------------------------------------------------
import vitro.ui.export as export  # noqa: E402

disc = export._load_discipline_summary()
d_ar = disc[disc["Код"] == "АР"].iloc[0]
check("export_disc_docs", int(d_ar["Листов"]) == 4,
      f"АР Листов={d_ar['Листов']}")
check("export_disc_remarks", int(d_ar["Всего замечаний"]) == 1,
      f"АР Всего замечаний={d_ar['Всего замечаний']} (замечание у "
      "старой ревизии не потеряно)")

cx = export._load_complex_summary()
c_k1 = cx[cx["Комплект"] == K1].iloc[0]
c_k2 = cx[cx["Комплект"] == K2].iloc[0]
check("export_cx_docs", int(c_k1["Листов"]) == 4, f"K1 Листов={c_k1['Листов']}")
check("export_cx_a", int(c_k1["Статус A"]) == 1,
      f"K1 Статус A={c_k1['Статус A']} (последняя ревизия 10.01)")
check("export_cx_b", int(c_k1["Статус B"]) == 1,
      f"K1 Статус B={c_k1['Статус B']} (10.02)")
check("export_cx_c", int(c_k1["Статус C"]) == 2,
      f"K1 Статус C={c_k1['Статус C']} (2 листа без номера)")
check("export_cx_remarks", int(c_k1["Всего"]) == 1,
      f"K1 Всего замечаний={c_k1['Всего']}")
check("export_cx_km", int(c_k2["Листов"]) == 1, f"K2 Листов={c_k2['Листов']}")

sect = export._load_section_summary()
# Сводная по разделам группируется по (дисциплина, раздел):
# «Корпус 1» → две строки: АР=4 листа, КМ=1 лист; суммарно 5.
sect_rows = sect[sect["Раздел"] == "Корпус 1"]
check("export_sect_docs", len(sect_rows) == 2
      and int(sect_rows["Листов"].sum()) == 5,
      f"Раздел «Корпус 1»: строк={len(sect_rows)}, "
      f"Листов всего={int(sect_rows['Листов'].sum())} (4 АР + 1 КМ)")

# ------------------------------------------------------------------
# 5. xlsx_builder.compute_stats
# ------------------------------------------------------------------
import vitro.xlsx_builder as xb  # noqa: E402

data = xb.load_all_data()
check("xlsx_load_cols", all("sheet_number" in d for d in data["documents"]))
st11 = xb.compute_stats(data["documents"], data["comments"])
k1s = st11["by_complex"][K1]
check("xlsx_cx_docs", k1s["docs_total"] == 4, f"K1 docs_total={k1s['docs_total']}")
check("xlsx_cx_a", k1s["doc_a"] == 1, f"K1 doc_a={k1s['doc_a']}")
check("xlsx_cx_remarks", k1s["total"] == 1,
      f"K1 всего замечаний={k1s['total']} (не потеряно)")
k2s = st11["by_complex"][K2]
check("xlsx_cx_km_docs", k2s["docs_total"] == 1, f"K2 docs_total={k2s['docs_total']}")

# ------------------------------------------------------------------
# 6. Поиск: замечание у старой ревизии находится
# ------------------------------------------------------------------
import vitro.ui.search as search  # noqa: E402

found = search._search_comments(query="Тест-замечание")
check("search_old_revision_remark", len(found) == 1 and int(
    found.iloc[0]["ID"]) == 101,
    f"найдено={len(found)}")

# ------------------------------------------------------------------
# Итог
# ------------------------------------------------------------------
print()
if FAILURES:
    print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
