import os
import re
import stat
import json
import uuid
import shutil
import zipfile
import logging
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from config import paths
from core.config_manager import ConfigManager
from core.settings_manager import SettingsManager
from core.uid_generator import is_valid_agent_uid
from core.file_utils import atomic_json_write

_backup_targets_lock = threading.RLock()

DISALLOWED_SYSTEM_DIRS = (
    "/", "/etc", "/usr", "/boot", "/sys", "/proc", "/dev",
    "/bin", "/sbin", "/lib", "/lib64", "/var"
)

DISALLOWED_SENSITIVE_HOME_ENTRIES = (
    ".ssh",
    ".bashrc",
    ".bash_profile",
    ".bash_history",
    ".zshrc",
    ".profile",
    ".gnupg",
    ".config",
    ".local",
    ".var",
)


def is_safe_external_backup_path(path: str) -> bool:
    """
    Validates that a path is safe for external backup target flagging or restoration.
    The path MUST be within the user's home directory, cannot be the home directory itself,
    cannot reside in root/system directories, and cannot match sensitive user dotfiles/directories.
    """
    if not path:
        return False

    abs_p = os.path.abspath(os.path.expanduser(path.strip()))
    home_dir = os.path.abspath(os.path.expanduser("~"))

    # Must be strictly within user's home directory
    if abs_p == home_dir or not abs_p.startswith(home_dir + os.sep):
        return False

    # Disallow root and system directories
    if abs_p in DISALLOWED_SYSTEM_DIRS:
        return False
    for sys_dir in DISALLOWED_SYSTEM_DIRS:
        if sys_dir != "/" and (abs_p == sys_dir or abs_p.startswith(sys_dir.rstrip(os.sep) + os.sep)):
            return False

    # Disallow sensitive user paths and dotfiles under the home directory
    for pattern in DISALLOWED_SENSITIVE_HOME_ENTRIES:
        sensitive_abs = os.path.abspath(os.path.join(home_dir, pattern))
        if abs_p == sensitive_abs or abs_p.startswith(sensitive_abs + os.sep):
            return False

    return True


def to_portable_path(path: str) -> str:
    """Normalizes a filesystem path and converts user home references to ~ for portability."""
    if not path:
        return ""
    expanded = os.path.abspath(os.path.expanduser(path.strip()))
    home_dir = os.path.abspath(os.path.expanduser("~"))
    if expanded == home_dir:
        return "~"
    if expanded.startswith(home_dir + os.sep):
        return "~" + expanded[len(home_dir):]
    return expanded


def from_portable_path(portable_path: str) -> str:
    """Expands a portable path (e.g. starting with ~) to an absolute host filesystem path."""
    if not portable_path:
        return ""
    return os.path.abspath(os.path.expanduser(portable_path.strip()))


def load_backup_targets(agent_id: str) -> List[Dict[str, Any]]:
    """Loads and returns the list of flagged backup target objects for an agent."""
    with _backup_targets_lock:
        if not agent_id:
            return []
        targets_file = paths.get_backup_targets_file(agent_id)
        if not os.path.exists(targets_file):
            return []
        try:
            with open(targets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_targets = data.get("targets", []) if isinstance(data, dict) else data
            normalized = []
            if isinstance(raw_targets, list):
                for item in raw_targets:
                    if isinstance(item, str):
                        p = to_portable_path(item)
                        normalized.append({
                            "path": p,
                            "type": "directory" if os.path.isdir(from_portable_path(p)) else "file",
                            "added_at": datetime.now().isoformat()
                        })
                    elif isinstance(item, dict) and "path" in item:
                        p = to_portable_path(item["path"])
                        normalized.append({
                            "path": p,
                            "type": item.get("type", "directory" if os.path.isdir(from_portable_path(p)) else "file"),
                            "added_at": item.get("added_at", datetime.now().isoformat())
                        })
            return normalized
        except Exception as e:
            logging.error(f"BackupManager: Error reading backup targets for agent {agent_id}: {e}")
            return []


def save_backup_targets(agent_id: str, targets: List[Dict[str, Any]]) -> None:
    """Atomically saves the list of flagged backup targets for an agent."""
    with _backup_targets_lock:
        if not agent_id:
            return
        targets_file = paths.get_backup_targets_file(agent_id)
        payload = {
            "version": 1,
            "targets": targets,
            "last_updated": datetime.now().isoformat()
        }
        atomic_json_write(targets_file, payload)


def sync_and_clean_backup_targets(
    agent_id: str,
    candidate_path: Optional[str] = None,
    action: str = "add"
) -> Tuple[bool, str, List[Dict[str, str]], List[Dict[str, Any]]]:
    """
    Validates, synchronizes, and prunes the flagged backup targets list for an agent.
    Checks for existence (prunes missing) and subsumption within flagged directories (prunes redundant).
    
    Args:
        agent_id: Agent identifier.
        candidate_path: Optional path to add or remove.
        action: 'add', 'remove', or 'list'.
        
    Returns:
        Tuple of (success: bool, message: str, removals: List[Dict[str, str]], current_targets: List[Dict[str, Any]])
    """
    with _backup_targets_lock:
        if not agent_id:
            return False, "Error: Agent ID is required.", [], []

        existing = load_backup_targets(agent_id)
        removals: List[Dict[str, str]] = []
        agent_data_dir = os.path.abspath(paths.get_agent_data_dir(agent_id))
        act = (action or "add").strip().lower()

        candidate_abs = None
        candidate_portable = None
        if candidate_path:
            c_strip = candidate_path.strip()
            if not os.path.isabs(os.path.expanduser(c_strip)):
                # Relative path resolved against ~/Documents (standard agent workdir)
                candidate_abs = os.path.abspath(os.path.join(os.path.expanduser("~/Documents"), c_strip))
            else:
                candidate_abs = os.path.abspath(os.path.expanduser(c_strip))
            candidate_portable = to_portable_path(candidate_abs)

        success = True
        msg = ""

        if act == "remove":
            if not candidate_portable:
                return False, "Error: Missing path parameter to remove.", [], existing
            found = False
            new_existing = []
            for t in existing:
                if t["path"] == candidate_portable or from_portable_path(t["path"]) == candidate_abs:
                    found = True
                else:
                    new_existing.append(t)
            existing = new_existing
            msg = f"Path '{candidate_portable}' was removed from backup targets." if found else f"Path '{candidate_path}' was not found in backup targets."
            success = found

        elif act == "list":
            msg = "Backup targets verified."
            success = True

        elif act == "add":
            if not candidate_path:
                return False, "Error: Missing required path parameter for action='add'.", [], existing

            is_inside_agent_dir = (candidate_abs == agent_data_dir or candidate_abs.startswith(agent_data_dir + os.sep))
            if not is_inside_agent_dir and not is_safe_external_backup_path(candidate_abs):
                msg = f"Cannot flag '{candidate_path}': Path is protected or unsafe."
                success = False
            elif not os.path.exists(candidate_abs):
                msg = f"Cannot flag '{candidate_path}': Path does not exist on filesystem."
                success = False
            else:
                cand_type = "directory" if os.path.isdir(candidate_abs) else "file"
                existing.append({
                    "path": candidate_portable,
                    "type": cand_type,
                    "added_at": datetime.now().isoformat()
                })
                msg = f"Path '{candidate_portable}' ({cand_type}) flagged for backup."
                success = True

        else:
            return False, f"Unknown action: '{action}'. Expected 'add', 'remove', or 'list'.", [], existing

        # --- Verification and Pruning Pass ---
        # 1. Existence check ("missing")
        surviving_step1 = []
        for item in existing:
            abs_p = from_portable_path(item["path"])
            if not os.path.exists(abs_p):
                removals.append({
                    "path": item["path"],
                    "reason": "missing",
                    "detail": f"Path no longer exists on filesystem ('{abs_p}')"
                })
            else:
                item["type"] = "directory" if os.path.isdir(abs_p) else "file"
                surviving_step1.append(item)

        # 2. Self-containment check (inside agent's stateful data directory)
        surviving_step2 = []
        for item in surviving_step1:
            abs_p = from_portable_path(item["path"])
            if abs_p == agent_data_dir or abs_p.startswith(agent_data_dir + os.sep):
                removals.append({
                    "path": item["path"],
                    "reason": "redundant",
                    "detail": "Path is inside agent state directory, which is already backed up automatically"
                })
            else:
                surviving_step2.append(item)

        # 3. Deduplicate exact paths
        surviving_step3 = []
        seen_abs = set()
        for item in surviving_step2:
            abs_p = from_portable_path(item["path"])
            if abs_p in seen_abs:
                removals.append({
                    "path": item["path"],
                    "reason": "redundant",
                    "detail": "Duplicate path already flagged"
                })
            else:
                seen_abs.add(abs_p)
                surviving_step3.append(item)

        # 4. Subsumption within flagged directories ("redundant")
        # Identify directories and sort by path length ascending so parents precede children
        dir_entries = [t for t in surviving_step3 if t["type"] == "directory"]
        dir_entries.sort(key=lambda d: len(from_portable_path(d["path"])))

        redundant_dirs = set()
        for i in range(len(dir_entries)):
            parent_abs = from_portable_path(dir_entries[i]["path"])
            if dir_entries[i]["path"] in redundant_dirs:
                continue
            for j in range(i + 1, len(dir_entries)):
                child_abs = from_portable_path(dir_entries[j]["path"])
                if child_abs.startswith(parent_abs + os.sep):
                    redundant_dirs.add(dir_entries[j]["path"])
                    removals.append({
                        "path": dir_entries[j]["path"],
                        "reason": "redundant",
                        "detail": f"Subsumed by flagged parent directory '{dir_entries[i]['path']}'"
                    })

        active_dirs = [d for d in dir_entries if d["path"] not in redundant_dirs]

        final_targets = []
        for item in surviving_step3:
            if item["type"] == "directory":
                if item["path"] not in redundant_dirs:
                    final_targets.append(item)
            else:
                f_abs = from_portable_path(item["path"])
                subsumed_by = None
                for d in active_dirs:
                    d_abs = from_portable_path(d["path"])
                    if f_abs.startswith(d_abs + os.sep):
                        subsumed_by = d["path"]
                        break
                if subsumed_by:
                    removals.append({
                        "path": item["path"],
                        "reason": "redundant",
                        "detail": f"Subsumed by flagged directory '{subsumed_by}'"
                    })
                else:
                    final_targets.append(item)

        # If candidate path itself was pruned during verification, update message accordingly
        if act == "add" and candidate_portable:
            for r in removals:
                if r["path"] == candidate_portable:
                    if r["reason"] == "redundant":
                        msg = f"Path '{candidate_portable}' is redundant ({r['detail']}) and was not added."
                        success = True
                    elif r["reason"] == "missing":
                        msg = f"Path '{candidate_portable}' is missing and was not added."
                        success = False
                    break

        save_backup_targets(agent_id, final_targets)
        return success, msg, removals, final_targets


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
    EXCLUDED_EXTERNAL_DIR_NAMES = {
        "node_modules", "puppeteer_cache", ".wwebjs_cache", "__pycache__",
        ".git", ".venv", "venv", ".cache"
    }

    # Synchronize and clean backup targets before archiving
    sync_and_clean_backup_targets(agent_id, action="list")
    backup_targets = load_backup_targets(agent_id)

    eligible_internal_files = []
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

            eligible_internal_files.append(full_file_path)

    # Discover external flagged targets
    eligible_external_files: List[Tuple[str, str]] = []  # (full_path, arcname)
    external_manifest: Dict[str, Any] = {"version": 1, "targets": []}

    for idx, target in enumerate(backup_targets):
        t_path = from_portable_path(target["path"])
        t_type = target.get("type", "file")
        archive_prefix = f"_external_targets/target_{idx}"
        manifest_entry = {
            "target_id": idx,
            "archive_prefix": archive_prefix,
            "target_type": t_type,
            "portable_path": target["path"]
        }

        if t_type == "file":
            if os.path.isfile(t_path):
                basename = os.path.basename(t_path)
                manifest_entry["basename"] = basename
                arcname = f"{archive_prefix}/{basename}"
                eligible_external_files.append((t_path, arcname))
                external_manifest["targets"].append(manifest_entry)
            else:
                logging.warning(f"BackupManager: Flagged file '{t_path}' does not exist during backup, skipping.")
        elif t_type == "directory":
            if os.path.isdir(t_path):
                manifest_entry["dir_name"] = os.path.basename(t_path.rstrip("/\\"))
                for root, dirs, files in os.walk(t_path):
                    dirs[:] = [d for d in dirs if d not in EXCLUDED_EXTERNAL_DIR_NAMES]
                    for f in files:
                        if f in EXCLUDED_FILE_NAMES:
                            continue
                        fpath = os.path.join(root, f)
                        if os.path.islink(fpath) and not os.path.exists(fpath):
                            continue
                        rel = os.path.relpath(fpath, t_path)
                        arcname = f"{archive_prefix}/{rel}"
                        eligible_external_files.append((fpath, arcname))
                external_manifest["targets"].append(manifest_entry)
            else:
                logging.warning(f"BackupManager: Flagged directory '{t_path}' does not exist during backup, skipping.")

    total_files = len(eligible_internal_files) + len(eligible_external_files)
    if external_manifest["targets"]:
        total_files += 1

    # Write compressed ZIP archive with .oaa extension
    temp_backup_path = backup_path + f".tmp_{uuid.uuid4().hex[:8]}"
    try:
        with zipfile.ZipFile(temp_backup_path, 'w', compression=zipfile.ZIP_DEFLATED) as zip_file:
            cur_file_idx = 1

            # Write external manifest if targets exist
            if external_manifest["targets"]:
                zip_file.writestr("_external_manifest.json", json.dumps(external_manifest, indent=2))
                if progress_callback:
                    progress_callback("_external_manifest.json", cur_file_idx, total_files)
                cur_file_idx += 1

            # Write internal agent data files
            for full_file_path in eligible_internal_files:
                try:
                    st = os.stat(full_file_path)
                    # Skip special files like sockets or FIFOs
                    if stat.S_ISSOCK(st.st_mode) or stat.S_ISFIFO(st.st_mode):
                        logging.debug(f"BackupManager: Skipping non-regular file: {full_file_path}")
                        continue

                    arcname = os.path.relpath(full_file_path, agent_dir)
                    zip_file.write(full_file_path, arcname=arcname)

                    if progress_callback:
                        progress_callback(os.path.basename(full_file_path), cur_file_idx, total_files)
                    cur_file_idx += 1
                except (FileNotFoundError, PermissionError, OSError) as write_err:
                    logging.warning(
                        f"BackupManager: Skipping transient or unreadable file '{full_file_path}': {write_err}"
                    )

            # Write external flagged files
            for full_file_path, arcname in eligible_external_files:
                try:
                    st = os.stat(full_file_path)
                    if stat.S_ISSOCK(st.st_mode) or stat.S_ISFIFO(st.st_mode):
                        continue

                    zip_file.write(full_file_path, arcname=arcname)

                    if progress_callback:
                        progress_callback(os.path.basename(full_file_path), cur_file_idx, total_files)
                    cur_file_idx += 1
                except (FileNotFoundError, PermissionError, OSError) as write_err:
                    logging.warning(
                        f"BackupManager: Skipping transient or unreadable external file '{full_file_path}': {write_err}"
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

            # Check for _external_manifest.json
            external_manifest = None
            for entry_name in namelist:
                norm = entry_name.replace("\\", "/").strip("/")
                if norm == "_external_manifest.json" or norm.endswith("/_external_manifest.json"):
                    try:
                        with zip_file.open(entry_name) as mf:
                            external_manifest = json.load(mf)
                    except Exception:
                        pass
                    break

            ext_targets = external_manifest.get("targets", []) if external_manifest else []

            return {
                "valid": True,
                "uid": uid,
                "name": name,
                "archetype": archetype,
                "creation_date": creation_date,
                "file_count": len(namelist),
                "archive_size": os.path.getsize(backup_path),
                "settings": settings_data,
                "external_targets": ext_targets,
                "external_targets_count": len(ext_targets)
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

            # Find _external_manifest.json if present
            external_manifest = None
            for n in namelist:
                norm = n.strip("/")
                if norm == "_external_manifest.json" or norm.endswith("/_external_manifest.json"):
                    try:
                        with zip_file.open(n) as mf:
                            external_manifest = json.load(mf)
                    except Exception as me:
                        logging.warning(f"BackupManager: Could not read _external_manifest.json: {me}")
                    break

            ext_target_map = {}
            if external_manifest and "targets" in external_manifest:
                for t in external_manifest["targets"]:
                    prefix = t.get("archive_prefix", "").strip("/")
                    if prefix:
                        ext_target_map[prefix] = t

            for member in zip_file.infolist():
                norm_name = member.filename.replace("\\", "/")
                
                # Strip prefix if needed
                if settings_prefix and norm_name.startswith(settings_prefix):
                    rel_name = norm_name[len(settings_prefix):]
                else:
                    rel_name = norm_name

                if not rel_name or rel_name.endswith("/"):
                    continue

                if rel_name == "_external_manifest.json":
                    continue

                # Check if this member is an external target
                if rel_name.startswith("_external_targets/"):
                    matched_target = None
                    matched_prefix = None
                    for pfx, tgt in ext_target_map.items():
                        if rel_name == pfx or rel_name.startswith(pfx + "/"):
                            matched_target = tgt
                            matched_prefix = pfx
                            break

                    if not matched_target:
                        logging.warning(f"BackupManager: Unknown external target archive path {member.filename}")
                        continue

                    portable_dest = matched_target.get("portable_path", "")
                    dest_base = os.path.abspath(from_portable_path(portable_dest))
                    target_type = matched_target.get("target_type", "file")

                    if target_type == "directory":
                        sub_rel = rel_name[len(matched_prefix) + 1:]
                        out_path = os.path.abspath(os.path.join(dest_base, sub_rel))
                        # Prevent Zip Slip / escaping dest_base
                        if not (out_path == dest_base or out_path.startswith(dest_base + os.sep)):
                            logging.warning(f"BackupManager: Skipping unsafe path {member.filename} escaping {dest_base}")
                            continue
                    else:  # file
                        out_path = dest_base

                    # Prevent writing to disallowed system directories and sensitive user paths
                    if not is_safe_external_backup_path(out_path):
                        logging.warning(f"BackupManager: Skipping restoration to disallowed or unsafe path: {out_path}")
                        continue

                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with zip_file.open(member) as src, open(out_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                    attr = (member.external_attr >> 16) & 0xFFFF
                    if attr:
                        try:
                            os.chmod(out_path, attr)
                        except Exception as chmod_err:
                            logging.debug(f"BackupManager: Could not set permissions on {out_path}: {chmod_err}")
                    continue

                # Ensure safe target path for internal state files
                out_path = os.path.abspath(os.path.join(target_dir, rel_name))
                abs_target = os.path.abspath(target_dir)
                if not (out_path == abs_target or out_path.startswith(abs_target + os.sep)):
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

        ext_count = len(external_manifest.get("targets", [])) if external_manifest else 0
        details = {
            "agent_id": target_agent_id,
            "is_rollback": is_rollback,
            "uid": uid,
            "name": agent_name,
            "target_dir": target_dir,
            "external_targets_restored": ext_count
        }

        action_word = "rolled back and restored" if is_rollback else "restored as a new agent instance"
        msg = f"Agent '{agent_name}' ({uid}) was successfully {action_word}."
        if ext_count > 0:
            msg += f" (Restored {ext_count} external backup target(s))."
        logging.info(f"BackupManager: {msg}")
        return True, msg, details

    except Exception as e:
        logging.error(f"BackupManager: Restore failed: {e}", exc_info=True)
        return False, f"Restore failed: {str(e)}", {}
