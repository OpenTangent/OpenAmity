import json
import os
import logging
import shutil
import threading
from config import paths


class ConfigManager:
    _lock = threading.Lock()

    def __init__(self, config_file=None):
        self.config_dir = paths.get_app_data_dir()
        os.makedirs(self.config_dir, exist_ok=True)

        self.config_file = config_file or paths.get_config_file()
        self.default_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'config', 'config.default.json')

        self.config = self.load_config()

    def deep_merge(self, dict1, dict2):
        """Recursively merge dict2 into dict1."""
        for k, v in dict2.items():
            if k in dict1 and isinstance(dict1[k], dict) and isinstance(v, dict):
                self.deep_merge(dict1[k], v)
            else:
                dict1[k] = v
        return dict1

    def load_config(self):
        with self._lock:
            default_config = {}
            if os.path.exists(self.default_file):
                try:
                    with open(self.default_file, "r") as f:
                        default_config = json.load(f)
                except Exception as e:
                    logging.error(
                        f"Error loading config.default.json: {e}", exc_info=True)

            user_config = {}
            load_error = False
            if os.path.exists(self.config_file):
                try:
                    with open(self.config_file, "r") as f:
                        user_config = json.load(f)
                except Exception as e:
                    load_error = True
                    logging.error(
                        f"Error loading config.json: {e}", exc_info=True)
                    try:
                        backup_file = self.config_file + ".corrupt"
                        shutil.copyfile(self.config_file, backup_file)
                        logging.warning(
                            f"Backed up corrupt config.json to {backup_file}")
                    except Exception as backup_e:
                        logging.error(
                            f"Failed to backup corrupt config file: {backup_e}")

            # Merge user config into default config
            merged_config = self.deep_merge(
                default_config.copy(), user_config)

            # If user config was missing or incomplete, rewrite to disk
            if merged_config != user_config and not load_error:
                try:
                    temp_file = self.config_file + ".tmp"
                    with open(temp_file, "w") as f:
                        json.dump(merged_config, f, indent=2)
                    os.replace(temp_file, self.config_file)
                except Exception as e:
                    logging.error(
                        f"Error saving updated config.json: {e}", exc_info=True)

            return merged_config

    def get(self, key, default=None):
        self.config = self.load_config()
        keys = key.split('.')
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, key, value):
        with self._lock:
            keys = key.split('.')
            val = self.config
            for k in keys[:-1]:
                if k not in val or not isinstance(val[k], dict):
                    val[k] = {}
                val = val[k]
            val[keys[-1]] = value

    def save(self):
        with self._lock:
            try:
                temp_file = self.config_file + ".tmp"
                with open(temp_file, "w") as f:
                    json.dump(self.config, f, indent=2)
                os.replace(temp_file, self.config_file)
            except Exception as e:
                logging.error(
                    f"Error saving config.json: {e}", exc_info=True)
