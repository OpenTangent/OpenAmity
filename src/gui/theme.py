# Centralised theme settings for the GUI
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication, QStyleFactory
from PySide6.QtCore import Qt

PRIMARY_ACCENT_COLOR = "#a12924"
SECONDARY_ACCENT_COLOR = "#f7e3a5"

BG_DARK = "#1a1a1a"
BG_CARD = "#242424"
BG_CARD_DISABLED = "#1a1a1a"
BG_INPUT = "#383838"
BG_INPUT_FOCUS = "#404040"
BG_BUTTON = "#484848"
BG_BUTTON_HOVER = "#585858"
BG_BUTTON_PRESSED = "#383838"

BORDER_COLOR = "#3c3c3c"
BORDER_COLOR_DISABLED = "#2e2e2e"
BORDER_INPUT = "#585858"
BORDER_BUTTON = "#666666"

TEXT_PRIMARY = "#eeeeee"
TEXT_SECONDARY = "#cccccc"
TEXT_MUTED = "#aaaaaa"
TEXT_DISABLED = "#777777"


def get_dark_palette() -> QPalette:
    """Returns a calibrated dark QPalette suitable for Fusion style with high contrast controls."""
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_DARK))
    palette.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Base, QColor(BG_INPUT))
    palette.setColor(QPalette.AlternateBase, QColor(BG_CARD))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    palette.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Button, QColor(BG_BUTTON))
    palette.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.BrightText, QColor("#ff3333"))
    palette.setColor(QPalette.Link, QColor(PRIMARY_ACCENT_COLOR))
    palette.setColor(QPalette.Highlight, QColor(PRIMARY_ACCENT_COLOR))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Mid, QColor("#aaaaaa"))
    palette.setColor(QPalette.Dark, QColor("#777777"))
    palette.setColor(QPalette.Light, QColor("#cccccc"))
    palette.setColor(QPalette.Midlight, QColor("#999999"))
    palette.setColor(QPalette.Shadow, QColor("#111111"))

    # Disabled colors
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_DISABLED))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(TEXT_DISABLED))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_DISABLED))
    palette.setColor(QPalette.Disabled, QPalette.Base, QColor("#222222"))
    palette.setColor(QPalette.Disabled, QPalette.Button, QColor("#333333"))
    palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor("#555555"))
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(TEXT_DISABLED))
    return palette


def setup_app_theme(app: QApplication):
    """Applies the centralized Fusion style, dark palette, and modern scrollbars to the application."""
    fusion_style = QStyleFactory.create("Fusion")
    if fusion_style:
        app.setStyle(fusion_style)
    app.setPalette(get_dark_palette())
    app.setStyleSheet("""
        QScrollBar:vertical {
            border: none;
            background: transparent;
            width: 14px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background-color: rgba(100, 100, 100, 150);
            min-height: 30px;
            border-radius: 7px;
            margin: 2px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: rgba(150, 150, 150, 200);
            margin: 0px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none;
            background: none;
            height: 0px;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
    """)

