import sys
import signal
import os
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QFontDatabase, QFont
from PySide6.QtCore import qInstallMessageHandler, QtMsgType
from gui.main_window import MainWindow
from gui.splash_screen import LoadingCard
from gui.theme import setup_app_theme
from core.logger_config import setup_logging
from config import paths
from core.version import __version__ as amity_version

logger = logging.getLogger("core")


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
    retention_days = 7
    debug_logging = False

    # Setup logging early to capture all logs
    setup_logging(retention_days=retention_days, debug_logging=debug_logging)
    logger.info(f"Starting Open Amity Version: {amity_version}")

    # Handle Ctrl+C (SIGINT) gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    qInstallMessageHandler(qt_message_handler)

    QApplication.setApplicationName("Open Amity")
    # Desktop file name helps Wayland and Linux DEs match the app to its .desktop file
    QApplication.setDesktopFileName("com.openamity.OpenAmity")
    app = QApplication(sys.argv)

    # Load custom fonts
    font_dir = os.path.join(paths.get_assets_dir(), "fonts")
    if os.path.exists(font_dir):
        for font_file in sorted(os.listdir(font_dir)):
            if font_file.lower().endswith((".ttf", ".otf")):
                font_path = os.path.join(font_dir, font_file)
                res_id = QFontDatabase.addApplicationFont(font_path)
                if res_id < 0:
                    logging.warning(f"Could not load font at {font_path}")
    else:
        logging.warning(f"Fonts directory not found at {font_dir}")

    # Set the default application font to Ubuntu
    app.setFont(QFont("Ubuntu", 10))

    # Set the application window icon
    icon_path = paths.get_icon_path()
    app.setWindowIcon(QIcon(icon_path))

    # Set centralized application theme, dark palette, and modern scrollbars
    setup_app_theme(app)

    from PySide6.QtCore import QTimer, QThread, Signal, QObject

    class InitWorker(QObject):
        finished = Signal(object)

        def run(self):
            from core.agent_manager import AgentManager
            agent_manager = AgentManager()

            # Pre-load orchestrators for existing agents to avoid blocking the main thread later
            agent_ids = agent_manager.get_all_agents()
            if not agent_ids:
                agent_manager.create_agent("default")
                agent_ids = ["default"]

            for aid in agent_ids:
                agent_manager.start_agent(aid)

            self.finished.emit(agent_manager)

    splash = LoadingCard()
    splash.show()

    worker = InitWorker()
    thread = QThread()
    worker.moveToThread(thread)

    class MainThreadRunner(QObject):
        def finish_init(self, agent_manager):
            window = MainWindow(agent_manager=agent_manager)
            splash.close()
            window.show()
            app._main_window = window
            thread.quit()
            thread.wait()

    runner = MainThreadRunner()
    worker.finished.connect(runner.finish_init)
    thread.started.connect(worker.run)

    app._init_thread = thread
    app._init_worker = worker
    app._runner = runner

    QTimer.singleShot(0, thread.start)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
