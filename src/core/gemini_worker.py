
from PySide6.QtCore import QProcess, Signal, QObject, QByteArray

class GeminiWorker(QObject):
    response_received = Signal(str)  # Emitted when data is read from stdout
    error_occurred = Signal(str)     # Emitted on stderr or process error

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProgram("/bin/bash") # Placeholder for now, will replace with "gemini" later
        self.process.setArguments(["-c", "while read line; do echo 'Echo: '$line; done"]) # Simple echo loop
        
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.handle_finished)

    def is_running(self):
        return self.process.state() == QProcess.Running

    def start_session(self):
        if self.process.state() == QProcess.NotRunning:
            self.process.start()

    def stop_session(self):
        if self.process.state() == QProcess.Running:
            self.process.terminate()
            self.process.waitForFinished(2000)
            if self.process.state() == QProcess.Running:
                self.process.kill()

    def send_prompt(self, prompt: str):
        if self.process.state() == QProcess.Running:
            self.process.write(prompt.encode('utf-8') + b'\n')
        else:
            self.error_occurred.emit("Gemini process is not running.")

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode('utf-8')
        self.response_received.emit(data)

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode('utf-8')
        self.error_occurred.emit(data)

    def handle_finished(self):
        self.error_occurred.emit("Gemini process finished unexpectedly.")
