import os
import time
import logging

class CacheManager:
    @staticmethod
    def clear_expired(directory: str, max_age_days: float = 1.0):
        """Removes files in the specified directory that are older than max_age_days."""
        if not os.path.exists(directory):
            return
            
        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 60 * 60
        
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                try:
                    file_mtime = os.path.getmtime(filepath)
                    if current_time - file_mtime > max_age_seconds:
                        os.remove(filepath)
                        logging.debug(f"CacheManager: Deleted old file {filepath}")
                except Exception as e:
                    logging.warning(f"CacheManager: Failed to process old file {filepath}: {e}")
