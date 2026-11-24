"""Тесты патча 22.16 — одиночный реестровый PDF «Р-NNN.pdf» в папке комплекта.

Проблема (следствие диагностики 22.11, раунд 2): в папках комплектов
(АТ-РД-КИ.ВК-2-00, АТ-РД-КИ.ГПТ-2-02, …, всего 9) лежит единственный
PDF с именем вида «Р-381.pdf». looks_like_rd_code такие имена не
распознаёт — синк пропускал файлы, комплекты оставались
«Планом к загрузке», хотя фактически загружены в Витро.

Исправление (vitro/sharepoint.py, выборка документов):
  - is_single_registry_pdf — строгий паттерн «Р-<цифры>.pdf»;
  - _collect_single_registry_pdfs — все условия обязательны:
      1) имя строго «Р-NNN.pdf»;
      2) файл непосредственно в папке комплекта (имя папки — РД-код);
      3) в папке (без подпапок) нет других PDF с именами
         looks_like_rd_code;
      4) файл не в служебных деревьях (ТЕСТ/, «на удаление/», корень).
  - fetch_atp_documents: реестровый PDF записывается документом
    (complex — нормализованный шифр папки, leaf — без расширения),
    идемпотентность — как у обычных документов (дедуп по UniqueId,
    save_all перезаписывает documents).

Блоки проверок:
  0. (д) is_single_registry_pdf: строгий паттерн.
  1. (а) папка комплекта с единственным Р-381.pdf → документ создан,
     complex = шифр комплекта, leaf = «Р-381».
  2. (б) повторный синк (fetch + save_all дважды) — дубликатов нет.
  3. (в) папка с шифрованными PDF + Р-381 → Р-381 игнорируется.
  4. (г) Р-381.pdf вне папки комплекта → игнорируется.
  5. (д) e2e: «Р-3.5.pdf», «РА-12.pdf», «р-381.pdf» → не матчатся.
  6. Регресс: обычные документы (КЖ1) не затронуты.
  7. (е) регресс: полный список патчей 21–22.15 построчно, EXIT=0.

СТОП-ЛИСТ: real data/vitro.db не открывается — временная БД
назначается через SQLITE_PATH ДО импорта vitro.sqlite_db.

Запуск: python scripts/test_patch22_16.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Временная БД ДО импорта vitro.* ---
_TMPDIR = tempfile.mkdtemp(prefix="vitro_test_patch22_16_")
os.environ["SQLITE_PATH"] = str(Path(_TMPDIR) / "test_vitro.db")

import io  # noqa: E402

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace")

import vitro.sqlite_db as sqlite_db  # noqa: E402
from vitro.sharepoint import (  # noqa: E402
    is_single_registry_pdf,
    _collect_single_registry_pdfs,
    fetch_atp_documents,
    ATP_WORK,
    GIS_WORK,
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


# 9 комплектов из диагностики 22.11 (раунд 2)
KITS = [
    "АТ-РД-КИ.ВК-2-00",
    "АТ-РД-КИ.ГПТ-2-02",
    "АТ-РД-КИ.ИС-2-00",
    "АТ-РД-КИ.ИС1-1-00",
    "АТ-РД-КИ.ИС1-2-00",
    "АТ-РД-КИ.ОВ1-2-00",
    "АТ-РД-КИ.ОВ2-2-00",
    "АТ-РД-КИ.ТМ-С-П1",
    "АТ-РД-КИ.ХЦ-С-П2",
]


def sp_item(item_id, guid, leaf, dir_ref, version="1.0", **extra):
    d = {
        "Id": item_id,
        "UniqueId": guid,
        "OData__UIVersionString": version,
        "FileLeafRef": leaf,
        "FileDirRef": dir_ref,
        "VitroBaseStatus": None,
        "VitroBaseDocumentRevision": "1.0",
        "VitroBaseFullName": "",
        "VitroBaseStatusDate": "2026-11-20",
        "VitroBaseDocSheetNumber": "",
    }
    d.update(extra)
    return d


# ===========================================================================
#  БЛОК 0 — (д) is_single_registry_pdf: строгий паттерн
# ===========================================================================
check("0: 'Р-381.pdf' матчится", is_single_registry_pdf("Р-381.pdf"))
check("0: 'Р-3810.pdf' матчится", is_single_registry_pdf("Р-3810.pdf"))
check("0: 'Р-381.PDF' матчится (регистр расширения не важен)",
      is_single_registry_pdf("Р-381.PDF"))
check("0: 'Р-3.5.pdf' НЕ матчится", not is_single_registry_pdf("Р-3.5.pdf"))
check("0: 'РА-12.pdf' НЕ матчится", not is_single_registry_pdf("РА-12.pdf"))
check("0: 'р-381.pdf' (строчная р) НЕ матчится",
      not is_single_registry_pdf("р-381.pdf"))
check("0: 'P-381.pdf' (латиница) НЕ матчится",
      not is_single_registry_pdf("P-381.pdf"))
check("0: 'Р-.pdf' НЕ матчится", not is_single_registry_pdf("Р-.pdf"))
check("0: 'Р-381.pdfx' НЕ матчится", not is_single_registry_pdf("Р-381.pdfx"))
check("0: '' НЕ матчится", not is_single_registry_pdf(""))
check("0: None НЕ матчится", not is_single_registry_pdf(None))
check("0: 'АТ-РД-КЖ1-1-00-1.1.PDF' НЕ матчится",
      not is_single_registry_pdf("АТ-РД-КЖ1-1-00-1.1.PDF"))


# ===========================================================================
#  БЛОК 1 — (а) папка комплекта с единственным Р-381.pdf
# ===========================================================================
# Проверяем _collect_single_registry_pdfs напрямую
RAW_A = [
    sp_item(5001, "guid-reg-vk", "Р-381.pdf",
            f"{ATP_WORK}/03_ВК/АТ-РД-КИ.ВК-2-00"),
]
picked = _collect_single_registry_pdfs(RAW_A)
check("1: единственный Р-381.pdf отобран правилом", len(picked) == 1,
      f"отобрано {len(picked)}")

# e2e через fetch_atp_documents (мок выдачи SharePoint)
MOCK_A = list(RAW_A) + [
    # обычный документ для регресса (блок 6)
    sp_item(5002, "guid-kzh1", "АТ-РД-КЖ1-1-00-1.1.PDF",
            f"{ATP_WORK}/02_КР/Раздел 1/АТ-РД-КЖ1-1-00"),
]


def mock_fetch_all(*args, **kwargs):
    flt = kwargs.get("flt", "") or (args[3] if len(args) > 3 else "")
    if "02_ГИС" in flt:
        return []
    return MOCK_A


def mock_fetch_doc_statuses(*a, **k):
    return {}


with patch("vitro.sharepoint._fetch_all", mock_fetch_all), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):
    docs_a = fetch_atp_documents()

reg_docs = [d for d in docs_a if d["leaf"] == "Р-381"]
check("1: (а) документ создан (ровно один с leaf='Р-381')",
      len(reg_docs) == 1, f"найдено {len(reg_docs)}")
if reg_docs:
    d = reg_docs[0]
    check("1: (а) complex = шифр комплекта из папки",
          d["complex"] == "АТ-РД-КИ.ВК-2-00",
          f"получено «{d['complex']}»")
    check("1: (а) leaf = 'Р-381' (без расширения)",
          d["leaf"] == "Р-381", f"получено «{d['leaf']}»")
    check("1: (а) dir_ref — папка комплекта",
          d["dir_ref"] == f"{ATP_WORK}/03_ВК/АТ-РД-КИ.ВК-2-00",
          f"получено «{d['dir_ref']}»")
    check("1: (а) discipline из пути = '03_ВК'",
          d["discipline"] == "03_ВК", f"получено «{d['discipline']}»")

# ===========================================================================
#  БЛОК 2 — (б) повторный синк: дубликатов нет
# ===========================================================================
with patch("vitro.sharepoint._fetch_all", mock_fetch_all), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):
    docs_b1 = fetch_atp_documents()
    docs_b2 = fetch_atp_documents()

ids1 = sorted(d["id"] for d in docs_b1)
ids2 = sorted(d["id"] for d in docs_b2)
check("2: (б) повторный fetch — тот же список id",
      ids1 == ids2, f"{len(ids1)} vs {len(ids2)}")
check("2: (б) в выборке нет дублей id", len(ids1) == len(set(ids1)))

# Полный цикл записи: save_all дважды — в БД без дублей
doc_ids = {d["id"] for d in docs_b1}
sqlite_db.save_all(docs_b1, [], doc_ids)
with sqlite_db.get_conn() as conn:
    n1 = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    n_reg1 = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE leaf = 'Р-381'"
    ).fetchone()["c"]
sqlite_db.save_all(docs_b2, [], doc_ids)
with sqlite_db.get_conn() as conn:
    n2 = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    n_reg2 = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE leaf = 'Р-381'"
    ).fetchone()["c"]
    n_pairs = conn.execute(
        "SELECT COUNT(*) c FROM (SELECT complex, leaf, COUNT(*) k "
        "FROM documents GROUP BY complex, leaf HAVING k > 1)"
    ).fetchone()["c"]
check("2: (б) save_all повторно — число документов не выросло",
      n1 == n2, f"{n1} vs {n2}")
check("2: (б) реестровых документов по-прежнему 1", n_reg1 == 1 and n_reg2 == 1,
      f"{n_reg1} vs {n_reg2}")
check("2: (б) нет дублей (complex, leaf)", n_pairs == 0, f"дублей {n_pairs}")

# ===========================================================================
#  БЛОК 3 — (в) папка с шифрованными PDF + Р-381 → Р-381 игнорируется
# ===========================================================================
RAW_C = [
    sp_item(6001, "guid-is1-sheet", "АТ-РД-КИ.ИС1-1-00-1.1.PDF",
            f"{ATP_WORK}/03_ИС/АТ-РД-КИ.ИС1-1-00"),
    sp_item(6002, "guid-is1-reg", "Р-381.pdf",
            f"{ATP_WORK}/03_ИС/АТ-РД-КИ.ИС1-1-00"),
]
picked_c = _collect_single_registry_pdfs(RAW_C)
check("3: (в) правило не срабатывает при РД-соседе", len(picked_c) == 0,
      f"отобрано {len(picked_c)}")

with patch("vitro.sharepoint._fetch_all",
           lambda *a, **k: RAW_C if "02_ГИС" not in (k.get("flt") or "") else []), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):
    docs_c = fetch_atp_documents()

check("3: (в) в выборке нет leaf='Р-381'",
      not any(d["leaf"] == "Р-381" for d in docs_c))
check("3: (в) шифрованный лист создан как обычно",
      any(d["leaf"] == "АТ-РД-КИ.ИС1-1-00-1.1.PDF"
          and d["complex"] == "АТ-РД-КИ.ИС1-1-00" for d in docs_c))

# ===========================================================================
#  БЛОК 4 — (г) Р-381.pdf вне папки комплекта → игнорируется
# ===========================================================================
RAW_D = [
    # корень «01_В процессе» (вне комплектов)
    sp_item(7001, "guid-root", "Р-381.pdf", ATP_WORK),
    # папка не-комплект (дисциплина)
    sp_item(7002, "guid-disc", "Р-381.pdf", f"{ATP_WORK}/03_ВК"),
    # подпапка внутри комплекта (не непосредственно в папке комплекта)
    sp_item(7003, "guid-sub", "Р-381.pdf",
            f"{ATP_WORK}/03_ВК/АТ-РД-КИ.ВК-2-00/Подпапка"),
    # служебное дерево «на удаление»
    sp_item(7004, "guid-del", "Р-381.pdf",
            f"{ATP_WORK}/на удаление/АТ-РД-КИ.ВК-2-00"),
    # служебное дерево ТЕСТ
    sp_item(7005, "guid-test", "Р-381.pdf",
            f"{ATP_WORK}/ТЕСТ/АТ-РД-КИ.ВК-2-00"),
]
picked_d = _collect_single_registry_pdfs(RAW_D)
check("4: (г) вне папки комплекта правило не срабатывает",
      len(picked_d) == 0, f"отобрано {len(picked_d)}")

with patch("vitro.sharepoint._fetch_all",
           lambda *a, **k: RAW_D if "02_ГИС" not in (k.get("flt") or "") else []), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):
    docs_d = fetch_atp_documents()
check("4: (г) в выборке нет ни одного leaf='Р-381'",
      not any(d["leaf"] == "Р-381" for d in docs_d))

# ===========================================================================
#  БЛОК 5 — (д) e2e: нестрогие имена в папке комплекта игнорируются
# ===========================================================================
RAW_E = [
    sp_item(8001, "guid-bad1", "Р-3.5.pdf",
            f"{ATP_WORK}/03_ВК/АТ-РД-КИ.ВК-2-00"),
    sp_item(8002, "guid-bad2", "РА-12.pdf",
            f"{ATP_WORK}/03_ВК/АТ-РД-КИ.ВК-2-00"),
    sp_item(8003, "guid-bad3", "р-381.pdf",
            f"{ATP_WORK}/03_ВК/АТ-РД-КИ.ВК-2-00"),
]
picked_e = _collect_single_registry_pdfs(RAW_E)
check("5: (д) 'Р-3.5.pdf' / 'РА-12.pdf' / 'р-381.pdf' не отбираются",
      len(picked_e) == 0, f"отобрано {len(picked_e)}")

with patch("vitro.sharepoint._fetch_all",
           lambda *a, **k: RAW_E if "02_ГИС" not in (k.get("flt") or "") else []), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):
    docs_e = fetch_atp_documents()
check("5: (д) в выборке нет документов из нестрогих имён",
      len(docs_e) == 0, f"найдено {len(docs_e)}")

# ===========================================================================
#  БЛОК 6 — регресс: обычные документы не затронуты
# ===========================================================================
kzh = [d for d in docs_a if d["complex"] == "АТ-РД-КЖ1-1-00"]
check("6: обычный лист КЖ1 на месте", len(kzh) == 1, f"найдено {len(kzh)}")
if kzh:
    check("6: leaf обычного листа — с расширением (как раньше)",
          kzh[0]["leaf"] == "АТ-РД-КЖ1-1-00-1.1.PDF",
          f"получено «{kzh[0]['leaf']}»")

# Все 9 комплектов диагностики: папка с единственным Р-NNN.pdf → документ
RAW_ALL = []
for i, kit in enumerate(KITS):
    RAW_ALL.append(sp_item(9000 + i, f"guid-kit-{i}", f"Р-{381 + i}.pdf",
                           f"{ATP_WORK}/03_ВК/{kit}"))
with patch("vitro.sharepoint._fetch_all",
           lambda *a, **k: RAW_ALL if "02_ГИС" not in (k.get("flt") or "") else []), \
     patch("vitro.sharepoint.fetch_doc_statuses", mock_fetch_doc_statuses):
    docs_all = fetch_atp_documents()
reg_all = [d for d in docs_all if d["leaf"].startswith("Р-")]
check("6: все 9 комплектов получили по документу", len(reg_all) == 9,
      f"найдено {len(reg_all)}")
got_kits = sorted(d["complex"] for d in reg_all)
check("6: шифры комплектов совпадают со списком диагностики",
      got_kits == sorted(KITS), f"получено {got_kits}")
check("6: комплекты не задублированы",
      len(set(got_kits)) == len(got_kits))

# ===========================================================================
#  БЛОК 7 — (е) регресс: полный список патчей 21–22.15 построчно
# ===========================================================================
REGRESSION = [
    "test_patch21.py",
    "test_patch22.py",
    "test_patch22_3.py",
    "test_patch22_5.py",
    "test_patch22_6.py",
    "test_patch22_8.py",
    "test_patch22_9.py",
    "test_patch22_10.py",
    "test_patch22_11.py",
    "test_patch22_12.py",
    "test_patch22_13.py",   # отсутствует в рабочей копии (см. примечание)
    "test_patch22_14.py",   # отсутствует в рабочей копии (см. примечание)
    "test_patch22_15.py",   # отсутствует в рабочей копии (см. примечание)
    "test_patch_gis_filters.py",
]

env = {k: v for k, v in os.environ.items() if k != "SQLITE_PATH"}
env["PYTHONIOENCODING"] = "utf-8"

for name in REGRESSION:
    script = ROOT / "scripts" / name
    if not script.exists():
        try:
            print(f"[SKIP] {name} — файла нет в scripts/ (патч не в этой "
                  f"рабочей копии)")
        except UnicodeEncodeError:
            pass
        continue
    r = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                       capture_output=True, env=env)
    check(f"7: (е) {name} EXIT=0", r.returncode == 0,
          f"EXIT={r.returncode}; хвост вывода: "
          f"{r.stdout.decode('utf-8', 'replace')[-300:]}")

# ===========================================================================
#  Итог
# ===========================================================================
if FAILED:
    print(f"\nПРОВАЛЕНО: {len(FAILED)} проверок:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("\nВсе проверки патча 22.16 пройдены.")
