import os
import time
import logging


class CacheManager:
    @staticmethod
    def clear_expired(directory: str, max_age_days: float = 1.0, recursive: bool = False):
        """Removes files in the specified directory that are older than max_age_days."""
        if not os.path.exists(directory):
            return

        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 60 * 60

        if recursive:
            for root, dirs, files in os.walk(directory, topdown=False):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    try:
                        file_mtime = os.path.getmtime(filepath)
                        if current_time - file_mtime > max_age_seconds:
                            os.remove(filepath)
                            logging.debug(
                                f"CacheManager: Deleted old file {filepath}")
                    except Exception as e:
                        logging.warning(
                            f"CacheManager: Failed to process old file {filepath}: {e}")
                for d in dirs:
                    dirpath = os.path.join(root, d)
                    try:
                        if not os.listdir(dirpath):
                            dir_mtime = os.path.getmtime(dirpath)
                            if current_time - dir_mtime > max_age_seconds:
                                os.rmdir(dirpath)
                    except Exception:
                        pass
        else:
            for filename in os.listdir(directory):
                filepath = os.path.join(directory, filename)
                if os.path.isfile(filepath):
                    try:
                        file_mtime = os.path.getmtime(filepath)
                        if current_time - file_mtime > max_age_seconds:
                            os.remove(filepath)
                            logging.debug(
                                f"CacheManager: Deleted old file {filepath}")
                    except Exception as e:
                        logging.warning(
                            f"CacheManager: Failed to process old file {filepath}: {e}")

    @staticmethod
    def clear_all_agent_expired_caches(agent_id: str, max_age_days: float = 1.0):
        """Removes expired temporary files across all cache directories for a given agent."""
        if not agent_id:
            return
        try:
            from config import paths

            # 1. Audio cache (Speaker_speak_to_file opus files)
            CacheManager.clear_expired(
                paths.get_audio_cache_dir(agent_id), max_age_days=max_age_days)

            # 2. Generated media (Media_generate images)
            CacheManager.clear_expired(
                paths.get_generated_media_dir(agent_id), max_age_days=max_age_days)

            # 3. Browser screenshots (Browser_screenshot and Browser_navigate screenshots)
            CacheManager.clear_expired(
                paths.get_browser_screenshot_dir(agent_id), max_age_days=max_age_days)

            # 4. Browser cache directory
            CacheManager.clear_expired(
                paths.get_browser_cache_dir(agent_id), max_age_days=max_age_days, recursive=True)

            # 5. Camoufox browser profile cache directories (cache2 and startupCache)
            browser_data_dir = paths.get_browser_data_dir(agent_id)
            for sub_name in ["cache2", "startupCache"]:
                CacheManager.clear_expired(
                    os.path.join(browser_data_dir, sub_name), max_age_days=max_age_days, recursive=True)

            # 6. Web search cache
            web_cache_dir = os.path.join(paths.get_agent_data_dir(agent_id), "web_cache")
            CacheManager.clear_expired(web_cache_dir, max_age_days=max_age_days)

            # 7. WhatsApp uploads cache
            wa_uploads_dir = os.path.join(paths.get_whatsapp_data_dir(agent_id), "uploads")
            CacheManager.clear_expired(wa_uploads_dir, max_age_days=max_age_days)
        except Exception as e:
            logging.warning(f"CacheManager: Failed to clear agent caches for {agent_id}: {e}")
