import email
import email.utils
import logging
import re
import threading
import time
from collections import deque
from email import policy
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.email_archive import decode_mime_header

logger = logging.getLogger("core.EmailMonitor")

MAX_POLL_BATCH_SIZE = 50
MAX_SEEN_MESSAGE_IDS = 500
MAX_BACKOFF_SECONDS = 900.0
DEFAULT_CHECK_INTERVAL_SECONDS = 60.0
MIN_CHECK_INTERVAL_SECONDS = 15.0


def _parse_imap_int_response(imap: Any, code: str) -> Optional[int]:
    """Safely extracts a positive integer from an imaplib untagged response (e.g. UIDVALIDITY / UIDNEXT)."""
    try:
        if not hasattr(imap, "response"):
            return None
        _, data = imap.response(code)
        if not data:
            return None
        for item in data:
            if item is None:
                continue
            raw = item.decode("ascii", errors="ignore") if isinstance(item, bytes) else str(item)
            match = re.search(r"\d+", raw)
            if match:
                return int(match.group(0))
    except Exception:
        pass
    return None


def _parse_uid_list(search_data: Any) -> List[int]:
    """Parses space-separated IMAP UID bytes returned by imap.uid('search', ...)."""
    if not search_data or not search_data[0]:
        return []
    raw = search_data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    else:
        raw = str(raw)
    uids: List[int] = []
    for token in raw.split():
        if token.isdigit():
            uids.append(int(token))
    return uids


class EmailMonitor:
    """
    Lightweight, dormant-by-default background IMAP header monitor for an agent's email account.

    - Only connects when Email tool is enabled, agent is unpaused, valid IMAP credentials exist,
      and email auto-pulsing is configured (`pulse-all-emails` or non-empty `whitelist`).
    - Uses read-only `SELECT` and `BODY.PEEK[HEADER.FIELDS ...]` so server `\\Seen` flags
      and messages are never modified or deleted by monitoring.
    - Baselines `UIDVALIDITY` / `UIDNEXT` on start, re-enable, unpause, or account change
      so historical unread emails never trigger a flood of pulses.
    """

    def __init__(
        self,
        email_tool: Any,
        orchestrator: Any = None,
        folder: str = "INBOX",
        email_received_callback: Optional[Callable[..., None]] = None,
        min_interval_seconds: float = MIN_CHECK_INTERVAL_SECONDS,
    ):
        self.email_tool = email_tool
        self.orchestrator = orchestrator
        self.agent_id = getattr(orchestrator, "agent_id", None) if orchestrator else None
        self.folder = folder or "INBOX"
        self.email_received_callback = email_received_callback
        self.min_interval_seconds = float(min_interval_seconds)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Watermark state (in-memory; re-baselined cleanly on monitor start / account switch)
        self._account_key: Optional[Tuple[str, int, str, str]] = None
        self._initialized: bool = False
        self._uidvalidity: Optional[int] = None
        self._last_seen_uid: int = 0
        self._seen_message_ids: deque = deque(maxlen=MAX_SEEN_MESSAGE_IDS)
        self._seen_message_id_set: set = set()
        self._consecutive_errors: int = 0

    @property
    def settings_manager(self):
        if self.orchestrator and hasattr(self.orchestrator, "settings_manager"):
            return self.orchestrator.settings_manager
        return None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set())

    def _reset_watermark_locked(self) -> None:
        self._initialized = False
        self._uidvalidity = None
        self._last_seen_uid = 0
        self._seen_message_ids.clear()
        self._seen_message_id_set.clear()
        self._consecutive_errors = 0

    def _remember_message_id(self, message_id: str) -> None:
        if not message_id:
            return
        if message_id in self._seen_message_id_set:
            return
        if len(self._seen_message_ids) == self._seen_message_ids.maxlen:
            evicted = self._seen_message_ids.popleft()
            self._seen_message_id_set.discard(evicted)
        self._seen_message_ids.append(message_id)
        self._seen_message_id_set.add(message_id)

    def get_check_interval_seconds(self) -> float:
        sm = self.settings_manager
        raw_val: Any = DEFAULT_CHECK_INTERVAL_SECONDS
        if sm and hasattr(sm, "get"):
            email_cfg = sm.get("core.auto-pulse.email", {}) or {}
            if isinstance(email_cfg, dict) and email_cfg.get("check-interval-seconds") is not None:
                raw_val = email_cfg.get("check-interval-seconds")
        try:
            interval = float(raw_val)
        except (TypeError, ValueError):
            interval = DEFAULT_CHECK_INTERVAL_SECONDS
        return max(self.min_interval_seconds, min(interval, 3600.0))

    def should_monitor(self) -> bool:
        """Returns True iff background IMAP polling should be active right now."""
        if self.orchestrator and getattr(self.orchestrator, "is_paused", False):
            return False

        sm = self.settings_manager
        if not sm or not hasattr(sm, "get"):
            return False

        if not sm.get("core.tools.email", False):
            return False

        email_pulse_cfg = sm.get("core.auto-pulse.email", {}) or {}
        if not isinstance(email_pulse_cfg, dict):
            return False

        pulse_all = bool(email_pulse_cfg.get("pulse-all-emails", False))
        whitelist = [
            str(entry).strip()
            for entry in (email_pulse_cfg.get("whitelist") or [])
            if str(entry or "").strip()
        ]
        if not pulse_all and not whitelist:
            return False

        try:
            cfg = self.email_tool._get_settings()
        except Exception:
            return False

        if not str(cfg.get("imap_host") or "").strip():
            return False
        if not str(cfg.get("email_address") or "").strip():
            return False

        if str(cfg.get("auth_type") or "password").lower() == "oauth2":
            if not (
                str(cfg.get("oauth_client_id") or "").strip()
                and str(cfg.get("oauth_client_secret") or "").strip()
                and str(cfg.get("oauth_refresh_token") or "").strip()
            ):
                return False
        else:
            if not str(cfg.get("password") or "").strip():
                return False

        return True

    def start(self) -> bool:
        """Starts the background monitor thread if eligible (idempotent)."""
        with self._lock:
            if not self.should_monitor():
                return False
            if self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set():
                self._wakeup_event.set()
                return True

            self._stop_event.clear()
            self._wakeup_event.clear()
            self._reset_watermark_locked()
            self._account_key = None

            self._thread = threading.Thread(
                target=self._monitor_loop,
                name=f"EmailMonitor-{self.agent_id or 'default'}",
                daemon=True,
            )
            self._thread.start()
            logger.info(f"EmailMonitor: Started background IMAP monitor for folder '{self.folder}'.")
            return True

    def stop(self, timeout: float = 2.0) -> None:
        """Stops the background monitor thread cleanly and resets watermark state."""
        thread_to_join = None
        with self._lock:
            self._stop_event.set()
            self._wakeup_event.set()
            thread_to_join = self._thread
            self._thread = None
            self._reset_watermark_locked()
            self._account_key = None

        if thread_to_join is not None and thread_to_join.is_alive() and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=timeout)
            logger.info("EmailMonitor: Stopped background IMAP monitor.")

    def sync_state(self) -> bool:
        """
        Reconciles live thread state with current settings and pause state.
        Starts monitoring if newly eligible, wakes/reconfigures if already running,
        or stops immediately if disabled/paused/unconfigured.
        """
        if self.should_monitor():
            with self._lock:
                if self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set():
                    self._consecutive_errors = 0
                    self._wakeup_event.set()
                    return True
            return self.start()
        else:
            self.stop()
            return False

    def _compute_next_wait(self) -> float:
        base = self.get_check_interval_seconds()
        if self._consecutive_errors <= 0:
            return base
        # Exponential backoff capped at 15 minutes
        backoff = base * (2 ** min(self._consecutive_errors, 6))
        return min(backoff, MAX_BACKOFF_SECONDS)

    def _monitor_loop(self) -> None:
        try:
            from core.logger_config import agent_id_var
            agent_id_var.set(self.agent_id)
        except Exception:
            pass

        while not self._stop_event.is_set():
            if not self.should_monitor():
                break

            try:
                self.poll_once()
            except Exception as e:
                self._consecutive_errors += 1
                logger.warning(f"EmailMonitor: Unexpected error in poll loop: {e}", exc_info=True)

            if self._stop_event.is_set():
                break

            wait_seconds = self._compute_next_wait()
            # Interruptible sleep: wakes promptly on stop() or sync_state()
            self._wakeup_event.wait(timeout=wait_seconds)
            self._wakeup_event.clear()

    def _close_imap_cleanly(self, imap: Any) -> None:
        if not imap:
            return
        try:
            imap.logout()
        except Exception:
            try:
                self.email_tool._safe_close_imap(imap)
            except Exception:
                pass

    def poll_once(self) -> List[Dict[str, str]]:
        """
        Executes a single read-only IMAP check cycle.
        Returns the list of newly discovered qualifying candidate email dicts that were dispatched
        to `email_received_callback` (empty on baseline pass or when no new unseen emails exist).
        """
        if not self.should_monitor():
            return []

        cfg = self.email_tool._get_settings()
        account_key = (
            str(cfg.get("imap_host") or "").strip().lower(),
            int(cfg.get("imap_port") or 993),
            str(cfg.get("email_address") or "").strip().lower(),
            self.folder,
        )

        with self._lock:
            if self._account_key != account_key:
                self._reset_watermark_locked()
                self._account_key = account_key

        ok, imap_or_err = self.email_tool._get_imap_connection()
        if not ok:
            self._consecutive_errors += 1
            if self._consecutive_errors == 1:
                logger.warning(f"EmailMonitor: IMAP poll connection failed: {imap_or_err}")
            else:
                logger.debug(
                    f"EmailMonitor: IMAP poll connection failed (attempt #{self._consecutive_errors}): {imap_or_err}"
                )
            return []

        imap = imap_or_err
        candidates: List[Dict[str, str]] = []
        try:
            status, _ = imap.select(f'"{self.folder}"', readonly=True)
            if status != "OK":
                self._consecutive_errors += 1
                logger.warning(f"EmailMonitor: Could not select folder '{self.folder}' (status={status}).")
                self._close_imap_cleanly(imap)
                return []

            uidvalidity = _parse_imap_int_response(imap, "UIDVALIDITY")
            uidnext = _parse_imap_int_response(imap, "UIDNEXT")

            with self._lock:
                # Re-baseline if server invalidated UIDs (RFC 3501 UIDVALIDITY change)
                if (
                    self._initialized
                    and self._uidvalidity is not None
                    and uidvalidity is not None
                    and uidvalidity != self._uidvalidity
                ):
                    logger.info(
                        f"EmailMonitor: UIDVALIDITY changed ({self._uidvalidity} -> {uidvalidity}) for '{self.folder}'; re-baselining."
                    )
                    self._reset_watermark_locked()
                    self._account_key = account_key

                needs_baseline = not self._initialized

            if needs_baseline:
                if uidnext is not None and uidnext > 0:
                    baseline_uid = uidnext - 1
                else:
                    s_status, s_data = imap.uid("search", None, "ALL")
                    all_uids = _parse_uid_list(s_data) if s_status == "OK" else []
                    baseline_uid = max(all_uids) if all_uids else 0

                with self._lock:
                    self._uidvalidity = uidvalidity
                    self._last_seen_uid = baseline_uid
                    self._initialized = True
                    self._consecutive_errors = 0

                logger.info(
                    f"EmailMonitor: Initialized baseline for '{account_key[2]}' "
                    f"(folder='{self.folder}', uidvalidity={uidvalidity}, last_seen_uid={baseline_uid})."
                )
                self._close_imap_cleanly(imap)
                return []

            with self._lock:
                last_seen_uid = self._last_seen_uid
                if uidvalidity is not None and self._uidvalidity is None:
                    self._uidvalidity = uidvalidity

            # Fast-path: UIDNEXT proves no new messages arrived since last poll
            if uidnext is not None and uidnext > 0 and (uidnext - 1) <= last_seen_uid:
                self._consecutive_errors = 0
                self._close_imap_cleanly(imap)
                return []

            if last_seen_uid > 0:
                s_status, s_data = imap.uid("search", None, "UNSEEN", "UID", f"{last_seen_uid + 1}:*")
            else:
                s_status, s_data = imap.uid("search", None, "UNSEEN")

            if s_status != "OK":
                self._consecutive_errors += 1
                logger.debug(f"EmailMonitor: IMAP UID SEARCH failed with status={s_status}.")
                self._close_imap_cleanly(imap)
                return []

            # Strictly filter > last_seen_uid because RFC 3501 UID <N>:* always includes max UID
            raw_uids = _parse_uid_list(s_data)
            new_uids = sorted(u for u in raw_uids if u > last_seen_uid)

            if not new_uids:
                with self._lock:
                    if uidnext is not None and uidnext > 0:
                        self._last_seen_uid = max(self._last_seen_uid, uidnext - 1)
                    self._consecutive_errors = 0
                self._close_imap_cleanly(imap)
                return []

            highest_uid = new_uids[-1]
            uids_to_inspect = new_uids[-MAX_POLL_BATCH_SIZE:]
            archive_local = bool(cfg.get("archive_local", True))
            archive = getattr(self.email_tool, "archive", None) if archive_local else None

            for uid_int in uids_to_inspect:
                uid_str = str(uid_int)
                res, msg_data = imap.uid(
                    "fetch",
                    uid_str.encode("ascii"),
                    "(BODY.PEEK[HEADER.FIELDS (FROM MESSAGE-ID DATE)] FLAGS)",
                )
                if res != "OK" or not msg_data:
                    continue

                headers_raw = b""
                flags_str = ""
                for part in msg_data:
                    if isinstance(part, tuple):
                        meta = part[0]
                        flags_str += meta.decode("ascii", errors="ignore") if isinstance(meta, bytes) else str(meta)
                        if isinstance(part[1], bytes):
                            headers_raw = part[1]
                    elif isinstance(part, bytes):
                        flags_str += part.decode("ascii", errors="ignore")

                # Double-check message is still unseen and not marked deleted (IMAP flags are case-insensitive)
                flags_lower = flags_str.lower()
                if "\\seen" in flags_lower or "\\deleted" in flags_lower:
                    continue

                if not headers_raw:
                    continue

                try:
                    parsed_msg = email.message_from_bytes(headers_raw, policy=policy.default)
                    from_header = decode_mime_header(parsed_msg.get("From", ""))
                    sender_name, sender_email = email.utils.parseaddr(from_header)
                    sender_email = (sender_email or "").strip().lower()
                    sender_name = (sender_name or "").strip()
                    message_id = str(parsed_msg.get("Message-ID", "") or "").strip()
                except Exception as parse_err:
                    logger.warning(
                        f"EmailMonitor: Skipping malformed email headers for UID {uid_str}: "
                        f"{type(parse_err).__name__}: {parse_err}"
                    )
                    continue

                if not sender_email:
                    continue

                with self._lock:
                    if message_id and message_id in self._seen_message_id_set:
                        continue

                # Skip emails already pulled into local EmailArchive (e.g. Gmail keeping server copies)
                if archive is not None and hasattr(archive, "find_email_read_status"):
                    try:
                        archived_status = archive.find_email_read_status(
                            folder=self.folder,
                            message_id=message_id,
                            server_uid=uid_str,
                        )
                        if archived_status is not None:
                            with self._lock:
                                self._remember_message_id(message_id)
                            continue
                    except Exception as e:
                        logger.debug(f"EmailMonitor: Local archive dedup lookup failed for UID {uid_str}: {e}")

                with self._lock:
                    self._remember_message_id(message_id)

                candidates.append({
                    "uid": uid_str,
                    "message_id": message_id,
                    "sender_email": sender_email,
                    "sender_name": sender_name,
                    "from_raw": from_header,
                    "folder": self.folder,
                })

            with self._lock:
                self._last_seen_uid = max(self._last_seen_uid, highest_uid)
                self._consecutive_errors = 0

            self._close_imap_cleanly(imap)

        except Exception as e:
            self._consecutive_errors += 1
            logger.warning(f"EmailMonitor: Error during IMAP poll: {type(e).__name__}: {e}")
            self._close_imap_cleanly(imap)
            return []

        # Dispatch callbacks only AFTER IMAP connection is closed
        callback = self.email_received_callback
        if callable(callback):
            for evt in candidates:
                try:
                    callback(
                        sender_email=evt["sender_email"],
                        sender_name=evt["sender_name"],
                        uid=evt["uid"],
                        message_id=evt["message_id"],
                        folder=evt["folder"],
                    )
                except Exception as e:
                    logger.error(f"EmailMonitor: Error in email_received_callback: {e}", exc_info=True)

        return candidates
