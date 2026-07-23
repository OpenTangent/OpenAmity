import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys

class VerbosityFilter(logging.Filter):
    def filter(self, record):
        if "AFC will be disabled" in str(record.msg):
            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"
            
        if record.levelno == logging.INFO:
            is_sdk_root = record.name == "root" and "google/antigravity" in record.pathname.replace('\\', '/')
            if is_sdk_root or record.name.startswith(("httpx", "httpcore", "mempalace", "urllib3", "google_genai", "google.antigravity")):
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
            
        return True

class BaseFormatter(logging.Formatter):
    def get_source(self, record):
        if record.name == "root":
            path = record.pathname.replace('\\', '/')
            if "/src/core/" in path:
                return "core"
            elif "/src/gui/" in path:
                return "gui"
            elif "/src/tools/" in path:
                return "tool"
            elif path.endswith("main.py"):
                return "core"
            else:
                return "other"
        return record.name

class FileFormatter(BaseFormatter):
    def format(self, record):
        source = self.get_source(record)
        if record.levelno == logging.INFO:
            format_str = f"%(asctime)s - [{source}] - %(message)s"
        else:
            format_str = f"%(asctime)s - [{source}] - %(levelname)s - %(message)s"
        formatter = logging.Formatter(format_str)
        return formatter.format(record)

class ColorFormatter(BaseFormatter):
    grey = "\x1b[90m"
    white = "\x1b[97m"
    yellow = "\x1b[33m"
    red = "\x1b[31m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"

    SECTION_COLORS = {
        "core": "\x1b[38;2;243;156;18m",
        "gui": "\x1b[38;2;253;121;168m",
        "main": "\x1b[38;2;173;216;230m",
        "tool": "\x1b[38;2;241;196;15m",
        "other": "\x1b[38;2;200;200;200m",
        "agent.thinker": "\x1b[38;2;155;89;182m",
        "agent.speaker": "\x1b[38;2;52;152;219m",
        "agent.tool.WhatsApp": "\x1b[38;2;37;211;102m",
        "agent.tool.Trajectory": "\x1b[38;2;26;188;156m",
        "agent.tool.Contacts": "\x1b[38;2;0;150;136m",
        "agent.tool.DateTime": "\x1b[38;2;139;195;74m",
        "agent.tool.Mastodon": "\x1b[38;2;99;100;255m",
        "agent.tool.MemPalace": "\x1b[38;2;205;133;63m",
        "agent.tool.PulseTool": "\x1b[38;2;224;64;251m",
        "agent.tool.Speaker": "\x1b[38;2;3;169;244m",
        "agent.tool.Terminal": "\x1b[38;2;112;128;144m",
        "piper.voice": "\x1b[38;2;128;128;0m",
    }

    FALLBACK_PALETTE = [
        "\x1b[38;2;46;204;113m",   # Emerald
        "\x1b[38;2;26;188;156m",   # Turquoise
        "\x1b[38;2;52;152;219m",   # Blue
        "\x1b[38;2;155;89;182m",   # Amethyst
        "\x1b[38;2;241;196;15m",   # Sun Flower
        "\x1b[38;2;230;126;34m",   # Carrot
        "\x1b[38;2;0;150;136m",    # Teal
        "\x1b[38;2;63;81;181m",    # Indigo
        "\x1b[38;2;156;39;176m",   # Purple
        "\x1b[38;2;255;152;0m",    # Orange
        "\x1b[38;2;139;195;74m",   # Light Green
        "\x1b[38;2;0;188;212m",    # Cyan
    ]

    def get_section_color(self, source):
        if source in self.SECTION_COLORS:
            return self.SECTION_COLORS[source]
        h = sum(ord(c) for c in source)
        return self.FALLBACK_PALETTE[h % len(self.FALLBACK_PALETTE)]

    def format(self, record):
        source = self.get_source(record)
        
        if record.levelno <= logging.INFO:
            level_color = self.grey
        elif record.levelno == logging.WARNING:
            level_color = self.yellow
        elif record.levelno >= logging.ERROR:
            level_color = self.red
        else:
            level_color = self.reset
            
        section_color = self.get_section_color(source)
        colored_source = f"{section_color}[{source}]{level_color}"
        
        if record.levelno == logging.INFO:
            format_str = f"%(asctime)s - {colored_source} - %(message)s"
        else:
            format_str = f"%(asctime)s - {colored_source} - %(levelname)s - %(message)s"
            
        formatter = logging.Formatter(level_color + format_str + self.reset, datefmt="%H:%M")
        return formatter.format(record)

EARLY_LOG_BUFFER = []

class EarlyBufferHandler(logging.Handler):
    def emit(self, record):
        EARLY_LOG_BUFFER.append(record)

early_buffer_handler = EarlyBufferHandler()

def setup_logging(retention_days=7, debug_logging=False):
    from config import paths
    log_dir = os.path.join(paths.get_app_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "open_amity.log")
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    verbosity_filter = VerbosityFilter()
        
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileFormatter())
    file_handler.addFilter(verbosity_filter)
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if debug_logging else logging.INFO)
    console_handler.setFormatter(ColorFormatter())
    console_handler.addFilter(verbosity_filter)
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    early_buffer_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(early_buffer_handler)
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        root_logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception
    
    # Also handle thread exceptions
    import threading
    def handle_thread_exception(args):
        root_logger.error("Uncaught thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    
    threading.excepthook = handle_thread_exception
    
    logging.captureWarnings(True)
    logging.getLogger("core").info(f"Logging initialized. Retaining logs for {retention_days} days.")
