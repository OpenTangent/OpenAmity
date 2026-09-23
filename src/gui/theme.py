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

CHAT_FONT_FAMILY = "EB Garamond"
CHAT_MONO_FONT_FAMILY = "Ubuntu Mono"

CHAT_DOCUMENT_CSS = f"""
body {{
    font-family: '{CHAT_FONT_FAMILY}', 'Liberation Serif', 'Georgia', serif;
    font-size: 18px;
    line-height: 1.65;
    color: #e0e0e0;
}}
p {{
    margin-top: 5px;
    margin-bottom: 7px;
    line-height: 1.65;
}}
pre {{
    background-color: #1e1e24;
    border: 1px solid #333338;
    border-radius: 5px;
    padding: 10px 12px;
    font-family: '{CHAT_MONO_FONT_FAMILY}', monospace;
    font-size: 14px;
    color: #e6edf3;
    margin-top: 6px;
    margin-bottom: 6px;
}}
code {{
    background-color: #2c2c32;
    font-family: '{CHAT_MONO_FONT_FAMILY}', monospace;
    font-size: 14px;
    color: #ff9e64;
    padding: 2px 5px;
    border-radius: 3px;
}}
pre code {{
    background-color: transparent;
    color: #e6edf3;
    padding: 0;
}}
blockquote {{
    border-left: 3px solid {SECONDARY_ACCENT_COLOR};
    margin-left: 0px;
    margin-top: 6px;
    margin-bottom: 6px;
    padding-left: 12px;
    color: #b0b0b0;
    font-style: italic;
}}
table {{
    border-collapse: collapse;
    margin-top: 8px;
    margin-bottom: 8px;
}}
th, td {{
    border: 1px solid #444444;
    padding: 6px 10px;
    text-align: left;
}}
th {{
    background-color: #2a2a2a;
    color: #ffffff;
    font-weight: bold;
}}
td {{
    background-color: #1f1f1f;
    color: #dddddd;
}}
a {{
    color: #58a6ff;
    text-decoration: none;
}}
h1, h2, h3, h4, h5, h6 {{
    font-family: '{CHAT_FONT_FAMILY}', 'Liberation Serif', 'Georgia', serif;
    color: #ffffff;
    font-weight: bold;
    margin-top: 10px;
    margin-bottom: 4px;
}}
h1 {{ font-size: 22px; }}
h2 {{ font-size: 20px; }}
h3 {{ font-size: 19px; }}
h4 {{ font-size: 18px; }}
ul, ol {{
    margin-top: 4px;
    margin-bottom: 6px;
    padding-left: 24px;
}}
li {{
    margin-top: 2px;
    margin-bottom: 2px;
}}
hr {{
    border: none;
    border-top: 1px solid #3c3c3c;
    margin-top: 8px;
    margin-bottom: 8px;
}}
"""


def get_dark_palette() -> QPalette:
    """Returns a calibrated dark QPalette suitable for Fusion style with high contrast controls."""
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_DARK))
    palette.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Base, QColor(BG_INPUT))
    palette.setColor(QPalette.AlternateBase, QColor(BG_CARD))
    palette.setColor(QPalette.ToolTipBase, QColor("#2b2b2b"))
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


MODERN_SCROLLBAR_STYLE = """
/* Standardized Modern Minimal Scrollbars */
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 8px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background-color: rgba(255, 255, 255, 0.2);
    min-height: 24px;
    border-radius: 3px;
    margin: 1px;
}
QScrollBar::handle:vertical:hover {
    background-color: rgba(255, 255, 255, 0.38);
    margin: 1px;
}
QScrollBar::handle:vertical:pressed {
    background-color: rgba(255, 255, 255, 0.55);
    margin: 1px;
}
QScrollBar::handle:vertical:disabled {
    background-color: transparent;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: transparent;
    height: 0px;
    width: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    border: none;
    background: transparent;
}

QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 8px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background-color: rgba(255, 255, 255, 0.2);
    min-width: 24px;
    border-radius: 3px;
    margin: 1px;
}
QScrollBar::handle:horizontal:hover {
    background-color: rgba(255, 255, 255, 0.38);
    margin: 1px;
}
QScrollBar::handle:horizontal:pressed {
    background-color: rgba(255, 255, 255, 0.55);
    margin: 1px;
}
QScrollBar::handle:horizontal:disabled {
    background-color: transparent;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    border: none;
    background: transparent;
    height: 0px;
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    border: none;
    background: transparent;
}

QScrollBar::corner {
    background: transparent;
    border: none;
}
"""


def setup_app_theme(app: QApplication):
    """Applies the centralized Fusion style, dark palette, and modern scrollbars to the application."""
    fusion_style = QStyleFactory.create("Fusion")
    if fusion_style:
        app.setStyle(fusion_style)
    app.setPalette(get_dark_palette())
    app.setStyleSheet(f"""
        QToolTip {{
            color: #ffffff;
            background-color: #2b2b2b;
            border: 1px solid #555555;
            padding: 4px 8px;
            border-radius: 4px;
        }}
        {MODERN_SCROLLBAR_STYLE}
    """)

