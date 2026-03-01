
import sys
import signal
import os
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow

def load_env():
    """Simple .env loader to set environment variables."""
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if "=" in line:
                            key, value = line.split("=", 1)
                            # Remove optional quotes
                            value = value.strip("'\"")
                            os.environ[key.strip()] = value
            print(f"Loaded environment variables from {env_path}")
        except Exception as e:
            print(f"Failed to load .env: {e}")

def main():
    # Load environment variables (like HF_TOKEN)
    load_env()
    
    # Handle Ctrl+C (SIGINT) gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName("Amity 4")

    # Set dark theme palette (optional, but good for base look)
    # app.setStyle("Fusion") 

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
