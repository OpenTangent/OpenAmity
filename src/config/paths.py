import os
import shutil
import sys

FLATPAK_APP_ID = "com.openamity.OpenAmity"
DEV_DESKTOP_APP_ID = "com.openamity.OpenAmity.Devel"
_DESKTOP_EXEC_RESERVED_CHARS = set(" \t\n\"'\\><~|&;$*?#()`")


def get_desktop_app_id() -> str:
    """Returns the desktop application ID for window matching on Linux/Wayland."""
    flatpak_id = os.environ.get("FLATPAK_ID")
    if flatpak_id:
        return flatpak_id
    return DEV_DESKTOP_APP_ID


def _escape_desktop_string(value: str) -> str:
    """Escapes a string value according to the FreeDesktop Desktop Entry spec."""
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def _escape_desktop_exec_arg(arg: str) -> str:
    """Escapes a single Exec argument according to the FreeDesktop Desktop Entry spec."""
    escaped = arg.replace("%", "%%")
    if any(ch in _DESKTOP_EXEC_RESERVED_CHARS for ch in escaped):
        quoted = (
            escaped.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        escaped = f'"{quoted}"'
    return _escape_desktop_string(escaped)


def ensure_dev_desktop_entry() -> str | None:
    """Registers or refreshes a local .Devel desktop entry outside Flatpak on Linux.

    This allows Wayland/GNOME Shell to resolve the development app icon in the dock
    without creating a `com.openamity.OpenAmity.desktop` file that would shadow the
    official Flatpak package.
    """
    if os.environ.get("FLATPAK_ID") or not sys.platform.startswith("linux"):
        return None

    icon_path = os.path.abspath(get_icon_path())
    if not os.path.isfile(icon_path):
        return None

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    launcher_script = os.path.join(repo_root, "run_OpenAmity.sh")
    if os.path.isfile(launcher_script):
        exec_value = _escape_desktop_exec_arg(launcher_script)
    else:
        main_script = os.path.join(repo_root, "src", "main.py")
        exec_value = f"{_escape_desktop_exec_arg(sys.executable)} {_escape_desktop_exec_arg(main_script)}"

    content = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Open Amity (Dev)\n"
        "GenericName=AI Companion & Multi-Agent Framework\n"
        "Comment=Proactive multi-agent AI desktop companion and team orchestration framework\n"
        f"Exec={exec_value}\n"
        f"Icon={_escape_desktop_string(icon_path)}\n"
        "Terminal=false\n"
        "Categories=Network;Chat;InstantMessaging;Utility;\n"
        "StartupNotify=true\n"
        f"StartupWMClass={DEV_DESKTOP_APP_ID}\n"
        "NoDisplay=true\n"
    )

    xdg_data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    apps_dir = os.path.join(xdg_data_home, "applications")
    desktop_file = os.path.join(apps_dir, f"{DEV_DESKTOP_APP_ID}.desktop")
    tmp_path = os.path.join(apps_dir, f".{DEV_DESKTOP_APP_ID}.desktop.{os.getpid()}.tmp")

    try:
        if os.path.isfile(desktop_file):
            with open(desktop_file, "r", encoding="utf-8") as existing:
                if existing.read() == content:
                    return desktop_file

        os.makedirs(apps_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, desktop_file)
        return desktop_file
    except Exception as e:
        import logging
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        logging.getLogger("config.paths").warning(
            f"Failed to register dev desktop entry at {desktop_file}: {e}"
        )
        return None


def get_app_data_dir() -> str:
    """Returns the writable XDG data directory for Open Amity"""
    return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.var/app/com.openamity.OpenAmity/data"))


def get_app_cache_dir() -> str:
    """Returns the writable XDG cache directory for Open Amity"""
    return os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))


def ensure_flatpak_persistence_links() -> None:
    """When running inside a Flatpak sandbox where $HOME is an ephemeral tmpfs,
    ensures ~/.cache and ~/.local/share symlink to the persistent XDG_CACHE_HOME
    and XDG_DATA_HOME directories so libraries that hardcode Path.home() / '.cache'
    (such as ChromaDB ONNXMiniLM_L6_V2 and Puppeteer) persist across restarts."""
    if not os.environ.get("FLATPAK_ID"):
        return
    try:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        home_cache = os.path.expanduser("~/.cache")
        if xdg_cache:
            os.makedirs(xdg_cache, exist_ok=True)
            if not os.path.exists(home_cache) and not os.path.islink(home_cache):
                os.symlink(xdg_cache, home_cache)

        xdg_data = os.environ.get("XDG_DATA_HOME")
        home_local_share = os.path.expanduser("~/.local/share")
        if xdg_data:
            os.makedirs(xdg_data, exist_ok=True)
            if not os.path.exists(home_local_share) and not os.path.islink(home_local_share):
                os.makedirs(os.path.dirname(home_local_share), exist_ok=True)
                os.symlink(xdg_data, home_local_share)
    except Exception as e:
        import logging
        logging.getLogger("config.paths").warning(f"Failed to set up Flatpak persistence links: {e}")


def get_agent_data_dir(agent_id: str) -> str:
    """Returns the writable data directory for a specific agent"""
    return os.path.join(get_app_data_dir(), "agents", agent_id)


def get_base_dir_for(agent_id: str) -> str:
    if not agent_id:
        raise ValueError(
            "agent_id is a required parameter for agent-specific paths.")
    return get_agent_data_dir(agent_id)


def get_env_file(agent_id: str) -> str:
    """Returns the path to the user's .env file"""
    return os.path.join(get_base_dir_for(agent_id), ".env")


def get_settings_file(agent_id: str) -> str:
    """Returns the path to the user's settings.json file"""
    return os.path.join(get_base_dir_for(agent_id), "settings.json")


def get_config_file() -> str:
    """Returns the path to the central config.json file"""
    return os.path.join(get_app_data_dir(), "config.json")


def get_assets_dir() -> str:
    """Returns the path to the static assets directory bundled with the app"""
    # Assuming this file is in src/config/
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(src_dir, "assets")


def get_icon_path() -> str:
    """Returns the path to the application icon"""
    return os.path.join(get_assets_dir(), "com.openamity.OpenAmity.png")


def get_mempalace_dir(agent_id: str) -> str:
    """Returns the path to the MemPalace database directory"""
    return os.path.join(get_base_dir_for(agent_id), "mempalace")


def get_whatsapp_bridge_dir(agent_id: str) -> str:
    """Returns the path to the writable whatsapp node bridge directory"""
    return os.path.join(get_base_dir_for(agent_id), "whatsapp_bridge")


def get_whatsapp_data_dir(agent_id: str) -> str:
    """Returns the path to the writable whatsapp internal data directory"""
    return os.path.join(get_base_dir_for(agent_id), "whatsapp_data")


def get_chatroom_dir() -> str:
    """Returns the path to the shared chatroom directory"""
    return os.path.join(get_app_data_dir(), "chatroom")


def get_chatroom_db_path() -> str:
    """Returns the path to the shared chatroom.db SQLite database"""
    return os.path.join(get_chatroom_dir(), "chatroom.db")


def get_chat_history_db_path(agent_id: str) -> str:
    """Returns the persistent path to the agent's one-on-one chat history SQLite database"""
    return os.path.join(get_base_dir_for(agent_id), "chat_history.db")


def get_session_cache_file(agent_id: str) -> str:
    """Returns the path to the active session cache JSON file for a specific agent"""
    return os.path.join(get_base_dir_for(agent_id), "session_cache.json")


def get_backup_targets_file(agent_id: str) -> str:
    """Returns the path to the backup targets JSON file for a specific agent"""
    return os.path.join(get_base_dir_for(agent_id), "backup_targets.json")


def get_browser_data_dir(agent_id: str) -> str:
    """Returns the persistent browser profile directory for a specific agent"""
    return os.path.join(get_base_dir_for(agent_id), "browser_data")


def get_browser_cache_dir(agent_id: str = None) -> str:
    """Returns the browser cache directory for a specific agent (or shared if None)"""
    if agent_id:
        return os.path.join(get_browser_data_dir(agent_id), "cache")
    return os.path.join(get_app_data_dir(), "browser_cache")


def get_browser_screenshot_dir(agent_id: str = None) -> str:
    """Returns the browser screenshot cache directory for a specific agent (or shared if None)"""
    if agent_id:
        return os.path.join(get_base_dir_for(agent_id), "media")
    return os.path.join(get_app_data_dir(), "media")


def get_browser_download_dir(agent_name: str) -> str:
    """Returns the default download directory for browser-initiated downloads"""
    return os.path.expanduser(f"~/Downloads/{agent_name}")


def get_audio_cache_dir(agent_id: str = None) -> str:
    """Returns the writable audio cache directory for a specific agent (or shared if None)"""
    if agent_id:
        return os.path.join(get_base_dir_for(agent_id), "audio_cache")
    return os.path.join(get_app_data_dir(), "audio_cache")


def get_speech_transcript_cache_dir(agent_id: str = None) -> str:
    """Returns the writable 24-hour cached speech transcript audio directory."""
    return os.path.join(get_audio_cache_dir(agent_id), "transcripts")


def get_generated_media_dir(agent_id: str = None) -> str:
    """Returns the writable generated media cache directory for a specific agent (or shared if None)"""
    if agent_id:
        return os.path.join(get_base_dir_for(agent_id), "generated_media")
    return os.path.join(get_app_data_dir(), "generated_media")


def get_email_archive_db_path(agent_id: str = None) -> str:
    """Returns the persistent path to the agent's email archive SQLite database."""
    if agent_id:
        return os.path.join(get_base_dir_for(agent_id), "email_archive.db")
    return os.path.join(get_app_data_dir(), "email_archive.db")


def get_openrouter_models_cache_file() -> str:
    """Returns the persistent writable path for cached OpenRouter models"""
    return os.path.join(get_app_data_dir(), "openrouter_models.json")


def get_default_openrouter_models_file() -> str:
    """Returns the shipped default OpenRouter models JSON file"""
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(src_dir, "config", "openrouter_models.json")


def get_openrouter_image_models_cache_file() -> str:
    """Returns the persistent writable path for cached OpenRouter image models"""
    return os.path.join(get_app_data_dir(), "openrouter_image_models.json")


def get_default_openrouter_image_models_file() -> str:
    """Returns the shipped default OpenRouter image models JSON file"""
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(src_dir, "config", "openrouter_image_models.json")


def ensure_openrouter_models_cache() -> str:
    """Ensures the user's OpenRouter models cache file exists in the app data directory,
    seeding it from the shipped defaults if missing or empty."""
    cache_file = get_openrouter_models_cache_file()
    if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
        default_file = get_default_openrouter_models_file()
        if os.path.exists(default_file):
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                shutil.copyfile(default_file, cache_file)
            except Exception as e:
                import logging
                logging.getLogger("config.paths").warning(f"Failed to prepopulate OpenRouter models cache: {e}")
    return cache_file


def ensure_openrouter_image_models_cache() -> str:
    """Ensures the user's OpenRouter image models cache file exists in the app data directory,
    seeding it from the shipped defaults if missing or empty."""
    cache_file = get_openrouter_image_models_cache_file()
    if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
        default_file = get_default_openrouter_image_models_file()
        if os.path.exists(default_file):
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                shutil.copyfile(default_file, cache_file)
            except Exception as e:
                import logging
                logging.getLogger("config.paths").warning(f"Failed to prepopulate OpenRouter image models cache: {e}")
    return cache_file


def get_provider_models_cache_file(provider_id: str) -> str:
    """Returns the persistent writable path for cached models of a given provider"""
    return os.path.join(get_app_data_dir(), f"{provider_id}_models.json")
