import os
import re
import stat
import json
import uuid
import shutil
import zipfile
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from config import paths
from core.config_manager import ConfigManager
from core.settings_manager import SettingsManager
from core.uid_generator import is_valid_agent_uid


def get_default_backup_location() -> str:
    """Returns the expanded absolute path for the configured backup location."""
    config = ConfigManager()
    loc = config.get("backup-location", "~/Documents/OpenAmity/Backups")
    return os.path.expanduser(loc.strip())


def sanitize_agent_name(name: str) -> str:
    """Sanitizes an agent name for safe use in filenames."""
    if not name:
        return "Agent"
    # Replace illegal filesystem characters and path separators with underscores
    sanitized = re.sub(r'[\\/*?:"<>|\r\n\t]+', '_', name).strip()
    return sanitized if sanitized else "Agent"


def get_backup_filename(agent_name: str, datestamp: Optional[str] = None) -> str:
    """Generates a standard .oaa backup filename: <AgentName>-<datestamp>.oaa"""
    safe_name = sanitize_agent_name(agent_name)
    if not datestamp:
        datestamp = datetime.now().strftime("%Y-%m-%d")
    return f"{safe_name}-{datestamp}.oaa"


def create_agent_backup(
    agent_id: str,
    destination_dir: Optional[str] = None,
    agent_manager: Optional[Any] = None,
    progress_callback: Optional[Any] = None
) -> str:
    """
    Creates a single compressed .oaa snapshot of an entire agent directory.
    
    Args:
        agent_id: The ID of the agent to backup.
        destination_dir: Optional custom destination directory. Defaults to configured location.
        agent_manager: Optional AgentManager instance.
        progress_callback: Optional callback receiving (filename: str, current_file_index: int, total_files: int)
        
    Returns:
        The absolute path to the created .oaa backup file.
    """
    agent_dir = paths.get_agent_data_dir(agent_id)
    if not os.path.exists(agent_dir):
        raise FileNotFoundError(f"Agent data directory not found for agent '{agent_id}' at {agent_dir}")

    # Determine agent name
    agent_name = "Agent"
    if agent_manager and hasattr(agent_manager, 'get_agent_name'):
        agent_name = agent_manager.get_agent_name(agent_id)
    else:
        try:
            settings = SettingsManager(agent_id=agent_id)
            agent_name = settings.get("core.agent.name", agent_id)
        except Exception:
            agent_name = agent_id

    # Resolve destination directory
    dest_dir = destination_dir or get_default_backup_location()
    dest_dir = os.path.expanduser(dest_dir.strip())
    os.makedirs(dest_dir, exist_ok=True)

    filename = get_backup_filename(agent_name)
    backup_path = os.path.join(dest_dir, filename)

    logging.info(f"BackupManager: Creating backup for agent '{agent_name}' ({agent_id}) at {backup_path}...")

    # Discover candidate files first for accurate progress reporting
    # Directories and files to exclude from backups (transient caches, lock files, bulky node_modules and browser binaries)
    EXCLUDED_DIR_NAMES = {"node_modules", "puppeteer_cache", ".wwebjs_cache", "__pycache__"}
    EXCLUDED_FILE_NAMES = {
        "SingletonLock", "SingletonCookie", "SingletonSocket",
        "daemon.pid", "daemon.port", ".last_engine_update"
    }

    eligible_files = []
    for root, dirs, files in os.walk(agent_dir):
        # Prune excluded directories in-place so os.walk does not traverse them
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]

        for file in files:
            if file in EXCLUDED_FILE_NAMES:
                continue

            full_file_path = os.path.join(root, file)

            # Check for broken symlink
            if os.path.islink(full_file_path) and not os.path.exists(full_file_path):
                continue

            eligible_files.append(full_file_path)

    total_files = len(eligible_files)

    # Write compressed ZIP archive with .oaa extension
    temp_backup_path = backup_path + f".tmp_{uuid.uuid4().hex[:8]}"
    try:
        with zipfile.ZipFile(temp_backup_path, 'w', compression=zipfile.ZIP_DEFLATED) as zip_file:
            for idx, full_file_path in enumerate(eligible_files, start=1):
                try:
                    st = os.stat(full_file_path)
                    # Skip special files like sockets or FIFOs
                    if stat.S_ISSOCK(st.st_mode) or stat.S_ISFIFO(st.st_mode):
                        logging.debug(f"BackupManager: Skipping non-regular file: {full_file_path}")
                        continue

                    arcname = os.path.relpath(full_file_path, agent_dir)
                    zip_file.write(full_file_path, arcname=arcname)

                    if progress_callback:
                        progress_callback(os.path.basename(full_file_path), idx, total_files)
                except (FileNotFoundError, PermissionError, OSError) as write_err:
                    logging.warning(
                        f"BackupManager: Skipping transient or unreadable file '{full_file_path}': {write_err}"
                    )
        
        # Atomically replace or move into destination
        os.replace(temp_backup_path, backup_path)
    except Exception as e:
        if os.path.exists(temp_backup_path):
            try:
                os.remove(temp_backup_path)
            except Exception:
                pass
        raise e

    logging.info(f"BackupManager: Successfully created backup {backup_path} ({os.path.getsize(backup_path)} bytes)")
    return os.path.abspath(backup_path)


def create_all_backups(
    destination_dir: Optional[str] = None,
    agent_manager: Optional[Any] = None
) -> List[str]:
    """
    Creates .oaa backups for all active agents in the Open Amity framework.
    
    Returns:
        List of absolute paths to created .oaa backup files.
    """
    if agent_manager is None:
        from core.agent_manager import AgentManager
        agent_manager = AgentManager()

    agent_ids = agent_manager.get_all_agents()
    created_backups = []

    for aid in agent_ids:
        try:
            path = create_agent_backup(aid, destination_dir=destination_dir, agent_manager=agent_manager)
            created_backups.append(path)
        except Exception as e:
            logging.error(f"BackupManager: Failed to backup agent {aid}: {e}", exc_info=True)

    return created_backups


def inspect_backup(backup_path: str) -> Dict[str, Any]:
    """
    Inspects an .oaa archive and returns metadata and validation info without fully extracting it.
    
    Returns:
        Dictionary containing:
        - valid: bool
        - uid: str
        - name: str
        - archetype: str
        - creation_date: str
        - file_count: int
        - archive_size: int
        - settings: dict (if found)
        - error: str (if invalid)
    """
    if not os.path.exists(backup_path):
        return {"valid": False, "error": f"File does not exist: {backup_path}"}

    if not zipfile.is_zipfile(backup_path):
        return {"valid": False, "error": "File is not a valid zip archive (.oaa)"}

    try:
        with zipfile.ZipFile(backup_path, 'r') as zip_file:
            namelist = zip_file.namelist()
            if not namelist:
                return {"valid": False, "error": "Archive is empty"}

            # Locate settings.json
            settings_entry = None
            for name in namelist:
                norm = name.replace("\\", "/").strip("/")
                if norm == "settings.json" or norm.endswith("/settings.json"):
                    settings_entry = name
                    break

            if not settings_entry:
                return {"valid": False, "error": "Archive does not contain settings.json"}

            with zip_file.open(settings_entry) as f:
                settings_data = json.load(f)

            agent_info = settings_data.get("core", {}).get("agent", {})
            uid = agent_info.get("uid", "")
            name = agent_info.get("name", "Unknown Agent")
            archetype = agent_info.get("archetype", "")
            creation_date = agent_info.get("creation-date", "")

            if not uid:
                return {"valid": False, "error": "settings.json is missing core.agent.uid"}

            return {
                "valid": True,
                "uid": uid,
                "name": name,
                "archetype": archetype,
                "creation_date": creation_date,
                "file_count": len(namelist),
                "archive_size": os.path.getsize(backup_path),
                "settings": settings_data
            }
    except Exception as e:
        return {"valid": False, "error": f"Failed to read backup archive: {str(e)}"}


def restore_agent_backup(
    backup_path: str,
    agent_manager: Optional[Any] = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Restores an agent from an .oaa backup archive.
    
    If the agent's UID matches an existing agent, performs a rollback restore
    (fully deleting all existing stateful files before extracting).
    Otherwise, restores the agent as a new instance.
    
    Returns:
        Tuple of (success: bool, message: str, details: dict)
    """
    if agent_manager is None:
        from core.agent_manager import AgentManager
        agent_manager = AgentManager()

    info = inspect_backup(backup_path)
    if not info.get("valid"):
        return False, info.get("error", "Invalid backup archive"), {}

    uid = info["uid"]
    agent_name = info.get("name", "Agent")

    # Check if an agent with this UID already exists in the framework
    existing_agent_id = agent_manager.get_agent_id_by_uid(uid)
    is_rollback = (existing_agent_id is not None)

    target_agent_id = existing_agent_id if is_rollback else str(uuid.uuid4())
    target_dir = paths.get_agent_data_dir(target_agent_id)

    logging.info(
        f"BackupManager: Restoring agent '{agent_name}' ({uid}) -> target_id='{target_agent_id}', "
        f"is_rollback={is_rollback}, target_dir='{target_dir}'"
    )

    try:
        # If rollback, shutdown in-memory orchestrator and completely delete existing directory
        if is_rollback:
            if hasattr(agent_manager, 'orchestrators') and target_agent_id in agent_manager.orchestrators:
                orch = agent_manager.orchestrators.pop(target_agent_id)
                try:
                    if hasattr(orch, 'shutdown'):
                        orch.shutdown(force_sleep=False)
                except Exception as shutdown_err:
                    logging.warning(f"BackupManager: Warning during agent shutdown: {shutdown_err}")

            if os.path.exists(target_dir):
                logging.info(f"BackupManager: Fully deleting existing agent state at {target_dir} before restore...")
                shutil.rmtree(target_dir, ignore_errors=True)

        os.makedirs(target_dir, exist_ok=True)

        # Extract archive contents
        with zipfile.ZipFile(backup_path, 'r') as zip_file:
            # Check if there is a common root directory prefix in the zip
            namelist = [n.replace("\\", "/") for n in zip_file.namelist()]
            
            # Find the path prefix of settings.json
            settings_prefix = ""
            for n in namelist:
                if n == "settings.json":
                    settings_prefix = ""
                    break
                elif n.endswith("/settings.json"):
                    settings_prefix = n[:-len("settings.json")]
                    break

            for member in zip_file.infolist():
                norm_name = member.filename.replace("\\", "/")
                
                # Strip prefix if needed
                if settings_prefix and norm_name.startswith(settings_prefix):
                    rel_name = norm_name[len(settings_prefix):]
                else:
                    rel_name = norm_name

                if not rel_name or rel_name.endswith("/"):
                    continue

                # Ensure safe target path
                out_path = os.path.abspath(os.path.join(target_dir, rel_name))
                if not out_path.startswith(os.path.abspath(target_dir)):
                    logging.warning(f"BackupManager: Skipping unsafe archive path {member.filename}")
                    continue

                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with zip_file.open(member) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                # Restore POSIX file permissions if available in member external_attr
                attr = (member.external_attr >> 16) & 0xFFFF
                if attr:
                    try:
                        os.chmod(out_path, attr)
                    except Exception as chmod_err:
                        logging.debug(f"BackupManager: Could not set permissions on {out_path}: {chmod_err}")

        # Ensure .env exists
        env_path = os.path.join(target_dir, ".env")
        if not os.path.exists(env_path):
            with open(env_path, "w") as f:
                f.write("")

        details = {
            "agent_id": target_agent_id,
            "is_rollback": is_rollback,
            "uid": uid,
            "name": agent_name,
            "target_dir": target_dir
        }

        action_word = "rolled back and restored" if is_rollback else "restored as a new agent instance"
        msg = f"Agent '{agent_name}' ({uid}) was successfully {action_word}."
        logging.info(f"BackupManager: {msg}")
        return True, msg, details

    except Exception as e:
        logging.error(f"BackupManager: Restore failed: {e}", exc_info=True)
        return False, f"Restore failed: {str(e)}", {}
