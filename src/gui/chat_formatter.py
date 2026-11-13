import re
import html
from html.parser import HTMLParser
from typing import Dict, Optional
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


_THOUGHT_TOOL_CALL_RE = re.compile(
    r"^(?:(?P<icon>[^\sA-Za-z0-9_<>]+)\s+)?"
    r"(?P<func>[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)"
    r"(?P<parens>\(\)\s*$|:(?P<args>.*))$"
)


def _get_tool_class(tool_name: str):
    """Resolves a registered Tool class by prefix without hard-failing outside full runtime."""
    try:
        from core.cerebrum import Tool
        return Tool.get_tool_class(tool_name)
    except ImportError:
        try:
            from src.core.cerebrum import Tool
            return Tool.get_tool_class(tool_name)
        except Exception:
            return None
    except Exception:
        return None


def _format_thought_card_line(line: str, tool_colors: Optional[Dict[str, str]] = None) -> str:
    """Formats one line of a thoughts card, adding tool emoji + color on tool call signatures."""
    match = _THOUGHT_TOOL_CALL_RE.match(line)
    if not match:
        return html.escape(line)

    raw_icon = match.group("icon")
    # Reject ASCII prefixes such as markdown bullets ('-', '*') mistaken for icons.
    if raw_icon is not None and any(ord(ch) <= 127 for ch in raw_icon):
        return html.escape(line)

    func_name = match.group("func")
    tool_name = func_name.split("_", 1)[0]
    tool_cls = _get_tool_class(tool_name)
    override_color = tool_colors.get(tool_name) if tool_colors else None

    if raw_icon is None and tool_cls is None and not override_color:
        return html.escape(line)

    icon = raw_icon if raw_icon is not None else getattr(tool_cls, "icon", "🔧")
    color = override_color or (getattr(tool_cls, "color", "#888888") if tool_cls is not None else "#888888")
    safe_icon = html.escape(icon)
    safe_color = html.escape(color, quote=True)

    args_part = match.group("args")
    if args_part is None:
        return f'{safe_icon} <span style="color: {safe_color};">{html.escape(func_name)}()</span>'
    return f'{safe_icon} <span style="color: {safe_color};">{html.escape(func_name)}</span>:{html.escape(args_part)}'


def _format_thought_card_body(content: str, tool_colors: Optional[Dict[str, str]] = None) -> str:
    """Formats expanded thoughts-card text line by line into safe HTML."""
    lines = content.strip().split("\n")
    return "<br>".join(_format_thought_card_line(line, tool_colors=tool_colors) for line in lines)


def render_thought_card_html(
    card_id,
    content: str,
    collapsed: bool = True,
    tool_colors: Optional[Dict[str, str]] = None,
) -> str:
    """Renders a collapsible Thoughts card with styled tool calls and monospace thought text."""
    chevron = "▶" if collapsed else "▼"
    safe_id = html.escape(str(card_id), quote=True)
    header_html = (
        f'<div style="text-align: left;">'
        f'<a href="thought:toggle:{safe_id}" '
        f'style="color: #8c8c94; text-decoration: none; font-weight: bold; font-size: 13px;">'
        f'<span style="color: #8c8c94;">{chevron} Thoughts</span>'
        f'</a>'
        f'</div>'
    )

    body_html = ""
    if not collapsed and content:
        escaped_body = _format_thought_card_body(content, tool_colors=tool_colors)
        body_html = (
            f'<div style="margin-top: 6px; color: #9c9ca4; '
            f'font-family: \'Ubuntu Mono\', monospace; font-size: 13px; line-height: 1.5;">'
            f'{escaped_body}'
            f'</div>'
        )

    padding = "8px 14px" if collapsed else "10px 16px"
    return wrap_in_isolated_container(
        header_html + body_html,
        margin_bottom=10,
        bg_color="#161618",
        border_color="#28282c",
        border_radius=8,
        padding=padding,
    )


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
        scroll_bar.rangeChanged.connect(
            lambda min_val, max_val: scroll_bar.setValue(max_val)
            if getattr(browser, "_auto_scroll_on_range_change", True)
            else None
        )
