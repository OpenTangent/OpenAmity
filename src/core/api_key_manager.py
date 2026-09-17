import os
import json
import uuid
import secrets
import hashlib
import hmac
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from config import paths
from core.file_utils import atomic_json_write

logger = logging.getLogger("core.ApiKeyManager")


class ApiKeyManager:
    """
    Manages isolated API keys per agent for third-party service integration.
    Keys are generated with high entropy, hashed with SHA-256, and stored atomically
    in the agent's state directory. Plaintext keys are never stored on disk.
    """

    def __init__(self, agent_id: str):
        if not agent_id:
            raise ValueError("agent_id is required for ApiKeyManager")
        self.agent_id = agent_id
        self.keys_file = os.path.join(paths.get_base_dir_for(agent_id), "api_keys.json")
        self._lock = threading.RLock()

    def _load_keys(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.keys_file):
                return []
            try:
                with open(self.keys_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "keys" in data and isinstance(data["keys"], list):
                        return data["keys"]
                    elif isinstance(data, list):
                        return data
                    return []
            except Exception as e:
                logger.error(f"Error loading {self.keys_file}: {e}", exc_info=True)
                return []

    def _save_keys(self, keys: List[Dict[str, Any]]) -> None:
        with self._lock:
            atomic_json_write(self.keys_file, {"keys": keys}, lock=self._lock)

    def generate_key(
        self,
        name: str,
        scopes: Optional[List[str]] = None,
        expires_in_days: Optional[int] = None
    ) -> Tuple[Dict[str, Any], str]:
        """
        Generates a new secure API key for the agent.
        Returns: (key_record, raw_key_string)
        Note: The raw key is returned ONLY once during generation and is never stored on disk.
        """
        clean_name = (name or "default").strip()
        assigned_scopes = list(scopes) if scopes else ["pulse:inject"]
        
        # High entropy token: oa_sec_ followed by 32 random bytes urlsafe
        raw_token = secrets.token_urlsafe(32)
        raw_key = f"oa_sec_{raw_token}"
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        key_prefix = f"{raw_key[:10]}...{raw_key[-4:]}"
        key_id = f"key_{uuid.uuid4().hex[:8]}"

        created_at = datetime.now()
        expires_at = None
        if expires_in_days and expires_in_days > 0:
            expires_at = (created_at + timedelta(days=expires_in_days)).isoformat()

        record = {
            "key_id": key_id,
            "name": clean_name,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "scopes": assigned_scopes,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at,
            "revoked": False,
            "last_used_at": None
        }

        with self._lock:
            keys = self._load_keys()
            keys.append(record)
            self._save_keys(keys)

        logger.info(f"Generated new API key '{clean_name}' ({key_id}) for agent {self.agent_id}")
        return record, raw_key

    def list_keys(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        """Returns metadata for all keys associated with this agent."""
        with self._lock:
            keys = self._load_keys()
            result = []
            for k in keys:
                if not include_revoked and k.get("revoked", False):
                    continue
                sanitized = dict(k)
                sanitized.pop("key_hash", None)
                result.append(sanitized)
            return result

    def revoke_key(self, key_id: str) -> bool:
        """Revokes a key by its ID or prefix match."""
        if not key_id:
            return False
        clean_target = key_id.strip()

        with self._lock:
            keys = self._load_keys()
            modified = False
            for k in keys:
                if k.get("key_id") == clean_target or k.get("key_prefix") == clean_target:
                    k["revoked"] = True
                    modified = True
                    logger.info(f"Revoked API key {k.get('key_id')} for agent {self.agent_id}")
                    break
            if modified:
                self._save_keys(keys)
                return True
            return False

    def verify_key(
        self,
        raw_key: str,
        required_scope: Optional[str] = "pulse:inject"
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validates an incoming API key against stored records in constant time.
        Returns: (is_valid: bool, error_code: Optional[str], key_record: Optional[Dict])
        """
        if not raw_key or not isinstance(raw_key, str):
            return False, "MISSING_API_KEY", None

        candidate_hash = hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()
        now = datetime.now()

        with self._lock:
            keys = self._load_keys()
            matched_record = None

            for k in keys:
                stored_hash = k.get("key_hash", "")
                if hmac.compare_digest(stored_hash, candidate_hash):
                    matched_record = k
                    break

            if not matched_record:
                return False, "INVALID_API_KEY", None

            if matched_record.get("revoked", False):
                return False, "INVALID_API_KEY", None

            expires_at_str = matched_record.get("expires_at")
            if expires_at_str:
                try:
                    exp_dt = datetime.fromisoformat(expires_at_str)
                    if exp_dt <= now:
                        return False, "INVALID_API_KEY", None
                except ValueError:
                    return False, "INVALID_API_KEY", None

            if required_scope:
                scopes = matched_record.get("scopes", [])
                if required_scope not in scopes and "*" not in scopes:
                    return False, "FORBIDDEN_SCOPE", None

            matched_record["last_used_at"] = now.isoformat()
            self._save_keys(keys)

            sanitized = dict(matched_record)
            sanitized.pop("key_hash", None)
            return True, None, sanitized
