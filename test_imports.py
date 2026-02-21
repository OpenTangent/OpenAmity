try:
    import sys
    sys.path.append("src")
    from gui.main_window import MainWindow
    from gui.visualizer import SoundWaveVisualizer
    from gui.mirror import MirrorPanel
    from core.gemini_worker import GeminiWorker
    print("Imports successful.")
except Exception as e:
    print(f"Import failed: {e}")
