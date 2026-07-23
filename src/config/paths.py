import os

def get_app_data_dir() -> str:
    """Returns the writable XDG data directory for Open Amity"""
    return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.var/app/com.openamity.OpenAmity/data"))

def get_env_file() -> str:
    """Returns the path to the user's .env file"""
    return os.path.join(get_app_data_dir(), ".env")

def get_settings_file() -> str:
    """Returns the path to the user's settings.json file"""
    return os.path.join(get_app_data_dir(), "settings.json")

def get_assets_dir() -> str:
    """Returns the path to the static assets directory bundled with the app"""
    # Assuming this file is in src/config/
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(src_dir, "assets")

def get_icon_path() -> str:
    """Returns the path to the application icon"""
    return os.path.join(get_assets_dir(), "Open Amity.png")

def get_mempalace_dir() -> str:
    """Returns the path to the MemPalace database directory"""
    return os.path.join(get_app_data_dir(), "mempalace")

def get_whatsapp_bridge_dir() -> str:
    """Returns the path to the writable whatsapp node bridge directory"""
    return os.path.join(get_app_data_dir(), "whatsapp_bridge")

def get_whatsapp_data_dir() -> str:
    """Returns the path to the writable whatsapp internal data directory"""
    return os.path.join(get_app_data_dir(), "whatsapp_data")
