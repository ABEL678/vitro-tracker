# vitro/ui/_auth.py
"""
Авторизация — запрос имени пользователя по требованию.

Используется в:
  - vitro/ui/categories.py (сохранение категорий, импорт из Excel)
  - vitro/ui/authors.py (сохранение в «Ждут заказчика»)

Логика:
  - Имя хранится в st.session_state["user"].
  - Если имени нет или оно = "инженер" — вызывается диалог.
  - Если пользователь отменил ввод — функция возвращает None,
    вызывающий код должен прервать сохранение.
"""

import streamlit as st


@st.dialog("👤 Представьтесь")
def ask_user_name_dialog():
    """Модальное окно для ввода имени. Используется только по требованию."""
    st.write("Имя сохраняется вместе с категориями замечаний.")

    name = st.text_input(
        "Фамилия и имя",
        value=st.session_state.get("user", "")
              if st.session_state.get("user") not in ("", "инженер")
              else "",
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
            st.session_state["user"] = ""
            st.session_state["user_confirmed"] = False
            st.rerun()


def ensure_user() -> str | None:
    """
    Гарантирует, что пользователь ввёл имя.

    Возвращает:
      - строку с именем, если оно есть.
      - None, если имени нет — тогда показывает диалог и возвращает None.
        Вызывающий код должен прервать сохранение и показать сообщение.

    ⚠️ ВАЖНО: если пользователь только что отменил ввод —
    st.session_state["user_confirmed"] = False,
    поэтому функция не зацикливается.
    """
    user = st.session_state.get("user", "")
    if user and user != "инженер":
        return user

    # Имени нет — показываем диалог
    ask_user_name_dialog()
    return None
