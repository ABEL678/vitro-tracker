# vitro/ui/_utils.py
"""
Утилиты для UI: скачивание графиков Plotly в PNG, безопасные имена файлов.

PNG генерируется ТОЛЬКО по кнопке — чтобы не тормозить рендер страницы.
"""

import hashlib
import re
import streamlit as st


def safe_filename(name: str) -> str:
    """
    Убирает из имени файла символы, недопустимые в Windows/Linux.
    Пробелы → подчёркивания.
    """
    if not name:
        return "file"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    name = re.sub(r"\s+", "_", name.strip())
    return name[:120] or "file"


def _fig_id(fig) -> str:
    """Уникальный хэш фигуры — чтобы не путать кэш между графиками."""
    try:
        s = fig.to_json()
    except Exception:
        s = str(id(fig))
    return hashlib.md5(s.encode()).hexdigest()[:12]


def download_plotly(fig, filename: str, key: str,
                    width: int = 1400, height: int = 700, scale: int = 2):
    """
    Кнопки скачивания графика в PNG (компактные, чтобы не обрезались).
    """
    fig_hash = _fig_id(fig)
    state_key = f"png_ready_{key}_{fig_hash}"

    col1, col2, _ = st.columns([2, 2, 6])

    with col1:
        if state_key not in st.session_state:
            if st.button(
                "📷 PNG",
                key=f"prep_{key}",
                use_container_width=True,
                help="Сгенерировать PNG (2–3 секунды)",
            ):
                with st.spinner("Готовим PNG..."):
                    try:
                        png_bytes = fig.to_image(
                            format="png", width=width,
                            height=height, scale=scale,
                        )
                        st.session_state[state_key] = png_bytes
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка PNG: {e}")
        else:
            st.download_button(
                "⬇️ PNG",
                data=st.session_state[state_key],
                file_name=f"{filename}.png",
                mime="image/png",
                key=f"png_{key}",
                use_container_width=True,
            )

    with col2:
        if state_key in st.session_state:
            if st.button(
                "✖",
                key=f"clear_{key}",
                use_container_width=True,
                help="Убрать PNG из памяти",
            ):
                del st.session_state[state_key]
                st.rerun()