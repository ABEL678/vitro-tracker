# app.py
"""
Главный файл Streamlit-приложения.

Фиксированный хедер с логотипом АТП ТЛП + имя пользователя справа.
Фиксированная полоса с датой последней выгрузки из Витро.
Фиксированный футер с именем пользователя и подписью.
8 вкладок: Сводка, Динамика, Авторы, Сроки, Категории, Поиск,
Экспорт, Управление.
Запуск: streamlit run app.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import base64
from datetime import datetime

import streamlit as st

from vitro.sqlite_db import init_db, get_conn
from vitro.ui import (
    summary, dynamics, authors, deadlines,
    categories, search, export, admin,
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
    initial_sidebar_state="collapsed",
)

init_db()
t1 = time.time()
print(f"[TIMING] app.py: imports + set_page_config: {t1 - _t_start:.2f} сек")
t2 = time.time()
print(f"[TIMING] app.py: init_db: {t2 - t1:.2f} сек")


# ---------------------------------------------------------------------------
#  Дата последней выгрузки из Витро
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _load_sync_date() -> str | None:
    try:
        with get_conn() as conn:
            row = conn.execute("""
                SELECT MAX(timestamp) AS ts FROM log
                WHERE event IN ('sync_from_ui', 'refresh')
                  AND status = 'OK'
            """).fetchone()
        if row and row["ts"]:
            return row["ts"]
    except Exception:
        pass
    return None


def _fmt_sync_date(ts) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%d.%m.%Y, %H:%M")
    except Exception:
        return str(ts)


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
    st.write("Имя сохраняется вместе с категориями замечаний.")
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
                     use_container_width=True,
                     key="dialog_save_btn"):
            if name.strip():
                st.session_state["user"] = name.strip()
                st.session_state["user_confirmed"] = True
                st.rerun()
            else:
                st.error("Введите имя")

    with col2:
        if st.button("Отмена", use_container_width=True,
                     key="dialog_cancel_btn"):
            if not st.session_state.get("user"):
                st.session_state["user"] = "инженер"
            st.session_state["user_confirmed"] = True
            st.rerun()


# ---------------------------------------------------------------------------
#  Инициализация session_state
# ---------------------------------------------------------------------------
if "user" not in st.session_state:
    st.session_state["user"] = ""
if "user_confirmed" not in st.session_state:
    st.session_state["user_confirmed"] = False

if st.session_state.get("user_confirmed") is not True:
    ask_user_name()

user_label = st.session_state.get("user", "инженер")

# ---------------------------------------------------------------------------
#  Высоты
# ---------------------------------------------------------------------------
HEADER_HEIGHT_PX = 64
DATE_BAR_HEIGHT_PX = 70
TOTAL_TOP_PX = HEADER_HEIGHT_PX + DATE_BAR_HEIGHT_PX

# ---------------------------------------------------------------------------
#  CSS
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
    header[data-testid="stHeader"] {{ display: none; }}
    button[kind="header"] {{ display: none; }}

    .block-container {{
        padding-top: {TOTAL_TOP_PX + 12}px !important;
        padding-bottom: 3.5rem !important;
        max-width: 100% !important;
    }}

    .date-bar {{
        position: fixed !important;
        top: {HEADER_HEIGHT_PX}px !important;
        left: 0 !important;
        right: 0 !important;
        height: {DATE_BAR_HEIGHT_PX}px !important;
        background: #e8f0f8 !important;
        border-bottom: 1px solid #d0d0d0 !important;
        z-index: 2147483645 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif !important;
        font-size: 15px !important;
        font-weight: 600 !important;
        color: #1F4E78 !important;
        box-sizing: border-box !important;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04) !important;
    }}

    div[data-testid="stTabs"] div[data-baseweb="tab-list"],
    div[data-baseweb="tab-list"],
    [role="tablist"] {{
        position: sticky !important;
        top: {TOTAL_TOP_PX}px !important;
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
        padding: 8px 200px 8px 16px;
        z-index: 2147483646;
        height: {HEADER_HEIGHT_PX}px;
        box-sizing: border-box;
        display: flex;
        align-items: center;
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04);
    }}
    .atp-header .logo {{ height: 42px; margin-right: 14px; }}
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
#  ПОЛОСА С ДАТОЙ ВЫГРУЗКИ
# ---------------------------------------------------------------------------
_sync_date = _load_sync_date()
DATE_BAR_HTML = f"""
<div class="date-bar">
    📅 База актуальна на {_fmt_sync_date(_sync_date)}
</div>
"""

try:
    st.html(DATE_BAR_HTML)
except AttributeError:
    st.markdown(DATE_BAR_HTML, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
#  Кнопка смены пользователя
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
#  Вкладки
# ---------------------------------------------------------------------------
t3 = time.time()
print(f"[TIMING] app.py: header/footer: {t3 - t2:.2f} сек")

_TABS = [
    "📊 Сводка по проекту",
    "📈 Динамика",
    "👤 Авторы",
    "⏰ Сроки",
    "🏷 Категории",
    "🔍 Поиск",
    "📄 Экспорт",
    "⚙️ Управление",
]

if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = _TABS[0]

# CSS для стилизации radio как вкладок
st.markdown("""
<style>
    div[data-testid="stRadio"] > div[role="radiogroup"] {
        flex-direction: row !important;
        flex-wrap: wrap !important;
        gap: 0 !important;
        border-bottom: 1px solid #d0d0d0 !important;
        padding: 0 !important;
        margin-bottom: 12px !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label {
        padding: 8px 14px !important;
        margin: 0 !important;
        border-bottom: 3px solid transparent !important;
        cursor: pointer !important;
        transition: all 0.15s !important;
        font-size: 0.92rem !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
        background: #f5f5f5 !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label[data-checked="true"] {
        border-bottom-color: #ff4b4b !important;
        color: #ff4b4b !important;
        font-weight: 600 !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
        display: none !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label > div {
        cursor: pointer !important;
    }
</style>
""", unsafe_allow_html=True)

_active = st.radio(
    "Вкладка",
    options=_TABS,
    index=_TABS.index(st.session_state["active_tab"]),
    horizontal=True,
    label_visibility="collapsed",
    key="tab_selector",
)

if _active != st.session_state["active_tab"]:
    st.session_state["active_tab"] = _active

# Рендер активной вкладки
if _active == "📊 Сводка по проекту":
    summary.render()
elif _active == "📈 Динамика":
    dynamics.render()
elif _active == "👤 Авторы":
    authors.render()
elif _active == "⏰ Сроки":
    deadlines.render()
elif _active == "🏷 Категории":
    categories.render()
elif _active == "🔍 Поиск":
    search.render()
elif _active == "📄 Экспорт":
    export.render()
elif _active == "⚙️ Управление":
    admin.render()