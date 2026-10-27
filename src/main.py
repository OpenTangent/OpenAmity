import sys
import signal
import os
import logging
import threading
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

    if "qt.multimedia" in message or "FFmpeg" in message or "ffmpeg" in message:
        logging.debug(f"Qt: {message}")
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


CHROMA_ONNX_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
CHROMA_ONNX_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
CHROMA_ONNX_FILES = (
    "config.json",
    "model.onnx",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "vocab.txt",
)


def _is_chroma_onnx_ready(base_dir: str) -> bool:
    extracted_dir = os.path.join(base_dir, "onnx")
    return all(os.path.exists(os.path.join(extracted_dir, fname)) for fname in CHROMA_ONNX_FILES)


_chroma_onnx_lock = threading.Lock()


def _ensure_chroma_onnx_model(status_callback=None) -> bool:
    """Ensures the ChromaDB all-MiniLM-L6-v2 ONNX model is present and extracted in
    ~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx, downloading it on first run
    with progress feedback and SHA-256 verification."""
    paths.ensure_flatpak_persistence_links()
    target_dir = os.path.expanduser("~/.cache/chroma/onnx_models/all-MiniLM-L6-v2")
    if _is_chroma_onnx_ready(target_dir):
        return True

    # Also check XDG_CACHE_HOME in case ~/.cache is a separate directory on a custom setup
    xdg_target_dir = os.path.join(paths.get_app_cache_dir(), "chroma", "onnx_models", "all-MiniLM-L6-v2")
    if os.path.abspath(xdg_target_dir) != os.path.abspath(target_dir) and _is_chroma_onnx_ready(xdg_target_dir):
        try:
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)
            if not os.path.exists(target_dir):
                os.symlink(xdg_target_dir, target_dir)
                return True
        except Exception:
            pass

    if status_callback:
        try:
            status_callback("Setting up local memory (one-time setup)...")
        except Exception:
            pass

    # Avoid live network downloads inside unit tests unless explicitly invoked/mocked
    if "pytest" in sys.modules:
        return False

    import hashlib
    import tarfile
    import requests
    try:
        import fcntl
    except ImportError:
        fcntl = None

    os.makedirs(target_dir, exist_ok=True)
    lock_file_path = os.path.join(target_dir, ".chroma_onnx.lock")
    archive_path = os.path.join(target_dir, "onnx.tar.gz")
    bundled_archive = "/app/share/chroma_onnx/all-MiniLM-L6-v2/onnx.tar.gz"
    tmp_archive = os.path.join(target_dir, f"onnx.{os.getpid()}.{threading.get_ident()}.tar.gz.tmp")

    def _verify_sha256(filepath: str) -> bool:
        if not os.path.exists(filepath):
            return False
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest() == CHROMA_ONNX_SHA256

    with _chroma_onnx_lock:
        lock_fd = None
        try:
            if fcntl is not None:
                lock_fd = open(lock_file_path, "w")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

            if _is_chroma_onnx_ready(target_dir):
                return True

            source_tar = None
            if _verify_sha256(archive_path):
                source_tar = archive_path
            elif _verify_sha256(bundled_archive):
                source_tar = bundled_archive
            else:
                logger.info("Downloading ChromaDB ONNX embedding model (all-MiniLM-L6-v2, ~79 MB)...")
                with requests.get(CHROMA_ONNX_URL, stream=True, timeout=(10, 60)) as resp:
                    resp.raise_for_status()
                    total_bytes = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    last_reported_pct = -1
                    sha = hashlib.sha256()
                    with open(tmp_archive, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            f.write(chunk)
                            sha.update(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0 and status_callback:
                                pct = min(99, int((downloaded * 100) / total_bytes))
                                if pct >= last_reported_pct + 5:
                                    last_reported_pct = pct
                                    try:
                                        status_callback(f"Setting up local memory (one-time setup)... {pct}%")
                                    except Exception:
                                        pass

                if sha.hexdigest() != CHROMA_ONNX_SHA256:
                    logger.error(
                        f"Chroma ONNX model SHA-256 mismatch: {sha.hexdigest()} != {CHROMA_ONNX_SHA256}"
                    )
                    if os.path.exists(tmp_archive):
                        os.remove(tmp_archive)
                    return False
                source_tar = tmp_archive

            if status_callback:
                try:
                    status_callback("Extracting local memory model...")
                except Exception:
                    pass

            with tarfile.open(source_tar, "r:gz") as tar:
                tar.extractall(path=target_dir)

            if os.path.exists(tmp_archive):
                try:
                    os.remove(tmp_archive)
                except Exception:
                    pass

            logger.info("ChromaDB ONNX embedding model ready.")
            return _is_chroma_onnx_ready(target_dir)
        except Exception as e:
            logger.warning(f"Could not pre-setup ChromaDB ONNX model: {e}")
            if os.path.exists(tmp_archive):
                try:
                    os.remove(tmp_archive)
                except Exception:
                    pass
            return False
        finally:
            if lock_fd is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass


def main():
    retention_days = 7
    debug_logging = False

    # Ensure persistent XDG symlinks exist in Flatpak before logging or caches initialize
    paths.ensure_flatpak_persistence_links()

    # Setup logging early to capture all logs
    setup_logging(retention_days=retention_days, debug_logging=debug_logging)
    logger.info(f"Starting Open Amity Version: {amity_version}")

    # Silence noisy multimedia and C-level FFmpeg decode logs
    try:
        from core.audio_output import silence_ffmpeg_logs
        silence_ffmpeg_logs()
    except Exception:
        pass

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
        status_changed = Signal(str)

        def run(self):
            agent_manager = None
            try:
                # Ensure out-of-the-box OpenRouter model and image model caches exist in app data dir
                try:
                    paths.ensure_openrouter_models_cache()
                    paths.ensure_openrouter_image_models_cache()
                except Exception as e:
                    logging.getLogger("main").warning(f"Could not seed OpenRouter caches: {e}")

                # Check if Chroma ONNX embedding model exists; if not, download and extract with status updates
                _ensure_chroma_onnx_model(status_callback=self.status_changed.emit)

                self.status_changed.emit("Starting agents...")
                from core.agent_manager import AgentManager
                agent_manager = AgentManager()

                # Pre-load orchestrators for existing agents to avoid blocking the main thread later
                agent_ids = agent_manager.get_all_agents()
                if not agent_ids:
                    agent_manager.create_agent("default")
                    agent_ids = ["default"]

                for aid in agent_ids:
                    try:
                        agent_manager.start_agent(aid)
                    except Exception as e:
                        logger.error(f"Failed to start agent '{aid}' during initialization: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error during application initialization: {e}", exc_info=True)
            finally:
                if agent_manager is None:
                    try:
                        from core.agent_manager import AgentManager
                        agent_manager = AgentManager()
                    except Exception as e:
                        logger.error(f"Fallback AgentManager creation failed: {e}", exc_info=True)
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
    worker.status_changed.connect(splash.set_status)
    worker.finished.connect(runner.finish_init)
    thread.started.connect(worker.run)

    app._init_thread = thread
    app._init_worker = worker
    app._runner = runner

    QTimer.singleShot(0, thread.start)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
