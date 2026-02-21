
from PySide6.QtCore import QProcess, Signal, QObject, QByteArray
import shutil

class GeminiWorker(QObject):
    response_received = Signal(str)  # Emitted when data is read from stdout
    error_occurred = Signal(str)     # Emitted on stderr or process error

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.gemini_path = shutil.which("gemini") or "gemini"
        self.prompt_queue = []
        self.is_processing = False
        
        # Connect signals
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.handle_finished)

    def is_running(self):
        # We consider it "running" if it's processing or has items in queue, 
        # or just generally available.
        # For the toggle button, we can just return True if we are ready to accept.
        return True

    def start_session(self):
        # No persistent session to start, but we can reset queue
        self.prompt_queue = []
        self.is_processing = False

    def stop_session(self):
        self.prompt_queue = []
        if self.process.state() == QProcess.Running:
            self.process.kill()

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False):
        self.prompt_queue.append((prompt, image_path, yolo))
        self.process_next()

    def process_next(self):
        if self.process.state() == QProcess.Running:
            return

        if not self.prompt_queue:
            return

        prompt, image_path, yolo = self.prompt_queue.pop(0)
        self.is_processing = True
        
        args = ["-p", prompt, "--resume", "latest"]
        
        if yolo:
            args.append("--approval-mode=yolo")
            
        if image_path:
            args.append(image_path)
            
        self.process.start(self.gemini_path, args)

    def handle_stdout(self):
        # We read later
        pass

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode('utf-8')
        # Filter out noisy logs
        if "Loaded cached credentials" in data or "Session cleanup" in data:
            return
        if data.strip():
            self.error_occurred.emit(data)

    def handle_finished(self):
        self.is_processing = False
        # Read all stdout
        data = self.process.readAllStandardOutput().data().decode('utf-8')
        
        # Clean up output
        lines = data.split('\n')
        clean_lines = [l for l in lines if "Loaded cached credentials" not in l and "Session cleanup" not in l]
        clean_text = "\n".join(clean_lines).strip()

        if clean_text:
            self.response_received.emit(clean_text)
        
        # Process next in queue
        self.process_next()
