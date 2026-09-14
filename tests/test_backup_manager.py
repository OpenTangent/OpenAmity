import os
import sys
import json
import shutil
import tempfile
import zipfile
import pytest

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.backup_manager import (
    get_default_backup_location,
    sanitize_agent_name,
    get_backup_filename,
    create_agent_backup,
    create_all_backups,
    inspect_backup,
    restore_agent_backup
)
from core.agent_manager import AgentManager
from core.settings_manager import SettingsManager
from tools.system_tool import SystemTool
from config import paths


@pytest.fixture
def temp_backup_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_backup_test_")
    app_data_dir = os.path.join(temp_dir, "data")
    os.makedirs(app_data_dir, exist_ok=True)
    
    # Patch paths.get_app_data_dir
    monkeypatch.setattr(paths, "get_app_data_dir", lambda: app_data_dir)
    
    backup_dest_dir = os.path.join(temp_dir, "backups")
    os.makedirs(backup_dest_dir, exist_ok=True)

    yield {
        "root": temp_dir,
        "app_data_dir": app_data_dir,
        "backup_dest_dir": backup_dest_dir
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_sanitize_agent_name():
    assert sanitize_agent_name("Nova") == "Nova"
    assert sanitize_agent_name("Amy/Assistant:2") == "Amy_Assistant_2"
    assert sanitize_agent_name("  Test Agent  ") == "Test Agent"
    assert sanitize_agent_name("Agent?*<>|") == "Agent_"
    assert sanitize_agent_name("") == "Agent"
    assert sanitize_agent_name(None) == "Agent"


def test_get_backup_filename():
    fn = get_backup_filename("Nova", datestamp="2026-08-19")
    assert fn == "Nova-2026-08-19.oaa"

    fn2 = get_backup_filename("Special/Name", datestamp="2026-08-19")
    assert fn2 == "Special_Name-2026-08-19.oaa"


def test_create_and_inspect_agent_backup(temp_backup_env):
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    
    # Configure agent details
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "Nova")
    settings.set("core.agent.archetype", "Explorer")
    settings.save()

    # Create dummy stateful files in agent directory
    agent_dir = paths.get_agent_data_dir(aid)
    with open(os.path.join(agent_dir, "trajectory.json"), "w") as f:
        json.dump({"goals": ["Learn Python"]}, f)

    mempalace_dir = os.path.join(agent_dir, "mempalace", "sanctuary")
    os.makedirs(mempalace_dir, exist_ok=True)
    with open(os.path.join(mempalace_dir, "mirrors.json"), "w") as f:
        json.dump({"mirrors": ["Reflective"]}, f)

    # Perform backup
    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    assert os.path.exists(backup_path)
    assert backup_path.endswith(".oaa")
    assert "Nova-" in os.path.basename(backup_path)

    # Inspect the backup
    info = inspect_backup(backup_path)
    assert info["valid"] is True
    assert info["uid"] == uid
    assert info["name"] == "Nova"
    assert info["archetype"] == "Explorer"
    assert info["file_count"] >= 3
    assert info["archive_size"] > 0


def test_backup_handles_transient_and_singleton_files(temp_backup_env):
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "SessionAgent")
    settings.save()

    agent_dir = paths.get_agent_data_dir(aid)
    session_dir = os.path.join(agent_dir, "whatsapp_data", ".wpp_session")
    os.makedirs(session_dir, exist_ok=True)

    # Create dummy regular session file
    with open(os.path.join(session_dir, "valid_state.json"), "w") as f:
        f.write('{"session": true}')

    # Create SingletonCookie as broken symlink
    cookie_path = os.path.join(session_dir, "SingletonCookie")
    try:
        os.symlink("/nonexistent/target/path/for/singleton", cookie_path)
    except OSError:
        pass

    # Create SingletonLock
    with open(os.path.join(session_dir, "SingletonLock"), "w") as f:
        f.write("dummy lock")

    # Create a broken symlink elsewhere
    broken_symlink = os.path.join(agent_dir, "broken_link")
    try:
        os.symlink("/nonexistent/another_path", broken_symlink)
    except OSError:
        pass

    # Perform backup - should not raise FileNotFoundError or crash
    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    assert os.path.exists(backup_path)
    info = inspect_backup(backup_path)
    assert info["valid"] is True
    assert info["name"] == "SessionAgent"
    assert info["uid"] == uid


def test_inspect_invalid_backup(temp_backup_env):
    # Non-existent file
    res = inspect_backup(os.path.join(temp_backup_env["root"], "nonexistent.oaa"))
    assert res["valid"] is False
    assert "does not exist" in res["error"]

    # Non-zip file
    corrupt_file = os.path.join(temp_backup_env["root"], "corrupt.oaa")
    with open(corrupt_file, "w") as f:
        f.write("not a zip")
    res = inspect_backup(corrupt_file)
    assert res["valid"] is False

    # Zip without settings.json
    empty_zip = os.path.join(temp_backup_env["root"], "empty.oaa")
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("test.txt", "hello")
    res = inspect_backup(empty_zip)
    assert res["valid"] is False
    assert "settings.json" in res["error"]


def test_create_all_backups(temp_backup_env):
    am = AgentManager()
    aid1 = am.create_new_agent()
    s1 = SettingsManager(agent_id=aid1)
    s1.set("core.agent.name", "AgentOne")
    s1.save()

    aid2 = am.create_new_agent()
    s2 = SettingsManager(agent_id=aid2)
    s2.set("core.agent.name", "AgentTwo")
    s2.save()

    backups = create_all_backups(
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    assert len(backups) == 2
    for b in backups:
        assert os.path.exists(b)
        assert b.endswith(".oaa")


def test_restore_new_instance(temp_backup_env):
    # Create an agent and backup
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "Atlas")
    settings.save()

    agent_dir = paths.get_agent_data_dir(aid)
    with open(os.path.join(agent_dir, "custom_data.txt"), "w") as f:
        f.write("Atlas custom data")

    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    # Delete original agent so it becomes a "non-existent agent"
    am.delete_agent(aid)
    assert aid not in am.get_all_agents()
    assert am.get_agent_id_by_uid(uid) is None

    # Restore backup into framework as new instance
    success, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert success is True
    assert details["is_rollback"] is False
    assert details["uid"] == uid
    assert details["name"] == "Atlas"

    new_aid = details["agent_id"]
    assert new_aid in am.get_all_agents()
    assert am.get_agent_name(new_aid) == "Atlas"

    # Verify files restored
    new_dir = paths.get_agent_data_dir(new_aid)
    assert os.path.exists(os.path.join(new_dir, "custom_data.txt"))
    with open(os.path.join(new_dir, "custom_data.txt")) as f:
        assert f.read() == "Atlas custom data"


def test_restore_rollback_overwriting_and_deleting_old_files(temp_backup_env):
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "RollbackAgent")
    settings.save()

    agent_dir = paths.get_agent_data_dir(aid)
    with open(os.path.join(agent_dir, "initial_state.txt"), "w") as f:
        f.write("Initial state content")

    # Create backup of initial state
    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    # Simulate agent evolving/polluting its state with extra files and altered settings
    settings.set("core.agent.name", "AlteredName")
    settings.save()

    with open(os.path.join(agent_dir, "extra_polluted_file.txt"), "w") as f:
        f.write("This file should NOT exist after rollback!")

    sub_dir = os.path.join(agent_dir, "polluted_subdir")
    os.makedirs(sub_dir, exist_ok=True)
    with open(os.path.join(sub_dir, "sub_polluted.txt"), "w") as f:
        f.write("Subdir should also be wiped cleanly")

    # Now perform rollback restore
    success, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert success is True
    assert details["is_rollback"] is True
    assert details["agent_id"] == aid
    assert details["uid"] == uid

    # Verify that the polluted extra files were COMPLETELY wiped and not left behind
    assert not os.path.exists(os.path.join(agent_dir, "extra_polluted_file.txt"))
    assert not os.path.exists(sub_dir)

    # Verify initial state files and settings are restored
    assert os.path.exists(os.path.join(agent_dir, "initial_state.txt"))
    with open(os.path.join(agent_dir, "initial_state.txt")) as f:
        assert f.read() == "Initial state content"

    restored_settings = SettingsManager(agent_id=aid)
    assert restored_settings.get("core.agent.name") == "RollbackAgent"


def test_system_tool_create_backup(temp_backup_env):
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "ToolAgent")
    settings.save()
    settings.set("core.agent.uid", "+OA-TOOL-7777")
    settings.save()

    # Mock Orchestrator
    class MockOrchestrator:
        def __init__(self, agent_id):
            self.agent_id = agent_id

    orch = MockOrchestrator(aid)
    tool = SystemTool(orchestrator=orch)

    # Test "self" backup
    result_self = tool.execute("create_backup", target="self", backup_location=temp_backup_env["backup_dest_dir"])
    assert "Successfully created backup snapshot" in result_self
    assert "ToolAgent-" in result_self

    # Test "all" backup
    result_all = tool.execute("create_backup", target="all", backup_location=temp_backup_env["backup_dest_dir"])
    assert "Successfully created backup(s) for all agents" in result_all


def test_config_manager_backup_location(temp_backup_env):
    from core.config_manager import ConfigManager
    config_file = os.path.join(temp_backup_env["app_data_dir"], "config.json")
    cm = ConfigManager(config_file=config_file)
    
    # Check default from config.default.json
    assert cm.get("backup-location") == "~/Documents/OpenAmity/Backups"
    
    # Update backup location
    custom_path = "/custom/backup/dir"
    cm.set("backup-location", custom_path)
    cm.save()

    cm2 = ConfigManager(config_file=config_file)
    assert cm2.get("backup-location") == custom_path


def test_system_tool_declarations():
    tool = SystemTool()
    declarations = tool.get_tool_declarations()
    names = [d["name"] for d in declarations]
    assert "System_create_backup" in names

    backup_decl = next(d for d in declarations if d["name"] == "System_create_backup")
    props = backup_decl["parameters"]["properties"]
    assert "target" in props
    assert "backup_location" in props


def test_restore_wrapped_archive(temp_backup_env):
    """Tests restoring a backup archive where files are inside a top-level folder wrapper."""
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    
    # Create wrapped zip manually
    wrapped_zip = os.path.join(temp_backup_env["backup_dest_dir"], "WrappedAgent-2026-08-19.oaa")
    with zipfile.ZipFile(wrapped_zip, "w") as zf:
        zf.writestr("WrappedAgent-2026-08-19/settings.json", json.dumps({
            "core": {
                "agent": {
                    "uid": uid,
                    "name": "WrappedAgent"
                }
            }
        }))
        zf.writestr("WrappedAgent-2026-08-19/nested/file.txt", "nested content")

    # Inspect wrapped archive
    info = inspect_backup(wrapped_zip)
    assert info["valid"] is True
    assert info["name"] == "WrappedAgent"
    assert info["uid"] == uid

    # Restore wrapped archive as rollback
    success, msg, details = restore_agent_backup(wrapped_zip, agent_manager=am)
    assert success is True
    assert details["is_rollback"] is True

    agent_dir = paths.get_agent_data_dir(aid)
    assert os.path.exists(os.path.join(agent_dir, "settings.json"))
    assert os.path.exists(os.path.join(agent_dir, "nested", "file.txt"))
    with open(os.path.join(agent_dir, "nested", "file.txt")) as f:
        assert f.read() == "nested content"


def test_restore_preserves_sqlite_and_subdirs(temp_backup_env):
    import sqlite3
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "SqliteAgent")
    settings.save()

    agent_dir = paths.get_agent_data_dir(aid)
    db_path = os.path.join(agent_dir, "pulses.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO test_table (name) VALUES ('pulse_entry_1');")
        conn.commit()

    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    # Modify sqlite database in active directory
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO test_table (name) VALUES ('unwanted_pulse');")
        conn.commit()

    # Restore backup
    success, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert success is True

    # Verify database was restored to initial state
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM test_table").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "pulse_entry_1"


def test_backup_excludes_node_modules_and_caches(temp_backup_env):
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "CacheAgent")
    settings.save()

    agent_dir = paths.get_agent_data_dir(aid)

    # 1. Stateful WhatsApp session and uploads (MUST be included)
    session_dir = os.path.join(agent_dir, "whatsapp_data", ".wpp_session")
    os.makedirs(session_dir, exist_ok=True)
    with open(os.path.join(session_dir, "session_state.json"), "w") as f:
        f.write('{"auth": "token"}')

    uploads_dir = os.path.join(agent_dir, "whatsapp_data", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    with open(os.path.join(uploads_dir, "received_photo.jpg"), "w") as f:
        f.write('photo_data')

    # 2. Transient directories (MUST be excluded)
    node_modules_dir = os.path.join(agent_dir, "whatsapp_bridge", "node_modules", "express")
    os.makedirs(node_modules_dir, exist_ok=True)
    with open(os.path.join(node_modules_dir, "index.js"), "w") as f:
        f.write('console.log("express");')

    puppeteer_cache_dir = os.path.join(agent_dir, "whatsapp_data", "puppeteer_cache", "chrome")
    os.makedirs(puppeteer_cache_dir, exist_ok=True)
    with open(os.path.join(puppeteer_cache_dir, "chrome"), "w") as f:
        f.write('binary')

    wwebjs_cache_dir = os.path.join(agent_dir, "whatsapp_data", ".wwebjs_cache")
    os.makedirs(wwebjs_cache_dir, exist_ok=True)
    with open(os.path.join(wwebjs_cache_dir, "temp.bin"), "w") as f:
        f.write('cache')

    # 3. Ephemeral daemon runtime files (MUST be excluded)
    bridge_dir = os.path.join(agent_dir, "whatsapp_bridge")
    os.makedirs(bridge_dir, exist_ok=True)
    with open(os.path.join(bridge_dir, "daemon.pid"), "w") as f:
        f.write('12345')
    with open(os.path.join(bridge_dir, "daemon.port"), "w") as f:
        f.write('3000')
    with open(os.path.join(agent_dir, "whatsapp_data", ".last_engine_update"), "w") as f:
        f.write('123456789.0')

    # Create backup
    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    assert os.path.exists(backup_path)

    with zipfile.ZipFile(backup_path, "r") as zf:
        namelist = zf.namelist()

        # Check preserved items
        assert any("whatsapp_data/.wpp_session/session_state.json" in n.replace("\\", "/") for n in namelist)
        assert any("whatsapp_data/uploads/received_photo.jpg" in n.replace("\\", "/") for n in namelist)

        # Check excluded items
        assert not any("node_modules" in n for n in namelist)
        assert not any("puppeteer_cache" in n for n in namelist)
        assert not any(".wwebjs_cache" in n for n in namelist)
        assert not any("daemon.pid" in n for n in namelist)
        assert not any("daemon.port" in n for n in namelist)
        assert not any(".last_engine_update" in n for n in namelist)


def test_restore_preserves_executable_permissions(temp_backup_env):
    import stat
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "ExecAgent")
    settings.save()

    agent_dir = paths.get_agent_data_dir(aid)
    script_path = os.path.join(agent_dir, "custom_executable.sh")
    with open(script_path, "w") as f:
        f.write("#!/bin/sh\necho 'hello'\n")
    os.chmod(script_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)  # 0755

    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    # Delete agent to restore as new instance
    am.delete_agent(aid)

    success, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert success is True
    new_aid = details["agent_id"]
    new_dir = paths.get_agent_data_dir(new_aid)
    restored_script = os.path.join(new_dir, "custom_executable.sh")

    assert os.path.exists(restored_script)
    assert os.access(restored_script, os.X_OK)


def test_backup_manager_c2_sensitive_path_rejection(temp_backup_env):
    from core.backup_manager import sync_and_clean_backup_targets
    am = AgentManager()
    aid = am.create_new_agent()

    # Attempt to flag sensitive dotfiles / system paths
    sensitive_paths = ["~/.ssh/id_rsa", "~/.bashrc", "~/.profile", "/etc/passwd", "/usr/bin"]
    for path in sensitive_paths:
        success, msg, removals, current = sync_and_clean_backup_targets(aid, path, action="add")
        assert success is False
        assert "protected or unsafe" in msg


def test_backup_manager_c1_zip_slip_rejection(temp_backup_env):
    am = AgentManager()
    aid = am.create_new_agent()
    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "SlipTest")
    settings.save()

    backup_path = create_agent_backup(
        aid,
        destination_dir=temp_backup_env["backup_dest_dir"],
        agent_manager=am
    )

    # Craft a malicious archive entry attempting Zip Slip
    with zipfile.ZipFile(backup_path, "a") as zf:
        zf.writestr("../evil.txt", "payload")

    # Delete agent to restore as new instance
    am.delete_agent(aid)

    # Restoring should skip the malicious entry without escaping target_dir
    success, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert success is True
    new_aid = details["agent_id"]
    new_dir = paths.get_agent_data_dir(new_aid)

    # Verify evil.txt was NOT written to parent of new_dir
    parent_dir = os.path.dirname(new_dir)
    assert not os.path.exists(os.path.join(parent_dir, "evil.txt"))



