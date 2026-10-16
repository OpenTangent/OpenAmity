import os
import sys
import time
import json
import zipfile
import threading
import tempfile
import shutil
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.backup_manager import (
    BackupTask,
    BackupTaskManager,
    create_agent_backup,
    create_all_backups,
    inspect_backup,
    restore_agent_backup,
    verify_snapshot_restore,
    reap_stale_backup_artifacts
)
from core.agent_manager import AgentManager
from core.settings_manager import SettingsManager
from tools.system_tool import SystemTool
from config import paths


@pytest.fixture(autouse=True)
def reset_backup_task_manager():
    """Ensure BackupTaskManager singleton state is cleanly reset before and after each test."""
    with BackupTaskManager._lock:
        BackupTaskManager._instance = None
    yield
    with BackupTaskManager._lock:
        BackupTaskManager._instance = None


@pytest.fixture
def temp_backup_env(monkeypatch):
    """Provides an isolated environment for backup testing."""
    temp_dir = tempfile.mkdtemp(prefix="openamity_async_backup_test_")
    app_data_dir = os.path.join(temp_dir, "data")
    os.makedirs(app_data_dir, exist_ok=True)

    monkeypatch.setattr(paths, "get_app_data_dir", lambda: app_data_dir)

    backup_dest_dir = os.path.join(temp_dir, "backups")
    os.makedirs(backup_dest_dir, exist_ok=True)

    yield {
        "root": temp_dir,
        "app_data_dir": app_data_dir,
        "backup_dest_dir": backup_dest_dir
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_backup_task_and_manager_units():
    """Unit tests for BackupTask state transitions and BackupTaskManager registry."""
    task = BackupTask(
        task_id="backup_test_1",
        target="self",
        agent_id="agent_123",
        destination_dir="/tmp/backups",
        call_id="call_999"
    )

    assert task.task_id == "backup_test_1"
    assert task.status == "in_progress"
    assert task.call_id == "call_999"
    assert task.get_elapsed_seconds() >= 0.0

    # in_progress summary
    summary_in_prog = task.to_status_summary()
    assert "IN PROGRESS" in summary_in_prog
    assert "backup_test_1" in summary_in_prog

    # cancel task
    task.cancel()
    assert task.status == "cancelled"
    assert task.cancel_event.is_set()
    summary_cancelled = task.to_status_summary()
    assert "TERMINATED / CANCELLED" in summary_cancelled

    # failed summary
    task.status = "failed"
    task.error = "Disk full"
    summary_failed = task.to_status_summary()
    assert "FAILED" in summary_failed
    assert "Disk full" in summary_failed

    # completed summary
    task.status = "completed"
    task.total_files = 12
    task.created_paths = ["/tmp/backups/Nova-2026-10-05.oaa"]
    summary_completed = task.to_status_summary()
    assert "COMPLETED" in summary_completed
    assert "Nova-2026-10-05.oaa" in summary_completed

    # BackupTaskManager tests
    mgr = BackupTaskManager.get_instance()
    assert mgr is BackupTaskManager.get_instance()

    t1 = mgr.create_task("self", "aid_1", "/tmp")
    assert t1.task_id == "backup_1"
    assert mgr.get_task("backup_1") is t1
    assert mgr.get_task() is t1  # most recent

    t2 = mgr.create_task("all", None, "/tmp")
    assert t2.task_id == "backup_2"
    assert mgr.get_task() is t2
    assert len(mgr.list_tasks()) == 2


def test_system_tool_async_backup_lifecycle(temp_backup_env):
    """Test full async backup lifecycle via SystemTool: immediate return, status checking, and pulse emission."""
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "AsyncAgent")
    settings.save()

    # Add mock trajectory and pulse DB
    agent_dir = paths.get_agent_data_dir(aid)
    with open(os.path.join(agent_dir, "trajectory.json"), "w") as f:
        json.dump({"tasks": []}, f)

    mock_pulse_engine = MagicMock()
    mock_pulse_engine.trigger_pulse = MagicMock()

    mock_orchestrator = MagicMock()
    mock_orchestrator.agent_id = aid
    mock_orchestrator.pulse_engine = mock_pulse_engine
    mock_orchestrator.on_tool_finished = MagicMock()
    mock_orchestrator.settings_manager = settings

    tool = SystemTool(orchestrator=mock_orchestrator)

    # 1. Check status before any backups
    assert "No backup tasks have been initiated" in tool.execute("check_backup_status")

    # 2. Trigger asynchronous backup
    dest_dir = temp_backup_env["backup_dest_dir"]
    res = tool.execute(
        "create_backup",
        target="self",
        backup_location=dest_dir,
        _call_id="pip_call_42"
    )

    assert "Backup process started in background with ID: 'backup_1'" in res
    assert "System_check_backup_status" in res
    assert "System_kill_backup" in res

    # Retrieve task
    mgr = BackupTaskManager.get_instance()
    task = mgr.get_task("backup_1")
    assert task is not None

    # Wait for the background worker thread to finish
    assert task.thread is not None
    task.thread.join(timeout=10.0)
    assert not task.thread.is_alive()

    # Verify task state
    assert task.status == "completed"
    assert len(task.created_paths) == 1
    created_oaa = task.created_paths[0]
    assert os.path.exists(created_oaa)
    assert created_oaa.endswith(".oaa")

    # Verify pip dismissal signal emitted
    mock_orchestrator.on_tool_finished.emit.assert_called_with("pip_call_42")

    # Verify system notification pulse emitted
    assert mock_pulse_engine.trigger_pulse.emit.called
    pulse_args, pulse_kwargs = mock_pulse_engine.trigger_pulse.emit.call_args
    assert "[SYSTEM_NOTIFICATION]" in pulse_args[0]
    assert "completed successfully" in pulse_args[0]
    assert "backup_1" in pulse_args[0]

    # Verify check_backup_status returns completed info
    status_out = tool.execute("check_backup_status", backup_id="backup_1")
    assert "COMPLETED" in status_out
    assert os.path.basename(created_oaa) in status_out


def test_system_tool_kill_backup(temp_backup_env):
    """Test killing/cancelling a running backup via SystemTool."""
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "KillTestAgent")
    settings.save()

    mock_pulse_engine = MagicMock()
    mock_pulse_engine.trigger_pulse = MagicMock()

    mock_orchestrator = MagicMock()
    mock_orchestrator.agent_id = aid
    mock_orchestrator.pulse_engine = mock_pulse_engine
    mock_orchestrator.on_tool_finished = MagicMock()
    mock_orchestrator.settings_manager = settings

    tool = SystemTool(orchestrator=mock_orchestrator)

    pause_event = threading.Event()
    started_event = threading.Event()

    def slow_stream(zf, src, arc, cancel_event=None, chunk_size=65536):
        started_event.set()
        # Wait until test signals cancel
        while not (cancel_event and cancel_event.is_set()):
            time.sleep(0.01)
        raise InterruptedError("Backup cancelled by request.")

    dest_dir = temp_backup_env["backup_dest_dir"]

    with patch("core.backup_manager._stream_file_to_zip", side_effect=slow_stream):
        # Trigger async backup
        tool.execute(
            "create_backup",
            target="self",
            backup_location=dest_dir,
            _call_id="pip_call_kill"
        )

        assert started_event.wait(timeout=5.0), "Worker did not reach streaming loop"

        mgr = BackupTaskManager.get_instance()
        task = mgr.get_active_task()
        assert task is not None
        assert task.is_active()

        # Kill running backup
        kill_res = tool.execute("kill_backup")
        assert "has been signalled to terminate and cancelled" in kill_res

        # Wait for thread termination
        task.thread.join(timeout=5.0)
        assert not task.thread.is_alive()
        assert task.status == "cancelled"

        # Verify pip dismissed
        mock_orchestrator.on_tool_finished.emit.assert_called_with("pip_call_kill")

        # Verify pulse notification emitted
        assert mock_pulse_engine.trigger_pulse.emit.called
        pulse_args, _ = mock_pulse_engine.trigger_pulse.emit.call_args
        assert "[SYSTEM_NOTIFICATION]" in pulse_args[0]
        assert "cancelled/killed" in pulse_args[0]

        # Verify status summary
        status_out = tool.execute("check_backup_status")
        assert "TERMINATED / CANCELLED" in status_out


def test_system_tool_collision_prevention(temp_backup_env):
    """Test that starting a backup while another is active is rejected."""
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.save()

    mock_orchestrator = MagicMock()
    mock_orchestrator.agent_id = aid
    mock_orchestrator.settings_manager = settings

    tool = SystemTool(orchestrator=mock_orchestrator)

    mgr = BackupTaskManager.get_instance()
    task = mgr.create_task("self", aid, temp_backup_env["backup_dest_dir"])
    task.status = "in_progress"

    # Create dummy alive thread
    stop_event = threading.Event()
    def dummy_loop():
        stop_event.wait(timeout=2.0)

    t = threading.Thread(target=dummy_loop)
    t.start()
    task.thread = t

    try:
        assert task.is_active()
        collision_res = tool.execute("create_backup", target="self")
        assert "Error: A backup task ('backup_1') is already in progress" in collision_res
    finally:
        stop_event.set()
        t.join(timeout=2.0)


def test_create_agent_backup_cooperative_cancellation(temp_backup_env):
    """Test create_agent_backup cooperative cancellation with cancel_event cleans up staging files."""
    am = AgentManager()
    aid = am.create_new_agent()
    dest_dir = temp_backup_env["backup_dest_dir"]

    cancel_evt = threading.Event()
    cancel_evt.set()  # Pre-cancelled

    with pytest.raises(InterruptedError):
        create_agent_backup(aid, destination_dir=dest_dir, cancel_event=cancel_evt)

    # Verify no .tmp_* staging files remain
    tmp_files = [f for f in os.listdir(dest_dir) if ".tmp_" in f]
    assert len(tmp_files) == 0


def test_reap_stale_backup_artifacts(temp_backup_env):
    """Test reap_stale_backup_artifacts cleans up old .tmp_* and .snap_* files but preserves new ones and .oaa files."""
    dest_dir = temp_backup_env["backup_dest_dir"]

    # 1. Stale temporary file (> 24 hours old)
    stale_tmp = os.path.join(dest_dir, "Agent-2026-10-04.oaa.tmp_abc123")
    with open(stale_tmp, "w") as f:
        f.write("stale")
    past_time = time.time() - (25 * 3600)
    os.utime(stale_tmp, (past_time, past_time))

    # 2. Fresh temporary file (< 24 hours old)
    fresh_tmp = os.path.join(dest_dir, "Agent-2026-10-05.oaa.tmp_def456")
    with open(fresh_tmp, "w") as f:
        f.write("fresh")

    # 3. Real .oaa archive (should never be reaped)
    real_oaa = os.path.join(dest_dir, "Agent-2026-10-04.oaa")
    with open(real_oaa, "w") as f:
        f.write("real archive")
    os.utime(real_oaa, (past_time, past_time))

    reaped = reap_stale_backup_artifacts([dest_dir], max_age_hours=24.0)
    assert reaped == 1
    assert not os.path.exists(stale_tmp)
    assert os.path.exists(fresh_tmp)
    assert os.path.exists(real_oaa)


def test_verify_snapshot_restore_required_inventory_fail_closed(temp_backup_env):
    """Test verify_snapshot_restore validates required inventory and fails closed if corrupted."""
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "InventoryAgent")
    settings.save()

    # Create baseline valid backup
    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    valid_res = verify_snapshot_restore(backup_path)
    assert valid_res["valid"] is True
    assert valid_res["hashes_verified"] > 0

    # Create a corrupted archive missing settings.json
    corrupted_oaa = os.path.join(temp_backup_env["backup_dest_dir"], "Corrupted-Agent.oaa")
    with zipfile.ZipFile(backup_path, 'r') as src_zf:
        with zipfile.ZipFile(corrupted_oaa, 'w') as dst_zf:
            for item in src_zf.infolist():
                if "settings.json" not in item.filename:
                    dst_zf.writestr(item, src_zf.read(item.filename))

    # 1. Missing settings.json fails inspect_backup and restore
    val_res = verify_snapshot_restore(corrupted_oaa)
    assert val_res["valid"] is False
    assert "missing" in val_res["error"].lower()

    success, msg, details = restore_agent_backup(corrupted_oaa, agent_manager=am)
    assert success is False
    assert "settings.json" in msg.lower()

    # 2. Tampered hash in snapshot manifest fails verify_snapshot_restore
    tampered_hash_oaa = os.path.join(temp_backup_env["backup_dest_dir"], "Tampered-Hash-Agent.oaa")
    with zipfile.ZipFile(backup_path, 'r') as src_zf:
        with zipfile.ZipFile(tampered_hash_oaa, 'w') as dst_zf:
            for item in src_zf.infolist():
                data = src_zf.read(item.filename)
                if item.filename == "settings.json":
                    # Mutate settings.json without updating manifest file_hashes
                    data = data + b" "
                dst_zf.writestr(item, data)

    val_res2 = verify_snapshot_restore(tampered_hash_oaa)
    assert val_res2["valid"] is False
    assert "checksum mismatch" in val_res2["error"].lower()

    # Ensure canary restore refuses to run on corrupted/tampered archive
    success2, msg2, details2 = restore_agent_backup(tampered_hash_oaa, agent_manager=am)
    assert success2 is False
    assert "Backup validation failed" in msg2


def test_gui_backup_worker_thread_cancellation(temp_backup_env):
    """Test BackupWorkerThread cancel() cooperative cancellation."""
    from gui.settings_panel import BackupWorkerThread

    am = AgentManager()
    aid = am.create_new_agent()

    worker = BackupWorkerThread(
        agent_ids=[aid],
        dest_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    assert not worker.cancel_event.is_set()
    worker.cancel()
    assert worker.cancel_event.is_set()

    # Run directly (simulated in thread)
    completed_results = []
    worker.backup_completed.connect(lambda created, failed: completed_results.append((created, failed)))

    worker.run()

    assert len(completed_results) == 1
    created, failed = completed_results[0]
    assert len(created) == 0
    assert any("cancelled" in err.lower() for err in failed)
