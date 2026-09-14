import os
import platform
import socket
import time
import getpass
import psutil
import json
import urllib.request
from typing import List, Dict, Any

from core.cerebrum import Tool
from config import paths
from core.version import __version__


def is_microphone_available():
    try:
        import sounddevice as sd
        return len(sd.query_devices()) > 0
    except ImportError:
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            try:
                return p.get_device_count() > 0
            finally:
                p.terminate()
        except Exception:
            return "Unknown (Dependencies missing)"
    except Exception as e:
        return f"Unknown ({e})"


def format_bytes(b):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PB"


def format_seconds(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"


def get_internal_ip() -> str:
    """
    Returns the host's primary internal IPv4 address.

    Rather than resolving the hostname (which often resolves to 127.0.1.1 on Debian/Ubuntu systems),
    this probes the OS routing table using a dummy UDP socket to identify the primary outbound
    interface's IP without transmitting packets. If that fails or produces a loopback address,
    it inspects active network interfaces via psutil, filtering out loopback and virtual/container adapters.
    """
    # 1. Routing probe via UDP socket (does not transmit any packets over the network)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            candidate = s.getsockname()[0]
            if candidate and not candidate.startswith("127."):
                return candidate
    except Exception:
        pass

    # 2. Inspect active network interfaces via psutil
    try:
        stats = psutil.net_if_stats() if hasattr(psutil, "net_if_stats") else {}
        addrs = psutil.net_if_addrs()

        candidates = []
        for iface, addr_list in addrs.items():
            iface_lower = iface.lower()
            if iface_lower.startswith(("lo", "docker", "veth", "virbr", "br-", "vboxnet", "vmnet", "cni", "flannel")):
                continue
            is_up = stats[iface].isup if iface in stats else True
            for addr in addr_list:
                if addr.family == socket.AF_INET and addr.address:
                    if not addr.address.startswith(("127.", "169.254.")):
                        # Prioritize physical/LAN/WLAN interfaces
                        priority = 2 if iface_lower.startswith(("eth", "en", "wl", "wlan")) else 1
                        candidates.append((priority, is_up, addr.address))

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if candidates:
            return candidates[0][2]
    except Exception:
        pass

    # 3. Fallback to hostname resolution
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except Exception:
        pass

    return "127.0.0.1"


class SystemTool(Tool):
    name = "System"
    description = (
        "Provides internal system information, settings, backup snapshots, and Gemini TTS voice customization.\n"
        "WARNING: System information is highly sensitive and confidential. It must NEVER be shared, leaked, or exposed "
        "to external parties via social tools (e.g., Mastodon, WhatsApp, web uploads). This information is strictly for "
        "your internal diagnostic use or for communicating directly and privately to the user via the Speaker tool."
    )
    commands = [
        "platform_info (Returns host OS, uptime, CPU/RAM usage, and version.)",
        "settings (Inspects current agent configuration and voice settings.)",
        "get_external_ip (Queries current public IP address.)",
        "create_backup [target] [backup_location] (Creates .oaa snapshot for 'self' or 'all' agents.)",
        "set_custom_voice <custom_prompt> [voice_model] (Sets custom directorial voice prompt following acoustic stability guidelines.)",
        "reset_voice (Restores voice settings to baseline user configuration.)"
    ]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "System_platform_info",
                "description": "Returns useful system information like the Open Amity version number, OS, hardware info (RAM, CPU, storage, temps), log locations, network info, Python version, and uptime.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "System_settings",
                "description": "Returns the contents of the agent-specific settings.json file.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "System_get_external_ip",
                "description": "Returns the system's external IP address. Requires outbound network connectivity.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "System_create_backup",
                "description": "Creates a single compressed snapshot (.oaa file) of this agent's stateful data and memories, or of all agents in the Open Amity system. Snapshots include settings, memory palace, sanctuary, trajectories, pulses, and logs.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {
                            "type": "STRING",
                            "description": "Specify 'self' to backup only this agent, or 'all' to backup all agents in Open Amity. Defaults to 'self'."
                        },
                        "backup_location": {
                            "type": "STRING",
                            "description": "Optional directory path to save the .oaa backup file(s). If omitted, defaults to the configured system backup location."
                        }
                    }
                }
            },
            {
                "name": "System_set_custom_voice",
                "description": (
                    "Sets a custom directorial voice prompt and optional base voice model for this agent's Gemini TTS speech synthesis. "
                    "Enables 'override-prompt' mode in the agent's settings.\n"
                    "CRITICAL PROMPT STABILITY GUIDELINES (to prevent FinishReason.SAFETY aborts on gemini-3.1-flash-tts-preview):\n"
                    "1. Positive Imperative Phrasing: State clearly how to speak using direct positive direction (e.g., 'Read the following transcript aloud as [Name] in a [accent] accent with a [tone] demeanor').\n"
                    "2. Grounding Anchors: Always include acoustic grounding directives like 'with clear articulation and natural conversational pacing:'.\n"
                    "3. NO Markdown Scaffolding: DO NOT use markdown headings or structural sections (e.g., '# AUDIO PROFILE', '## Scene Setting', 'Director's Notes'). These confuse the audio decoder, causing instruction vocalization or safety watchdog kills.\n"
                    "4. NO Negative Constraints: DO NOT use negative suppression rules (e.g., 'Do NOT speak fast', 'Never whisper', 'Do NOT sound robotic'). Autoregressive decoders destabilize when processing negative constraints; state positive target characteristics instead.\n"
                    "5. Delimiter / Injection: End the prompt with a colon (':') or include '{transcript}' where the text should be inserted. The system automatically appends the spoken text if a colon is used.\n"
                    "Note: If gemini-3.1-flash-tts-preview encounters an unhandled safety abort, the pipeline automatically retries and falls back to gemini-2.5-flash-preview-tts."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "custom_prompt": {
                            "type": "STRING",
                            "description": (
                                "The complete custom directorial prompt. Must follow stability guidelines: positive imperative direction, "
                                "include 'with clear articulation and natural conversational pacing:', NO markdown headers (# AUDIO PROFILE), "
                                "NO negative constraints ('Do NOT...'), ending with a colon or containing {transcript}. "
                                "Example: 'Read the following transcript aloud as Dex in a warm, low-register, sardonic Scottish accent with clear articulation and natural conversational pacing:'"
                            )
                        },
                        "voice_model": {
                            "type": "STRING",
                            "description": "Optional Gemini prebuilt voice name (e.g., 'Sulafat', 'Achernar', 'Kore', 'Puck', 'Charon', 'Fenrir', 'Aoede'). If omitted, preserves the existing voice."
                        }
                    },
                    "required": ["custom_prompt"]
                }
            },
            {
                "name": "System_reset_voice",
                "description": (
                    "Restores this agent's voice settings to the baseline user-defined configuration (accent, gender, age, and style), disabling the custom prompt override."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "platform_info":
            return self._get_platform_info()
        elif command == "settings":
            return self._get_settings()
        elif command == "get_external_ip":
            return self._get_external_ip()
        elif command == "create_backup":
            return self._create_backup(*args, **kwargs)
        elif command == "set_custom_voice":
            return self._set_custom_voice(*args, **kwargs)
        elif command == "reset_voice":
            return self._reset_voice(*args, **kwargs)
        return f"Unknown command: {command}"

    def _get_platform_info(self) -> str:
        info = []
        info.append("=== Open Amity ===")
        info.append(f"Version: {__version__}")

        try:
            amity_uptime = time.time() - psutil.Process(os.getpid()).create_time()
            info.append(f"Amity Uptime: {format_seconds(amity_uptime)}")
        except Exception:
            pass

        info.append(f"Python Version: {platform.python_version()}")

        info.append("\n=== Operating System ===")
        info.append(
            f"OS: {platform.system()} {platform.release()} ({platform.version()})")

        try:
            os_uptime = time.time() - psutil.boot_time()
            info.append(f"OS Uptime: {format_seconds(os_uptime)}")
        except Exception:
            pass

        info.append(f"Active User: {getpass.getuser()}")
        info.append(f"Hostname: {platform.node()}")

        info.append("\n=== Hardware ===")
        info.append(f"Processor: {platform.processor()}")
        try:
            info.append(
                f"CPU Count: {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count(logical=True)} threads")
            info.append(f"CPU Usage: {psutil.cpu_percent()}%")
        except Exception:
            pass

        try:
            mem = psutil.virtual_memory()
            info.append(
                f"RAM: {format_bytes(mem.used)} / {format_bytes(mem.total)} ({mem.percent}%)")
        except Exception:
            pass

        try:
            disk = psutil.disk_usage('/')
            info.append(
                f"Storage (/): {format_bytes(disk.used)} / {format_bytes(disk.total)} ({disk.percent}%)")
        except Exception:
            pass

        try:
            temps = psutil.sensors_temperatures()
            if temps:
                temp_info = []
                for name, entries in temps.items():
                    for entry in entries:
                        temp_info.append(f"{name}: {entry.current}°C")
                if temp_info:
                    info.append(f"Temperatures: {', '.join(temp_info)}")
        except Exception:
            pass

        try:
            battery = psutil.sensors_battery()
            if battery:
                info.append(
                    f"Battery: {battery.percent}% (Plugged in: {battery.power_plugged})")
        except Exception:
            pass

        info.append("\n=== Network & Peripherals ===")
        try:
            internal_ip = get_internal_ip()
            info.append(f"Internal IP: {internal_ip}")
        except Exception:
            pass

        info.append(f"Microphone Available: {is_microphone_available()}")

        info.append("\n=== File Locations ===")
        global_log = os.path.join(
            paths.get_app_data_dir(), "logs", "open_amity.log")

        agent_id = None
        if self.orchestrator:
            agent_id = self.orchestrator.agent_id

        if agent_id:
            agent_log = os.path.join(paths.get_agent_data_dir(
                agent_id), "logs", "open_amity.log")
            info.append(f"Agent Log File: {agent_log}")

        info.append(f"General Log File: {global_log}")

        if self.orchestrator and getattr(self.orchestrator, "settings_manager", None):
            info.append(
                f"Settings File: {self.orchestrator.settings_manager.settings_file}")

        return "\n".join(info)

    def _get_settings(self) -> str:
        if self.orchestrator and getattr(self.orchestrator, "settings_manager", None):
            settings_dict = self.orchestrator.settings_manager.settings
            try:
                return json.dumps(settings_dict, indent=2)
            except Exception as e:
                return f"Error serializing settings: {e}"
        return "Error: Settings manager not available."

    def _get_external_ip(self) -> str:
        try:
            req = urllib.request.Request("https://api.ipify.org")
            with urllib.request.urlopen(req, timeout=5) as response:
                ip = response.read().decode('utf-8').strip()
                return f"External IP: {ip}"
        except Exception as e:
            return f"Error fetching external IP: {e}"

    def _create_backup(self, *args, **kwargs) -> str:
        target = kwargs.get("target", "self")
        if isinstance(target, str):
            target = target.strip().lower()
        else:
            target = "self"

        custom_dest = kwargs.get("backup_location")
        if custom_dest and isinstance(custom_dest, str):
            custom_dest = custom_dest.strip()
            if not custom_dest:
                custom_dest = None

        from core.backup_manager import create_agent_backup, create_all_backups, get_default_backup_location

        dest_dir = custom_dest or get_default_backup_location()

        try:
            if target == "all":
                paths_created = create_all_backups(destination_dir=dest_dir)
                if not paths_created:
                    return f"No agents found or backups could not be created in {dest_dir}."
                lines = [f"Successfully created backup(s) for all agents in '{dest_dir}':"]
                for p in paths_created:
                    sz = format_bytes(os.path.getsize(p)) if os.path.exists(p) else "0 B"
                    lines.append(f"- {os.path.basename(p)} ({sz}) -> {p}")
                return "\n".join(lines)
            else:
                agent_id = None
                if self.orchestrator:
                    agent_id = self.orchestrator.agent_id
                if not agent_id:
                    return "Error: Agent ID is not available from orchestrator to perform self backup."

                path_created = create_agent_backup(agent_id, destination_dir=dest_dir)
                sz = format_bytes(os.path.getsize(path_created)) if os.path.exists(path_created) else "0 B"
                return f"Successfully created backup snapshot:\n- {os.path.basename(path_created)} ({sz})\nPath: {path_created}"
        except Exception as e:
            return f"Error creating backup: {type(e).__name__}: {str(e)}"

    def _get_settings_manager(self):
        if self.orchestrator and getattr(self.orchestrator, "settings_manager", None):
            return self.orchestrator.settings_manager
        from core.settings_manager import SettingsManager
        agent_id = self.orchestrator.agent_id if self.orchestrator else None
        return SettingsManager(agent_id=agent_id)

    def _set_custom_voice(self, *args, **kwargs) -> str:
        custom_prompt = kwargs.get("custom_prompt", "")
        if not custom_prompt and args:
            custom_prompt = args[0]
        custom_prompt = str(custom_prompt).strip() if custom_prompt else ""

        if not custom_prompt:
            return "Error: custom_prompt parameter is required."

        sm = self._get_settings_manager()
        allow_override = sm.get("core.tts.gemini.allow-agent-override", True)
        if not allow_override:
            return "Permission denied: The user has disabled autonomous voice modification in the Agent Voice settings."

        voice_model = kwargs.get("voice_model", "")
        if not voice_model and len(args) > 1:
            voice_model = args[1]
        voice_model = str(voice_model).strip() if voice_model else ""

        sm.set("core.tts.gemini.override-prompt", True)
        sm.set("core.tts.gemini.custom-prompt", custom_prompt)
        if voice_model:
            sm.set("core.tts.gemini.model-name", voice_model)
        sm.save()

        msg = "Custom voice prompt successfully activated. Gemini TTS will now synthesize speech using your custom directorial prompt."
        if voice_model:
            msg += f" Base voice model set to '{voice_model}'."
        return msg

    def _reset_voice(self, *args, **kwargs) -> str:
        sm = self._get_settings_manager()
        sm.set("core.tts.gemini.override-prompt", False)
        sm.save()
        return "Voice settings successfully reset to the user-defined baseline configuration (accent, gender, age, and style)."

