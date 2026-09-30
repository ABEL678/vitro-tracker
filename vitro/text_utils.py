# vitro/text_utils.py
"""
Утилиты для работы с текстом.
...
"""

_HOMOGLYPHS = {
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H",
    "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T",
    "Х": "X", "У": "Y",
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h",
    "к": "k", "м": "m", "о": "o", "р": "p", "т": "t",
    "х": "x", "у": "y",
}


def normalize_homoglyphs(s: str) -> str:
    if not s:
        return s
    return "".join(_HOMOGLYPHS.get(ch, ch) for ch in str(s))


def clean_leaf_key(leaf) -> str:
    if not leaf:
        return ""
    s = str(leaf)
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    return normalize_homoglyphs(s)