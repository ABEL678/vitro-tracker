# vitro/sharepoint.py

import urllib3
from typing import Any
from collections import defaultdict
from requests_ntlm import HttpNtlmAuth
import requests

from vitro.config import (
    VITRO_USER, VITRO_PASSWORD, VITRO_BASE_URL, VITRO_OKHTA_URL,
    VITRO_VERIFY_SSL, HEADERS,
)

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
    "АТ-РД-СП-0-00.PDF",
    "АТ-РД-СП-0-00.DWG",
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
    """
    if not file_dir_ref or not file_dir_ref.startswith(ATP_WORK + "/"):
        return ("", "", "")
    rel = file_dir_ref[len(ATP_WORK) + 1:].strip("/")
    parts = [p for p in rel.split("/") if p]
    if len(parts) < 2:
        return ("", "", "")

    discipline = parts[0]
    complex_idx = next(
        (i for i in range(1, len(parts)) if parts[i].startswith("АТ-РД-")),
        None,
    )
    if complex_idx is None:
        return (discipline, "/".join(parts[1:]), "")

    comp = parts[complex_idx]
    section = "/".join(parts[1:complex_idx]) if complex_idx > 1 else ""
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
    PDF-листы АТ-РД-* из /01_В процессе/.
    Исключает TECT/ТЕСТ, служебные рендеры, файлы без папки комплекта.
    """
    flt = (f"startswith(FileDirRef, '{ATP_WORK}/') "
           f"and substringof('.pdf', FileLeafRef)")
    raw = _fetch_all(
        VITRO_OKHTA_URL, LIST_DOCS,
        ["Id", "FileLeafRef", "FileDirRef",
         "VitroBaseStatus", "VitroBaseDocumentRevision",
         "VitroBaseFullName", "VitroBaseStatusDate",
         "VitroBaseDocSheetNumber"],
        flt=flt, max_items=max_items,
    )

    doc_statuses = fetch_doc_statuses()
    docs: list[dict] = []
    for it in raw:
        leaf = (it.get("FileLeafRef") or "").strip()
        dir_ref = it.get("FileDirRef") or ""

        if not leaf.startswith("АТ-РД-"):
            continue
        if _is_excluded(dir_ref, leaf):
            continue

        disc, sec, comp = parse_path(dir_ref)
        if not comp:
            continue

        docs.append({
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
        })
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
    Возвращает список комплектов с названиями:
      [{"code": "АТ-РД-КЖ0-С-П2.1", "name": "...", "discipline": "02_КР"}]
    Комплекты берутся из путей PDF-файлов; названия — из VitroBaseFullName папок.
    """
    # 1. Все объекты в 01_В процессе (и папки, и файлы) — без фильтра по имени
    flt = f"startswith(FileDirRef, '{ATP_WORK}/')"
    raw = _fetch_all(
        VITRO_OKHTA_URL, LIST_DOCS,
        ["Id", "FileLeafRef", "FileDirRef", "VitroBaseFullName"],
        flt=flt,
        max_items=0,
    )

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
        if not leaf.startswith("АТ-РД-"):
            continue
        dir_ref = f.get("FileDirRef") or ""
        disc, sec, comp = parse_path(dir_ref)
        if not comp:
            continue
        code = normalize_complex(comp)
        complex_codes.setdefault(code, disc)

    # 4. Для каждого кода ищем название папки
    folder_names: dict[str, str] = {}
    for fol in folders:
        leaf = (fol.get("FileLeafRef") or "").strip()
        if not leaf.startswith("АТ-РД-"):
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