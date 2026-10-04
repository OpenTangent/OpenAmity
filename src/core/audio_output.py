import subprocess
import logging
import math
import queue
import threading
import time
import re
import os
import requests
from pathlib import Path
from .events import Signal
from .settings_manager import SettingsManager
from google import genai
from google.genai import types

TTS_PLAYBACK_LOCK = threading.Lock()

GEMINI_VOICES = {
    # Female Voices
    "Sulafat": {"gender": "Female", "description": "Warm", "tags": ["warm", "empathetic", "caring", "gentle"]},
    "Achernar": {"gender": "Female", "description": "Soft", "tags": ["soft", "calm", "serene", "quiet"]},
    "Vindemiatrix": {"gender": "Female", "description": "Gentle", "tags": ["gentle", "serene", "reassuring"]},
    "Kore": {"gender": "Female", "description": "Firm", "tags": ["firm", "analytical", "professional", "serious"]},
    "Erinome": {"gender": "Female", "description": "Clear", "tags": ["clear", "articulate", "precise"]},
    "Pulcherrima": {"gender": "Female", "description": "Forward", "tags": ["forward", "direct", "confident"]},
    "Leda": {"gender": "Female", "description": "Youthful", "tags": ["youthful", "bright", "cheerful"]},
    "Zephyr": {"gender": "Female", "description": "Bright", "tags": ["bright", "lively", "energetic"]},
    "Laomedeia": {"gender": "Female", "description": "Upbeat", "tags": ["upbeat", "cheerful", "playful"]},
    "Aoede": {"gender": "Female", "description": "Breezy", "tags": ["breezy", "casual", "relaxed"]},
    "Callirrhoe": {"gender": "Female", "description": "Easy-going", "tags": ["easy-going", "casual", "conversational"]},
    "Autonoe": {"gender": "Female", "description": "Bright", "tags": ["bright", "vibrant", "sassy"]},
    "Despina": {"gender": "Female", "description": "Smooth", "tags": ["smooth", "melodic", "calm"]},

    # Male Voices
    "Achird": {"gender": "Male", "description": "Friendly", "tags": ["friendly", "warm", "casual", "empathetic"]},
    "Puck": {"gender": "Male", "description": "Upbeat", "tags": ["upbeat", "energetic", "cheerful", "playful"]},
    "Sadachbia": {"gender": "Male", "description": "Lively", "tags": ["lively", "vibrant", "dynamic"]},
    "Charon": {"gender": "Male", "description": "Informative", "tags": ["informative", "analytical", "professional", "serious"]},
    "Sadaltager": {"gender": "Male", "description": "Knowledgeable", "tags": ["knowledgeable", "intellectual", "calm"]},
    "Rasalgethi": {"gender": "Male", "description": "Informative", "tags": ["informative", "clear", "direct"]},
    "Gacrux": {"gender": "Male", "description": "Mature", "tags": ["mature", "grounded", "deep", "authoritative"]},
    "Alnilam": {"gender": "Male", "description": "Firm", "tags": ["firm", "strong", "direct"]},
    "Orus": {"gender": "Male", "description": "Firm", "tags": ["firm", "commanding", "steady"]},
    "Algenib": {"gender": "Male", "description": "Gravelly", "tags": ["gravelly", "gritty", "deep", "husky"]},
    "Fenrir": {"gender": "Male", "description": "Excitable", "tags": ["excitable", "energetic", "passionate"]},
    "Enceladus": {"gender": "Male", "description": "Breathy", "tags": ["breathy", "soft", "intimate", "subdued"]},
    "Umbriel": {"gender": "Male", "description": "Easy-going", "tags": ["easy-going", "relaxed", "casual"]},
    "Zubenelgenubi": {"gender": "Male", "description": "Casual", "tags": ["casual", "conversational", "laid-back"]},
    "Schedar": {"gender": "Male", "description": "Even", "tags": ["even", "balanced", "neutral", "calm"]},

    # Neutral / Flexible Voices
    "Iapetus": {"gender": "Neutral", "description": "Clear", "tags": ["clear", "neutral", "balanced"]},
    "Algieba": {"gender": "Neutral", "description": "Smooth", "tags": ["smooth", "gentle", "neutral"]}
}


def resolve_base_voice(gender: str = "Female", age: str = "Young Adult", style: str = "Warm & Empathetic") -> str:
    """Automatically selects the best-matching prebuilt Gemini voice based on Gender, Age, and Style."""
    g = (gender or "Female").strip().lower()
    s = (style or "").strip().lower()
    a = (age or "").strip().lower()

    if "female" in g:
        if any(k in s for k in ["soft", "quiet"]):
            return "Achernar"
        if any(k in s for k in ["warm", "empath", "caring"]):
            return "Sulafat"
        if any(k in s for k in ["gentle", "serene", "reassur"]):
            return "Vindemiatrix"
        if any(k in s for k in ["calm", "analyt", "profess", "firm", "serio"]):
            return "Kore"
        if any(k in s for k in ["clear", "articulate", "precis"]):
            return "Erinome"
        if any(k in s for k in ["youth", "bright", "cheer"]) or "youth" in a:
            return "Leda"
        if any(k in s for k in ["upbeat", "playful"]):
            return "Laomedeia"
        if any(k in s for k in ["breezy", "relaxed"]):
            return "Aoede"
        if any(k in s for k in ["casual", "easy"]):
            return "Callirrhoe"
        if any(k in s for k in ["vibrant", "sassy", "energ"]):
            return "Autonoe"
        if any(k in s for k in ["smooth", "melodic"]):
            return "Despina"
        return "Sulafat"
    elif "male" in g:
        if any(k in s for k in ["warm", "friend", "caring", "empath"]):
            return "Achird"
        if any(k in s for k in ["upbeat", "cheer", "playful"]):
            return "Puck"
        if any(k in s for k in ["lively", "vibrant", "dynam"]):
            return "Sadachbia"
        if any(k in s for k in ["knowledg", "intellect"]):
            return "Sadaltager"
        if any(k in s for k in ["mature", "deep", "authorit"]) or "mature" in a or "senior" in a:
            return "Gacrux"
        if any(k in s for k in ["soft", "breathy", "subdued", "intimat"]):
            return "Enceladus"
        if any(k in s for k in ["excit", "passionat"]):
            return "Fenrir"
        if any(k in s for k in ["casual", "laid-back"]):
            return "Zubenelgenubi"
        if any(k in s for k in ["gravel", "gritty", "husky"]):
            return "Algenib"
        if any(k in s for k in ["even", "balance"]):
            return "Schedar"
        return "Charon"
    else:  # Neutral / Non-binary
        if any(k in s for k in ["smooth", "gentle", "warm"]):
            return "Algieba"
        return "Iapetus"


def voice_to_character_profile(voice_name: str) -> dict:
    """Derives intuitive Gender, Age, and Style character traits from a prebuilt Gemini voice name."""
    info = GEMINI_VOICES.get(voice_name, {})
    if not info:
        return {}
    gender_raw = info.get("gender", "Female")
    if gender_raw == "Female":
        gender = "Female"
    elif gender_raw == "Male":
        gender = "Male"
    else:
        gender = "Non-binary / Neutral"

    tags = [t.lower() for t in info.get("tags", [])]
    desc = info.get("description", "").lower()
    all_text = " ".join(tags + [desc])

    # Age mapping
    if any(k in all_text for k in ["youthful"]):
        age = "Youthful (18 - 25)"
    elif any(k in all_text for k in ["mature", "senior", "gravelly", "gritty"]):
        age = "Mature / Senior (50+)"
    elif any(k in all_text for k in ["firm", "commanding", "authoritative", "informative", "serious"]):
        age = "Adult (30s - 50s)"
    else:
        age = "Young Adult (20s - 30s)"

    # Style mapping
    if any(k in all_text for k in ["vibrant", "dynamic", "sassy", "forward", "confident"]):
        style = "Vibrant & Sassy"
    elif any(k in all_text for k in ["warm", "empathetic", "caring", "friendly"]):
        style = "Warm & Empathetic"
    elif any(k in all_text for k in ["gentle", "serene", "soft", "reassuring", "breathy"]):
        style = "Gentle & Serene"
    elif any(k in all_text for k in ["calm", "smooth", "intellectual", "knowledgeable", "husky"]):
        style = "Calm & Analytical"
    elif any(k in all_text for k in ["informative", "professional", "serious", "clear", "articulate", "precise", "firm", "strong", "commanding", "authoritative", "direct", "grounded"]):
        style = "Professional & Informative"
    elif any(k in all_text for k in ["cheerful", "upbeat", "lively", "energetic", "excitable", "bright"]):
        style = "Cheerful & Energetic"
    elif any(k in all_text for k in ["casual", "playful", "breezy", "relaxed", "easy-going", "conversational", "laid-back"]):
        style = "Casual & Playful"
    else:
        style = "Warm & Empathetic"

    return {"gender": gender, "age": age, "style": style}


def build_gemini_tts_prompt(settings, agent_id: str, text: str, somatic_text: str = "") -> str:
    """Formats the TTS prompt cleanly using natural positive direction without markdown scaffolding."""
    override_prompt = settings.get("core.tts.gemini.override-prompt", False)
    custom_prompt = settings.get("core.tts.gemini.custom-prompt", "").strip()

    if override_prompt and custom_prompt:
        if "{transcript}" in custom_prompt:
            return custom_prompt.replace("{transcript}", text)
        elif "{text}" in custom_prompt:
            return custom_prompt.replace("{text}", text)
        elif custom_prompt.endswith(":"):
            return f"{custom_prompt}\n\n{text}"
        else:
            return f"{custom_prompt}:\n\n{text}"

    agent_name = settings.get("core.agent.name", "Assistant")
    gender = settings.get("core.tts.gemini.gender", "Female")
    age = settings.get("core.tts.gemini.age", "Young Adult")
    custom_accent = settings.get("core.tts.gemini.custom-accent", "").strip()
    accent = custom_accent if custom_accent else settings.get("core.tts.gemini.accent", "South African")

    custom_style = settings.get("core.tts.gemini.custom-style", "").strip()
    style = custom_style if custom_style else settings.get("core.tts.gemini.style", "Warm & Empathetic")
    clean_style = style.replace("&", "and").strip()

    demeanor_part = f" with a {somatic_text.lower()} demeanor" if somatic_text else ""
    return (
        f"Read the following transcript aloud as {agent_name} in a natural {accent} accent, "
        f"using a {clean_style.lower()} tone{demeanor_part} with clear articulation and natural conversational pacing:\n\n"
        f"{text}"
    )



def build_gemini_tts_style(settings, agent_id: str = None, somatic_text: str = "") -> str:
    """Builds the directorial delivery style instruction for Gemini 3.8 speech_metadata."""
    override_prompt = settings.get("core.tts.gemini.override-prompt", False)
    custom_prompt = settings.get("core.tts.gemini.custom-prompt", "").strip()

    if override_prompt and custom_prompt:
        clean_prompt = custom_prompt
        for placeholder in ["{transcript}", "{text}"]:
            clean_prompt = clean_prompt.replace(placeholder, "")
        clean_prompt = clean_prompt.strip()
        if clean_prompt.lower().startswith("read the following transcript aloud as"):
            colon_idx = clean_prompt.rfind(":")
            if colon_idx != -1:
                clean_prompt = clean_prompt[:colon_idx].strip()
        elif clean_prompt.endswith(":"):
            clean_prompt = clean_prompt[:-1].strip()
        if clean_prompt:
            return clean_prompt

    agent_name = settings.get("core.agent.name", "Assistant")
    gender = settings.get("core.tts.gemini.gender", "Female")
    age = settings.get("core.tts.gemini.age", "Young Adult")
    custom_accent = settings.get("core.tts.gemini.custom-accent", "").strip()
    accent = custom_accent if custom_accent else settings.get("core.tts.gemini.accent", "South African")

    custom_style = settings.get("core.tts.gemini.custom-style", "").strip()
    style = custom_style if custom_style else settings.get("core.tts.gemini.style", "Warm & Empathetic")
    clean_style = style.replace("&", "and").strip()

    demeanor_part = f", with a {somatic_text.lower()} demeanor" if somatic_text else ""
    return (
        f"natural {accent} accent, {clean_style.lower()} tone{demeanor_part} with clear articulation and natural conversational pacing"
    )


def build_gemini_tts_payload(model: str, settings, agent_id: str, text: str, somatic_text: str = ""):
    """Builds model-specific request payload.
    Gemini 3.8 uses a verbatim transcript with speech_metadata delivery direction.
    Legacy preview models use an integrated directive prompt preamble."""
    alias_map = {
        "gemini-3.1-flash-tts": "gemini-3.1-flash-tts-preview",
        "gemini-3-flash-tts-preview": "gemini-3.1-flash-tts-preview",
        "gemini-3-flash-tts": "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-preview-tts": "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-lite-preview-tts": "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-lite-tts": "gemini-3.1-flash-tts-preview",
    }
    model = alias_map.get(model, model)
    if model and ("3.8" in model or model.startswith("gemini-3.8")):
        style = build_gemini_tts_style(settings, agent_id, somatic_text=somatic_text)
        if hasattr(types, "SpeechMetadata"):
            speech_metadata = types.SpeechMetadata(style=style)
            part = types.Part(text=text, speech_metadata=speech_metadata)
            return [types.Content(role="user", parts=[part])]
        else:
            return f"Read the following transcript aloud in style: {style}\n\n{text}"
    return build_gemini_tts_prompt(settings, agent_id, text, somatic_text=somatic_text)


def silence_ffmpeg_logs():
    """Silences Qt Multimedia and FFmpeg logging clutter on stderr."""
    import os
    os.environ.setdefault("AV_LOG_FORCE_NOCOLOR", "1")

    try:
        from PySide6.QtCore import QLoggingCategory
        QLoggingCategory.setFilterRules("qt.multimedia*=false\nqt.multimedia.ffmpeg*=false")
    except Exception:
        pass

    try:
        import ctypes
        import ctypes.util

        crypto_name = ctypes.util.find_library("crypto") or "libcrypto.so.3"
        try:
            ctypes.CDLL(crypto_name, ctypes.RTLD_GLOBAL)
        except Exception:
            pass

        # Try PySide6 bundled libavutil
        try:
            import PySide6
            pyside_dir = os.path.dirname(PySide6.__file__)
            bundled_libavutil = os.path.join(pyside_dir, "Qt", "lib", "libavutil.so")
            if os.path.exists(bundled_libavutil):
                lib = ctypes.CDLL(bundled_libavutil, ctypes.RTLD_GLOBAL)
                if hasattr(lib, "av_log_set_level"):
                    lib.av_log_set_level(-8)
        except Exception:
            pass

        # Try system libavutil
        try:
            sys_avutil = ctypes.util.find_library("avutil")
            if sys_avutil:
                lib = ctypes.CDLL(sys_avutil, ctypes.RTLD_GLOBAL)
                if hasattr(lib, "av_log_set_level"):
                    lib.av_log_set_level(-8)
        except Exception:
            pass
    except Exception:
        pass


def synthesize_voice_sample(api_key: str, voice_name: str, style: str = "", text: str = "") -> bytes:
    """Synthesizes a short audition audio sample using gemini-3.8-flash-tts."""
    if not text:
        text = f"Hello, I am {voice_name}, one of the Gemini voices."
    client = genai.Client(api_key=api_key)
    if hasattr(types, "SpeechMetadata") and style:
        part = types.Part(text=text, speech_metadata=types.SpeechMetadata(style=style))
        contents = [types.Content(role="user", parts=[part])]
    elif style:
        contents = f"Read the following transcript aloud in style: {style}\n\n{text}"
    else:
        contents = text

    response = client.models.generate_content(
        model="gemini-3.8-flash-tts",
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=(
                    types.VoiceConfig(voice=voice_name)
                    if voice_name and voice_name.startswith("voice_")
                    else types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            )
        )
    )
    if response.candidates:
        for cand in response.candidates:
            if cand.content and cand.content.parts:
                for p in cand.content.parts:
                    if hasattr(p, "inline_data") and p.inline_data and p.inline_data.data:
                        raw_data = p.inline_data.data
                        if raw_data.startswith(b"RIFF"):
                            return raw_data
                        import io, wave
                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(24000)
                            wf.writeframes(raw_data)
                        return buf.getvalue()
    return b""


def load_piper_voice(settings=None):
    """Loads and returns the configured PiperVoice instance, downloading files if necessary."""
    try:
        piper_dir = Path.home() / ".local" / "share" / "Open Amity" / "piper_voices"
        piper_dir.mkdir(parents=True, exist_ok=True)

        voice_name = "en_GB-cori-high"
        if settings:
            voice_name = settings.get("core.tts.piper.model-name-piper", "en_GB-cori-high")
        if not voice_name or "-" not in voice_name:
            if voice_name == "cori":
                voice_name = "en_GB-cori-high"
            else:
                logging.getLogger("core.TTS.Piper").warning(
                    f"Invalid Piper voice name '{voice_name}'. Using fallback.")
                voice_name = "en_GB-cori-high"

        parts = voice_name.split("-")
        if len(parts) < 3:
            logging.getLogger("core.TTS.Piper").warning(
                f"Invalid Piper voice name format '{voice_name}'. Format should be locale-voice-quality. Using fallback.")
            voice_name = "en_GB-cori-high"
            parts = voice_name.split("-")

        locale = parts[0]
        language = locale.split("_")[0]
        voice = parts[1]
        quality = parts[2]

        model_path = piper_dir / f"{voice_name}.onnx"
        config_path = piper_dir / f"{voice_name}.onnx.json"

        if not model_path.exists() or not config_path.exists():
            logging.getLogger("core.TTS.Piper").info(
                f"Downloading Piper TTS model '{voice_name}'...")
            model_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{language}/{locale}/{voice}/{quality}/{voice_name}.onnx"
            config_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{language}/{locale}/{voice}/{quality}/{voice_name}.onnx.json"
            try:
                with requests.get(model_url, stream=True, timeout=10) as r_model:
                    r_model.raise_for_status()
                    with open(model_path, 'wb') as f:
                        for chunk in r_model.iter_content(chunk_size=8192):
                            f.write(chunk)

                with requests.get(config_url, stream=True, timeout=10) as r_config:
                    r_config.raise_for_status()
                    with open(config_path, 'wb') as f:
                        for chunk in r_config.iter_content(chunk_size=8192):
                            f.write(chunk)
                logging.getLogger("core.TTS.Piper").info(
                    "Piper TTS model downloaded successfully.")
            except requests.RequestException as e:
                logging.getLogger("core.TTS.Piper").warning(
                    f"Offline or failed to download Piper model: {e}")
                if model_path.exists():
                    model_path.unlink()
                if config_path.exists():
                    config_path.unlink()
                return None

        from piper.voice import PiperVoice
        return PiperVoice.load(str(model_path))
    except Exception as e:
        logging.getLogger("core.TTS.Piper").error(f"Error loading Piper voice: {e}")
        return None


def normalize_gemini_voice_models(voice_models) -> list:
    """Sanitizes voice model list: maps legacy/invalid model identifiers to valid ones,
    deduplicates, strips any gemini-2.5 models, ensures 'gemini-3.8-flash-tts' is primary,
    and ensures fallback model 'gemini-3.1-flash-tts-preview' is present."""
    alias_map = {
        "gemini-3.1-flash-tts": "gemini-3.1-flash-tts-preview",
        "gemini-3-flash-tts-preview": "gemini-3.1-flash-tts-preview",
        "gemini-3-flash-tts": "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-preview-tts": "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-lite-preview-tts": "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-lite-tts": "gemini-3.1-flash-tts-preview",
    }
    normalized = []
    if isinstance(voice_models, list):
        for m in voice_models:
            if isinstance(m, str) and m.strip():
                target = alias_map.get(m.strip(), m.strip())
                if target not in normalized and "2.5" not in target:
                    normalized.append(target)
    elif isinstance(voice_models, str) and voice_models.strip():
        target = alias_map.get(voice_models.strip(), voice_models.strip())
        if "2.5" not in target:
            normalized.append(target)

    # Strip any 2.5 models completely
    normalized = [m for m in normalized if "2.5" not in m]

    if not normalized:
        return [
            "gemini-3.8-flash-tts",
            "gemini-3.8-flash-lite-tts",
            "gemini-3.1-flash-tts-preview"
        ]

    if "gemini-3.8-flash-tts" not in normalized:
        normalized.insert(0, "gemini-3.8-flash-tts")
    elif normalized[0] != "gemini-3.8-flash-tts":
        normalized.remove("gemini-3.8-flash-tts")
        normalized.insert(0, "gemini-3.8-flash-tts")

    if "gemini-3.1-flash-tts-preview" not in normalized:
        normalized.append("gemini-3.1-flash-tts-preview")
    return normalized


class TTSWorker(threading.Thread):
    def __init__(self, text, voice=None, daemon=True, agent_id=None):
        super().__init__(daemon=daemon)
        self.started_playback = Signal()
        self.amplitude_emitted = Signal()
        self.error_occurred = Signal()
        self.agent_id = agent_id

        self.settings = SettingsManager(agent_id=self.agent_id)
        self.text = text

        override_prompt = self.settings.get("core.tts.gemini.override-prompt", False)
        saved_voice = self.settings.get("core.tts.gemini.model-name")
        voice_selection_mode = self.settings.get("core.tts.gemini.voice-selection-mode", "auto")
        custom_voice_id = self.settings.get("core.tts.gemini.custom-voice-id", "").strip()

        if voice:
            self.voice = voice
        elif voice_selection_mode == "custom_voice_id" and custom_voice_id:
            self.voice = custom_voice_id
        elif voice_selection_mode == "prebuilt" and saved_voice:
            self.voice = saved_voice
        elif saved_voice and (override_prompt or saved_voice in GEMINI_VOICES or saved_voice.startswith("voice_")):
            self.voice = saved_voice
        else:
            gender = self.settings.get("core.tts.gemini.gender", "Female")
            age = self.settings.get("core.tts.gemini.age", "Young Adult")
            custom_style = self.settings.get("core.tts.gemini.custom-style", "").strip()
            style = custom_style if custom_style else self.settings.get("core.tts.gemini.style", "Warm & Empathetic")
            self.voice = resolve_base_voice(gender, age, style)

        self.voice_prompt = self.settings.get("core.tts.gemini.prompt", {})

        raw_voice_models = self.settings.get(
            "core.gemini.voice-models", [
                "gemini-3.8-flash-tts",
                "gemini-3.8-flash-lite-tts",
                "gemini-3.1-flash-tts-preview"
            ]
        )
        self.voice_models = normalize_gemini_voice_models(raw_voice_models)
        self.model = self.voice_models[0]

        self.process = None
        self._is_stopped = False
        self.on_finished = Signal()

    def stop(self):
        self._is_stopped = True
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        try:
            if not self._is_stopped:
                with TTS_PLAYBACK_LOCK:
                    if not self._is_stopped:
                        self._stream_and_play()
        except Exception as e:
            logging.getLogger("core.TTS").error(
                f"TTS Error: {e}", exc_info=True)
        finally:
            self.on_finished.emit()

    def _stream_and_play(self):
        if self.settings.get("core.low-token-mode", False):
            self._stream_and_play_piper()
            return

        provider = self.settings.get("core.tts.provider")
        if provider is None:
            # Backwards compatibility fallback
            if not self.settings.get("core.tts.piper.prefer-piper", True):
                provider = "gemini"
            else:
                provider = "piper"

        if provider == "gemini":
            api_key = self.settings.get_env("GEMINI_TTS_API_KEY") or self.settings.get_env("GEMINI_API_KEY")
            if api_key:
                self._stream_and_play_gemini(api_key=api_key)
            else:
                logging.getLogger("core.TTS").warning(
                    "Gemini TTS selected but no Gemini API key found in GEMINI_TTS_API_KEY or GEMINI_API_KEY. "
                    "Falling back to local Piper TTS.")
                self._stream_and_play_piper()
        else:
            self._stream_and_play_piper()

    def _stream_and_play_gemini(self, api_key=None):
        self.started_playback.emit()
        if self._is_stopped:
            return

        # Inject Somatic State
        somatic_state_text = ""
        try:
            from config import paths
            import os
            import json
            state_path = os.path.join(paths.get_base_dir_for(
                self.agent_id), "somatic_state.json")
            if os.path.exists(state_path):
                with open(state_path, "r") as f:
                    somatic = json.load(f)
                    percent = somatic.get("percent")
                    if percent is None:
                        current_weight = somatic.get("session_fatigue_tokens", somatic.get("current_task_weight", 0))
                        max_weight = somatic.get("token_limit", somatic.get("max_weight", 1000))
                        percent = (current_weight / max_weight) * 100 if max_weight else 0

                    if percent >= 50:
                        somatic_state_text = "Slightly tired, weary, and subdued due to cognitive fatigue."
                    elif percent >= 25:
                        somatic_state_text = "Slightly subdued and contemplative due to moderate fatigue."
        except Exception:
            pass

        if not api_key:
            api_key = self.settings.get_env("GEMINI_TTS_API_KEY") or self.settings.get_env("GEMINI_API_KEY")
        if not api_key:
            logging.getLogger("core.TTS.Gemini").error(
                "Cannot stream Gemini TTS: GEMINI_TTS_API_KEY or GEMINI_API_KEY is not configured.")
            self.error_occurred.emit(
                "[System: Gemini voice synthesis unavailable - API key is not configured.]")
            return

        client = genai.Client(api_key=api_key)

        raw_voice_models = self.settings.get(
            "core.gemini.voice-models", [
                "gemini-3.8-flash-tts",
                "gemini-3.8-flash-lite-tts",
                "gemini-3.1-flash-tts-preview"
            ]
        )
        voice_models = normalize_gemini_voice_models(raw_voice_models)
        self.voice_models = voice_models

        max_attempts = max(2, len(voice_models))
        for attempt in range(1, max_attempts + 1):
            if self._is_stopped:
                return

            current_model = voice_models[min(attempt - 1, len(voice_models) - 1)]
            self.model = current_model
            payload = build_gemini_tts_payload(
                current_model, self.settings, self.agent_id, self.text, somatic_text=somatic_state_text
            )
            try:
                self._stream_gemini_attempt(client, payload)
                return
            except Exception as e:
                if self._is_stopped:
                    return

                if attempt < max_attempts:
                    next_model = voice_models[min(attempt, len(voice_models) - 1)]
                    logging.getLogger("core.TTS.Gemini").warning(
                        f"Gemini TTS streaming error on attempt {attempt} ({current_model}): {e}. Retrying with {next_model}...")
                    time.sleep(0.5)
                else:
                    logging.getLogger("core.TTS.Gemini").error(
                        f"Error during TTS streaming after {max_attempts} consecutive attempts: {e}")
                    self.error_occurred.emit(
                        "[System: Gemini voice synthesis connection interrupted. Full text output is available above.]")

    def _stream_gemini_attempt(self, client, contents_payload, **kwargs):
        target_model = kwargs.get("model") or getattr(self, "model", "gemini-3.8-flash-tts")
        alias_map = {
            "gemini-3.1-flash-tts": "gemini-3.1-flash-tts-preview",
            "gemini-3-flash-tts-preview": "gemini-3.1-flash-tts-preview",
            "gemini-3-flash-tts": "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts": "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-lite-preview-tts": "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-lite-tts": "gemini-3.1-flash-tts-preview",
        }
        target_model = alias_map.get(target_model, target_model)
        response_stream = client.models.generate_content_stream(
            model=target_model,
            contents=contents_payload,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=(
                        types.VoiceConfig(voice=self.voice)
                        if self.voice and self.voice.startswith("voice_")
                        else types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.voice
                            )
                        )
                    )
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            )
        )

        self.process = subprocess.Popen(
            ["ffplay", "-f", "s16le", "-ar", "24000",
                "-nodisp", "-autoexit", "-i", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        audio_queue = queue.Queue()
        abort_event = threading.Event()

        def writer_thread():
            try:
                while not self._is_stopped:
                    try:
                        data = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if data is None:
                        break

                    if self.process and self.process.stdin:
                        self.process.stdin.write(data)
                        self.process.stdin.flush()

                        samples = len(data) // 2
                        if samples > 0:
                            sum_sq = 0
                            stride = max(1, samples // 100)
                            if stride > 0:
                                for i in range(0, samples * 2, stride * 2):
                                    val = int.from_bytes(
                                        data[i:i+2], byteorder='little', signed=True)
                                    sum_sq += val * val
                                rms = math.sqrt(
                                    sum_sq / (samples / stride))
                                amp = min(1.0, rms / 32768.0)
                                self.amplitude_emitted.emit(amp)
            except BrokenPipeError:
                if not self._is_stopped and not abort_event.is_set():
                    logging.getLogger("core.TTS.Gemini").warning("ffplay process closed unexpectedly (Broken pipe).")
            except Exception as e:
                logging.getLogger("core.TTS.Gemini").error(
                    f"TTS writer thread error: {e}")
            finally:
                if self.process and self.process.stdin and not self._is_stopped:
                    try:
                        self.process.stdin.write(b'\x00' * 48000)
                        self.process.stdin.flush()
                    except Exception:
                        pass
                if self.process and self.process.stdin:
                    try:
                        self.process.stdin.close()
                    except Exception:
                        pass

        writer = threading.Thread(target=writer_thread, daemon=True)
        writer.start()

        stream_err = None
        leftover_pcm = b""
        try:
            for chunk in response_stream:
                if self._is_stopped:
                    break

                try:
                    if not chunk.candidates:
                        continue

                    candidate = chunk.candidates[0]
                    finish_reason = getattr(candidate, "finish_reason", None)
                    reason_name = None
                    if finish_reason is not None:
                        if hasattr(finish_reason, "name") and isinstance(finish_reason.name, str):
                            reason_name = finish_reason.name
                        elif isinstance(finish_reason, str):
                            reason_name = finish_reason

                    if reason_name and reason_name not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                        abort_event.set()
                        logging.getLogger("core.TTS.Gemini").warning(
                            f"Gemini TTS stream terminated prematurely with finish_reason: {reason_name}")
                        raise RuntimeError(f"Gemini TTS stream aborted early with finish_reason: {reason_name}")

                    if not candidate.content or not candidate.content.parts:
                        continue

                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.data:
                            data = leftover_pcm + part.inline_data.data
                            if len(data) % 2 != 0:
                                leftover_pcm = data[-1:]
                                data = data[:-1]
                            else:
                                leftover_pcm = b""
                            if data:
                                audio_queue.put(data)
                except RuntimeError:
                    raise
                except (IndexError, AttributeError, TypeError) as inner_err:
                    logging.getLogger("core.TTS.Gemini").warning(
                        f"Skipped an unexpected TTS chunk. Reason: {inner_err}")
        except Exception as err:
            abort_event.set()
            stream_err = err
        finally:
            if leftover_pcm:
                audio_queue.put(leftover_pcm + b"\x00")
                leftover_pcm = b""

            if stream_err is not None or self._is_stopped:
                if self.process:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                audio_queue.put(None)
                writer.join(timeout=2)
                if self.process:
                    try:
                        self.process.wait(timeout=2)
                    except Exception:
                        pass
                    self.process = None
            else:
                audio_queue.put(None)
                writer.join()
                if self.process:
                    try:
                        self.process.wait(timeout=15)
                    except Exception:
                        try:
                            self.process.kill()
                            self.process.wait(timeout=2)
                        except Exception:
                            pass
                    self.process = None

        if stream_err is not None:
            raise stream_err

    def _stream_and_play_piper(self):
        try:
            voice = load_piper_voice(self.settings)
            if not voice:
                return

            clean_text = re.sub(r'\[.*?\]', '', self.text)
            clean_text = re.sub(r'<.*?>', '', clean_text)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()

            if not clean_text:
                return

            self.started_playback.emit()
            if self._is_stopped:
                return

            self.process = subprocess.Popen(
                ["ffplay", "-f", "s16le", "-ar",
                    str(voice.config.sample_rate), "-nodisp", "-autoexit", "-i", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            audio_queue = queue.Queue()

            def writer_thread():
                try:
                    while not self._is_stopped:
                        try:
                            data = audio_queue.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        if data is None:
                            break

                        if self.process and self.process.stdin:
                            self.process.stdin.write(data)
                            self.process.stdin.flush()

                            samples = len(data) // 2
                            if samples > 0:
                                sum_sq = 0
                                stride = max(1, samples // 100)
                                if stride > 0:
                                    for i in range(0, samples * 2, stride * 2):
                                        val = int.from_bytes(
                                            data[i:i+2], byteorder='little', signed=True)
                                        sum_sq += val * val
                                    rms = math.sqrt(
                                        sum_sq / (samples / stride))
                                    amp = min(1.0, rms / 32768.0)
                                    self.amplitude_emitted.emit(amp)
                except BrokenPipeError:
                    if not self._is_stopped:
                        logging.getLogger("core.TTS.Piper").warning("ffplay process closed unexpectedly (Broken pipe).")
                except Exception as e:
                    logging.getLogger("core.TTS.Piper").error(
                        f"Piper writer thread error: {e}")
                finally:
                    if self.process and self.process.stdin and not self._is_stopped:
                        try:
                            self.process.stdin.write(
                                b'\x00' * (voice.config.sample_rate * 2))
                            self.process.stdin.flush()
                        except Exception:
                            pass
                    if self.process and self.process.stdin:
                        try:
                            self.process.stdin.close()
                        except Exception:
                            pass

            writer = threading.Thread(target=writer_thread, daemon=True)
            writer.start()

            for chunk in voice.synthesize(clean_text):
                if self._is_stopped:
                    break
                if chunk and chunk.audio_int16_bytes:
                    audio_queue.put(chunk.audio_int16_bytes)

        except Exception as e:
            logging.getLogger("core.TTS.Piper").error(
                f"Error during Piper TTS streaming: {e}")
        finally:
            if 'audio_queue' in locals():
                audio_queue.put(None)
            if 'writer' in locals():
                if self._is_stopped:
                    if self.process:
                        try:
                            self.process.kill()
                        except Exception:
                            pass
                    writer.join(timeout=2)
                else:
                    writer.join()
            if self.process:
                try:
                    self.process.wait(timeout=15 if not self._is_stopped else 2)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None


def synthesize_speech_to_file(text: str, output_path: str, agent_id: str = None, voice: str = None, settings=None) -> str:
    """
    Synthesizes text into an Opus audio file (.opus) at output_path.
    Uses Gemini TTS if configured and reachable (with fallback cascade),
    otherwise gracefully falls back to local Piper TTS.
    Returns the absolute path to the generated .opus file.
    """
    if not text or not text.strip():
        raise ValueError("Transcript text is required for speech synthesis.")

    if settings is None:
        try:
            settings = SettingsManager(agent_id=agent_id)
        except Exception:
            settings = None

    abs_output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)

    # Check low token mode
    is_low_token = settings.get("core.low-token-mode", False) if settings else False

    provider = None
    if settings:
        provider = settings.get("core.tts.provider")
        if provider is None:
            if not settings.get("core.tts.piper.prefer-piper", True):
                provider = "gemini"
            else:
                provider = "piper"

    if is_low_token:
        provider = "piper"

    # Attempt Gemini TTS if selected
    if provider == "gemini" and settings:
        api_key = settings.get_env("GEMINI_TTS_API_KEY") or settings.get_env("GEMINI_API_KEY")
        if api_key:
            try:
                return _synthesize_gemini_to_opus(text, abs_output_path, settings, agent_id=agent_id, voice=voice, api_key=api_key)
            except Exception as e:
                logging.getLogger("core.TTS.Gemini").warning(
                    f"Gemini TTS synthesis to file failed: {e}. Falling back to local Piper TTS.")
        else:
            logging.getLogger("core.TTS").warning(
                "Gemini TTS selected but no Gemini API key configured. Falling back to local Piper TTS.")

    # Fallback / Default: Piper TTS
    return _synthesize_piper_to_opus(text, abs_output_path, settings=settings)


def _synthesize_gemini_to_opus(text: str, output_path: str, settings, agent_id: str = None, voice: str = None, api_key: str = None) -> str:
    somatic_state_text = ""
    try:
        from config import paths
        import json
        if agent_id:
            state_path = os.path.join(paths.get_base_dir_for(agent_id), "somatic_state.json")
            if os.path.exists(state_path):
                with open(state_path, "r") as f:
                    somatic = json.load(f)
                    percent = somatic.get("percent")
                    if percent is None:
                        current_weight = somatic.get("session_fatigue_tokens", somatic.get("current_task_weight", 0))
                        max_weight = somatic.get("token_limit", somatic.get("max_weight", 1000))
                        percent = (current_weight / max_weight) * 100 if max_weight else 0
                    if percent >= 50:
                        somatic_state_text = "Slightly tired, weary, and subdued due to cognitive fatigue."
                    elif percent >= 25:
                        somatic_state_text = "Slightly subdued and contemplative due to moderate fatigue."
    except Exception:
        pass

    override_prompt = settings.get("core.tts.gemini.override-prompt", False) if settings else False
    saved_voice = settings.get("core.tts.gemini.model-name") if settings else None
    voice_selection_mode = settings.get("core.tts.gemini.voice-selection-mode", "auto") if settings else "auto"
    custom_voice_id = settings.get("core.tts.gemini.custom-voice-id", "").strip() if settings else ""

    if voice:
        resolved_voice = voice
    elif voice_selection_mode == "custom_voice_id" and custom_voice_id:
        resolved_voice = custom_voice_id
    elif voice_selection_mode == "prebuilt" and saved_voice:
        resolved_voice = saved_voice
    elif saved_voice and (override_prompt or saved_voice in GEMINI_VOICES or saved_voice.startswith("voice_")):
        resolved_voice = saved_voice
    else:
        gender = settings.get("core.tts.gemini.gender", "Female") if settings else "Female"
        age = settings.get("core.tts.gemini.age", "Young Adult") if settings else "Young Adult"
        custom_style = settings.get("core.tts.gemini.custom-style", "").strip() if settings else ""
        style = custom_style if custom_style else (settings.get("core.tts.gemini.style", "Warm & Empathetic") if settings else "Warm & Empathetic")
        resolved_voice = resolve_base_voice(gender, age, style)

    raw_voice_models = settings.get(
        "core.gemini.voice-models", [
            "gemini-3.8-flash-tts",
            "gemini-3.8-flash-lite-tts",
            "gemini-3.1-flash-tts-preview"
        ]
    ) if settings else []
    voice_models = normalize_gemini_voice_models(raw_voice_models)

    client = genai.Client(api_key=api_key)
    max_attempts = max(2, len(voice_models))
    last_err = None

    for attempt in range(1, max_attempts + 1):
        current_model = voice_models[min(attempt - 1, len(voice_models) - 1)]
        alias_map = {
            "gemini-3.1-flash-tts": "gemini-3.1-flash-tts-preview",
            "gemini-3-flash-tts-preview": "gemini-3.1-flash-tts-preview",
            "gemini-3-flash-tts": "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts": "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-lite-preview-tts": "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-lite-tts": "gemini-3.1-flash-tts-preview",
        }
        current_model = alias_map.get(current_model, current_model)
        payload = build_gemini_tts_payload(
            current_model, settings, agent_id, text, somatic_text=somatic_state_text
        )
        try:
            response_stream = client.models.generate_content_stream(
                model=current_model,
                contents=payload,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=(
                            types.VoiceConfig(voice=resolved_voice)
                            if resolved_voice and resolved_voice.startswith("voice_")
                            else types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=resolved_voice
                                )
                            )
                        )
                    ),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    )
                )
            )

            cmd = [
                "ffmpeg", "-y",
                "-f", "s16le",
                "-ar", "24000",
                "-ac", "1",
                "-i", "-",
                "-c:a", "libopus",
                output_path
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )

            try:
                leftover_pcm = b""
                for chunk in response_stream:
                    if not chunk.candidates:
                        continue
                    candidate = chunk.candidates[0]
                    finish_reason = getattr(candidate, "finish_reason", None)
                    reason_name = None
                    if finish_reason is not None:
                        if hasattr(finish_reason, "name") and isinstance(finish_reason.name, str):
                            reason_name = finish_reason.name
                        elif isinstance(finish_reason, str):
                            reason_name = finish_reason
                    if reason_name and reason_name not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                        raise RuntimeError(f"Gemini TTS stream aborted early with finish_reason: {reason_name}")

                    if not candidate.content or not candidate.content.parts:
                        continue

                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.data:
                            data = leftover_pcm + part.inline_data.data
                            if len(data) % 2 != 0:
                                leftover_pcm = data[-1:]
                                data = data[:-1]
                            else:
                                leftover_pcm = b""
                            if data:
                                proc.stdin.write(data)

                if leftover_pcm:
                    proc.stdin.write(leftover_pcm + b"\x00")
                proc.stdin.close()
                stderr = proc.stderr.read()
                proc.wait(timeout=15)
                if proc.returncode != 0:
                    raise RuntimeError(f"FFmpeg encoding error (code {proc.returncode}): {stderr.decode(errors='replace')}")

                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return os.path.abspath(output_path)
                else:
                    raise RuntimeError("Output file was not generated or is empty.")
            except Exception:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                next_model = voice_models[min(attempt, len(voice_models) - 1)]
                logging.getLogger("core.TTS.Gemini").warning(
                    f"Gemini TTS attempt {attempt} failed ({current_model}): {e}. Retrying with {next_model}...")
                time.sleep(0.5)
            else:
                raise last_err


def _synthesize_piper_to_opus(text: str, output_path: str, settings=None) -> str:
    voice = load_piper_voice(settings=settings)
    if not voice:
        raise RuntimeError("Could not load local Piper voice model.")

    clean_text = re.sub(r'\[.*?\]', '', text)
    clean_text = re.sub(r'<.*?>', '', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    if not clean_text:
        raise ValueError("Text contains no speakable characters after tag removal.")

    sample_rate = voice.config.sample_rate
    cmd = [
        "ffmpeg", "-y",
        "-f", "s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-i", "-",
        "-c:a", "libopus",
        output_path
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )
    try:
        for chunk in voice.synthesize(clean_text):
            if chunk and chunk.audio_int16_bytes:
                proc.stdin.write(chunk.audio_int16_bytes)
        proc.stdin.close()
        stderr = proc.stderr.read()
        proc.wait(timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg encoding error (code {proc.returncode}): {stderr.decode(errors='replace')}")

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return os.path.abspath(output_path)
        else:
            raise RuntimeError("Piper output file was not generated or is empty.")
    except Exception:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        raise

