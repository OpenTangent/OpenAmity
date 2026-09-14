import copy
import json
import os
import logging
import shutil
import threading
import uuid
try:
    from dotenv import set_key, get_key
except ImportError:
    set_key = None
    get_key = None


class SettingsManager:
    def __init__(self, agent_id=None, settings_file="settings.json"):
        self.agent_id = agent_id
        # Ensure XDG Config Directory exists
        from config import paths
        self.config_dir = paths.get_base_dir_for(agent_id)
        os.makedirs(self.config_dir, exist_ok=True)

        self._lock = threading.RLock()
        self._settings_file = os.path.join(self.config_dir, settings_file)
        self.default_file = os.path.join(os.path.dirname(
            os.path.dirname(__file__)), 'config', 'settings.default.json')

        self.env_file = os.path.join(self.config_dir, '.env')
        self.default_env_file = os.path.join(os.path.dirname(
            os.path.dirname(__file__)), 'config', '.env.example')

        # Ensure .env exists
        if not os.path.exists(self.env_file):
            if os.path.exists(self.default_env_file):
                shutil.copyfile(self.default_env_file, self.env_file)
            else:
                with open(self.env_file, 'w') as f:
                    pass

        self.settings = self.load_settings()

    @property
    def settings_file(self):
        return self._settings_file

    @settings_file.setter
    def settings_file(self, value):
        self._settings_file = value
        if hasattr(self, '_lock'):
            self.refresh()

    def deep_merge(self, dict1, dict2):
        """Recursively merge dict2 into dict1."""
        for k, v in dict2.items():
            if k in dict1 and isinstance(dict1[k], dict) and isinstance(v, dict):
                self.deep_merge(dict1[k], v)
            else:
                dict1[k] = v
        return dict1

    _lock = threading.RLock()

    def refresh(self):
        with self._lock:
            self.settings = self.load_settings()

    def load_settings(self):
        with self._lock:
            default_settings = {}
            if os.path.exists(self.default_file):
                try:
                    with open(self.default_file, "r") as f:
                        default_settings = json.load(f)
                except Exception as e:
                    logging.error(
                        f"Error loading settings.default.json: {e}", exc_info=True)

            user_settings = {}
            load_error = False
            if os.path.exists(self.settings_file):
                try:
                    with open(self.settings_file, "r") as f:
                        user_settings = json.load(f)
                except Exception as e:
                    load_error = True
                    logging.error(
                        f"Error loading settings.json: {e}", exc_info=True)
                    try:
                        backup_file = self.settings_file + ".corrupt"
                        shutil.copyfile(self.settings_file, backup_file)
                        logging.warning(
                            f"Backed up corrupt settings.json to {backup_file}")
                    except Exception as backup_e:
                        logging.error(
                            f"Failed to backup corrupt settings file: {backup_e}")

            # Merge user settings into default settings
            merged_settings = self.deep_merge(
                default_settings.copy(), user_settings)

            # If user settings were missing or incomplete, rewrite them to disk
            # Skip this if there was a load error to avoid overwriting a corrupted but fixable file with defaults
            if merged_settings != user_settings and not load_error:
                try:
                    temp_file = f"{self.settings_file}.tmp.{uuid.uuid4().hex}"
                    with open(temp_file, "w") as f:
                        json.dump(merged_settings, f, indent=2)
                    os.replace(temp_file, self.settings_file)
                except Exception as e:
                    logging.error(
                        f"Error saving updated settings.json: {e}", exc_info=True)

            return merged_settings

    def get(self, key, default=None):
        with self._lock:
            keys = key.split('.')
            val = self.settings
            for k in keys:
                if isinstance(val, dict) and k in val:
                    val = val[k]
                else:
                    return default
            if isinstance(val, (dict, list)):
                return copy.deepcopy(val)
            return val

    def set(self, key, value):
        with self._lock:
            keys = key.split('.')
            val = self.settings
            for k in keys[:-1]:
                if k not in val or not isinstance(val[k], dict):
                    val[k] = {}
                val = val[k]
            val[keys[-1]] = value

    def save(self):
        with self._lock:
            try:
                temp_file = f"{self.settings_file}.tmp.{uuid.uuid4().hex}"
                with open(temp_file, "w") as f:
                    json.dump(self.settings, f, indent=2)
                os.replace(temp_file, self.settings_file)
            except Exception as e:
                logging.error(
                    f"Error saving settings.json: {e}", exc_info=True)

    def get_env(self, key, default=""):
        if get_key is None:
            return default

        # Suppress dotenv warning about missing keys to handle them gracefully
        dotenv_logger = logging.getLogger("dotenv.main")
        original_level = dotenv_logger.level
        dotenv_logger.setLevel(logging.ERROR)

        val = get_key(self.env_file, key)

        dotenv_logger.setLevel(original_level)
        if val is not None:
            return val
        return default

    def set_env(self, key, value):
        if set_key is not None:
            set_key(self.env_file, key, str(value) if value is not None else "")
