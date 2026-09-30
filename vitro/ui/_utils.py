# vitro/text_utils.py
"""
Утилиты для работы с текстом.

Основное назначение — нормализация имён листов РД, которые могут
храниться в SharePoint как в латинице, так и в кириллице.
Визуально одинаковые символы (C/С, P/Р, A/А и т. д.) — разные байты,
поэтому наивная группировка «разваливает» один лист на два.
"""

import streamlit as st

# ---------------------------------------------------------------------------
#  Таблица гомоглифов: кириллица → латиница
# ---------------------------------------------------------------------------
_HOMOGLYPHS = {
    # Заглавные
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H",
    "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T",
    "Х": "X", "У": "Y",
    # Строчные
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h",
    "к": "k", "м": "m", "о": "o", "р": "p", "т": "t",
    "х": "x", "у": "y",
}


def normalize_homoglyphs(s: str) -> str:
    """
    Заменяет визуально похожие кириллические символы на латинские.

    ВАЖНО: используется ТОЛЬКО для ключа группировки.
    Оригинальные имена листов можно показывать как есть.
    """
    if not s:
        return s
    return "".join(_HOMOGLYPHS.get(ch, ch) for ch in str(s))


def clean_leaf_key(leaf) -> str:
    """
    Возвращает нормализованный ключ листа:
      1. Убирает расширение .pdf / .PDF.
      2. Заменяет кириллические гомоглифы на латинские.

    Используется для сопоставления листов между `documents` и `comments`.
    """
    if not leaf:
        return ""
    s = str(leaf)
    # Убираем .pdf (в обоих регистрах)
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    return normalize_homoglyphs(s)


def download_plotly(fig, filename_base: str, key_suffix: str,
                    width: int = 1200, height: int = 700,
                    scale: float = 1.5):
    """
    Универсальная кнопка скачивания Plotly-графика в PNG.

    fig           — plotly-фигура
    filename_base — имя файла без расширения (например, «Отстающие_по_разбору»)
    key_suffix    — уникальный ключ для Streamlit (чтобы не было DuplicateElementId)
    width/height  — размер PNG в пикселях
    scale         — масштаб (1.5 = retina-качество)

    Требует установленный `kaleido`. Если его нет — покажет подсказку.
    """
    try:
        png_bytes = fig.to_image(
            format="png",
            width=width,
            height=height,
            scale=scale,
        )
    except Exception:
        st.caption(
            "📷 Для экспорта графиков в PNG установите `kaleido`: "
            "`pip install kaleido`"
        )
        return

    st.download_button(
        "📷 PNG",
        data=png_bytes,
        file_name=f"{filename_base}.png",
        mime="image/png",
        key=key_suffix,
        use_container_width=True,
    )


def safe_filename(s: str) -> str:
    """
    Превращает строку в безопасное имя файла:
    убирает запрещённые символы, оставляет буквы/цифры/дефис/подчёркивание.
    """
    if not s:
        return "file"
    # Запрещённые в Windows: \ / : * ? " < > |
    bad = '\\/:*?"<>|'
    out = "".join(ch if ch not in bad else "_" for ch in str(s))
    out = out.strip().rstrip(".")
    return out or "file"