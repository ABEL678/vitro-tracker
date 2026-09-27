# app.py
"""
Главный файл Streamlit-приложения.
Фиксированный хедер + фиксированный футер + вкладки.
streamlit run app.py
"""

# app.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from vitro.sqlite_db import init_db
from vitro.ui import (
    overview, complexes, categories, deadlines, lagging,
    authors, search, dynamics, revisions, export, logs, admin,
)

st.set_page_config(
    page_title="Замечания АТП ТЛП",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

st.markdown("""
<style>
    header[data-testid="stHeader"] { display: none; }
    button[kind="header"] { display: none; }

    .block-container {
        padding-top: 0.4rem !important;
        padding-bottom: 2.4rem !important;
        max-width: 100% !important;
    }

    /* Маленький заголовок */
    .app-header-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: #1F4E78;
        line-height: 1.1;
        margin: 0;
    }
    .app-header-subtitle {
        font-size: 0.68rem;
        color: #888;
        margin-top: 2px;
    }

    /* Компактные вкладки */
    button[data-baseweb="tab"] {
        font-size: 0.82rem !important;
        padding: 6px 10px !important;
    }
    h2 { font-size: 1.05rem !important; margin-top: 0.4rem !important; }
    h3 { font-size: 0.95rem !important; }
    div[data-testid="stMetricValue"] { font-size: 1.25rem !important; }
    div[data-testid="stMetricLabel"] { font-size: 0.7rem !important; }

    /* Фиксированный футер */
    .app-footer {
        position: fixed;
        left: 0; right: 0; bottom: 0;
        background: #ffffff;
        border-top: 1px solid #e0e0e0;
        padding: 4px 16px;
        font-size: 0.7rem;
        color: #666;
        text-align: center;
        z-index: 999;
        height: 26px;
        box-sizing: border-box;
    }
</style>
""", unsafe_allow_html=True)

# --- Хедер: компактный, в потоке ---
hc1, hc2 = st.columns([6, 1])
with hc1:
    st.markdown(
        "<div class='app-header-title'>"
        "📊 Замечания по комплектам РД — АТП ТЛП"
        "</div>"
        "<div class='app-header-subtitle'>"
        "Витрокад / SharePoint → SQLite → Аналитика"
        "</div>",
        unsafe_allow_html=True,
    )
with hc2:
    user = st.text_input(
        "Пользователь",
        value=st.session_state.get("user", ""),
        key="user_input",
        placeholder="Введите имя",
        label_visibility="collapsed",
    )
    st.session_state["user"] = user.strip() or "инженер"

# --- Вкладки ---
tabs = st.tabs([
    "📊 Обзор",
    "🏗 Комплекты",
    "🏷 Категории",
    "⏰ Сроки",
    "⚠️ Отстающие",
    "👤 Авторы",
    "🔍 Поиск",
    "📈 Динамика",
    "🔁 Ревизии",
    "📄 Экспорт",
    "📜 Логи",
    "⚙️ Управление",
])

with tabs[0]:  overview.render()
with tabs[1]:  complexes.render()
with tabs[2]:  categories.render()
with tabs[3]:  deadlines.render()
with tabs[4]:  lagging.render()
with tabs[5]:  authors.render()
with tabs[6]:  search.render()
with tabs[7]:  dynamics.render()
with tabs[8]:  revisions.render()   # ← новая
with tabs[9]:  export.render()
with tabs[10]: logs.render()
with tabs[11]: admin.render()

# --- Футер ---
user_label = st.session_state.get("user", "инженер")
st.markdown(
    f"<div class='app-footer'>"
    f"© АТП ТЛП · 2026 · замечания к комплектам РД · "
    f"пользователь: <b>{user_label}</b>"
    f"</div>",
    unsafe_allow_html=True,
)