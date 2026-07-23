import logging
import re
import html
from PySide6.QtCore import QObject, Signal

from core.logger_config import ColorFormatter

class LogSignals(QObject):
    new_message = Signal(str)

class QtLoggingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.signals = LogSignals()
        self.setFormatter(ColorFormatter())

    def emit(self, record):
        msg = self.format(record)
        escaped_msg = html.escape(msg)
        
        parts = re.split(r'(\x1b\[[0-9;]*m)', escaped_msg)
        
        html_out = ""
        span_open = False
        
        for part in parts:
            if part.startswith('\x1b['):
                if span_open:
                    html_out += "</span>"
                    span_open = False
                    
                if part == '\x1b[0m':
                    pass
                elif part == '\x1b[90m':
                    html_out += '<span style="color: #888888;">'
                    span_open = True
                elif part == '\x1b[33m':
                    html_out += '<span style="color: #FFD700;">'
                    span_open = True
                elif part == '\x1b[31m':
                    html_out += '<span style="color: #FF0000;">'
                    span_open = True
                elif part == '\x1b[31;1m':
                    html_out += '<span style="color: #FF0000; font-weight: bold;">'
                    span_open = True
                elif part == '\x1b[97m':
                    html_out += '<span style="color: #FFFFFF;">'
                    span_open = True
                else:
                    m = re.match(r'\x1b\[38;2;(\d+);(\d+);(\d+)m', part)
                    if m:
                        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        html_out += f'<span style="color: #{r:02x}{g:02x}{b:02x};">'
                        span_open = True
            else:
                html_out += part
                
        if span_open:
            html_out += "</span>"
            
        html_out = html_out.replace('\n', '<br>')
        self.signals.new_message.emit(html_out)

class StreamLogger(QObject):
    new_message = Signal(str)

    def __init__(self, stream):
        super().__init__()
        self.stream = stream

    def write(self, text):
        if text.strip():
            clean_text = re.sub(r'\x1b\[[0-9;]*m', '', text.strip())
            self.new_message.emit(clean_text)
        if self.stream:
            self.stream.write(text)

    def flush(self):
        if self.stream:
            self.stream.flush()
