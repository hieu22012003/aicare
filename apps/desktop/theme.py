"""
Design Tokens, Theme Stylesheet, and UI Utilities for AI Rehabilitation & Safety Assistant.
Theme: Modern Medical Center (Light canvas, white cards, navy typography, royal blue accents).
Complies with KE_HOACH_CHINH_UI_VA_THU_FALL_VIDEO.md & KE_HOACH_REFACTOR_UI_AI_NGUOI_CAO_TUOI.md.
"""

from __future__ import annotations

# ==================== DESIGN TOKENS ====================
# Surfaces and Backgrounds
COLOR_CANVAS = "#F3F5F1"        # Light calm medical canvas
COLOR_SURFACE = "#FFFFFF"       # White card surface
COLOR_SURFACE_ALT = "#F8FAF7"   # Secondary light container
COLOR_SURFACE_HOVER = "#EDF2E9" # Interactive hover surface

# Typography
COLOR_TEXT_PRIMARY = "#183A34"    # Deep medical navy
COLOR_TEXT_SECONDARY = "#667B74"  # Slate muted text (>= 13-14px)
COLOR_TEXT_MUTED = "#8A99AD"      # Light captions
COLOR_TEXT_WHITE = "#FFFFFF"

# Borders
COLOR_BORDER = "#DFE5DF"          # Clean crisp card border
COLOR_BORDER_SUBTLE = "#EDF2F7"   # Inner separator border
COLOR_BORDER_FOCUS = "#285C4D"    # Active focus border

# Accent and Brand
COLOR_PRIMARY = "#285C4D"         # Royal blue
COLOR_PRIMARY_HOVER = "#214F42"   # Hover blue
COLOR_PRIMARY_BG = "#EAF0E2"      # Pale blue tint
COLOR_ACCENT = "#8EA783"          # Sky accent

# Semantic Statuses
COLOR_SUCCESS = "#167D62"         # Emerald medical green
COLOR_SUCCESS_BG = "#E8F8F3"      # Pale green
COLOR_SUCCESS_BORDER = "#B2E5D4"

COLOR_WARNING = "#A96300"         # Amber warm warning
COLOR_WARNING_BG = "#FEF6E9"      # Pale amber
COLOR_WARNING_BORDER = "#FCE0B6"

COLOR_DANGER = "#C3293E"          # Medical crimson
COLOR_DANGER_BG = "#FEECEE"       # Pale crimson
COLOR_DANGER_BORDER = "#F8C1C8"

COLOR_NEUTRAL = "#4A5568"         # Slate neutral
COLOR_NEUTRAL_BG = "#EDF2F7"      # Pale neutral
COLOR_NEUTRAL_BORDER = "#CBD5E1"

# Test Mode Dry-Run Tokens
COLOR_TEST_BG = "#FFF7ED"         # Warm amber test canvas
COLOR_TEST_BORDER = "#FDBA74"     # Warm amber test border
COLOR_TEST_TEXT = "#9A3412"       # Dark burnt orange text

# Font Family specification - prioritized for high-fidelity Vietnamese rendering across Linux, Windows and macOS
FONT_FAMILY = '"Segoe UI", "Ubuntu", "DejaVu Sans", "Noto Sans", Roboto, "Helvetica Neue", Arial, sans-serif'

# ==================== UNIFIED LIGHT STYLESHEET ====================
LIGHT_THEME_STYLESHEET = f"""
QMainWindow {{
    background-color: {COLOR_CANVAS};
}}

QWidget {{
    font-family: {FONT_FAMILY};
    color: {COLOR_TEXT_PRIMARY};
    font-size: 14px;
}}

/* Window Splitter */
QSplitter::handle {{
    background-color: {COLOR_BORDER};
    width: 3px;
}}

/* Header Bar */
#HeaderBar {{
    background-color: {COLOR_SURFACE};
    border-bottom: 1.5px solid {COLOR_BORDER};
    padding: 8px 18px;
}}
#BrandTitle {{
    font-size: 20px;
    font-weight: 800;
    color: {COLOR_TEXT_PRIMARY};
    letter-spacing: 0.2px;
}}
#BrandSubtitle {{
    font-size: 13px;
    color: {COLOR_TEXT_SECONDARY};
    font-weight: 500;
}}

/* Source Selector Buttons in Header */
QPushButton.btn-source-tab, QPushButton[class="btn-source-tab"] {{
    background-color: {COLOR_SURFACE_ALT};
    color: {COLOR_TEXT_PRIMARY};
    border: 1.5px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton.btn-source-tab:hover, QPushButton[class="btn-source-tab"]:hover {{
    background-color: {COLOR_PRIMARY_BG};
    border-color: {COLOR_PRIMARY};
    color: {COLOR_PRIMARY};
}}
QPushButton.btn-source-tab-active, QPushButton[class="btn-source-tab-active"] {{
    background-color: {COLOR_PRIMARY};
    color: #FFFFFF;
    border: 1.5px solid {COLOR_PRIMARY};
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 14px;
    font-weight: 700;
}}

/* Global Safety Banner */
#SafetyBanner {{
    background-color: {COLOR_SURFACE};
    border: 1.5px solid {COLOR_BORDER};
    border-radius: 12px;
    padding: 10px 16px;
    margin: 6px 14px 4px 14px;
}}

/* Cards */
QFrame.ui-card, QFrame[class="ui-card"] {{
    background-color: {COLOR_SURFACE};
    border: 1.5px solid {COLOR_BORDER};
    border-radius: 12px;
    padding: 14px;
}}
QFrame.ui-card-highlight, QFrame[class="ui-card-highlight"] {{
    background-color: {COLOR_SURFACE};
    border: 2px solid {COLOR_PRIMARY};
    border-radius: 12px;
    padding: 14px;
}}
QFrame.ui-card-sub, QFrame[class="ui-card-sub"] {{
    background-color: {COLOR_SURFACE_ALT};
    border: 1px solid {COLOR_BORDER_SUBTLE};
    border-radius: 8px;
    padding: 10px;
}}

/* Card Titles & Labels */
QLabel.card-title, QLabel[class="card-title"] {{
    font-size: 15px;
    font-weight: 700;
    color: {COLOR_TEXT_PRIMARY};
}}
QLabel.card-title-primary, QLabel[class="card-title-primary"] {{
    font-size: 15px;
    font-weight: 700;
    color: {COLOR_PRIMARY};
}}
QLabel.card-value, QLabel[class="card-value"] {{
    font-size: 15px;
    font-weight: 700;
    color: {COLOR_TEXT_PRIMARY};
}}
QLabel.card-subtext, QLabel[class="card-subtext"] {{
    font-size: 13px;
    color: {COLOR_TEXT_SECONDARY};
}}

/* Action Buttons */
#BtnStart, QPushButton.btn-primary, QPushButton[class="btn-primary"] {{
    background-color: #285C4D;
    color: #FFFFFF;
    font-size: 15px;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    min-height: 38px;
}}
#BtnStart:hover, QPushButton.btn-primary:hover, QPushButton[class="btn-primary"]:hover {{
    background-color: #214F42;
    color: #FFFFFF;
}}
#BtnStart:disabled, QPushButton.btn-primary:disabled, QPushButton[class="btn-primary"]:disabled {{
    background-color: #E4EBDD;
    color: #84998A;
}}

#BtnStop, QPushButton.btn-danger, QPushButton[class="btn-danger"] {{
    background-color: #C3293E;
    color: #FFFFFF;
    font-size: 15px;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    min-height: 38px;
}}
#BtnStop:hover, QPushButton.btn-danger:hover, QPushButton[class="btn-danger"]:hover {{
    background-color: #A52133;
    color: #FFFFFF;
}}
#BtnStop:disabled, QPushButton.btn-danger:disabled, QPushButton[class="btn-danger"]:disabled {{
    background-color: #F8D7DA;
    color: #C47F87;
}}

#BtnReset, #BtnReport, QPushButton.btn-secondary, QPushButton[class="btn-secondary"] {{
    background-color: #FFFFFF;
    color: #183A34;
    font-size: 14px;
    font-weight: 600;
    border: 1.5px solid #DFE5DF;
    border-radius: 8px;
    padding: 8px 16px;
    min-height: 36px;
}}
#BtnReset:hover, #BtnReport:hover, QPushButton.btn-secondary:hover, QPushButton[class="btn-secondary"]:hover {{
    background-color: #EDF2E9;
    border-color: #285C4D;
    color: #285C4D;
}}

/* Quick action pills */
QPushButton.btn-pill, QPushButton[class="btn-pill"] {{
    background-color: {COLOR_PRIMARY_BG};
    color: {COLOR_PRIMARY};
    border: 1px solid #D8E3D0;
    border-radius: 14px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton.btn-pill:hover, QPushButton[class="btn-pill"]:hover {{
    background-color: {COLOR_PRIMARY};
    color: #FFFFFF;
}}

QPushButton.btn-pill-secondary, QPushButton[class="btn-pill-secondary"] {{
    background-color: {COLOR_SURFACE_ALT};
    color: {COLOR_TEXT_SECONDARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 14px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton.btn-pill-secondary:hover, QPushButton[class="btn-pill-secondary"]:hover {{
    background-color: {COLOR_BORDER};
    color: {COLOR_TEXT_PRIMARY};
}}

/* Form Inputs */
QLineEdit, QSpinBox, QComboBox {{
    background-color: {COLOR_SURFACE};
    border: 1.5px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
    color: {COLOR_TEXT_PRIMARY};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {COLOR_PRIMARY};
    background-color: #FFFFFF;
}}

/* Video Viewport Container */
#VideoContainer {{
    background-color: #0E1626;
    border: 2px solid {COLOR_BORDER};
    border-radius: 12px;
}}

/* Video Timeline Slider */
QSlider::groove:horizontal {{
    height: 8px;
    background: #DFE5DF;
    border-radius: 4px;
}}
QSlider::sub-page:horizontal {{
    background: #285C4D;
    border-radius: 4px;
}}
QSlider::handle:horizontal {{
    background: #285C4D;
    border: 2px solid #FFFFFF;
    width: 18px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{
    background: #214F42;
}}

/* Tab Widget & Bar */
QTabWidget::pane {{
    border: 1.5px solid {COLOR_BORDER};
    background-color: {COLOR_SURFACE};
    border-radius: 8px;
    top: -1px;
}}
QTabBar::tab {{
    font-family: {FONT_FAMILY};
    font-size: 14px;
    font-weight: 600;
    color: {COLOR_TEXT_SECONDARY};
    background-color: {COLOR_SURFACE_ALT};
    border: 1.5px solid {COLOR_BORDER};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 16px;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    color: {COLOR_PRIMARY};
    background-color: {COLOR_SURFACE};
    font-weight: 700;
    border-color: {COLOR_BORDER};
    border-bottom: 2px solid {COLOR_SURFACE};
}}
QTabBar::tab:hover:!selected {{
    background-color: {COLOR_SURFACE_HOVER};
    color: {COLOR_PRIMARY_HOVER};
}}

/* Bottom Status Bar */
#BottomStatusBar {{
    background-color: {COLOR_SURFACE};
    border-top: 1px solid {COLOR_BORDER};
    padding: 6px 18px;
    font-size: 13px;
    color: {COLOR_TEXT_SECONDARY};
}}
"""


LIGHT_THEME_STYLESHEET += """
#DesktopSidebar { background: #FBFCF9; border-right: 1px solid #DFE5DF; }
#DesktopWorkspace { background: #F3F5F1; }
#SidebarMonogram { background: #285C4D; color: white; font-size: 24px; font-weight: 700; border-radius: 10px; }
#SidebarBrand { color: #183A34; font-size: 24px; font-weight: 700; padding-top: 8px; }
#SidebarCaption { color: #788A7D; font-size: 11px; letter-spacing: 1px; }
#SidebarFooter { color: #718477; font-size: 12px; border-top: 1px solid #DFE5DF; padding-top: 20px; }
QPushButton#SidebarNav { background: transparent; color: #4D685C; border: none; border-radius: 8px; text-align: left; padding: 10px 9px; font-size: 14px; font-weight: 500; }
QPushButton#SidebarNav:hover { background: #F0F3EA; }
QPushButton#SidebarNav:checked { background: #E8EFDf; color: #214F42; font-weight: 700; }
QTabWidget#MainWorkspaceTabs::pane { border: none; background: #F3F5F1; }
#HeaderBar { border: 1px solid #DFE5DF; border-radius: 9px; background: #FFFFFF; }
#BrandTitle { font-size: 18px; font-weight: 700; }
QFrame[class="ui-card"], QFrame[class="ui-card-highlight"] { border: 1px solid #DFE5DF; border-radius: 10px; background: #FFFFFF; }
QProgressBar { background: #F0F3EB; border: 1px solid #DCE5D5; border-radius: 5px; color: #285C4D; }
QProgressBar::chunk { background: #91AB78; border-radius: 4px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 9px; background: #F3F5F1; }
QScrollBar::handle:vertical { background: #C5CFC1; border-radius: 4px; min-height: 32px; }
"""


# ==================== UTILITY STYLESHEET HELPERS ====================
def get_badge_stylesheet(
    kind: str = "neutral",
    font_size: int = 14,
    font_weight: str = "700",
    padding: str = "6px 14px",
    rounded: int = 8,
) -> str:
    """
    Returns inline stylesheet for a badge or status pill without !important.
    """
    kind = kind.lower()
    if kind == "success":
        bg, color, border = COLOR_SUCCESS_BG, COLOR_SUCCESS, COLOR_SUCCESS_BORDER
    elif kind == "warning":
        bg, color, border = COLOR_WARNING_BG, COLOR_WARNING, COLOR_WARNING_BORDER
    elif kind == "danger":
        bg, color, border = COLOR_DANGER_BG, COLOR_DANGER, COLOR_DANGER_BORDER
    elif kind == "primary":
        bg, color, border = COLOR_PRIMARY_BG, COLOR_PRIMARY, "#B9D7FB"
    elif kind == "info":
        bg, color, border = "#EBF5FF", "#1D4ED8", "#BFDBFE"
    elif kind == "test":
        bg, color, border = COLOR_TEST_BG, COLOR_TEST_TEXT, COLOR_TEST_BORDER
    else:  # neutral
        bg, color, border = COLOR_NEUTRAL_BG, COLOR_NEUTRAL, COLOR_NEUTRAL_BORDER

    return (
        f"QLabel {{ "
        f"background-color: {bg}; "
        f"color: {color}; "
        f"border: 1.5px solid {border}; "
        f"border-radius: {rounded}px; "
        f"padding: {padding}; "
        f"font-size: {font_size}px; "
        f"font-weight: {font_weight}; "
        f"}}"
    )

