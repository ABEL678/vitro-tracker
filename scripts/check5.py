import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vitro.ui.deadlines import _load_all_categorized

df = _load_all_categorized()
print(f"Всего: {len(df):,}\n")

# Нормализуем doc_status для чистоты
df["_ds"] = df["doc_status"].astype(str).str.strip().str.upper()

# ---------- 1. Активные на листах Аннулировано ----------
active_flags = [
    "new_overdue", "new_in_progress",
    "in_work_overdue", "in_work_in_progress",
    "rejected_overdue", "rejected_in_progress",
    "discussion_overdue", "discussion_in_progress",
]

mask_annul = df["_ds"] == "АННУЛИРОВАНО"
mask_active = df["category_flag"].isin(active_flags)

sub1 = df[mask_annul & mask_active]
print(f"=== 1. Активные к листам АННУЛИРОВАНО: {len(sub1)} ===")
print(f"  new_*:        {sub1['category_flag'].isin(['new_overdue','new_in_progress']).sum()}")
print(f"  in_work_*:    {sub1['category_flag'].isin(['in_work_overdue','in_work_in_progress']).sum()}")
print(f"  rejected_*:   {sub1['category_flag'].isin(['rejected_overdue','rejected_in_progress']).sum()}")
print(f"  discussion_*: {sub1['category_flag'].isin(['discussion_overdue','discussion_in_progress']).sum()}")
print(f"  flags: {sub1['category_flag'].value_counts().to_dict()}")

# ---------- 2. closed_by_doc на листах Аннулировано ----------
sub2 = df[mask_annul & (df["category_flag"] == "closed_by_doc_status")]
print(f"\n=== 2. closed_by_doc_status & АННУЛИРОВАНО: {len(sub2)} ===")

# ---------- 3. closed_by_doc на листах A/B/других ----------
print("\n=== 3. closed_by_doc_status по doc_status ===")
sub3 = df[df["category_flag"] == "closed_by_doc_status"]
print(sub3["_ds"].value_counts().to_dict())

# ---------- 4. Все замечания по doc_status ----------
print("\n=== 4. Все замечания по doc_status ===")
print(df["_ds"].value_counts().to_dict())

# ---------- 5. waiting_* на листах Аннулировано ----------
sub5 = df[mask_annul & df["category_flag"].str.startswith("waiting_", na=False)]
print(f"\n=== 5. waiting_* на листах АННУЛИРОВАНО: {len(sub5)} ===")
if len(sub5):
    print(f"  flags: {sub5['category_flag'].value_counts().to_dict()}")

# ---------- 6. abandoned на листах Аннулировано ----------
sub6 = df[mask_annul & (df["category_flag"] == "abandoned")]
print(f"\n=== 6. abandoned на листах АННУЛИРОВАНО: {len(sub6)} ===")

# ---------- 7. Сводка ----------
print(f"\n=== 7. Сводная проверка ===")
n_active_annul = len(sub1)
n_closed_annul = len(sub2)
print(f"  Активные к аннулированным листам: {n_active_annul}  (Секция 3, справочно)")
print(f"  Выполнено к аннулированным листам: {n_closed_annul}  (Секция 4)")