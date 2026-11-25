# vitro/sharepoint.py

import re
import urllib3
from typing import Any
from collections import defaultdict
from requests_ntlm import HttpNtlmAuth
import requests

from vitro.config import (
    VITRO_USER, VITRO_PASSWORD, VITRO_BASE_URL, VITRO_OKHTA_URL,
    VITRO_VERIFY_SSL, HEADERS,
)
from vitro.disciplines import GIS_DISCIPLINE
from vitro.sheets import looks_like_rd_code

# Гасим предупреждение InsecureRequestWarning ТОЛЬКО когда проверка
# сертификата осознанно отключена (.env: VITRO_VERIFY_SSL=false).
if not VITRO_VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
#  GUID-ы списков SharePoint
# ---------------------------------------------------------------------------
LIST_DOCS = "0ef108b5-50ea-4dfe-a70f-f7714e4c47b0"
LIST_COMMENTS = "1cbbafc5-1d8d-423a-a417-eec9a41859de"
LIST_COMMENT_STATUSES = "4eb52759-616f-4541-b41e-602a03079247"
LIST_AUTHORS = "28e41ba2-1a23-4b2d-aa05-55a203388a61"
LIST_DOC_STATUSES = "a00854d8-6130-4f26-89ea-6f4f940350e3"

# ---------------------------------------------------------------------------
#  Корневые пути
# ---------------------------------------------------------------------------
ATP_ROOT = "/okhta/DocProjectLib/01_РД/02_АТП"
ATP_WORK = ATP_ROOT + "/01_В процессе"

# Папка ГИС (патч 20)
GIS_ROOT = "/okhta/DocProjectLib/01_РД/02_ГИС"
GIS_WORK = GIS_ROOT + "/01_В процессе"

# ---------------------------------------------------------------------------
#  Исключения (TECT/, сервисные рендеры, аннулированные, архив)
# ---------------------------------------------------------------------------
_EXCLUDED_MARKERS = (
    "TECT", "ТЕСТ", "TEST",
    "_PRPDF_", "_EXPPDF_", "_PRPDF.", "_EXPPDF.",
    "_BACKUP", "_OLD", "_TRASH", "~$",
    "АННУЛИРОВАН", "АРХИВ", "ARCHIVE",
)

_EXCLUDED_LEAFS = {
    # Убрано: АТ-РД-СП-0-00.PDF / .DWG — патч 22.10, ранее исключались
    # вручную, теперь проходят обычную проверку looks_like_rd_code
}

# ---------------------------------------------------------------------------
#  Нормализация комплекта (точная копия Query_Complexes)
# ---------------------------------------------------------------------------
_LAT2CYR_PARTIAL = {
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н",
    "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т",
    "X": "Х", "Y": "У",
}


def normalize_complex(code: str) -> str:
    """Посимвольная нормализация: 11 конкретных букв латиницы → кириллица,
    затем верхний регистр и trim."""
    if not code:
        return ""
    s = code.replace("\u00a0", " ").strip()
    chars = []
    for ch in s:
        up = ch.upper()
        if up in _LAT2CYR_PARTIAL:
            cyr = _LAT2CYR_PARTIAL[up]
            chars.append(cyr if ch.isupper() else cyr.lower())
        else:
            chars.append(ch)
    return "".join(chars).upper().strip()


# ---------------------------------------------------------------------------
#  Статусы замечаний
# ---------------------------------------------------------------------------
STATUS_CLOSED = {"ЗАКРЫТО", "ВЫПОЛНЕНО"}
STATUS_ANNULLED = {"АННУЛИРОВАНО"}
STATUS_ACTIVE = {"НОВОЕ", "ПРИНЯТО В РАБОТУ", "НЕ ПРИНЯТО", "К ОБСУЖДЕНИЮ"}


def classify_comment_status(title: str) -> str:
    s = (title or "").strip().upper()
    if s in STATUS_CLOSED:
        return "closed"
    if s in STATUS_ANNULLED:
        return "annulled"
    if s in STATUS_ACTIVE:
        return "active"
    return "other"


# ---------------------------------------------------------------------------
#  HTTP
# ---------------------------------------------------------------------------
_AUTH = HttpNtlmAuth(VITRO_USER, VITRO_PASSWORD)


def _get(url: str, params: dict | None = None) -> dict:
    r = requests.get(
        url, auth=_AUTH, headers=HEADERS, params=params,
        verify=VITRO_VERIFY_SSL, timeout=180,
    )
    r.raise_for_status()
    return r.json()


def _items_url(base: str, guid: str) -> str:
    return f"{base}/_api/web/lists(guid'{guid}')/items"


def _fetch_all(
    base: str, guid: str, select: list[str],
    flt: str | None = None, page_size: int = 5000, max_items: int = 0,
) -> list[dict[str, Any]]:
    url = _items_url(base, guid)
    params: dict[str, str] = {"$select": ",".join(select), "$top": str(page_size)}
    if flt:
        params["$filter"] = flt

    out: list[dict] = []
    next_url: str | None = url
    next_params: dict | None = params

    while next_url:
        data = _get(next_url, next_params)
        d = data.get("d", {})
        out.extend(d.get("results", []))
        if max_items and len(out) >= max_items:
            return out[:max_items]
        next_url = d.get("__next")
        next_params = None
    return out


# ---------------------------------------------------------------------------
#  Разбор пути: дисциплина / раздел / комплект
# ---------------------------------------------------------------------------
def parse_path(file_dir_ref: str) -> tuple[str, str, str]:
    """
    По пути родительской папки определяет (дисциплина, раздел, комплект).
    Ожидает путь внутри /01_В процессе/.

    Патч 19: комплектом считается папка, чьё имя похоже на РД-код
    ЛЮБОЙ организации (looks_like_rd_code: «АТ-РД-…», «ГИС-РД-…» и
    др.), а не только начинающаяся с «АТ-РД-».

    Патч 22.13: для любого пути внутри GIS_WORK дисциплина — всегда
    константа "ГИС" (одно значение на все ГИС-комплекты), а не имя
    комплекта/шифр. Раздел и комплект разбираются как раньше.
    """
    # Патч 20: поддержка путей АТП и ГИС
    work_prefixes = (ATP_WORK + "/", GIS_WORK + "/")
    if not file_dir_ref or not any(file_dir_ref.startswith(p) for p in work_prefixes):
        return ("", "", "")

    # Патч 22.13: путь ГИС определяется по префиксу GIS_WORK
    is_gis = file_dir_ref.startswith(GIS_WORK + "/")

    # Определяем, какой префикс совпал, и обрезаем его
    rel = file_dir_ref
    for prefix in work_prefixes:
        if file_dir_ref.startswith(prefix):
            rel = file_dir_ref[len(prefix):].strip("/")
            break
    parts = [p for p in rel.split("/") if p]
    # Патч 20: комплект может идти сразу после папки «В процессе»
    # (без дисциплины): «01_В процессе/ГИС-РД-СБ-АСЗП-0-00»
    if not parts:
        return ("", "", "")

    # Ищем индекс комплекта
    complex_idx = next(
        (i for i in range(len(parts)) if looks_like_rd_code(parts[i])),
        None,
    )
    if complex_idx is None:
        # Нет комплекта — возвращаем дисциплину (если есть)
        discipline = GIS_DISCIPLINE if is_gis else parts[0]
        return (discipline, "/".join(parts[1:]), "")

    # Всё, что до complex_idx — это дисциплина/раздел
    discipline = GIS_DISCIPLINE if is_gis else parts[0]
    section = "/".join(parts[1:complex_idx]) if complex_idx > 1 else ""
    comp = parts[complex_idx]
    return (discipline, section, comp)


def _is_excluded(file_dir_ref: str, file_leaf_ref: str) -> bool:
    combined = (file_dir_ref or "") + "/" + (file_leaf_ref or "")
    upper = combined.upper()
    if any(m in upper for m in _EXCLUDED_MARKERS):
        return True
    if (file_leaf_ref or "").upper() in _EXCLUDED_LEAFS:
        return True
    return False


# ---------------------------------------------------------------------------
# Патч 22.16 — одиночный реестровый PDF вида «Р-NNN.pdf» в папке комплекта
# ---------------------------------------------------------------------------
# Строгий паттерн имени: «Р-» + цифры, строго верхняя кириллическая «Р»
# (регистр — как в данных), расширение «.pdf» в конце (регистр расширения
# не важен — SharePoint хранит FileLeafRef и как «.pdf», и как «.PDF»).
# «Р-3.5.pdf», «РА-12.pdf», «р-381.pdf» (строчная «р») НЕ проходят.
_SINGLE_REGISTRY_PDF_STEM_PATTERN = re.compile(r"^Р-\d+$")

# Служебные деревья для правила 22.16 в дополнение к _EXCLUDED_MARKERS:
# «на удаление/» там нет, а ТЕСТ/TECT/TEST, АННУЛИРОВАН, АРХИВ, ~$ и др.
# уже отсекаются _is_excluded. Корень вне комплектов отсекается
# условием «имя папки — шифр РД-комплекта».
_REGISTRY_SERVICE_DIR_MARKERS = ("НА УДАЛЕНИЕ",)


def is_single_registry_pdf(leaf: str) -> bool:
    """Патч 22.16: имя файла — одиночный реестровый PDF «Р-NNN.pdf».

    Строго: кириллическая «Р» верхнего регистра, дефис, только цифры;
    расширение «.pdf» в конце (регистр расширения не важен).
    «Р-3.5.pdf», «РА-12.pdf», «р-381.pdf» (строчная «р») НЕ проходят.
    """
    s = (leaf or "").strip()
    stem, dot, ext = s.rpartition(".")
    if not dot or ext.lower() != "pdf":
        return False
    return bool(_SINGLE_REGISTRY_PDF_STEM_PATTERN.match(stem))


def _registry_dir_is_service(dir_ref: str) -> bool:
    """Патч 22.16: папка в служебном дереве («на удаление/» и др.).
    ТЕСТ/TECT/TEST и прочие маркеры уже отсекает _is_excluded."""
    upper = (dir_ref or "").upper()
    return any(m in upper for m in _REGISTRY_SERVICE_DIR_MARKERS)


def _collect_single_registry_pdfs(raw: list[dict]) -> list[dict]:
    """
    Патч 22.16: отбор «одиночных реестровых PDF» из сырой выдачи SharePoint.

    Возвращает список элементов SharePoint (dict) — кандидатов в документы.
    Все условия обязательны; правило не срабатывает, если хоть одно
    нарушено:
      1. имя файла матчится по строгому паттерну «Р-\\d+.pdf»
         (is_single_registry_pdf);
      2. файл лежит непосредственно в папке комплекта — имя папки
         распознаётся как шифр РД-комплекта (looks_like_rd_code);
      3. в этой папке (без подпапок) нет других PDF с именами,
         проходящими looks_like_rd_code (другие PDF с «нердовыми»
         именами, включая прочие «Р-NNN.pdf», не мешают);
      4. файл не в служебных деревьях — ТЕСТ/, «на удаление/», корень
         вне комплектов (_is_excluded + маркеры + условие 2).
    """
    # 1. Группируем PDF по папке (без подпапок — ключ точный FileDirRef)
    by_dir: dict[str, list[dict]] = {}
    for it in raw:
        leaf = (it.get("FileLeafRef") or "").strip()
        dir_ref = (it.get("FileDirRef") or "").strip()
        if not leaf.lower().endswith(".pdf"):
            continue
        if _is_excluded(dir_ref, leaf):
            continue
        by_dir.setdefault(dir_ref, []).append(it)

    result: list[dict] = []
    for dir_ref, items in by_dir.items():
        # Условие 2: файл лежит непосредственно в папке комплекта —
        # последний сегмент пути распознаётся как шифр РД-комплекта
        folder_name = dir_ref.rstrip("/").rsplit("/", 1)[-1]
        if not looks_like_rd_code(folder_name):
            continue
        # Условие 4: не в служебных деревьях («на удаление/» и др.)
        if _registry_dir_is_service(dir_ref):
            continue
        # Условие 3: в папке нет других PDF с именами-шифрами РД
        has_rd_sibling = any(
            looks_like_rd_code((x.get("FileLeafRef") or "").strip())
            for x in items
        )
        if has_rd_sibling:
            continue
        # Условие 1: имя файла — строго «Р-NNN.pdf»
        for x in items:
            if is_single_registry_pdf((x.get("FileLeafRef") or "").strip()):
                result.append(x)
    return result


# ---------------------------------------------------------------------------
#  Справочники
# ---------------------------------------------------------------------------
def fetch_doc_statuses() -> dict[int, str]:
    """Справочник статусов листов: {Id: 'A' / 'B' / 'C' / другое}. Живёт в корне сайта."""
    raw = _fetch_all(VITRO_BASE_URL, LIST_DOC_STATUSES, ["Id", "Title"])
    return {r["Id"]: r["Title"] for r in raw}


def fetch_comment_statuses() -> dict[int, str]:
    """Справочник статусов замечаний: {Id: Title}. Живёт в корне сайта."""
    raw = _fetch_all(VITRO_BASE_URL, LIST_COMMENT_STATUSES, ["Id", "Title"])
    return {r["Id"]: r["Title"] for r in raw}


def fetch_authors() -> dict[int, str]:
    """Справочник авторов: {Id: Title}. Живёт в корне сайта."""
    raw = _fetch_all(VITRO_BASE_URL, LIST_AUTHORS, ["Id", "Title"])
    return {r["Id"]: r["Title"] for r in raw}


# ---------------------------------------------------------------------------
#  Документы (листы) АТП
# ---------------------------------------------------------------------------
def fetch_atp_documents(max_items: int = 0) -> list[dict]:
    """
    PDF-листы РД-комплектов из /01_В процессе/ — ЛЮБОЙ организации:
    «АТ-РД-*», «ГИС-РД-*» и др. (патч 19, looks_like_rd_code).
    Исключает TECT/ТЕСТ, служебные рендеры, файлы без папки комплекта.

    Патч 20: запрашивает из двух папок — АТП и ГИС, объединяет с
    дедупликацией по UniqueId (последняя ревизия побеждает).

    Патч 22.16: одиночный реестровый PDF вида «Р-NNN.pdf» в папке
    комплекта (имя папки — шифр РД-комплекта, других PDF с РД-именами
    в папке нет, вне служебных деревьев) принимается как документ
    комплекта: complex — нормализованный шифр из имени папки,
    leaf — имя файла без расширения («Р-381»). Идемпотентность —
    как у обычных документов: дедуп по UniqueId здесь, повторный
    синк не создаёт дубликаты (save_all перезаписывает documents).
    """
    work_paths = (ATP_WORK, GIS_WORK)  # патч 20

    raw: list[dict] = []
    for wp in work_paths:
        flt = (f"startswith(FileDirRef, '{wp}/') "
               f"and substringof('.pdf', FileLeafRef)")
        raw.extend(
            _fetch_all(
                VITRO_OKHTA_URL, LIST_DOCS,
                ["Id", "UniqueId", "OData__UIVersionString",
                 "FileLeafRef", "FileDirRef",
                 "VitroBaseStatus", "VitroBaseDocumentRevision",
                 "VitroBaseFullName", "VitroBaseStatusDate",
                 "VitroBaseDocSheetNumber"],
                flt=flt, max_items=max_items,
            )
        )

    # Патч 22.16: одиночные реестровые PDF «Р-NNN.pdf» по папкам.
    # Отбираются из той же сырой выдачи ДО обычного фильтра
    # looks_like_rd_code (который такие имена отсекает).
    registry_pdfs = _collect_single_registry_pdfs(raw)
    registry_ids = {id(it) for it in registry_pdfs}

    doc_statuses = fetch_doc_statuses()
    docs: list[dict] = []
    # Дедупликация по UniqueId (патч 20): последняя ревизия побеждает
    seen: dict[str, dict] = {}
    for it in raw:
        leaf = (it.get("FileLeafRef") or "").strip()
        dir_ref = it.get("FileDirRef") or ""

        # Патч 22.16: одиночный реестровый PDF принимается отдельным
        # правилом ниже; в обычном потоке его пропускаем, чтобы не
        # отсекать по looks_like_rd_code и не задвоить запись.
        if id(it) in registry_ids:
            continue
        if not looks_like_rd_code(leaf):
            continue
        if _is_excluded(dir_ref, leaf):
            continue

        disc, sec, comp = parse_path(dir_ref)
        if not comp:
            continue

        ui_version_raw = it.get("OData__UIVersionString") or ""
        try:
            vitro_version = int(float(str(ui_version_raw)))
        except (ValueError, TypeError):
            vitro_version = 1

        record = {
            "id": it.get("Id"),
            "leaf": leaf,
            "discipline": disc,
            "section": sec,
            "complex": normalize_complex(comp),
            "dir_ref": dir_ref,
            "status": doc_statuses.get(it.get("VitroBaseStatus"), ""),
            "revision": it.get("VitroBaseDocumentRevision"),
            "name": it.get("VitroBaseFullName"),
            "status_date": it.get("VitroBaseStatusDate"),
            "sheet_number": it.get("VitroBaseDocSheetNumber"),
            "vitro_guid": it.get("UniqueId"),
            "vitro_version": vitro_version,
        }
        # Патч 22.10: для «Ведомости комплектов» discipline = код комплекта,
        # а не первый элемент пути (parse_path вернул бы "01_РД")
        if record["complex"] == "АТ-РД-СП-0-00":
            record["discipline"] = "АТ-РД-СП-0-00"
            record["section"] = ""
        # Дедуп по UniqueId: оставляем последнюю ревизию
        guid = record["vitro_guid"]
        if guid:
            existing = seen.get(guid)
            if existing is None or vitro_version >= existing.get("vitro_version", 0):
                seen[guid] = record

    # Патч 22.16: одиночные реестровые PDF «Р-NNN.pdf» в папках
    # комплектов — записываем как документы комплекта.
    # complex — нормализованный шифр комплекта из имени папки,
    # leaf — имя файла без расширения («Р-381»), расширение/тип —
    # по факту (.pdf). Идемпотентность: тот же дедуп по UniqueId,
    # что и для обычных документов; повторный синк перезаписывает
    # documents целиком (save_all), дубликаты не создаются.
    for it in registry_pdfs:
        leaf = (it.get("FileLeafRef") or "").strip()
        dir_ref = (it.get("FileDirRef") or "").strip()
        folder_name = dir_ref.rstrip("/").rsplit("/", 1)[-1]
        disc, sec, comp = parse_path(dir_ref)

        ui_version_raw = it.get("OData__UIVersionString") or ""
        try:
            vitro_version = int(float(str(ui_version_raw)))
        except (ValueError, TypeError):
            vitro_version = 1

        record = {
            "id": it.get("Id"),
            "leaf": leaf[: -len(".pdf")],  # «Р-381» — без расширения
            "discipline": disc,
            "section": sec,
            "complex": normalize_complex(folder_name),
            "dir_ref": dir_ref,
            "status": doc_statuses.get(it.get("VitroBaseStatus"), ""),
            "revision": it.get("VitroBaseDocumentRevision"),
            "name": it.get("VitroBaseFullName"),
            "status_date": it.get("VitroBaseStatusDate"),
            "sheet_number": it.get("VitroBaseDocSheetNumber"),
            "vitro_guid": it.get("UniqueId"),
            "vitro_version": vitro_version,
        }
        guid = record["vitro_guid"]
        if guid:
            existing = seen.get(guid)
            if existing is None or vitro_version >= existing.get("vitro_version", 0):
                seen[guid] = record

    docs = list(seen.values())
    return docs


# ---------------------------------------------------------------------------
#  Замечания АТП
# ---------------------------------------------------------------------------
def fetch_atp_comments(
    doc_ids: set[int] | None = None, max_items: int = 0,
) -> list[dict]:
    """Замечания, привязанные к документам АТП."""
    raw = _fetch_all(
        VITRO_OKHTA_URL, LIST_COMMENTS,
        ["Id", "VitroBaseLibraryItemId", "VitroBaseCommentNote",
         "VitroBaseCommentStatus", "VitroBaseCommentAuthor", "Created",
         "VitroBaseCommentFixDate"],
        max_items=max_items,
    )
    if doc_ids is not None:
        raw = [c for c in raw if c.get("VitroBaseLibraryItemId") in doc_ids]
    return raw


def fetch_comments_enriched(doc_ids: set[int]) -> list[dict]:
    """Замечания с расшифрованными статусом и автором."""
    statuses = fetch_comment_statuses()
    authors = fetch_authors()
    comments = fetch_atp_comments(doc_ids=doc_ids)
    out = []
    for c in comments:
        out.append({
            "id": c.get("Id"),
            "doc_id": c.get("VitroBaseLibraryItemId"),
            "comment": c.get("VitroBaseCommentNote"),
            "status": statuses.get(c.get("VitroBaseCommentStatus"), ""),
            "author": authors.get(c.get("VitroBaseCommentAuthor"), ""),
            "created": c.get("Created"),
        })
    return out


# ---------------------------------------------------------------------------
#  Сводка по листам (аналог Query_Document_Summary)
# ---------------------------------------------------------------------------
def build_documents_summary(docs: list[dict]) -> list[dict]:
    """
    Группировка листов по комплектам.
    Поля: Шифр комплекта, Дисциплина, Раздел, Всего листов, C, B, A, Другой, %.
    """
    agg: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "A": 0, "B": 0, "C": 0, "other": 0,
        "discipline": "", "section": "",
    })

    for d in docs:
        comp = d["complex"]
        row = agg[comp]
        row["total"] += 1
        row["discipline"] = row["discipline"] or d["discipline"]
        row["section"] = row["section"] or d["section"]

        st = (d.get("status") or "").strip().upper()
        if st in ("A", "B", "C"):
            row[st] += 1
        else:
            row["other"] += 1

    out = []
    for comp, row in agg.items():
        total = row["total"]
        pct = 0.0 if total == 0 else round((row["A"] + row["B"]) / total * 100, 1)
        out.append({
            "Шифр комплекта": comp,
            "Дисциплина": row["discipline"],
            "Раздел": row["section"],
            "Всего листов": total,
            "Статус C": row["C"],
            "Статус B": row["B"],
            "Статус A": row["A"],
            "Другой статус": row["other"],
            "% выполнения": pct,
        })
    out.sort(key=lambda r: r["Шифр комплекта"])
    return out


# ---------------------------------------------------------------------------
#  Сводка по замечаниям (аналог Query_Complexes)
# ---------------------------------------------------------------------------
def build_comments_summary(docs: list[dict], comments: list[dict]) -> list[dict]:
    """
    Группировка замечаний по комплектам.
    Поддерживает оба формата: VitroBaseLibraryItemId и doc_id.
    """
    from collections import defaultdict

    doc_by_id = {d["id"]: d for d in docs}
    statuses = fetch_comment_statuses()

    agg: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "closed": 0, "annulled": 0, "active": 0, "other": 0,
        "discipline": "", "section": "",
    })

    for c in comments:
        # Универсальный доступ к ID документа
        doc_id = c.get("doc_id") or c.get("VitroBaseLibraryItemId")
        doc = doc_by_id.get(doc_id)
        if not doc:
            continue

        comp = doc["complex"]
        row = agg[comp]
        row["total"] += 1
        row["discipline"] = row["discipline"] or doc["discipline"]
        row["section"] = row["section"] or doc["section"]

        # Универсальный доступ к статусу:
        # либо уже строка ("Закрыто"), либо ID → lookup
        raw_status = c.get("status") or c.get("VitroBaseCommentStatus")
        if isinstance(raw_status, int):
            status_title = statuses.get(raw_status, "")
        else:
            status_title = raw_status or ""

        kind = classify_comment_status(status_title)
        row[kind] = row.get(kind, 0) + 1

    out = []
    for comp, row in agg.items():
        total = row["total"]
        pct = 0.0 if total == 0 else round(
            (row["closed"] + row["annulled"]) / total * 100, 1)
        out.append({
            "Комплект": comp,
            "Дисциплина": row["discipline"],
            "Раздел": row["section"],
            "Всего замечаний": total,
            "Закрыто": row["closed"],
            "Аннулировано": row["annulled"],
            "Активные": row["active"],
            "% выполнения": pct,
        })
    out.sort(key=lambda r: r["Комплект"])
    return out


# ---------------------------------------------------------------------------
#  Объединённый дашборд
# ---------------------------------------------------------------------------
def build_dashboard(docs: list[dict], comments: list[dict]) -> list[dict]:
    """
    Один ряд = один комплект. Сводит обе сводки.
    """
    comments_sum = {r["Комплект"]: r for r in build_comments_summary(docs, comments)}
    docs_sum = {r["Шифр комплекта"]: r for r in build_documents_summary(docs)}

    all_complexes = sorted(set(comments_sum) | set(docs_sum))
    out = []
    for comp in all_complexes:
        c = comments_sum.get(comp, {})
        d = docs_sum.get(comp, {})
        out.append({
            "Комплект": comp,
            "Дисциплина": d.get("Дисциплина") or c.get("Дисциплина", ""),
            "Раздел": d.get("Раздел") or c.get("Раздел", ""),
            "Всего замечаний": c.get("Всего замечаний", 0),
            "Закрыто": c.get("Закрыто", 0),
            "Аннулировано": c.get("Аннулировано", 0),
            "Активные": c.get("Активные", 0),
            "% замечаний": c.get("% выполнения", 0),
            "Всего листов": d.get("Всего листов", 0),
            "Статус C": d.get("Статус C", 0),
            "Статус B": d.get("Статус B", 0),
            "Статус A": d.get("Статус A", 0),
            "Другой статус": d.get("Другой статус", 0),
            "% листов": d.get("% выполнения", 0),
        })
    return out


def fetch_complex_names() -> list[dict]:
    """
    Возвращает список комплектов с названиями (патч 19: организации
    любые — «АТ-РД-…», «ГИС-РД-…» и др., фильтр looks_like_rd_code):
      [{"code": "АТ-РД-КЖ0-С-П2.1", "name": "...", "discipline": "02_КР"}]
    Комплекты берутся из путей PDF-файлов; названия — из VitroBaseFullName папок.

    Патч 22.11: помимо АТП («01_В процессе») обходится и папка ГИС
    (GIS_WORK), поэтому комплекты ГИС-РД-* попадают в complexes и
    каскадный kit-фильтр работает для них.
    """
    # 1. Все объекты в 01_В процессе АТП и ГИС (и папки, и файлы)
    raw: list[dict] = []
    for wp in (ATP_WORK, GIS_WORK):  # патч 22.11
        flt = f"startswith(FileDirRef, '{wp}/')"
        raw.extend(_fetch_all(
            VITRO_OKHTA_URL, LIST_DOCS,
            ["Id", "FileLeafRef", "FileDirRef", "VitroBaseFullName"],
            flt=flt,
            max_items=0,
        ))

    # 2. Разделяем на файлы и папки
    pdf_files = []
    folders = []
    for it in raw:
        leaf = (it.get("FileLeafRef") or "").strip()
        if _is_excluded(it.get("FileDirRef") or "", leaf):
            continue
        if leaf.lower().endswith(".pdf"):
            pdf_files.append(it)
        else:
            folders.append(it)

    # 3. Собираем уникальные коды комплектов из путей PDF
    complex_codes: dict[str, str] = {}   # code → discipline
    for f in pdf_files:
        leaf = f.get("FileLeafRef") or ""
        if not looks_like_rd_code(leaf):
            continue
        dir_ref = f.get("FileDirRef") or ""
        disc, sec, comp = parse_path(dir_ref)
        if not comp:
            continue
        code = normalize_complex(comp)
        # Патч 22.10: «Ведомость комплектов» — discipline = код комплекта
        if code == "АТ-РД-СП-0-00":
            disc = "АТ-РД-СП-0-00"
        complex_codes.setdefault(code, disc)

    # 4. Для каждого кода ищем название папки
    folder_names: dict[str, str] = {}
    for fol in folders:
        leaf = (fol.get("FileLeafRef") or "").strip()
        if not looks_like_rd_code(leaf):
            continue
        code = normalize_complex(leaf)
        # Если название ещё не задано и есть в VitroBaseFullName
        if code not in folder_names:
            name = (fol.get("VitroBaseFullName") or "").strip()
            if name:
                folder_names[code] = name

    # 5. Собираем итог
    result = []
    for code, disc in complex_codes.items():
        result.append({
            "code": code,
            "name": folder_names.get(code, ""),
            "discipline": disc,
        })

    # 6. Исключаем мусорные комплекты (АННУЛИРОВАН, АРХИВ и т.п.)
    EXCLUDED_MARKERS = ("АННУЛИРОВАН", "АРХИВ", "ARCHIVE", "УДАЛЕН")
    filtered = []
    for item in result:
        code_upper = item["code"].upper()
        if any(marker in code_upper for marker in EXCLUDED_MARKERS):
            print(f"  Исключён: {item['code']}")
            continue
        filtered.append(item)

    filtered.sort(key=lambda r: (r["discipline"], r["code"]))
    return filtered