# app.py
"""
Главный файл Streamlit-приложения.

Фиксированный хедер с логотипом АТП ТЛП + имя пользователя справа.
Фиксированный футер с именем пользователя и подписью.
14 вкладок: Дашборд, Обзор, Комплекты, Листы, Категории, Сроки,
Авторы, Поиск, Динамика, Ревизии, Прогноз, Экспорт, Логи, Управление.
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
    dashboard, overview, complexes, sheets, categories, deadlines,
    authors, search, dynamics, revisions, forecast, export, logs, admin,
)

import time
_t_start = time.time()

# ---------------------------------------------------------------------------
#  Настройки страницы
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Замечания АТП ТЛП",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",   # сайдбар не нужен
)

init_db()
t1 = time.time()
print(f"[TIMING] app.py: imports + set_page_config: {t1 - _t_start:.2f} сек")
t2 = time.time()
print(f"[TIMING] app.py: init_db: {t2 - t1:.2f} сек")

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
#  Модальный диалог ввода имени
# ---------------------------------------------------------------------------
@st.dialog("👤 Представьтесь")
def ask_user_name():
    """Модальное окно для ввода имени."""
    st.write(
        "Имя сохраняется вместе с категориями замечаний. "
        "Оно будет отображаться в хедере и футере."
    )
    name = st.text_input(
        "Фамилия и имя",
        value=st.session_state.get("user", "")
              if st.session_state.get("user") != "инженер" else "",
        key="dialog_user_input",
        placeholder="Например: Иванов Пётр",
    )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("💾 Сохранить", type="primary",
                     use_container_width=True):
            if name.strip():
                st.session_state["user"] = name.strip()
                st.session_state["user_confirmed"] = True
                st.rerun()
            else:
                st.error("Введите имя")

    with col2:
        if st.button("Отмена", use_container_width=True):
            if not st.session_state.get("user"):
                st.session_state["user"] = "инженер"
            st.session_state["user_confirmed"] = True
            st.rerun()

    # Инициализация session_state


if "user" not in st.session_state:
    st.session_state["user"] = ""
if "user_confirmed" not in st.session_state:
    st.session_state["user_confirmed"] = False

    # Показываем диалог, если имя ещё не подтверждено
if not st.session_state.get("user_confirmed"):
    ask_user_name()

user_label = st.session_state.get("user", "инженер")

# ---------------------------------------------------------------------------
#  Высота хедера
# ---------------------------------------------------------------------------
HEADER_HEIGHT_PX = 64

# ---------------------------------------------------------------------------
#  CSS
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
    /* Скрываем служебный хедер Streamlit */
    header[data-testid="stHeader"] {{
        display: none;
    }}
    button[kind="header"] {{
        display: none;
    }}

    /* Основной контейнер */
    .block-container {{
        padding-top: 5rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 100% !important;
    }}

    /* Вкладки — прижаты к хедеру */
    div[data-testid="stTabs"] div[data-baseweb="tab-list"],
    div[data-baseweb="tab-list"],
    [role="tablist"] {{
        position: sticky !important;
        top: {HEADER_HEIGHT_PX}px !important;
        background: #ffffff !important;
        z-index: 9999 !important;
        padding: 4px 0 0 0 !important;
        margin: 0 !important;
        border-bottom: 1px solid #d0d0d0 !important;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05) !important;
    }}

    div[data-baseweb="tab-list"] button[role="tab"],
    button[role="tab"] {{
        font-size: 0.82rem !important;
        padding: 6px 10px !important;
    }}

    h2 {{ font-size: 1.05rem !important; margin-top: 0.4rem !important; }}
    h3 {{ font-size: 0.95rem !important; }}
    div[data-testid="stMetricValue"] {{ font-size: 1.25rem !important; }}
    div[data-testid="stMetricLabel"] {{ font-size: 0.7rem !important; }}
    a.header-anchor {{ display: none !important; }}

    /* Кнопка смены пользователя в хедере (правый верхний угол) */
    .st-key-change_user_header_btn button {{
        position: fixed !important;
        top: 16px !important;
        right: 16px !important;
        z-index: 2147483647 !important;
        width: auto !important;
        height: 32px !important;
        padding: 0 12px !important;
        font-size: 12px !important;
        background: #ffffff !important;
        border: 1px solid #d0d0d0 !important;
        border-radius: 6px !important;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05) !important;
        color: #1F4E78 !important;
    }}

    .st-key-change_user_header_btn button:hover {{
        background: #f0f0f0 !important;
        border-color: #1F4E78 !important;
    }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
#  ХЕДЕР
# ---------------------------------------------------------------------------
HEADER_HTML = f"""
<style>
    .atp-header {{
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        background: #ffffff;
        border-bottom: 1px solid #d0d0d0;
        padding: 8px 200px 8px 16px;   /* правый отступ 200px под кнопку */
        z-index: 2147483646;
        height: {HEADER_HEIGHT_PX}px;
        box-sizing: border-box;
        display: flex;
        align-items: center;
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04);
    }}
    .atp-header .logo {{
        height: 42px;
        margin-right: 14px;
    }}
    .atp-header .title {{
        font-size: 14px;
        font-weight: 600;
        color: #1F4E78;
        line-height: 1.2;
    }}
    .atp-header .subtitle {{
        font-size: 11px;
        color: #888;
        margin-top: 2px;
    }}
</style>
<div class="atp-header">
    {'<img src="data:image/png;base64,' + LOGO_B64 + '" class="logo" alt="АТП ТЛП"/>' if LOGO_B64 else ''}
    <div>
        <div class="title">Замечания по комплектам РД — АТП ТЛП</div>
        <div class="subtitle">Витрокад / SharePoint → SQLite → Аналитика</div>
    </div>
</div>
"""

try:
    st.html(HEADER_HTML)
except AttributeError:
    st.markdown(HEADER_HTML, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
#  Кнопка смены пользователя (правый верхний угол, через CSS fixed)
# ---------------------------------------------------------------------------
def _open_user_dialog():
    st.session_state["user_confirmed"] = False
    st.rerun()

st.button(
    f"👤 {user_label}",
    key="change_user_header_btn",
    on_click=_open_user_dialog,
)

# ---------------------------------------------------------------------------
#  ФУТЕР
# ---------------------------------------------------------------------------
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
    st.markdown(FOOTER_HTML, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
#  Вкладки (14 штук)
# ---------------------------------------------------------------------------
t3 = time.time()
print(f"[TIMING] app.py: header/footer: {t3 - t2:.2f} сек")

tabs = st.tabs([
    "📊 Дашборд РП",     # 0
    "📈 Обзор",          # 1
    "🏗 Комплекты",      # 2
    "📄 Листы",          # 3
    "🏷 Категории",      # 4
    "⏰ Сроки",          # 5
    "👤 Авторы",         # 6
    "🔍 Поиск",          # 7
    "📈 Динамика",       # 8
    "🔁 Ревизии",        # 9
    "🔮 Прогноз",        # 10
    "📄 Экспорт",        # 11
    "📜 Логи",           # 12
    "⚙️ Управление",     # 13
])

# ---------------------------------------------------------------------------
#  Вкладки обёрнуты в @st.fragment там, где нет каскадных фильтров
#  (там, где каскады — оставлены без @st.fragment, чтобы не ломать логику)
# ---------------------------------------------------------------------------

@st.fragment
def _tab_dashboard():
    dashboard.render()

@st.fragment
def _tab_overview():
    overview.render()

@st.fragment
def _tab_complexes():
    complexes.render()

@st.fragment
def _tab_sheets():
    sheets.render()

@st.fragment
def _tab_categories():
    categories.render()

@st.fragment
def _tab_deadlines():
    deadlines.render()

@st.fragment
def _tab_authors():
    authors.render()

@st.fragment
def _tab_search():
    search.render()

@st.fragment
def _tab_dynamics():
    dynamics.render()

@st.fragment
def _tab_revisions():
    revisions.render()

@st.fragment
def _tab_forecast():
    forecast.render()

@st.fragment
def _tab_export():
    export.render()

@st.fragment
def _tab_logs():
    logs.render()

@st.fragment
def _tab_admin():
    admin.render()


with tabs[0]:  _tab_dashboard()
with tabs[1]:  _tab_overview()
with tabs[2]:  _tab_complexes()
with tabs[3]:  _tab_sheets()
with tabs[4]:  _tab_categories()
with tabs[5]:  _tab_deadlines()
with tabs[6]:  _tab_authors()
with tabs[7]:  _tab_search()
with tabs[8]:  _tab_dynamics()
with tabs[9]:  _tab_revisions()
with tabs[10]: _tab_forecast()
with tabs[11]: _tab_export()
with tabs[12]: _tab_logs()
with tabs[13]: _tab_admin()