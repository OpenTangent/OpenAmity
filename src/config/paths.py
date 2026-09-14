import os


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

