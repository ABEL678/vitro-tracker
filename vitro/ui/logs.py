# vitro/ui/logs.py
"""
📜 Логи — журнал операций: синхронизация, изменения категорий, ошибки.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn
from vitro.ui._utils import download_plotly


# ---------------------------------------------------------------------------
#  Загрузчики
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def _load_logs(limit: int = 500) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(f"""
            SELECT timestamp AS "Время",
                   event AS "Событие",
                   message AS "Сообщение",
                   rows AS "Строк",
                   status AS "Статус"
            FROM log
            ORDER BY timestamp DESC
            LIMIT {limit}
        """, conn)


@st.cache_data(ttl=60, show_spinner=False)
def _load_user_activity(limit: int = 500) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(f"""
            SELECT
                timestamp AS "Время",
                user AS "Пользователь",
                comment_id AS "ID замечания",
                old_cat AS "Было",
                new_cat AS "Стало"
            FROM users_activity
            ORDER BY timestamp DESC
            LIMIT {limit}
        """, conn)


@st.cache_data(ttl=60, show_spinner=False)
def _load_events_stats() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT event AS "Событие",
                   COUNT(*) AS "Количество",
                   MAX(timestamp) AS "Последнее"
            FROM log
            GROUP BY event
            ORDER BY "Количество" DESC
        """, conn)


# ---------------------------------------------------------------------------
#  Подсветка статусов через pandas Styler
# ---------------------------------------------------------------------------
def _style_status(val):
    if val == "OK":
        return "background-color: #C6EFCE; color: #006100;"
    if val == "ERROR":
        return "background-color: #FFC7CE; color: #9C0006;"
    return ""


def _apply_status_style(df: pd.DataFrame):
    """
    Применяет подсветку к колонке «Статус».
    Совместимо с pandas 2.x и 3.x:
      - pandas 3.x: Styler.map
      - pandas 2.x: Styler.applymap
    """
    styler = df.style
    # В pandas 3.0+ появился метод .map для Styler, в 2.x — .applymap
    if hasattr(styler, "map"):
        return styler.map(_style_status, subset=["Статус"])
    return styler.applymap(_style_status, subset=["Статус"])


# ---------------------------------------------------------------------------
#  Рендер
# ---------------------------------------------------------------------------
def render():
    st.header("📜 Логи и история")
    st.caption("Журнал операций синхронизации и история изменений категорий.")

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🔄 Обновить", key="logs_refresh",
                     use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    limit = st.slider("Сколько последних записей показать",
                      min_value=50, max_value=5000,
                      value=500, step=50, key="logs_limit")

    tab1, tab2 = st.tabs(["📋 Журнал операций", "✏️ История категорий"])

    # ======================================================================
    #  Журнал операций
    # ======================================================================
    with tab1:
        df = _load_logs(limit=int(limit))

        if df.empty:
            st.info("Журнал пуст. Записи появятся после запуска синхронизации.")
        else:
            stats = _load_events_stats()
            if not stats.empty:
                st.markdown("##### Сводка по событиям")
                st.dataframe(stats, use_container_width=True, hide_index=True)

                fig = px.bar(
                    stats.sort_values("Количество", ascending=True),
                    x="Количество", y="Событие", orientation="h",
                    text="Количество",
                    title="Количество событий по типам",
                    color_discrete_sequence=["#64B5F6"],
                )
                fig.update_traces(textposition="outside")
                fig.update_layout(height=max(250, 40 * len(stats)))
                st.plotly_chart(fig, use_container_width=True)
                download_plotly(fig, "Логи_события", "logs_events")

            st.divider()
            st.markdown(f"##### Последние {len(df)} записей")

            styled = _apply_status_style(df)
            st.dataframe(styled, use_container_width=True,
                         hide_index=True, height=500)

    # ======================================================================
    #  История изменений категорий
    # ======================================================================
    with tab2:
        activity = _load_user_activity(limit=int(limit))

        if activity.empty:
            st.info("Пока никто не менял категории. История появится после "
                    "первого назначения.")
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("Всего изменений", len(activity))
        c2.metric("Уникальных замечаний",
                  activity["ID замечания"].nunique())
        c3.metric("Пользователей",
                  activity["Пользователь"].nunique())

        st.divider()

        by_user = (activity.groupby("Пользователь").size()
                   .reset_index(name="Изменений")
                   .sort_values("Изменений", ascending=True))

        fig = px.bar(
            by_user, x="Изменений", y="Пользователь", orientation="h",
            text="Изменений",
            title="Кто сколько категорий назначил",
            color_discrete_sequence=["#A5D6A7"],
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(height=max(200, 40 * len(by_user)))
        st.plotly_chart(fig, use_container_width=True)
        download_plotly(fig, "Логи_активность_пользователей", "logs_users")

        st.divider()
        st.markdown(f"##### Последние {len(activity)} изменений категорий")

        st.dataframe(activity, use_container_width=True,
                     hide_index=True, height=500)