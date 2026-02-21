
import sys
import signal
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
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
