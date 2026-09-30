# vitro/ui/admin.py
"""
⚙️ Управление — кнопки запуска синхронизации и служебные операции.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from vitro.sqlite_db import get_conn, db_stats, log_event


ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
#  KPI базы данных
# ---------------------------------------------------------------------------
def _render_db_stats():
    stats = db_stats()

    st.markdown("##### 💾 Состояние базы данных")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Комплектов", stats["complexes"])
    c2.metric("Листов РД", f"{stats['documents']:,}".replace(",", " "))
    c3.metric("Замечаний", f"{stats['comments']:,}".replace(",", " "))
    c4.metric("С категорией", f"{stats['categorized']:,}".replace(",", " "))
    c5.metric("Снимков истории", stats["history_snapshots"])

    # Информация о последних снимках
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
    """Запуск синхронизации из UI. Не блокирует приложение надолго."""
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
            log_event("sync_from_ui", "Синхронизация из UI",
                      rows=result["comments"])
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
#  Служебные операции
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


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def render():
    st.header("⚙️ Управление")
    st.caption("Служебные операции: синхронизация, генерация отчётов, "
               "мониторинг БД.")

    _render_db_stats()

    st.divider()
    _render_sync()

    st.divider()
    _render_xlsx()

    st.divider()
    _render_danger_zone()