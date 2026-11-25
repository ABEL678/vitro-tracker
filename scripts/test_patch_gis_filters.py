"""Тест правки 4 — фильтр дисциплин и атрибуты ГИС-строк.

Обновлён патчем 22.13:
  е1. Вариант «ГИС» в фильтре «Дисциплина» РОВНО ОДИН — константа
      "ГИС", без по-комплектных значений (load_discipline_options).
  е2. Фильтр «Комплект» содержит ГИС-комплекты (_plan_kit_options_fn).
  е3. Фильтр «Раздел» содержит КИТСО (load_section_options).
  е4. Строки ГИС-РД-* в таблице плана несут дисциплину 03_ЭС и раздел
      КИТСО (_vitro_kit_meta / _build_overview).

Тест работает на КОПИИ боевой БД (data/vitro.db не меняется):
файл копируется во временную папку, SQLITE_PATH указывает на копию
ДО импорта vitro.*.

Запуск: .venv\\Scripts\\python.exe scripts\\test_patch_gis_filters.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_gis_filters_")
_COPY = Path(_TMPDIR) / "vitro_copy.db"
shutil.copyfile(ROOT / "data" / "vitro.db", _COPY)
os.environ["SQLITE_PATH"] = str(_COPY)

# Патч 22.13: тест не зависит от того, применена ли миграция к боевой
# БД — прогоняем её на копии (идемпотентно).
import migrate_patch22_13 as mig  # noqa: E402

with sqlite3.connect(_COPY) as _conn:
    mig.migrate_conn(_conn)

import vitro.ui._filters as flt  # noqa: E402
import vitro.ui.plan as plan  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    msg = f"[{status}] {name}" + (f" — {detail}" if detail and not cond else "")
    print(msg)
    if not cond:
        FAILED.append(name)


# ---- е1: ровно один пункт «ГИС» ----
disc = flt.load_discipline_options()
gis_keys = [k for k in disc if "ГИС" in k.upper()]
check("е1: в «Дисциплине» ровно один пункт «ГИС»",
      gis_keys == ["ГИС"], f"найдено: {gis_keys}")
check("е1: подпись пункта — «ГИС»",
      disc.get("ГИС") == "ГИС", f"подпись: {disc.get('ГИС')!r}")
check("е1: «Дисциплина» не пустая", len(disc) > 0, f"len={len(disc)}")

# ---- е2: ГИС-комплекты в фильтре «Комплект» ----
kits = plan._plan_kit_options_fn((), (), ())
gis_kits = [k for k in kits if k.startswith("ГИС-РД-")]
check("е2: в «Комплекте» есть ГИС-комплекты",
      len(gis_kits) > 0, f"ГИС-комплектов: {len(gis_kits)}")
check("е2: количество ГИС-комплектов = 31",
      len(gis_kits) == 31, f"фактически: {len(gis_kits)}")

# ---- е3: КИТСО в разделах ----
sections = flt.load_section_options(())
check("е3: в «Разделе» есть КИТСО",
      "КИТСО" in sections, f"разделы: {sorted(sections)[:20]}")

# ---- е4: ГИС-строки плана: 03_ЭС и КИТСО ----
meta = plan._vitro_kit_meta()
gis_codes = [c for c in meta if c.startswith("ГИС-РД-")]
bad = [(c, meta[c]["discipline"], meta[c]["section"]) for c in gis_codes
       if meta[c]["discipline"] != "03_ЭС" or meta[c]["section"] != "КИТСО"]
check("е4: все ГИС-РД-* в _vitro_kit_meta: дисциплина 03_ЭС, раздел КИТСО",
      gis_codes and not bad,
      f"ГИС={len(gis_codes)}, отклонения: {bad[:5]}")

ov = plan._build_overview(include_vitro_only=True)
ov_gis = ov[ov["code"].str.startswith("ГИС-РД-")]
bad_ov = ov_gis[(ov_gis["discipline"] != "03_ЭС")
                | (ov_gis["section"] != "КИТСО")]
check("е4: сводная таблица — ГИС-строки несут 03_ЭС и КИТСО",
      len(ov_gis) > 0 and bad_ov.empty,
      f"строк={len(ov_gis)}, отклонения={len(bad_ov)}")

if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)}")
    for n in FAILED:
        print("  -", n.encode("ascii", "replace").decode("ascii"))
    sys.exit(1)
print("\nБлок (е) пройден.")
