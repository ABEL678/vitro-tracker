# vitro/pdf_builder.py
"""
Экспорт таблиц в PDF через reportlab.

Особенности:
  - Поддержка кириллицы через DejaVu Sans (или системный шрифт).
  - Автоматическая разбивка длинных таблиц на страницы.
  - Ограничение длины текста в ячейке.
  - Хедер с логотипом АТП ТЛП и заголовком документа.
  - Футер с датой и номером страницы.
"""

import io
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
)


# ---------------------------------------------------------------------------
#  Пути
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
LOGO_PATH = ROOT / "assets" / "logo.png"


# ---------------------------------------------------------------------------
#  Регистрация шрифта с поддержкой кириллицы
# ---------------------------------------------------------------------------
FONT_NAME = "DejaVuSans"
FONT_NAME_BOLD = "DejaVuSans-Bold"


def _find_font() -> tuple[str, str] | None:
    """Ищет DejaVu Sans или совместимый шрифт в системе."""
    candidates = []

    # 1. Через matplotlib (там DejaVu Sans всегда есть)
    try:
        import matplotlib
        mpl_data = Path(matplotlib.get_data_path())
        candidates.append((
            mpl_data / "fonts" / "ttf" / "DejaVuSans.ttf",
            mpl_data / "fonts" / "ttf" / "DejaVuSans-Bold.ttf",
        ))
    except ImportError:
        pass

    # 2. Windows системные
    if os.name == "nt":
        win_fonts = Path("C:/Windows/Fonts")
        candidates.extend([
            (win_fonts / "DejaVuSans.ttf", win_fonts / "DejaVuSans-Bold.ttf"),
            (win_fonts / "arial.ttf",      win_fonts / "arialbd.ttf"),
            (win_fonts / "calibri.ttf",    win_fonts / "calibrib.ttf"),
        ])

    # 3. Linux / macOS
    candidates.extend([
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/Library/Fonts/Arial.ttf"),
         Path("/Library/Fonts/Arial Bold.ttf")),
    ])

    for regular, bold in candidates:
        if regular.exists():
            return str(regular), str(bold) if bold.exists() else str(regular)

    return None


def _register_fonts() -> str:
    """Регистрирует шрифт. Возвращает имя (regular)."""
    fonts = _find_font()
    if not fonts:
        return "Helvetica"

    regular_path, bold_path = fonts
    try:
        pdfmetrics.registerFont(TTFont(FONT_NAME, regular_path))
        pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, bold_path))
        return FONT_NAME
    except Exception:
        return "Helvetica"


ACTIVE_FONT = _register_fonts()


# ---------------------------------------------------------------------------
#  Константы
# ---------------------------------------------------------------------------
MAX_CELL_LEN = 120
CHUNK_SIZE = 25


# ---------------------------------------------------------------------------
#  Утилиты
# ---------------------------------------------------------------------------
def _trim_cell(value, max_len: int = MAX_CELL_LEN) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value)
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _build_table(data: list, col_widths: list) -> Table:
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0070C0")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("ALIGN",      (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("GRID",       (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME",   (0, 0), (-1, -1), ACTIVE_FONT),
        ("FONTSIZE",   (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F2F2")]),
    ]))
    return table


def _get_logo_image(width_mm: float = 40):
    """Возвращает Image логотипа или None."""
    if not LOGO_PATH.exists():
        return None
    try:
        # Соотношение 1:0.33 примерно для горизонтальных логотипов
        img = Image(str(LOGO_PATH),
                    width=width_mm * mm,
                    height=width_mm * mm * 0.33)
        return img
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Хедер и футер страницы
# ---------------------------------------------------------------------------
def draw_header_footer(canvas, doc, header_title: str = ""):
    """
    Рисует хедер (лого + заголовок) и футер (подпись + номер стр.)
    на каждой странице PDF.
    """
    canvas.saveState()
    width, height = doc.pagesize

    # --- Хедер: логотип ---
    logo = _get_logo_image(width_mm=30)
    if logo:
        try:
            logo.drawOn(canvas, 12 * mm, height - 14 * mm)
        except Exception:
            pass

    # --- Хедер: заголовок справа от лого ---
    if header_title:
        canvas.setFont(ACTIVE_FONT, 10)
        canvas.setFillColor(colors.HexColor("#1F4E78"))
        canvas.drawString(50 * mm, height - 11 * mm, header_title)

    # --- Разделительная линия ---
    canvas.setStrokeColor(colors.HexColor("#CCCCCC"))
    canvas.setLineWidth(0.5)
    canvas.line(12 * mm, height - 16 * mm, width - 12 * mm, height - 16 * mm)

    # --- Футер: подпись слева ---
    canvas.setFont(ACTIVE_FONT, 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(
        12 * mm, 8 * mm,
        f"АТП ТЛП · сформировано {datetime.now():%d.%m.%Y %H:%M}"
    )

    # --- Футер: номер страницы справа ---
    canvas.drawRightString(width - 12 * mm, 8 * mm, f"Стр. {doc.page}")

    canvas.restoreState()


# ---------------------------------------------------------------------------
#  Основная функция: сборка простого PDF из DataFrame
# ---------------------------------------------------------------------------
def build_pdf(title: str, df: pd.DataFrame, subtitle: str = "",
              max_rows: int = 1000) -> bytes:
    """
    Строит PDF из DataFrame (простой, без хедера/футера).
    Используется в разделе «Экспорт» для выгрузки таблиц.

    max_rows — максимум строк в PDF. Если больше — обрезается.
    """
    buf = io.BytesIO()

    original_rows = len(df)
    if original_rows > max_rows:
        df = df.head(max_rows).copy()

    page_size = landscape(A4) if len(df.columns) > 5 else A4

    doc = SimpleDocTemplate(
        buf, pagesize=page_size,
        leftMargin=8 * mm, rightMargin=8 * mm,
        topMargin=8 * mm, bottomMargin=8 * mm,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"],
                        fontName=ACTIVE_FONT, fontSize=14,
                        textColor=colors.HexColor("#1F4E78"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontName=ACTIVE_FONT, fontSize=9,
                        textColor=colors.grey)
    warn_style = ParagraphStyle("warn", parent=styles["Normal"],
                                fontName=ACTIVE_FONT, fontSize=8,
                                textColor=colors.HexColor("#B71C1C"))
    cell_style = ParagraphStyle("cell", parent=styles["Normal"],
                                fontName=ACTIVE_FONT, fontSize=7, leading=9)

    story = [Paragraph(title, h1)]
    if subtitle:
        story.append(Paragraph(subtitle, h2))
    story.append(Paragraph(
        f"Сформировано: {datetime.now():%d.%m.%Y %H:%M}", h2,
    ))

    if original_rows > max_rows:
        story.append(Paragraph(
            f"⚠ Показаны первые {max_rows} строк из {original_rows}. "
            f"Для полной выгрузки используйте Excel.",
            warn_style,
        ))

    story.append(Spacer(1, 4 * mm))

    if df.empty:
        story.append(Paragraph(
            "Нет данных для отображения.",
            ParagraphStyle("normal", parent=styles["Normal"],
                           fontName=ACTIVE_FONT, fontSize=10),
        ))
        doc.build(story)
        buf.seek(0)
        return buf.getvalue()

    n_cols = len(df.columns)
    total_width = page_size[0] - 16 * mm
    col_width = total_width / n_cols

    header = [Paragraph(f"<b>{_trim_cell(c, 60)}</b>", cell_style)
              for c in df.columns]

    rows = df.values.tolist()
    chunks = [rows[i:i + CHUNK_SIZE] for i in range(0, len(rows), CHUNK_SIZE)]

    for chunk_idx, chunk in enumerate(chunks):
        data = [header]
        for row in chunk:
            data.append([Paragraph(_trim_cell(v), cell_style) for v in row])
        story.append(_build_table(data, [col_width] * n_cols))
        if chunk_idx < len(chunks) - 1:
            story.append(Spacer(1, 3 * mm))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()