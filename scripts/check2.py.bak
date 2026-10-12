from vitro.text_utils import clean_leaf_key

test_cases = [
    "АТ-РД-ОВ2-С-00-РР.04.pdf",   # кириллическая С
    "AT-РД-ОВ2-C-00-РР.04.pdf",   # латинская C
    "АТ-РД-ОВ2-С-00-РР.04",       # без .pdf
    None,
    "",
]

for s in test_cases:
    result = clean_leaf_key(s)
    print(f"{s!r:>40}  →  {result!r}")