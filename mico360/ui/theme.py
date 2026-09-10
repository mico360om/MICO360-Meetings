"""Dark / light theming via Qt style sheets.

Colors are defined as a palette dict and interpolated into one QSS template so
the two themes stay visually consistent. Sizing uses points/relative padding so
the UI scales correctly under different DPI and zoom levels.
"""
from __future__ import annotations

# MICO360 brand palette: deep maroon (#8B1E1E) + charcoal/warm-neutral, matching
# the logo. Accents use the brand-red family (#8B1E1E → #A83326 → #C7513F).
DARK = {
    "bg": "#17151A",            # warm near-black
    "surface": "#201E24",
    "surface2": "#2A272F",
    "border": "#39353F",
    "text": "#ECEAEF",
    "muted": "#A79FA9",
    "accent": "#A83326",        # brand red, brightened for dark surfaces
    "accent_hover": "#8B1E1E",
    "accent_text": "#FFFFFF",
    "danger": "#E5484D",
    "success": "#3FB950",
    "input_bg": "#1A181E",
}

LIGHT = {
    "bg": "#F6F4F3",            # warm off-white
    "surface": "#FFFFFF",
    "surface2": "#F1ECEB",
    "border": "#E4DEDC",
    "text": "#221D1D",         # warm near-black
    "muted": "#6C6269",
    "accent": "#8B1E1E",        # brand maroon
    "accent_hover": "#6E1717",
    "accent_text": "#FFFFFF",
    "danger": "#DC2626",
    "success": "#16A34A",
    "input_bg": "#FFFFFF",
}

_QSS = """
* {{ font-family: "Segoe UI", "Inter", Arial, sans-serif; font-size: 10.5pt; }}
QWidget {{ color: {text}; background: transparent; }}
QMainWindow, #Root {{ background: {bg}; }}

#Sidebar {{ background: {surface}; border-right: 1px solid {border}; }}
#Brand {{ font-size: 14pt; font-weight: 700; color: {text}; }}
#BrandSub {{ color: {muted}; font-size: 8.5pt; }}

QPushButton#NavBtn {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    color: {muted}; background: transparent; font-size: 10.5pt;
}}
QPushButton#NavBtn:hover {{ background: {surface2}; color: {text}; }}
QPushButton#NavBtn:checked {{ background: {accent}; color: {accent_text}; font-weight: 600; }}

#Card {{ background: {surface}; border: 1px solid {border}; border-radius: 12px; }}
QToolButton#SectionHeader {{
    border: none; background: transparent; color: {text};
    font-size: 11.5pt; font-weight: 600; text-align: left; padding: 10px 6px;
}}
QToolButton#SectionHeader:hover {{ color: {accent}; }}
#SectionHeaderRow:hover {{ background: {surface2}; border-top-left-radius: 12px; border-top-right-radius: 12px; }}
#PageTitle {{ font-size: 17pt; font-weight: 700; }}
#PageSub {{ color: {muted}; }}
#SectionTitle {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#Muted, #Hint {{ color: {muted}; font-size: 9.5pt; }}

QPushButton {{
    background: {surface2}; color: {text}; border: 1px solid {border};
    border-radius: 8px; padding: 8px 14px;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {muted}; border-color: {border}; }}

QPushButton#Primary {{
    background: {accent}; color: {accent_text}; border: none; font-weight: 600;
    padding: 10px 18px;
}}
QPushButton#Primary:hover {{ background: {accent_hover}; }}
QPushButton#Primary:disabled {{ background: {surface2}; color: {muted}; }}
QPushButton#Danger {{ color: {accent_text}; background: {danger}; border: none; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid {border}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {input_bg}; border: 1px solid {border}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {accent};
}}
QPlainTextEdit, QTextEdit {{ padding: 10px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: 1px solid {border};
    selection-background-color: {accent}; selection-color: {accent_text};
}}

#Drop {{
    border: 2px dashed {border}; border-radius: 12px; background: {surface2};
}}
#Drop[hover="true"] {{ border-color: {accent}; background: {surface}; }}
#DropTitle {{ font-size: 12pt; font-weight: 600; }}

QProgressBar {{
    border: none; border-radius: 6px; background: {surface2};
    text-align: center; height: 18px; color: {text};
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}

QTableWidget {{
    background: {surface}; border: 1px solid {border}; border-radius: 10px;
    gridline-color: {border};
}}
QHeaderView::section {{
    background: {surface2}; color: {muted}; border: none;
    border-bottom: 1px solid {border}; padding: 8px;
}}
QTableWidget::item {{ padding: 4px; }}
QTableWidget::item:selected {{ background: {accent}; color: {accent_text}; }}

QListWidget {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}
QListWidget::item {{ padding: 10px; border-bottom: 1px solid {border}; }}
QListWidget::item:selected {{ background: {accent}; color: {accent_text}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px; min-width: 30px; }}

#StatusChip {{ border-radius: 10px; padding: 4px 10px; font-size: 9pt; }}
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {muted}; padding: 8px 16px;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ color: {text}; background: {surface}; border: 1px solid {border}; border-bottom: none; }}
QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {border}; padding: 6px; }}
QSplitter::handle {{ background: {border}; }}
"""


def palette(name: str) -> dict:
    return LIGHT if name == "light" else DARK


def build_qss(name: str) -> str:
    return _QSS.format(**palette(name))
