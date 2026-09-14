import os
import sys
import json
import shutil
import tempfile
import zipfile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tools.terminal_tool import TerminalSkill
from core.agent_manager import AgentManager
from core.settings_manager import SettingsManager
from core.backup_manager import (
    load_backup_targets,
    create_agent_backup,
    inspect_backup,
    restore_agent_backup,
    to_portable_path,
    from_portable_path
)
from config import paths


class MockOrchestrator:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.settings_manager = SettingsManager(agent_id=agent_id)


@pytest.fixture
def test_env(monkeypatch):
    temp_dir = tempfile.mkdtemp(prefix="openamity_term_test_")
    app_data_dir = os.path.join(temp_dir, "data")
    os.makedirs(app_data_dir, exist_ok=True)
    monkeypatch.setattr(paths, "get_app_data_dir", lambda: app_data_dir)

    backup_dest_dir = os.path.join(temp_dir, "backups")
    os.makedirs(backup_dest_dir, exist_ok=True)

    # Mock user home directory to stay within temp_dir
    fake_home = os.path.join(temp_dir, "fake_home")
    os.makedirs(fake_home, exist_ok=True)
    monkeypatch.setenv("HOME", fake_home)

    yield {
        "root": temp_dir,
        "app_data_dir": app_data_dir,
        "backup_dest_dir": backup_dest_dir,
        "home": fake_home
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_terminal_tool_declarations():
    tool = TerminalSkill()
    declarations = tool.get_tool_declarations()
    names = [d["name"] for d in declarations]
    assert "Terminal_flag_for_backup" in names
    assert "flag_for_backup" in tool.commands

    decl = next(d for d in declarations if d["name"] == "Terminal_flag_for_backup")
    props = decl["parameters"]["properties"]
    assert "path" in props
    assert "action" in props


def test_flag_backup_add_and_list(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    # Create dummy external files
    doc_dir = os.path.join(test_env["home"], "Documents", "Nova")
    os.makedirs(doc_dir, exist_ok=True)
    note_file = os.path.join(doc_dir, "notes.txt")
    with open(note_file, "w") as f:
        f.write("Nova notes")

    # Flag file for backup
    result = tool.execute("flag_for_backup", path=note_file)
    assert "SUCCESS" in result
    assert "flagged for backup" in result
    assert "notes.txt" in result

    # Verify persisted in agent's data directory
    targets = load_backup_targets(aid)
    assert len(targets) == 1
    assert targets[0]["path"] == "~/Documents/Nova/notes.txt"
    assert targets[0]["type"] == "file"

    # List targets
    list_res = tool.execute("flag_for_backup", action="list")
    assert "Current active flagged backup targets (1):" in list_res
    assert "[FILE] ~/Documents/Nova/notes.txt" in list_res


def test_flag_backup_prunes_missing_files(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    # Create two files
    doc_dir = os.path.join(test_env["home"], "Documents", "Nova")
    os.makedirs(doc_dir, exist_ok=True)
    file1 = os.path.join(doc_dir, "persist.txt")
    file2 = os.path.join(doc_dir, "temporary.txt")
    with open(file1, "w") as f:
        f.write("keep me")
    with open(file2, "w") as f:
        f.write("delete me")

    tool.execute("flag_for_backup", path=file1)
    tool.execute("flag_for_backup", path=file2)
    assert len(load_backup_targets(aid)) == 2

    # Delete file2 from disk
    os.remove(file2)

    # Call flag_for_backup with action="list"
    res = tool.execute("flag_for_backup", action="list")
    assert "Pruned from backup targets:" in res
    assert "temporary.txt': Removed [missing]" in res

    # Verify targets list pruned
    targets = load_backup_targets(aid)
    assert len(targets) == 1
    assert targets[0]["path"] == "~/Documents/Nova/persist.txt"


def test_flag_backup_subsumes_child_files_when_parent_added(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    project_dir = os.path.join(test_env["home"], "Documents", "Nova", "Code", "ProjectX")
    os.makedirs(project_dir, exist_ok=True)
    file1 = os.path.join(project_dir, "main.py")
    file2 = os.path.join(project_dir, "utils.py")
    with open(file1, "w") as f:
        f.write("print('hello')")
    with open(file2, "w") as f:
        f.write("def util(): pass")

    # Flag individual files first
    tool.execute("flag_for_backup", path=file1)
    tool.execute("flag_for_backup", path=file2)
    assert len(load_backup_targets(aid)) == 2

    # Now flag the parent directory
    res = tool.execute("flag_for_backup", path=project_dir)
    assert "Pruned from backup targets:" in res
    assert "main.py': Removed [redundant]" in res
    assert "utils.py': Removed [redundant]" in res

    # Verify targets now only contain the parent directory
    targets = load_backup_targets(aid)
    assert len(targets) == 1
    assert targets[0]["path"] == "~/Documents/Nova/Code/ProjectX"
    assert targets[0]["type"] == "directory"


def test_flag_backup_rejects_child_file_if_parent_already_flagged(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    project_dir = os.path.join(test_env["home"], "Documents", "Nova", "Code", "ProjectY")
    os.makedirs(project_dir, exist_ok=True)
    file1 = os.path.join(project_dir, "script.py")
    with open(file1, "w") as f:
        f.write("script")

    # Flag directory first
    tool.execute("flag_for_backup", path=project_dir)
    assert len(load_backup_targets(aid)) == 1

    # Attempt to flag file inside already flagged directory
    res = tool.execute("flag_for_backup", path=file1)
    assert "redundant" in res.lower()
    assert "script.py" in res

    # Targets should still only contain the directory
    targets = load_backup_targets(aid)
    assert len(targets) == 1
    assert targets[0]["path"] == "~/Documents/Nova/Code/ProjectY"


def test_flag_backup_prunes_agent_state_dir(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    # Attempt to flag internal trajectory.json
    agent_dir = paths.get_agent_data_dir(aid)
    traj_file = os.path.join(agent_dir, "trajectory.json")
    with open(traj_file, "w") as f:
        f.write("{}")

    res = tool.execute("flag_for_backup", path=traj_file)
    assert "redundant" in res.lower()
    assert "agent state directory" in res.lower()
    assert len(load_backup_targets(aid)) == 0


def test_flag_backup_remove_action(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    doc_dir = os.path.join(test_env["home"], "Documents", "Nova")
    os.makedirs(doc_dir, exist_ok=True)
    fpath = os.path.join(doc_dir, "remove_me.txt")
    with open(fpath, "w") as f:
        f.write("content")

    tool.execute("flag_for_backup", path=fpath, action="add")
    assert len(load_backup_targets(aid)) == 1

    # Remove
    res = tool.execute("flag_for_backup", path=fpath, action="remove")
    assert "was removed" in res
    assert len(load_backup_targets(aid)) == 0


def test_backup_and_restore_with_flagged_files_and_directories(test_env):
    am = AgentManager()
    aid = am.create_new_agent()
    uid = am.get_agent_uid(aid)

    settings = SettingsManager(agent_id=aid)
    settings.set("core.agent.name", "BackupAgent")
    settings.save()

    orch = MockOrchestrator(aid)
    tool = TerminalSkill(orchestrator=orch)

    # Create external files & project directory
    proj_dir = os.path.join(test_env["home"], "Documents", "BackupAgent", "MyProject")
    os.makedirs(proj_dir, exist_ok=True)
    main_py = os.path.join(proj_dir, "main.py")
    with open(main_py, "w") as f:
        f.write("print('hello world')")

    os.chmod(main_py, 0o755)

    sub_dir = os.path.join(proj_dir, "subpkg")
    os.makedirs(sub_dir, exist_ok=True)
    with open(os.path.join(sub_dir, "helper.txt"), "w") as f:
        f.write("helper data")

    single_file = os.path.join(test_env["home"], "Pictures", "BackupAgent", "avatar.png")
    os.makedirs(os.path.dirname(single_file), exist_ok=True)
    with open(single_file, "w") as f:
        f.write("fake-png-data")

    # Flag both for backup
    tool.execute("flag_for_backup", path=proj_dir)
    tool.execute("flag_for_backup", path=single_file)

    # Create backup
    backup_path = create_agent_backup(
        aid,
        destination_dir=test_env["backup_dest_dir"],
        agent_manager=am
    )
    assert os.path.exists(backup_path)

    # Inspect backup
    info = inspect_backup(backup_path)
    assert info["valid"] is True
    assert info["name"] == "BackupAgent"
    assert info["external_targets_count"] == 2
    assert len(info["external_targets"]) == 2

    # Check zip contents
    with zipfile.ZipFile(backup_path, "r") as zf:
        names = zf.namelist()
        assert "_external_manifest.json" in names
        assert any("main.py" in n for n in names)
        assert any("helper.txt" in n for n in names)
        assert any("avatar.png" in n for n in names)
        assert "backup_targets.json" in names

    # Now delete external files from disk to simulate restoring to clean host
    shutil.rmtree(proj_dir)
    os.remove(single_file)
    assert not os.path.exists(proj_dir)
    assert not os.path.exists(single_file)

    # Restore backup
    success, msg, details = restore_agent_backup(backup_path, agent_manager=am)
    assert success is True
    assert details["external_targets_restored"] == 2

    # Verify external directory and files were recreated
    assert os.path.exists(proj_dir)
    assert os.path.exists(main_py)
    with open(main_py) as f:
        assert f.read() == "print('hello world')"
    assert os.access(main_py, os.X_OK)

    assert os.path.exists(os.path.join(sub_dir, "helper.txt"))
    with open(os.path.join(sub_dir, "helper.txt")) as f:
        assert f.read() == "helper data"

    assert os.path.exists(single_file)
    with open(single_file) as f:
        assert f.read() == "fake-png-data"

    # Verify agent's backup targets file is restored
    restored_targets = load_backup_targets(details["agent_id"])
    assert len(restored_targets) == 2
