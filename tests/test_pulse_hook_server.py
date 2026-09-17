import os
import sys
import json
import time
import socket
import sqlite3
import tempfile
import shutil
import pytest
import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from core.hook_server import HookServer
from core.api_key_manager import ApiKeyManager
from core.uid_generator import generate_agent_uid


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_amity_env():
    temp_dir = tempfile.mkdtemp(prefix="openamity_hook_test_")
    agent_id = "agent_test_uuid_123"
    agent_uid = generate_agent_uid()
    agent_dir = os.path.join(temp_dir, "agents", agent_id)
    os.makedirs(agent_dir, exist_ok=True)

    # Pre-seed pulses.db
    db_path = os.path.join(agent_dir, "pulses.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pulses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            context TEXT,
            scheduled_time TEXT,
            recurrence TEXT,
            status TEXT,
            has_run BOOLEAN DEFAULT 0,
            created_at TEXT,
            pulse_type TEXT DEFAULT 'standard'
        )
    """)
    conn.commit()
    conn.close()

    # Pre-seed API key
    with patch("config.paths.get_base_dir_for", return_value=agent_dir),          patch("config.paths.get_agent_data_dir", return_value=agent_dir):
        key_mgr = ApiKeyManager(agent_id=agent_id)
        _, raw_key = key_mgr.generate_key(name="TestKey", scopes=["pulse:inject"])
        _, read_only_key = key_mgr.generate_key(name="ReadOnlyKey", scopes=["status:read"])

    # Mock AgentManager and Orchestrator
    agent_manager = MagicMock()
    orchestrator = MagicMock()
    orchestrator.agent_id = agent_id
    orchestrator.is_paused = False
    orchestrator.pulse_engine = MagicMock()

    agent_manager.get_agent_id_by_uid.side_effect = lambda uid: agent_id if uid.upper() == agent_uid.upper() else None
    agent_manager.get_orchestrator.side_effect = lambda aid: orchestrator if aid == agent_id else None

    free_port = find_free_port()

    with patch("config.paths.get_base_dir_for", return_value=agent_dir),          patch("config.paths.get_agent_data_dir", return_value=agent_dir):
        server = HookServer(agent_manager=agent_manager, host="127.0.0.1", port=free_port)
        started = server.start()
        assert started is True
        # Give server thread a moment to start
        time.sleep(0.05)

        yield {
            "server": server,
            "port": free_port,
            "agent_id": agent_id,
            "agent_uid": agent_uid,
            "agent_dir": agent_dir,
            "db_path": db_path,
            "raw_key": raw_key,
            "read_only_key": read_only_key,
            "agent_manager": agent_manager,
            "orchestrator": orchestrator
        }

        server.stop()

    shutil.rmtree(temp_dir, ignore_errors=True)


def request_json(url, method="GET", headers=None, data=None):
    req_headers = headers.copy() if headers else {}
    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        if "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            resp_headers = resp.headers
            resp_data = json.loads(resp.read().decode("utf-8"))
            return status, resp_data, resp_headers
    except urllib.error.HTTPError as e:
        status = e.code
        resp_headers = e.headers
        try:
            resp_data = json.loads(e.read().decode("utf-8"))
        except Exception:
            resp_data = None
        return status, resp_data, resp_headers


def test_health_endpoint(mock_amity_env):
    port = mock_amity_env["port"]
    url = f"http://127.0.0.1:{port}/api/v1/health"
    status, data, _ = request_json(url)
    assert status == 200
    assert data["success"] is True
    assert data["data"]["status"] == "online"
    assert "version" in data["data"]


def test_successful_pulse_injection(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    key = mock_amity_env["raw_key"]
    orch = mock_amity_env["orchestrator"]

    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"
    payload = {
        "title": "Security Perimeter Triggered",
        "context": "Sensor 4 front gate triggered.",
        "recurrence": "none"
    }
    headers = {"Authorization": f"Bearer {key}"}

    status, data, _ = request_json(url, method="POST", headers=headers, data=payload)
    assert status == 201
    assert data["success"] is True
    assert data["data"]["title"] == "Security Perimeter Triggered"
    assert data["data"]["agent_uid"] == agent_uid
    assert data["data"]["status"] == "pending"
    assert "pulse_id" in data["data"]

    # Verify immediate evaluation notice
    orch.pulse_engine.notify_external_pulse.assert_called_once()

    # Verify database insertion
    conn = sqlite3.connect(mock_amity_env["db_path"])
    c = conn.cursor()
    c.execute("SELECT title, context, pulse_type FROM pulses WHERE id = ?", (data["data"]["pulse_id"],))
    row = c.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "Security Perimeter Triggered"
    assert row[1] == "Sensor 4 front gate triggered."
    assert row[2] == "external_hook"


def test_alias_route_and_x_api_key_header(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    key = mock_amity_env["raw_key"]

    # Test alias route /api/v1/pulses/{uid} and X-API-Key header
    url = f"http://127.0.0.1:{port}/api/v1/pulses/{agent_uid}"
    payload = {
        "title": "Daily Status Ping",
        "context": "Run system health review.",
        "scheduled_time": (datetime.now() + timedelta(hours=2)).isoformat(),
        "recurrence": "daily"
    }
    headers = {"X-API-Key": key}

    status, data, _ = request_json(url, method="POST", headers=headers, data=payload)
    assert status == 201
    assert data["success"] is True
    assert data["data"]["recurrence"] == "daily"


def test_missing_and_invalid_api_key(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"
    payload = {"title": "Test", "context": "Context"}

    # 1. Missing key
    status, data, _ = request_json(url, method="POST", data=payload)
    assert status == 401
    assert data["error"]["code"] == "MISSING_API_KEY"

    # 2. Invalid key
    headers = {"Authorization": "Bearer oa_sec_completely_wrong_key"}
    status2, data2, _ = request_json(url, method="POST", headers=headers, data=payload)
    assert status2 == 401
    assert data2["error"]["code"] == "INVALID_API_KEY"


def test_forbidden_scope(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    read_only_key = mock_amity_env["read_only_key"]

    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"
    payload = {"title": "Test", "context": "Context"}
    headers = {"Authorization": f"Bearer {read_only_key}"}

    status, data, _ = request_json(url, method="POST", headers=headers, data=payload)
    assert status == 403
    assert data["error"]["code"] == "FORBIDDEN_SCOPE"


def test_invalid_uid_and_agent_not_found(mock_amity_env):
    port = mock_amity_env["port"]
    key = mock_amity_env["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}
    payload = {"title": "Test", "context": "Context"}

    # 1. Bad UID syntax
    url_bad_uid = f"http://127.0.0.1:{port}/api/v1/agents/not-a-valid-uid/pulses"
    status, data, _ = request_json(url_bad_uid, method="POST", headers=headers, data=payload)
    assert status == 400
    assert data["error"]["code"] == "INVALID_UID"

    # 2. Non-existent UID (valid Crockford format but unknown agent)
    url_unknown_uid = f"http://127.0.0.1:{port}/api/v1/agents/+OA-0000-0000/pulses"
    status2, data2, _ = request_json(url_unknown_uid, method="POST", headers=headers, data=payload)
    assert status2 == 404
    assert data2["error"]["code"] == "AGENT_NOT_FOUND"


def test_paused_agent_rejected(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    key = mock_amity_env["raw_key"]
    orch = mock_amity_env["orchestrator"]

    # Pause agent
    orch.is_paused = True

    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"
    payload = {"title": "Test", "context": "Context"}
    headers = {"Authorization": f"Bearer {key}"}

    status, data, _ = request_json(url, method="POST", headers=headers, data=payload)
    assert status == 409
    assert data["error"]["code"] == "AGENT_PAUSED"


def test_payload_validation(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    key = mock_amity_env["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}
    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"

    # Missing title
    status, data, _ = request_json(url, method="POST", headers=headers, data={"context": "Context"})
    assert status == 400
    assert data["error"]["code"] == "INVALID_PAYLOAD"

    # Missing context
    status2, data2, _ = request_json(url, method="POST", headers=headers, data={"title": "Title"})
    assert status2 == 400
    assert data2["error"]["code"] == "INVALID_PAYLOAD"

    # Bad ISO timestamp
    bad_time_payload = {"title": "Title", "context": "Context", "scheduled_time": "not-a-timestamp"}
    status3, data3, _ = request_json(url, method="POST", headers=headers, data=bad_time_payload)
    assert status3 == 400
    assert data3["error"]["code"] == "INVALID_PAYLOAD"

    # Bad recurrence
    bad_rec_payload = {"title": "Title", "context": "Context", "recurrence": "yearly"}
    status4, data4, _ = request_json(url, method="POST", headers=headers, data=bad_rec_payload)
    assert status4 == 400
    assert data4["error"]["code"] == "INVALID_PAYLOAD"


def test_rate_limit_cooldown(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    key = mock_amity_env["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}
    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"

    # 1. First injection succeeds
    payload1 = {"title": "Pulse 1", "context": "Context 1"}
    status1, _, _ = request_json(url, method="POST", headers=headers, data=payload1)
    assert status1 == 201

    # 2. Immediate second injection within 60s fails with 429 RATE_LIMITED
    # Use different scheduled_time far in future to isolate cooldown rule from collision rule
    payload2 = {
        "title": "Pulse 2",
        "context": "Context 2",
        "scheduled_time": (datetime.now() + timedelta(days=10)).isoformat()
    }
    status2, data2, resp_headers = request_json(url, method="POST", headers=headers, data=payload2)
    assert status2 == 429
    assert data2["error"]["code"] == "RATE_LIMITED"
    assert "retry_after" in data2["error"]
    assert data2["error"]["retry_after"] > 0
    assert "Retry-After" in resp_headers


def test_database_collision_guard(mock_amity_env):
    port = mock_amity_env["port"]
    agent_uid = mock_amity_env["agent_uid"]
    key = mock_amity_env["raw_key"]
    db_path = mock_amity_env["db_path"]
    headers = {"Authorization": f"Bearer {key}"}
    url = f"http://127.0.0.1:{port}/api/v1/agents/{agent_uid}/pulses"

    # Insert an existing scheduled pulse at T + 3 hours into pulses.db
    target_dt = datetime.now() + timedelta(hours=3)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("Existing Scheduled Pulse", "Existing context", target_dt.isoformat(), "none", "pending", 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    # Reset injection cooldown timestamp so we isolate the database collision check
    server = mock_amity_env["server"]
    with server.rate_limit_lock:
        server.last_injections.clear()

    # Attempt to schedule a pulse 30 seconds after the existing pulse (< 60s collision)
    colliding_time = (target_dt + timedelta(seconds=30)).isoformat()
    payload = {
        "title": "Colliding Pulse",
        "context": "Colliding details",
        "scheduled_time": colliding_time
    }

    status, data, _ = request_json(url, method="POST", headers=headers, data=payload)
    assert status == 409
    assert data["error"]["code"] == "PULSE_COLLISION"
    assert "Existing Scheduled Pulse" in data["error"]["message"]
    assert "details" in data["error"]
