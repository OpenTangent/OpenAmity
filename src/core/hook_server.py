import os
import json
import time
import re
import sqlite3
import logging
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from typing import Optional, Dict, Any

from config import paths
from core.version import __version__
from core.uid_generator import is_valid_agent_uid
from core.api_key_manager import ApiKeyManager

logger = logging.getLogger("core.HookServer")

PULSE_AGENT_PATH_REGEX = re.compile(r"^/api/v1/agents/([^/]+)/pulses/?$")
PULSE_ALIAS_PATH_REGEX = re.compile(r"^/api/v1/pulses/([^/]+)/?$")


class HookRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Open Amity external pulse hooks and API endpoints."""

    server_version = f"OpenAmity/{__version__}"

    def log_message(self, format, *args):
        # Route standard HTTP server logs to logger
        logger.debug(f"HookServer HTTP: {format % args}")

    def _send_json(self, data: Dict[str, Any], status_code: int = 200, headers: Optional[Dict[str, str]] = None):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if headers:
            for k, v in headers.items():
                self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retry_after: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        err_payload = {
            "success": False,
            "error": {
                "code": code,
                "message": message
            }
        }
        if retry_after is not None:
            err_payload["error"]["retry_after"] = retry_after
        if details:
            err_payload["error"]["details"] = details

        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)

        self._send_json(err_payload, status_code=status_code, headers=headers)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ["/api/v1/health", "/health"]:
            self._handle_health()
            return
        self._send_error("NOT_FOUND", f"Endpoint '{self.path}' not found.", status_code=404)

    def do_POST(self):
        clean_path = self.path.split("?")[0]
        m_agent = PULSE_AGENT_PATH_REGEX.match(clean_path)
        m_alias = PULSE_ALIAS_PATH_REGEX.match(clean_path)

        agent_uid_candidate = None
        if m_agent:
            agent_uid_candidate = m_agent.group(1)
        elif m_alias:
            agent_uid_candidate = m_alias.group(1)

        if agent_uid_candidate:
            self._handle_pulse_injection(agent_uid_candidate)
            return

        self._send_error("NOT_FOUND", f"Endpoint '{self.path}' not found.", status_code=404)

    def _handle_health(self):
        self._send_json({
            "success": True,
            "data": {
                "status": "online",
                "version": __version__,
                "timestamp": datetime.now().isoformat()
            }
        })

    def _get_api_key(self) -> Optional[str]:
        # 1. Check Authorization: Bearer <key>
        auth_header = self.headers.get("Authorization", "").strip()
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1]
            elif len(parts) == 1 and parts[0].startswith("oa_sec_"):
                return parts[0]

        # 2. Check X-API-Key
        x_api_key = self.headers.get("X-API-Key", "").strip()
        if x_api_key:
            return x_api_key

        return None

    def _handle_pulse_injection(self, raw_uid: str):
        agent_manager = getattr(self.server, "agent_manager", None)
        if not agent_manager:
            self._send_error("INTERNAL_SERVER_ERROR", "AgentManager service unavailable.", status_code=500)
            return

        clean_uid = raw_uid.strip().upper()
        # 1. Validate UID format
        if not is_valid_agent_uid(clean_uid):
            self._send_error(
                "INVALID_UID",
                f"Agent UID '{raw_uid}' does not match valid Crockford Base32 format (+OA-XXXX-XXXX).",
                status_code=400
            )
            return

        # 2. Resolve Agent ID
        agent_id = agent_manager.get_agent_id_by_uid(clean_uid)
        if not agent_id:
            self._send_error(
                "AGENT_NOT_FOUND",
                f"No agent found with UID '{clean_uid}'.",
                status_code=404
            )
            return

        # 3. Authentication
        api_key = self._get_api_key()
        if not api_key:
            self._send_error(
                "MISSING_API_KEY",
                "Missing API key. Provide via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header.",
                status_code=401
            )
            return

        key_mgr = ApiKeyManager(agent_id=agent_id)
        is_valid, err_code, key_record = key_mgr.verify_key(api_key, required_scope="pulse:inject")
        if not is_valid:
            if err_code == "FORBIDDEN_SCOPE":
                self._send_error(
                    "FORBIDDEN_SCOPE",
                    "API key lacks required scope 'pulse:inject'.",
                    status_code=403
                )
            else:
                self._send_error(
                    "INVALID_API_KEY",
                    "Provided API key is invalid, revoked, or expired.",
                    status_code=401
                )
            return

        # 4. Agent Paused State Check
        orch = agent_manager.get_orchestrator(agent_id)
        if orch and getattr(orch, "is_paused", False):
            self._send_error(
                "AGENT_PAUSED",
                f"Agent '{clean_uid}' is currently paused/offline. Cannot inject pulse.",
                status_code=409
            )
            return

        # 5. Payload Read & Validation
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0

        if content_length <= 0:
            self._send_error("INVALID_PAYLOAD", "Request body is empty.", status_code=400)
            return

        if content_length > 131072:  # 128 KB
            self._send_error("PAYLOAD_TOO_LARGE", "Request payload exceeds maximum size of 128KB.", status_code=413)
            return

        try:
            raw_body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw_body)
        except Exception as e:
            self._send_error("INVALID_PAYLOAD", f"Malformed JSON payload: {e}", status_code=400)
            return

        if not isinstance(payload, dict):
            self._send_error("INVALID_PAYLOAD", "JSON payload must be an object.", status_code=400)
            return

        title = payload.get("title")
        if not title or not isinstance(title, str) or not title.strip() or len(title.strip()) > 256:
            self._send_error("INVALID_PAYLOAD", "Field 'title' is required and must be between 1 and 256 characters.", status_code=400)
            return
        title = title.strip()

        context = payload.get("context")
        if not context or not isinstance(context, str) or not context.strip() or len(context.strip()) > 65536:
            self._send_error("INVALID_PAYLOAD", "Field 'context' is required and must be between 1 and 65,536 characters.", status_code=400)
            return
        context = context.strip()

        now = datetime.now()
        sched_str = payload.get("scheduled_time")
        if sched_str:
            if not isinstance(sched_str, str):
                self._send_error("INVALID_PAYLOAD", "Field 'scheduled_time' must be an ISO-8601 string.", status_code=400)
                return
            try:
                target_sched = datetime.fromisoformat(sched_str.strip())
                if target_sched.tzinfo is not None:
                    target_sched = target_sched.astimezone().replace(tzinfo=None)
            except ValueError:
                self._send_error("INVALID_PAYLOAD", f"Invalid ISO-8601 timestamp for 'scheduled_time': {sched_str}", status_code=400)
                return
        else:
            target_sched = now

        recurrence = payload.get("recurrence", "none")
        if not isinstance(recurrence, str) or recurrence.strip().lower() not in ["none", "daily", "weekly", "monthly"]:
            self._send_error("INVALID_PAYLOAD", "Field 'recurrence' must be one of 'none', 'daily', 'weekly', or 'monthly'.", status_code=400)
            return
        recurrence = recurrence.strip().lower()

        # 6. Rate Limit Rule A: Injection Cooldown (>= 60 seconds between pulse injections for this agent)
        with self.server.rate_limit_lock:
            last_injection = self.server.last_injections.get(agent_id, 0.0)
            elapsed = time.time() - last_injection
            if elapsed < 60.0:
                retry_after = int(60.0 - elapsed) + 1
                self._send_error(
                    "RATE_LIMITED",
                    f"Pulse injection rate limit exceeded. You must wait {retry_after}s before injecting another pulse for this agent.",
                    status_code=429,
                    retry_after=retry_after
                )
                return

        # 7. Rate Limit Rule B: Database Anti-Collision Guard (+/- 60 seconds from existing scheduled pulse)
        db_path = os.path.join(paths.get_base_dir_for(agent_id), "pulses.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                try:
                    c = conn.cursor()
                    c.execute("SELECT id, title, scheduled_time FROM pulses WHERE status = 'pending' OR recurrence != 'none'")
                    existing_pulses = c.fetchall()
                    for p_id, p_title, p_time_str in existing_pulses:
                        try:
                            p_time = datetime.fromisoformat(p_time_str)
                            if p_time.tzinfo is not None:
                                p_time = p_time.astimezone().replace(tzinfo=None)
                            diff_sec = abs((target_sched - p_time).total_seconds())
                            if diff_sec < 60.0:
                                conn.close()
                                self._send_error(
                                    "PULSE_COLLISION",
                                    f"A pulse is already scheduled within 1 minute of requested time (Pulse ID {p_id}: '{p_title}' at {p_time_str}).",
                                    status_code=409,
                                    details={
                                        "colliding_pulse_id": p_id,
                                        "colliding_pulse_title": p_title,
                                        "colliding_pulse_scheduled_time": p_time_str
                                    }
                                )
                                return
                        except Exception:
                            continue
                finally:
                    conn.close()
            except Exception as e:
                logger.error(f"Error checking database collision in {db_path}: {e}", exc_info=True)

        # 8. Commit to Database
        try:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            conn = sqlite3.connect(db_path)
            try:
                c = conn.cursor()
                c.execute('''
                    INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (title, context, target_sched.isoformat(), recurrence, "pending", 0, now.isoformat(), "external_hook"))
                conn.commit()
                pulse_id = c.lastrowid
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to insert pulse into {db_path}: {e}", exc_info=True)
            self._send_error("INTERNAL_SERVER_ERROR", f"Database insert failed: {e}", status_code=500)
            return

        # Record successful injection timestamp for rate limit cooldown
        with self.server.rate_limit_lock:
            self.server.last_injections[agent_id] = time.time()

        # 9. Trigger Pulse Engine Immediate Evaluation
        if orch and hasattr(orch, "pulse_engine") and orch.pulse_engine:
            try:
                orch.pulse_engine.notify_external_pulse()
            except Exception as e:
                logger.debug(f"Error triggering pulse engine wakeup: {e}")

        logger.info(f"Injected pulse {pulse_id} ('{title}') for agent {clean_uid} via HookServer")

        self._send_json({
            "success": True,
            "data": {
                "pulse_id": pulse_id,
                "agent_uid": clean_uid,
                "title": title,
                "scheduled_time": target_sched.isoformat(),
                "recurrence": recurrence,
                "status": "pending",
                "created_at": now.isoformat()
            }
        }, status_code=201)


class HookServer:
    """
    Lightweight, thread-safe HTTP gateway for external integrations and pulse hooks.
    """

    def __init__(self, agent_manager, host: str = "127.0.0.1", port: int = 7965):
        self.agent_manager = agent_manager
        self.host = host
        self.port = int(port)
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.rate_limit_lock = threading.Lock()
        self.last_injections: Dict[str, float] = {}

    def start(self) -> bool:
        if self.is_running:
            return True

        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), HookRequestHandler)
            self.httpd.agent_manager = self.agent_manager
            self.httpd.rate_limit_lock = self.rate_limit_lock
            self.httpd.last_injections = self.last_injections

            self.is_running = True
            self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.server_thread.start()
            logger.info(f"HookServer: Started listening on http://{self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"HookServer: Failed to bind to http://{self.host}:{self.port}: {e}", exc_info=True)
            self.is_running = False
            self.httpd = None
            return False

    def stop(self):
        if not self.is_running:
            return

        logger.info("HookServer: Stopping server...")
        self.is_running = False
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception as e:
                logger.debug(f"HookServer: Error during server shutdown: {e}")
            self.httpd = None
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
        logger.info("HookServer: Server stopped.")
