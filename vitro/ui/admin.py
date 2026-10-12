# vitro/ui/admin.py
"""
⚙️ Управление — служебные операции + журнал работы.

2 под-вкладки:
  ⚙️ Управление — синхронизация, генерация отчётов, мониторинг БД.
  📜 Логи       — журнал операций и история изменений категорий.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from vitro.sqlite_db import get_conn, db_stats, log_event


ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
#  Человеческие названия событий
# ---------------------------------------------------------------------------
EVENT_LABELS = {
    "refresh":        "Автоматическая (по расписанию)",
    "sync_from_ui":   "Ручная (из Управления)",
    "sync_error":     "Ошибка синхронизации",
    "xlsx_generated": "Генерация XLSX",
}


def _event_label(event: str) -> str:
    """Техническое имя события → человеческое."""
    if not event:
        return "—"
    return EVENT_LABELS.get(event, event)


# ===========================================================================
#  ЧАСТЬ 1. УПРАВЛЕНИЕ
# ===========================================================================

def _render_db_stats():
    stats = db_stats()

    st.markdown("##### 💾 Состояние базы данных")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Комплектов", stats["complexes"])
    c2.metric("Листов РД", f"{stats['documents']:,}".replace(",", " "))
    c3.metric("Замечаний", f"{stats['comments']:,}".replace(",", " "))
    c4.metric("С категорией", f"{stats['categorized']:,}".replace(",", " "))
    c5.metric("Снимков истории", stats["history_snapshots"])

    with get_conn() as conn:
        last_sync = conn.execute("""
            SELECT MAX(timestamp) AS t FROM log WHERE event = 'refresh'
        """).fetchone()["t"]
        last_snapshots = conn.execute("""
            SELECT snapshot_date, COUNT(*) AS n
            FROM history_summary
            GROUP BY snapshot_date
            ORDER BY snapshot_date DESC
            LIMIT 5
        """).fetchall()

    st.caption(
        f"**Последняя синхронизация:** "
        f"{last_sync if last_sync else 'ещё не было'}"
    )

    if last_snapshots:
        st.markdown("**Последние снимки истории:**")
        snap_df = pd.DataFrame([dict(r) for r in last_snapshots])
        snap_df.columns = ["Дата снимка", "Комплектов"]
        st.dataframe(snap_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
#  Синхронизация
# ---------------------------------------------------------------------------
def _run_sync():
    with st.spinner("Синхронизация с SharePoint... (займёт 3–5 минут)"):
        try:
            from vitro.sync import refresh_data
            result = refresh_data(verbose=False)

            st.success(
                f"✅ Синхронизация завершена\n\n"
                f"- Листов: **{result['documents']:,}**\n"
                f"- Замечаний: **{result['comments']:,}**\n"
                f"- Комплектов: **{result['complexes']:,}**\n"
                f"- Время: **{result['elapsed']} сек**"
            )
            st.cache_data.clear()
        except Exception as e:
            st.error(f"❌ Ошибка синхронизации: {e}")
            st.code(traceback.format_exc())
            try:
                log_event("sync_error", str(e), status="ERROR")
            except Exception:
                pass


def _render_sync():
    st.markdown("##### 🔄 Синхронизация с SharePoint")

    st.caption(
        "Загружает свежие данные из Витрокад и создаёт снимок истории. "
        "Занимает 3–5 минут. Существующие категории сохраняются."
    )

    if st.button("🚀 Запустить синхронизацию", type="primary",
                 use_container_width=True, key="btn_sync"):
        _run_sync()


# ---------------------------------------------------------------------------
#  Генерация XLSX-отчётов
# ---------------------------------------------------------------------------
def _render_xlsx():
    st.markdown("##### 📊 Генерация форматированных XLSX-отчётов")

    st.caption(
        "Создаёт 11 файлов по дисциплинам + 1 сводный, с гиперссылками, "
        "цветными процентами и выпадающими списками категорий."
    )

    if st.button("📊 Сгенерировать все XLSX", use_container_width=True,
                 key="btn_xlsx"):
        with st.spinner("Генерация отчётов... (~1 минута)"):
            try:
                from vitro.xlsx_builder import build_all_files
                files = build_all_files("Отчёты")
                st.success(f"✅ Создано файлов: **{len(files)}**")
                st.session_state["xlsx_generated"] = [str(f) for f in files]
                log_event("xlsx_generated", f"Создано {len(files)} файлов",
                          rows=len(files))
            except Exception as e:
                st.error(f"❌ Ошибка: {e}")
                st.code(traceback.format_exc())

    if "xlsx_generated" in st.session_state:
        files = st.session_state["xlsx_generated"]
        st.markdown(f"**Готово файлов: {len(files)}**")

        for f in files:
            path = Path(f)
            if not path.exists():
                continue
            with open(path, "rb") as fh:
                st.download_button(
                    f"⬇️ {path.name}",
                    data=fh.read(),
                    file_name=path.name,
                    mime=("application/vnd.openxmlformats-officedocument"
                          ".spreadsheetml.sheet"),
                    key=f"dl_admin_{path.name}",
                    use_container_width=True,
                )


# ---------------------------------------------------------------------------
#  Опасная зона
# ---------------------------------------------------------------------------
def _render_danger_zone():
    st.markdown("##### ⚠️ Опасная зона")

    st.caption(
        "Эти операции необратимы. Используйте только при необходимости."
    )

    with st.expander("🧹 Очистить журнал операций", expanded=False):
        st.warning("Удалит все записи журнала (синхронизации, ошибки).")
        if st.button("Очистить журнал", key="btn_clear_log"):
            with get_conn() as conn:
                conn.execute("DELETE FROM log")
            st.success("Журнал очищен")
            st.cache_data.clear()

    with st.expander("🧹 Очистить историю изменений категорий",
                     expanded=False):
        st.warning("Удалит все записи об изменениях категорий "
                   "(кто, когда, что менял).")
        if st.button("Очистить историю", key="btn_clear_activity"):
            with get_conn() as conn:
                conn.execute("DELETE FROM users_activity")
            st.success("История очищена")
            st.cache_data.clear()

    with st.expander("🗑 Полная очистка базы данных", expanded=False):
        st.error(
            "Удалит **ВСЕ** данные: листы, комплекты, замечания, "
            "историю, журнал. **Категории замечаний тоже удалятся.** "
            "Понадобится полная синхронизация с SharePoint."
        )
        confirm = st.text_input("Введите DELETE для подтверждения",
                                key="confirm_delete")
        if st.button("🗑 Удалить всё", key="btn_clear_all",
                     disabled=confirm != "DELETE"):
            with get_conn() as conn:
                conn.execute("DELETE FROM comments")
                conn.execute("DELETE FROM documents")
                conn.execute("DELETE FROM complexes")
                conn.execute("DELETE FROM history_status")
                conn.execute("DELETE FROM history_summary")
                conn.execute("DELETE FROM log")
                conn.execute("DELETE FROM users_activity")
            st.success("База очищена. Запустите синхронизацию заново.")
            st.cache_data.clear()


def _render_admin_tab():
    _render_db_stats()

    st.divider()
    _render_sync()

    st.divider()
    _render_xlsx()

    st.divider()
    _render_danger_zone()


# ===========================================================================
#  ЧАСТЬ 2. ЛОГИ
# ===========================================================================

@st.cache_data(ttl=60, show_spinner=False)
def _load_logs(limit: int = 500) -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql(f"""
            SELECT timestamp AS "Время",
                   event AS "Событие",
                   message AS "Сообщение",
                   rows AS "Строк",
                   status AS "Статус"
            FROM log
            ORDER BY timestamp DESC
            LIMIT {limit}
        """, conn)
    # Маппинг событий → человеческие названия
    if not df.empty:
        df["Событие"] = df["Событие"].apply(_event_label)
    return df


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
        df = pd.read_sql("""
            SELECT event AS "Событие",
                   COUNT(*) AS "Количество",
                   MAX(timestamp) AS "Последнее"
            FROM log
            GROUP BY event
            ORDER BY "Количество" DESC
        """, conn)
    # Маппинг событий → человеческие названия
    if not df.empty:
        df["Событие"] = df["Событие"].apply(_event_label)
    return df


def _style_status(val):
    if val == "OK":
        return "background-color: #C6EFCE; color: #006100;"
    if val == "ERROR":
        return "background-color: #FFC7CE; color: #9C0006;"
    return ""


def _apply_status_style(df: pd.DataFrame):
    styler = df.style
    if hasattr(styler, "map"):
        return styler.map(_style_status, subset=["Статус"])
    return styler.applymap(_style_status, subset=["Статус"])


def _render_logs_tab():
    st.caption(
        "Журнал операций синхронизации и история изменений категорий."
    )

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

        st.divider()
        st.markdown(f"##### Последние {len(activity)} изменений категорий")

        st.dataframe(activity, use_container_width=True,
                     hide_index=True, height=500)


# ===========================================================================
#  ТОЧКА ВХОДА
# ===========================================================================
def render():
    st.header("⚙️ Управление")
    st.caption("Служебные операции и журнал работы.")

    tab_admin, tab_logs = st.tabs([
        "⚙️ Управление",
        "📜 Логи",
    ])

    with tab_admin:
        _render_admin_tab()

    with tab_logs:
        _render_logs_tab()