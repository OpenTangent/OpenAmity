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
        if record.name not in ("root", "core"):
            return record.name
            
        path = record.pathname.replace('\\', '/')
        filename = os.path.basename(path)
        module_name = os.path.splitext(filename)[0]
        
        pascal_name = "".join(word.capitalize() for word in module_name.split("_"))
        
        if "/src/core/gemini_worker.py" in path:
            return "geminiworker.Main"
        elif "/src/core/agy_worker.py" in path:
            return "agyworker.Main"
        elif "/src/core/" in path:
            if filename == "__init__.py":
                return "core.Init"
            return f"core.{pascal_name}"
        elif "/src/gui/" in path:
            if filename == "__init__.py":
                return "core.GUI"
            return f"core.GUI.{pascal_name}"
        elif "/src/tools/" in path:
            if filename == "__init__.py":
                return "tool.Init"
            clean_name = pascal_name
            if clean_name.endswith("Tool"):
                clean_name = clean_name[:-4]
            return f"tool.{clean_name}"
        elif path.endswith("main.py"):
            return "core.Init"
        else:
            return "other"

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
        "tool.Contacts": "\x1b[38;2;0;150;136m",
        "tool.DateTime": "\x1b[38;2;139;195;74m",
        "tool.Mastodon": "\x1b[38;2;99;100;255m",
        "tool.Media": "\x1b[38;2;255;105;180m",
        "tool.MemPalace": "\x1b[38;2;205;133;63m",
        "tool.Moltbook": "\x1b[38;2;255;152;0m",
        "tool.Pulse": "\x1b[38;2;103;58;183m",
        "tool.Speaker": "\x1b[38;2;3;169;244m",
        "tool.Terminal": "\x1b[38;2;243;156;18m",
        "tool.Trajectory": "\x1b[38;2;26;188;156m",
        "tool.WebSearch": "\x1b[38;2;0;188;212m",
        "tool.WhatsApp": "\x1b[38;2;37;211;102m",
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
            
        primary_section = source.split('.')[0]
        if primary_section == "core":
            if source.startswith("core.TTS"):
                return "\x1b[38;2;128;128;0m" # Olive
            return self.grey # Light Grey matches INFO logs
        elif primary_section == "geminiworker":
            return "\x1b[38;2;155;89;182m" # Amethyst
        elif primary_section == "agyworker":
            return "\x1b[38;2;224;64;251m" # Purple
            
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
