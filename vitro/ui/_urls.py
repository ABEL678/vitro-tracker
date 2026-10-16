"""
URL-хелперы для вкладок Vitro-tracker.

Содержит функции для построения прямых ссылок на объекты в Витро.

Используется в:
  - vitro/ui/authors.py (Детали автора — колонка «Витро»)
  - vitro/ui/deadlines.py (Сп��сок замечаний — колонка «Витро»)
"""

# ---------------------------------------------------------------------------
#  Витро
# ---------------------------------------------------------------------------
VITRO_BASE_URL = "https://pdm-lakhta.ru/okhta"


def build_vitro_comment_url(vitro_guid, vitro_version, note_id) -> str:
    """
    Собирает прямую ссылку на замечание в Витро.
    Формат: {VITRO_BASE_URL}/_layouts/15/Vitro/Viewer/PDF/web/Viewer.aspx
            ?Id={vitro_guid}&version={vitro_version}&noteId={note_id}

    Для закрытых/аннулированных замечаний открывает лист без подсветки —
    это нормально.
    """
    if not vitro_guid or not note_id:
        return ""
    try:
        ver = int(vitro_version) if vitro_version else 1
    except (ValueError, TypeError):
        ver = 1
    return (
        f"{VITRO_BASE_URL}/_layouts/15/Vitro/Viewer/PDF/web/Viewer.aspx"
        f"?Id={vitro_guid}&version={ver}&noteId={note_id}"
    )
