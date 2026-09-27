# app.py
"""
Главный файл Streamlit-приложения.

Фиксированный хедер с логотипом АТП ТЛП + имя пользователя справа.
Фиксированный футер с именем пользователя и подписью.
13 вкладок: Дашборд, Обзор, Комплекты, Категории, Сроки, Отстающие,
Авторы, Поиск, Динамика, Ревизии, Экспорт, Логи, Управление.
Запуск: streamlit run app.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import base64
import streamlit as st

from vitro.sqlite_db import init_db
from vitro.ui import (
    dashboard, overview, complexes, categories, deadlines, lagging,
    authors, search, dynamics, revisions, forecast, export, logs, admin,
)

# ---------------------------------------------------------------------------
#  Настройки страницы
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Замечания АТП ТЛП",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

# ---------------------------------------------------------------------------
#  Логотип в base64
# ---------------------------------------------------------------------------
LOGO_PATH = ROOT / "assets" / "logo.png"
LOGO_B64 = ""
if LOGO_PATH.exists():
    try:
        with open(LOGO_PATH, "rb") as f:
            LOGO_B64 = base64.b64encode(f.read()).decode()
    except Exception:
        LOGO_B64 = ""

# ---------------------------------------------------------------------------
#  CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Убираем служебный хедер Streamlit */
    header[data-testid="stHeader"] {
        display: none;
    }
    button[kind="header"] {
        display: none;
    }

    /* Основной контейнер: отступы сверху (под хедер) и снизу (под футер) */
    .block-container {
        padding-top: 0.4rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 100% !important;
    }

    /* Заголовок-хедер */
    .app-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: #1F4E78;
        line-height: 1.1;
        margin: 0;
    }
    .app-subtitle {
        font-size: 0.68rem;
        color: #888;
        margin-top: 2px;
    }
    .app-logo {
        height: 42px;
        margin-right: 12px;
    }

    /* Компактные вкладки */
    button[data-baseweb="tab"] {
        font-size: 0.82rem !important;
        padding: 6px 10px !important;
    }

    /* Компактные заголовки внутри вкладок */
    h2 { font-size: 1.05rem !important; margin-top: 0.4rem !important; }
    h3 { font-size: 0.95rem !important; }

    /* Компактные метрики */
    div[data-testid="stMetricValue"] { font-size: 1.25rem !important; }
    div[data-testid="stMetricLabel"] { font-size: 0.7rem !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
#  ФУТЕР — через st.html, рендерим ДО вкладок,
#  чтобы он не попал в контейнер контента
# ---------------------------------------------------------------------------
user_label = st.session_state.get("user", "инженер")

FOOTER_HTML = f"""
<style>
    .atp-footer {{
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        background: #fafafa;
        border-top: 1px solid #d0d0d0;
        padding: 8px 16px;
        font-size: 12px;
        color: #555;
        text-align: center;
        z-index: 2147483647;
        height: 34px;
        box-sizing: border-box;
        line-height: 18px;
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        box-shadow: 0 -2px 4px rgba(0, 0, 0, 0.04);
        pointer-events: none;
    }}
</style>
<div class="atp-footer">
    © АТП ТЛП · 2026 · замечания к комплектам РД ·
    пользователь: <b>{user_label}</b>
</div>
"""

try:
    st.html(FOOTER_HTML)
except AttributeError:
    # Fallback для Streamlit < 1.35
    st.markdown(FOOTER_HTML, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
#  ХЕДЕР
# ---------------------------------------------------------------------------
header_left, header_right = st.columns([5, 1])

with header_left:
    if LOGO_B64:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center;">
                <img src="data:image/png;base64,{LOGO_B64}"
                     class="app-logo" alt="АТП ТЛП"/>
                <div>
                    <div class="app-title">Замечания по комплектам РД — АТП ТЛП</div>
                    <div class="app-subtitle">Витрокад / SharePoint → SQLite → Аналитика</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='app-title'>📊 Замечания по комплектам РД — АТП ТЛП</div>"
            "<div class='app-subtitle'>Витрокад / SharePoint → SQLite → Аналитика</div>",
            unsafe_allow_html=True,
        )

with header_right:
    user = st.text_input(
        "Пользователь",
        value=st.session_state.get("user", ""),
        key="user_input",
        placeholder="Введите имя",
        label_visibility="collapsed",
    )
    st.session_state["user"] = user.strip() or "инженер"

# ---------------------------------------------------------------------------
#  Вкладки (13 штук)
# ---------------------------------------------------------------------------
tabs = st.tabs([
    "📊 Дашборд РП",
    "📈 Обзор",
    "🏗 Комплекты",
    "🏷 Категории",
    "⏰ Сроки",
    "⚠️ Отстающие",
    "👤 Авторы",
    "🔍 Поиск",
    "📈 Динамика",
    "🔁 Ревизии",
    "🔮 Прогноз",     # ← новая
    "📄 Экспорт",
    "📜 Логи",
    "⚙️ Управление",
])

with tabs[0]:  dashboard.render()
with tabs[1]:  overview.render()
with tabs[2]:  complexes.render()
with tabs[3]:  categories.render()
with tabs[4]:  deadlines.render()
with tabs[5]:  lagging.render()
with tabs[6]:  authors.render()
with tabs[7]:  search.render()
with tabs[8]:  dynamics.render()
with tabs[9]:  revisions.render()
with tabs[10]: forecast.render()   # ← новая
with tabs[11]: export.render()
with tabs[12]: logs.render()
with tabs[13]: admin.render()