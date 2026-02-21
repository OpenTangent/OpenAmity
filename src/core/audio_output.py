
import asyncio
import edge_tts
import tempfile
import os
import subprocess
from PySide6.QtCore import QThread, Signal

class TTSWorker(QThread):
    started_playback = Signal()
    finished = Signal()
    
    def __init__(self, text, voice="en-ZA-LeahNeural"):
        super().__init__()
        self.text = text
        self.voice = voice
        self.output_file = tempfile.mktemp(suffix=".mp3")

    def run(self):
        try:
            asyncio.run(self._synthesize())
            self._play()
        except Exception as e:
            print(f"TTS Error: {e}")
        finally:
            if os.path.exists(self.output_file):
                os.remove(self.output_file)
            self.finished.emit()

    async def _synthesize(self):
        communicate = edge_tts.Communicate(self.text, self.voice)
        await communicate.save(self.output_file)

    def _play(self):
        # Use paplay or aplay to play the audio
        self.started_playback.emit()
        subprocess.run(["ffplay", "-nodisp", "-autoexit", self.output_file], check=True, stderr=subprocess.DEVNULL)
