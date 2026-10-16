# vitro/ui/_filters.py
"""
Общие фильтры для вкладок Vitro-tracker.

Содержит:
  - Статусы листов (группы + обратный маппинг в сырые значения).
  - Хелперы для SQL-фильтрации по группам.

Используется в:
  - vitro/ui/summary.py (Сводка по проекту)
  - vitro/ui/search.py (Поиск по замечаниям)
"""

# ---------------------------------------------------------------------------
#  Статусы листов
# ---------------------------------------------------------------------------
SHEET_GROUP_OPTIONS = [
    "A — утверждён",
    "B — к сдаче",
    "C — в работе",
    "И — информационный",
    "На согласовании Заказчика",
    "Готовится к загрузке",
    "Аннулировано",
    "Прочее",
]

# Обратный маппинг: группа → список сырых статусов (для SQL-фильтра)
_SHEET_GROUP_TO_RAW = {
    "A — утверждён":              ["A"],
    "B — к сдаче":                ["B"],
    "C — в работе":               ["C"],
    "И — информационный":         ["И"],
    "На согласовании Заказчика":  ["ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"],
    "Готовится к загрузке":       ["НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"],
    "Аннулировано":               ["АННУЛИРОВАНО"],
    # «Прочее» обрабатывается отдельно — через NOT IN
}

# Все известные сырые статусы — для группы «Прочее»
_ALL_KNOWN_RAW_STATUSES = [
    "A", "B", "C", "И",
    "ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ",
    "НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО",
    "АННУЛИРОВАНО",
]


# ---------------------------------------------------------------------------
#  Хелперы
# ---------------------------------------------------------------------------
def _doc_status_group(raw) -> str:
    """Сырой doc_status → группа (как в бублике Секции 2 Сводки)."""
    if raw is None:
        return "Прочее"
    s = str(raw).strip().upper()
    if s == "A":
        return "A — утверждён"
    if s == "B":
        return "B — к сдаче"
    if s == "C":
        return "C — в работе"
    if s == "И":
        return "И — информационный"
    if s in ("ДЛЯ СОГЛАСОВАНИЯ", "НА РАССМОТРЕНИИ"):
        return "На согласовании Заказчика"
    if s in ("НА КОРРЕКТИРОВКЕ", "РАЗМЕЩЕНО"):
        return "Готовится к загрузке"
    if s == "АННУЛИРОВАНО":
        return "Аннулировано"
    return "Прочее"


def _sheet_groups_to_sql(sheet_groups: tuple, doc_alias: str = "d"):
    """
    Возвращает (sql_condition, params) для фильтра по группам статусов листов.
    sql_condition — строка вида " AND (...)" или "".
    Использует NOT IN для группы «Прочее».
    """
    if not sheet_groups:
        return "", []

    raw = []
    for g in sheet_groups:
        raw += _SHEET_GROUP_TO_RAW.get(g, [])

    conditions = []
    params: list = []

    if raw:
        conditions.append(
            f"{doc_alias}.status IN ({','.join('?' * len(raw))})"
        )
        params += raw

    if "Прочее" in sheet_groups:
        placeholders = ",".join("?" * len(_ALL_KNOWN_RAW_STATUSES))
        conditions.append(
            f"({doc_alias}.status NOT IN ({placeholders}) "
            f"OR {doc_alias}.status IS NULL "
            f"OR {doc_alias}.status = '')"
        )
        params += _ALL_KNOWN_RAW_STATUSES

    if not conditions:
        return "", []

    return " AND (" + " OR ".join(conditions) + ")", params
