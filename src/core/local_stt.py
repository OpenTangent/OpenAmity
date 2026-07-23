import logging
from faster_whisper import WhisperModel

# Suppress Hugging Face Hub unauthenticated warnings
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

class LocalSTT:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LocalSTT, cls).__new__(cls)
            cls._instance.model = None
            cls._instance.initial_prompt = "This recording explores the local colour and how we organise our town centres. It involves travelling and analysing behaviour."
        return cls._instance

    def _get_model(self):
        if self.model is None:
            logging.info("Initializing faster-whisper (tiny.en) for the first time...")
            self.model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
            logging.info("faster-whisper loaded.")
        return self.model

    def transcribe(self, audio_path: str) -> str:
        """Transcribes the audio at audio_path using faster-whisper."""
        try:
            model = self._get_model()
            logging.debug(f"Local STT transcribing: {audio_path}")
            
            segments, info = model.transcribe(
                audio_path,
                initial_prompt=self.initial_prompt
            )
            
            text = " ".join([segment.text for segment in segments]).strip()
            logging.info(f"Local Transcription result: '{text}'")
            
            if not text:
                return "[Transcription empty]"
                
            return text
        except Exception as e:
            logging.error(f"Local Transcription failed: {e}", exc_info=True)
            return f"[Transcription Failed: {e}]"
