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


from core.file_utils import atomic_json_write


class SettingsManager:
    _instances = {}
    _class_lock = threading.RLock()

    def __new__(cls, agent_id=None, settings_file="settings.json"):
        with cls._class_lock:
            from config import paths
            config_dir = paths.get_base_dir_for(agent_id)
            norm_config_dir = os.path.abspath(config_dir)
            key = (norm_config_dir, settings_file)
            if key not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[key] = instance
            return cls._instances[key]

    @classmethod
    def remove_instance(cls, agent_id=None, settings_file="settings.json"):
        with cls._class_lock:
            from config import paths
            config_dir = paths.get_base_dir_for(agent_id)
            norm_config_dir = os.path.abspath(config_dir)
            key = (norm_config_dir, settings_file)
            cls._instances.pop(key, None)

    @classmethod
    def clear_instances(cls):
        with cls._class_lock:
            cls._instances.clear()

    def __init__(self, agent_id=None, settings_file="settings.json"):
        with self._class_lock:
            if getattr(self, '_initialized', False):
                self._check_and_refresh()
                return

            self.agent_id = agent_id
            # Ensure XDG Config Directory exists
            from config import paths
            self.config_dir = paths.get_base_dir_for(agent_id)
            os.makedirs(self.config_dir, exist_ok=True)

            self._lock = threading.RLock()
            self._settings_file = os.path.join(self.config_dir, settings_file)
            self._last_mtime = 0.0
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
            self._initialized = True

    def _check_and_refresh(self):
        from core.state_barrier import state_barrier
        with state_barrier(self.config_dir), self._lock:
            if os.path.exists(self.settings_file):
                try:
                    mtime = os.path.getmtime(self.settings_file)
                    if mtime > getattr(self, '_last_mtime', 0.0):
                        self.settings = self.load_settings()
                except Exception:
                    pass

    @property
    def settings_file(self):
        return self._settings_file

    @settings_file.setter
    def settings_file(self, value):
        with self._class_lock:
            old_norm = os.path.abspath(self.config_dir)
            old_key = (old_norm, os.path.basename(self._settings_file))
            SettingsManager._instances.pop(old_key, None)
            self._settings_file = value
            new_key = (old_norm, os.path.basename(value))
            SettingsManager._instances[new_key] = self
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

    def refresh(self):
        from core.state_barrier import state_barrier
        with state_barrier(self.config_dir), self._lock:
            self.settings = self.load_settings()

    def load_settings(self):
        from core.state_barrier import state_barrier
        with state_barrier(self.config_dir), self._lock:
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
                    self._last_mtime = os.path.getmtime(self.settings_file)
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
                    atomic_json_write(self.settings_file, merged_settings, lock=self._lock)
                    if os.path.exists(self.settings_file):
                        self._last_mtime = os.path.getmtime(self.settings_file)
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
        from core.state_barrier import state_barrier
        with state_barrier(self.config_dir), self._lock:
            try:
                atomic_json_write(self.settings_file, self.settings, lock=self._lock)
                if os.path.exists(self.settings_file):
                    self._last_mtime = os.path.getmtime(self.settings_file)
            except Exception as e:
                logging.error(
                    f"Error saving settings.json: {e}", exc_info=True)
                raise RuntimeError(f"Error saving settings.json: {e}") from e

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
