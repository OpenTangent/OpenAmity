import re
import html
from html.parser import HTMLParser
import markdown
from PySide6.QtWidgets import QTextBrowser
from PySide6.QtGui import QTextCursor, QTextBlockFormat, QTextCharFormat, QTextOption

try:
    from gui.theme import (
        PRIMARY_ACCENT_COLOR,
        SECONDARY_ACCENT_COLOR,
        CHAT_DOCUMENT_CSS,
        CHAT_FONT_FAMILY,
        MODERN_SCROLLBAR_STYLE,
    )
except ImportError:
    PRIMARY_ACCENT_COLOR = "#a12924"
    SECONDARY_ACCENT_COLOR = "#f7e3a5"
    CHAT_FONT_FAMILY = "Inter"
    CHAT_DOCUMENT_CSS = ""
    MODERN_SCROLLBAR_STYLE = ""


class HtmlTagBalancer(HTMLParser):
    """Tracks opening and closing HTML tags and generates missing closing tags to ensure encapsulation."""

    VOID_TAGS = {
        "br", "hr", "img", "input", "meta", "link",
        "area", "base", "col", "embed", "param", "source", "track", "wbr"
    }

    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t not in self.VOID_TAGS:
            self.stack.append(t)

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.stack:
            while self.stack:
                popped = self.stack.pop()
                if popped == t:
                    break

    def get_closing_tags(self) -> str:
        return "".join(f"</{t}>" for t in reversed(self.stack))


ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "s", "del", "code", "pre", "blockquote",
    "ol", "ul", "li", "table", "thead", "tbody", "tr", "th", "td",
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "span", "div", "a"
}

_TAG_PATTERN = re.compile(r"<\s*(/)?\s*([a-zA-Z0-9]+)([^>]*)>")


def sanitize_disallowed_html_tags(html_str: str) -> str:
    """Escapes any HTML tags that are not in the allowed text-formatting whitelist."""
    if not html_str:
        return ""

    def tag_replacer(match):
        full_tag = match.group(0)
        tag_name = match.group(2).lower()
        if tag_name not in ALLOWED_TAGS:
            return html.escape(full_tag)
        return full_tag

    return _TAG_PATTERN.sub(tag_replacer, html_str)


def balance_html(html_str: str) -> str:
    """Balances and closes any unclosed HTML tags in the provided string."""
    if not html_str:
        return ""
    parser = HtmlTagBalancer()
    try:
        parser.feed(html_str)
        return html_str + parser.get_closing_tags()
    except Exception:
        return html_str


def render_markdown_to_html(text: str) -> str:
    """Renders text as GitHub-flavored markdown with sane lists, code fences, and tables, ensuring balanced tags."""
    if not text:
        return ""
    rendered = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"]
    )
    sanitized = sanitize_disallowed_html_tags(rendered)
    return balance_html(sanitized)


def render_user_message_content(text: str) -> str:
    """Renders user message text safely with markdown formatting and sanitized tags."""
    return render_markdown_to_html(text)


def wrap_in_isolated_container(
    inner_html: str,
    margin_bottom: int = 14,
    bg_color: str = "#222225",
    border_color: str = "#36363b",
    border_radius: int = 8,
    padding: str = "12px 18px",
    border_left: str = "",
) -> str:
    """Wraps message HTML in a dedicated single-cell card container with padding and rounded corners.
    Qt Scribe assigns an isolated QTextTable frame to each table, guaranteeing that
    lists, block margins, and styles within the cell cannot merge with or leak into adjacent messages.
    """
    balanced_inner = balance_html(inner_html)
    border_left_style = f"border-left: {border_left}; " if border_left else ""
    table_border = f"border: 1px solid {border_color}; {border_left_style}border-radius: {border_radius}px;"
    td_style = f"padding: {padding}; background-color: {bg_color}; border-radius: {border_radius}px;"

    return (
        f'<table width="100%" cellpadding="12" cellspacing="0" '
        f'bgcolor="{bg_color}" '
        f'style="margin-bottom: {margin_bottom}px; background-color: {bg_color}; {table_border}">'
        f'<tr><td bgcolor="{bg_color}" style="{td_style}">'
        f'{balanced_inner}'
        f'</td></tr>'
        f'</table>'
    )


def insert_message_into_log(browser: QTextBrowser, message_html: str):
    """Safely appends a message into the conversation log browser.
    Resets character format, block format, and cursor positioning to prevent format bleeding.
    """
    cursor = browser.textCursor()
    cursor.movePosition(QTextCursor.End)

    if not browser.document().isEmpty():
        cursor.setCharFormat(QTextCharFormat())
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())

    browser.setTextCursor(cursor)
    cursor.insertHtml(message_html)

    # Auto scroll to bottom
    scroll_bar = browser.verticalScrollBar()
    if scroll_bar:
        scroll_bar.setValue(scroll_bar.maximum())


def setup_chat_browser(browser: QTextBrowser):
    """Applies standardized chat settings, typography, default CSS, and word-wrapping to a QTextBrowser."""
    browser.setReadOnly(True)
    browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
    browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    if CHAT_DOCUMENT_CSS:
        browser.document().setDefaultStyleSheet(CHAT_DOCUMENT_CSS)

    browser.setStyleSheet(f"""
        QTextBrowser {{
            background-color: #141416;
            color: #FFF;
            border: none;
            padding: 16px;
            font-family: '{CHAT_FONT_FAMILY}', 'Liberation Serif', 'Georgia', serif;
            font-size: 18px;
        }}
        {MODERN_SCROLLBAR_STYLE}
    """)

    scroll_bar = browser.verticalScrollBar()
    if scroll_bar:
        scroll_bar.rangeChanged.connect(lambda min_val, max_val: scroll_bar.setValue(max_val))
