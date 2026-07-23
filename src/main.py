import sys
import signal
import os
import logging
import shutil
from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QFontDatabase, QFont
from PySide6.QtCore import qInstallMessageHandler, QtMsgType
from gui.main_window import MainWindow
from core.settings_manager import SettingsManager
from core.logger_config import setup_logging
from config import paths
from core.version import __version__ as amity_version

logger = logging.getLogger("core")

def factory_reset_configs():
    """Copy default configuration files if they are missing from the config directory."""
    config_dir = paths.get_app_data_dir()
    os.makedirs(config_dir, exist_ok=True)
    
    src_config_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "config"))
    
    env_target = paths.get_env_file()
    if not os.path.exists(env_target):
        env_example = os.path.join(src_config_dir, ".env.example")
        if os.path.exists(env_example):
            shutil.copyfile(env_example, env_target)
            logger.info(f"Created default .env at {env_target}")
            
    settings_target = paths.get_settings_file()
    if not os.path.exists(settings_target):
        settings_example = os.path.join(src_config_dir, "settings.default.json")
        if os.path.exists(settings_example):
            shutil.copyfile(settings_example, settings_target)
            logger.info(f"Created default settings.json at {settings_target}")

def load_env():
    """Simple .env loader to set environment variables."""
    env_path = paths.get_env_file()
    if os.path.exists(env_path):
        try:
            load_dotenv(env_path)
            logger.info(f"Loaded environment variables from {env_path}")
        except Exception as e:
            logger.error(f"Failed to load .env: {e}", exc_info=True)

def qt_message_handler(mode, context, message):
    if "qt.qpa.wayland" in message or "QWayland" in message:
        logging.debug(f"Qt Wayland: {message}")
        return
        
    if mode == QtMsgType.QtDebugMsg:
        logging.debug(f"Qt: {message}")
    elif mode == QtMsgType.QtInfoMsg:
        logging.info(f"Qt: {message}")
    elif mode == QtMsgType.QtWarningMsg:
        logging.warning(f"Qt: {message}")
    elif mode == QtMsgType.QtCriticalMsg:
        logging.error(f"Qt: {message}")
    elif mode == QtMsgType.QtFatalMsg:
        logging.critical(f"Qt: {message}")
    else:
        logging.debug(f"Qt: {message}")

def main():
    import json
    settings_file = paths.get_settings_file()
    
    retention_days = 7
    debug_logging = False
    
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r") as f:
                s = json.load(f)
                retention_days = s.get("core", {}).get("logging", {}).get("retention-days", 7)
                debug_logging = s.get("core", {}).get("logging", {}).get("show-debug", False)
        except Exception as e:
            logging.debug(f"Could not load settings.json early, using defaults: {e}")

    # Setup logging early to capture all logs
    setup_logging(retention_days=retention_days, debug_logging=debug_logging)
    logger.info(f"Starting Open Amity Version: {amity_version}")

    factory_reset_configs()
    
    # Load environment variables (like HF_TOKEN)
    load_env()

    # SettingsManager can now be instantiated with logging fully configured
    settings_manager = SettingsManager()

    # Handle Ctrl+C (SIGINT) gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    qInstallMessageHandler(qt_message_handler)

    QApplication.setApplicationName("Open Amity")
    # Desktop file name helps Wayland and Linux DEs match the app to its .desktop file
    QApplication.setDesktopFileName("com.openamity.OpenAmity")
    app = QApplication(sys.argv)

    # Load custom fonts
    font_dir = os.path.join(paths.get_assets_dir(), "fonts")
    ubuntu_font_path = os.path.join(font_dir, "Ubuntu-Regular.ttf")
    ubuntu_mono_font_path = os.path.join(font_dir, "UbuntuMono-Regular.ttf")
    ubuntu_light_font_path = os.path.join(font_dir, "Ubuntu-Light.ttf")
    
    if os.path.exists(ubuntu_font_path):
        QFontDatabase.addApplicationFont(ubuntu_font_path)
    else:
        logging.warning(f"Could not find Ubuntu font at {ubuntu_font_path}")
        
    if os.path.exists(ubuntu_mono_font_path):
        QFontDatabase.addApplicationFont(ubuntu_mono_font_path)
    else:
        logging.warning(f"Could not find Ubuntu Mono font at {ubuntu_mono_font_path}")

    if os.path.exists(ubuntu_light_font_path):
        QFontDatabase.addApplicationFont(ubuntu_light_font_path)
    else:
        logging.warning(f"Could not find Ubuntu Light font at {ubuntu_light_font_path}")

    # Set the default application font to Ubuntu
    app.setFont(QFont("Ubuntu", 10))

    # Set the application window icon
    icon_path = paths.get_icon_path()
    app.setWindowIcon(QIcon(icon_path))

    # Set modern global scrollbar style
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

    # Set dark theme palette (optional, but good for base look)
    # app.setStyle("Fusion") 

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
