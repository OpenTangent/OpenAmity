
import asyncio
import edge_tts
import tempfile
import os
import subprocess
from PySide6.QtCore import QThread, Signal

class TTSWorker(QThread):
    started_playback = Signal()
    
    def __init__(self, text, voice="en-ZA-LeahNeural", parent=None):
        super().__init__(parent)
        self.text = text
        self.voice = voice
        self.output_file = tempfile.mktemp(suffix=".mp3")
        self.process = None
        self._is_stopped = False

    def stop(self):
        self._is_stopped = True
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def run(self):
        try:
            if not self._is_stopped:
                asyncio.run(self._synthesize())
            if not self._is_stopped:
                self._play()
        except Exception as e:
            print(f"TTS Error: {e}")
        finally:
            if os.path.exists(self.output_file):
                try:
                    os.remove(self.output_file)
                except Exception as e:
                    print(f"Error removing temp file: {e}")

    async def _synthesize(self):
        communicate = edge_tts.Communicate(self.text, self.voice)
        await communicate.save(self.output_file)

    def _play(self):
        # Use paplay or aplay to play the audio
        self.started_playback.emit()
        if not self._is_stopped:
            self.process = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", self.output_file], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            self.process.wait()
