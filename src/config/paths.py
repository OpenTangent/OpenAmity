import os
import shutil


def get_app_data_dir() -> str:
    """Returns the writable XDG data directory for Open Amity"""
    return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.var/app/com.openamity.OpenAmity/data"))


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
    return os.path.join(get_assets_dir(), "Open Amity.png")


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


def get_backup_targets_file(agent_id: str) -> str:
    """Returns the path to the backup targets JSON file for a specific agent"""
    return os.path.join(get_base_dir_for(agent_id), "backup_targets.json")


def get_browser_data_dir(agent_id: str) -> str:
    """Returns the persistent browser profile directory for a specific agent"""
    return os.path.join(get_base_dir_for(agent_id), "browser_data")


def get_browser_cache_dir(agent_id: str) -> str:
    """Returns the browser cache directory for a specific agent"""
    return os.path.join(get_browser_data_dir(agent_id), "cache")


def get_browser_download_dir(agent_name: str) -> str:
    """Returns the default download directory for browser-initiated downloads"""
    return os.path.expanduser(f"~/Downloads/{agent_name}")


def get_audio_cache_dir(agent_id: str = None) -> str:
    """Returns the writable audio cache directory for a specific agent (or shared if None)"""
    if agent_id:
        return os.path.join(get_base_dir_for(agent_id), "audio_cache")
    return os.path.join(get_app_data_dir(), "audio_cache")


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


