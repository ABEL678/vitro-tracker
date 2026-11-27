"""
vitro/sheet_kind.py — нормализация номера листа СО/ВР.

Патч 22.21: СО и ВР — это ЗНАЧЕНИЯ sheet_number, а не суффиксы complex.
Модуль предоставляет функцию нормализации номера листа:
  - trim + верхний регистр
  - замена латинских двойников (C→С, O→О, B→В, P→Р)
  - возврат: 'СО' | 'ВР' | None

Мусорные значения (пусто, «хх», «ЗД.01» и прочие) → None.
"""

# Переиспользуем маппинг латиница→кириллица из sharepoint.py
_LAT2CYR_PARTIAL = {
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н",
    "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т",
    "X": "Х", "Y": "У",
}

# Значения, которые точно не являются СО/ВР
_MISVALUES = frozenset({
    "XX", "ХХ", "XXX", "ХХХ", "0", "1", "2", "3", "4", "5",
    "10", "01", "02", "03", "04", "05", "06", "07", "08",
    "ЗД.01", "ЗД.02", "РР", "СС", "КЖ", "П",
})


def normalize_sheet_number(sheet_number: str | None) -> str | None:
    """
    Нормализует номер листа: trim, верхний регистр, латинские двойники.

    Возвращает:
      - 'СО'  — если после нормализации совпадает с СО
      - 'ВР'  — если после нормализации совпадает с ВР
      - None  — если мусорное значение или не совпадает

    Примеры:
      'СО'  → 'СО'
      'CO'  → 'СО'  (латиница → кириллица)
      'CО'  → 'СО'  (смешанный регистр)
      'ВР'  → 'ВР'
      'BP'  → 'ВР'
      ' со ' → 'СО' (trim)
      'хх'  → None
      'ЗД.01' → None
      ''    → None
    """
    if not sheet_number:
        return None

    s = sheet_number.strip().upper()

    # Замена латинских двойников → кириллица
    chars = []
    for ch in s:
        up = ch.upper()
        if up in _LAT2CYR_PARTIAL:
            cyr = _LAT2CYR_PARTIAL[up]
            chars.append(cyr if ch.isupper() else cyr.lower())
        else:
            chars.append(ch)
    s = "".join(chars).upper().strip()

    if not s:
        return None

    # Мусорные значения
    if s in _MISVALUES:
        return None

    # Точное совпадение
    if s == "СО":
        return "СО"
    if s == "ВР":
        return "ВР"

    return None
