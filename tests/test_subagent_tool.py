import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.cerebrum import Cerebrum
from core.orchestrator import AmityOrchestrator
from core.settings_manager import SettingsManager
from core.subagent_file_tools import SubagentFilePolicy, SubagentFileToolset
from core.subagent_worker import SubagentWorker
from tools.subagent_tool import SubagentSkill


def _build_minimal_orchestrator(tmp_path):
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = "test_subagent_orch"
    orch.settings_manager = SettingsManager(agent_id="test_subagent_orch")
    orch.is_paused = False
    orch.active_subagents = {}
    orch.subagent_last_activity = {}
    orch.subagent_call_ids = {}
    orch.event_queue = []
    orch.on_tool_finished = MagicMock()
    orch.check_cycle_completion = MagicMock()
    orch.cerebrum = MagicMock()
    orch.cerebrum.tools = {}
    return orch


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "Documents").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("config.paths.get_app_data_dir", lambda: str(data_dir))
    monkeypatch.setattr(
        "config.paths.get_base_dir_for",
        lambda aid: str(data_dir / "agents" / (aid or "default")),
    )

    SettingsManager.clear_instances()
    yield {"home": fake_home, "docs": fake_home / "Documents", "data": data_dir, "tmp": tmp_path}
    SettingsManager.clear_instances()


def test_subagent_tool_declarations_and_main_agent_isolation(isolated_env):
    orch = MagicMock()
    orch.settings_manager = MagicMock()
    orch.settings_manager.get.return_value = False

    skill = SubagentSkill(orchestrator=orch)
    decls = skill.get_tool_declarations()
    spawn_decl = next(d for d in decls if d["name"] == "Subagent_spawn")
    props = spawn_decl["parameters"]["properties"]

    assert "task_description" in props
    assert "model_tier" in props
    assert "allowed_files" in props
    assert props["allowed_files"]["type"] == "ARRAY"
    assert props["allowed_files"]["items"]["type"] == "STRING"
    assert "allowed_files" not in spawn_decl["parameters"]["required"]

    # Main agent Cerebrum must NOT expose File_read or File_edit
    cerebrum = Cerebrum(orchestrator=None)
    main_names = {d["name"] for d in cerebrum.get_all_tool_declarations()}
    assert "File_read" not in main_names
    assert "File_edit" not in main_names
    cerebrum.shutdown()


def test_subagent_skill_execute_routes_allowed_files(isolated_env):
    orch = MagicMock()
    orch.settings_manager.get.return_value = False
    orch.spawn_subagent.return_value = "Subagent abc123 spawned."

    skill = SubagentSkill(orchestrator=orch)
    res = skill.execute(
        "spawn",
        task_description="Inspect notes",
        model_tier="standard",
        allowed_files=["notes.md"],
        _call_id="call-42",
    )
    assert "spawned" in res
    orch.spawn_subagent.assert_called_once_with(
        "Inspect notes",
        "standard",
        call_id="call-42",
        allowed_files=["notes.md"],
    )

    orch.spawn_subagent.reset_mock()
    skill.execute("spawn", task_description="Web task")
    orch.spawn_subagent.assert_called_once_with(
        "Web task",
        "light",
        call_id=None,
        allowed_files=None,
    )


def test_subagent_file_policy_validation(isolated_env):
    docs = isolated_env["docs"]
    allowed_file = docs / "allowed.txt"
    allowed_file.write_text("ok\n", encoding="utf-8")

    # Empty / None
    assert not SubagentFilePolicy.from_paths(None).has_permissions
    assert not SubagentFilePolicy.from_paths([]).has_permissions

    # Reject non-list types and malformed entries
    with pytest.raises(TypeError):
        SubagentFilePolicy.from_paths("allowed.txt")
    with pytest.raises(ValueError):
        SubagentFilePolicy.from_paths([""])
    with pytest.raises(ValueError):
        SubagentFilePolicy.from_paths(["bad\x00path.txt"])
    with pytest.raises(ValueError):
        SubagentFilePolicy.from_paths(["/"])

    # Relative path (against ~/Documents) and tilde path resolve to same canonical path
    policy = SubagentFilePolicy.from_paths(["allowed.txt", "~/Documents/allowed.txt"])
    assert len(policy.allowed_files) == 1
    ok, real_p, err = policy.authorize_path(str(allowed_file))
    assert ok is True
    assert err is None
    assert real_p == os.path.realpath(allowed_file)


def test_subagent_file_policy_dir_prefix_and_symlink_blocks(isolated_env):
    docs = isolated_env["docs"]
    allowed_dir = docs / "project"
    allowed_dir.mkdir()
    inside_file = allowed_dir / "main.py"
    inside_file.write_text("print('hi')\n", encoding="utf-8")

    # Similar-prefix sibling directory must NOT match
    sibling_dir = docs / "project_secret"
    sibling_dir.mkdir()
    secret_file = sibling_dir / "keys.txt"
    secret_file.write_text("SECRET=123\n", encoding="utf-8")

    # Symlink inside allowed_dir pointing outside must be blocked
    escape_link = allowed_dir / "escape.txt"
    escape_link.symlink_to(secret_file)

    # Dangling symlink inside allowed_dir pointing outside must also be blocked
    dangling_escape = allowed_dir / "dangling_escape.txt"
    dangling_escape.symlink_to(sibling_dir / "future_secret.txt")

    policy = SubagentFilePolicy.from_paths([str(allowed_dir)])
    assert policy.authorize_path(str(inside_file))[0] is True
    assert policy.authorize_path("project/nested/new_file.py")[0] is True
    assert policy.authorize_path(str(secret_file))[0] is False
    assert policy.authorize_path(str(allowed_dir / ".." / "project_secret" / "keys.txt"))[0] is False
    assert policy.authorize_path(str(escape_link))[0] is False
    assert policy.authorize_path(str(dangling_escape))[0] is False


def test_subagent_file_toolset_read_and_edit_operations(isolated_env):
    docs = isolated_env["docs"]
    proj = docs / "proj"
    proj.mkdir()
    target = proj / "code.py"
    target.write_text("alpha = 1\nbeta = 2\nbeta = 2\n", encoding="utf-8")
    os.chmod(target, 0o750)

    new_file = docs / "created_report.md"
    policy = SubagentFilePolicy.from_paths([str(proj), str(new_file)])
    toolset = SubagentFileToolset(policy=policy, subagent_id="sub_rw")

    # 1. Read allowed file
    read_res = toolset.execute_tool_call("File_read", {"file_path": str(target)})
    assert "1 | alpha = 1" in read_res
    assert "3 | beta = 2" in read_res

    # 2. Directory passed to File_read returns directory error without Terminal_run hint
    dir_res = toolset.execute_tool_call("File_read", {"file_path": str(proj)})
    assert "is a directory, not a regular file" in dir_res
    assert "Terminal_run" not in dir_res

    # 3. Ambiguous old_text fails without modifying file
    ambig_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(target), "old_text": "beta = 2", "new_text": "beta = 3"},
    )
    assert "matched 2 times" in ambig_res
    assert "beta = 2" in target.read_text(encoding="utf-8")

    # 4. replace_all=True updates all matches and preserves mode bits
    repl_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(target), "old_text": "beta = 2", "new_text": "beta = 3", "replace_all": True},
    )
    assert "Successfully edited" in repl_res
    assert "replaced 2 occurrences" in repl_res
    assert target.read_text(encoding="utf-8") == "alpha = 1\nbeta = 3\nbeta = 3\n"
    assert (os.stat(target).st_mode & 0o777) == 0o750

    # 5. Unique targeted edit
    uniq_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(target), "old_text": "alpha = 1", "new_text": "alpha = 10"},
    )
    assert "replaced 1 occurrence" in uniq_res
    assert target.read_text(encoding="utf-8").startswith("alpha = 10\n")

    # 6. Missing old_text fails cleanly
    miss_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(target), "old_text": "nonexistent_symbol", "new_text": "x"},
    )
    assert "was not found" in miss_res
    assert "File_read" in miss_res

    # 7. Mutually exclusive args / invalid edits rejected
    both_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(target), "old_text": "alpha = 10", "new_text": "x", "content": "full"},
    )
    assert "not both" in both_res

    empty_old = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(target), "old_text": "", "new_text": "x"},
    )
    assert "cannot be empty" in empty_old

    # 8. Create explicit allowed new file via content, plus nested file inside allowed dir
    create_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(new_file), "content": "# Report\nDone.\n"},
    )
    assert "Successfully created" in create_res
    assert new_file.read_text(encoding="utf-8") == "# Report\nDone.\n"

    nested_file = proj / "sub" / "notes.txt"
    nested_res = toolset.execute_tool_call(
        "File_edit",
        {"file_path": str(nested_file), "content": "nested ok\n"},
    )
    assert "Successfully created" in nested_res
    assert nested_file.read_text(encoding="utf-8") == "nested ok\n"

    # 9. Binary file safety
    bin_file = proj / "blob.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03")
    bin_read = toolset.execute_tool_call("File_read", {"file_path": str(bin_file)})
    assert "binary file" in bin_read
    assert "Media_read" not in bin_read
    bin_edit = toolset.execute_tool_call(
        "File_edit", {"file_path": str(bin_file), "content": "overwrite"}
    )
    assert "binary file" in bin_edit


def test_subagent_blocks_unauthorized_paths_without_existence_oracle(isolated_env):
    docs = isolated_env["docs"]
    allowed = docs / "allowed.txt"
    allowed.write_text("ok", encoding="utf-8")

    existing_secret = docs / "secret_exists.txt"
    existing_secret.write_text("top secret", encoding="utf-8")
    missing_secret = docs / "secret_missing.txt"
    secret_dir = docs / "secret_dir"
    secret_dir.mkdir()

    policy = SubagentFilePolicy.from_paths([str(allowed)])
    toolset = SubagentFileToolset(policy=policy, subagent_id="sub_oracle")

    r_exist = toolset.execute_tool_call("File_read", {"file_path": str(existing_secret)})
    r_miss = toolset.execute_tool_call("File_read", {"file_path": str(missing_secret)})
    r_dir = toolset.execute_tool_call("File_read", {"file_path": str(secret_dir)})

    assert r_exist == f"Error: Access denied. Subagent is not permitted to interact with '{existing_secret}'."
    assert r_miss == f"Error: Access denied. Subagent is not permitted to interact with '{missing_secret}'."
    assert r_dir == f"Error: Access denied. Subagent is not permitted to interact with '{secret_dir}'."

    w_exist = toolset.execute_tool_call(
        "File_edit", {"file_path": str(existing_secret), "content": "hacked"}
    )
    assert w_exist.startswith("Error: Access denied.")
    assert existing_secret.read_text(encoding="utf-8") == "top secret"

    # Empty policy also denies all File_read / File_edit calls
    empty_toolset = SubagentFileToolset(policy=SubagentFilePolicy(), subagent_id="sub_empty")
    assert empty_toolset.get_tool_declarations() == []
    assert empty_toolset.execute_tool_call("File_read", {"file_path": str(allowed)}).startswith("Error: Access denied.")
    assert empty_toolset.execute_tool_call("File_edit", {"file_path": str(allowed), "content": "x"}).startswith("Error: Access denied.")


def test_subagent_concurrent_edits_stay_consistent(isolated_env):
    target = isolated_env["docs"] / "concurrent.txt"
    target.write_text("line_a\n", encoding="utf-8")
    policy = SubagentFilePolicy.from_paths([str(target)])
    toolset = SubagentFileToolset(policy=policy, subagent_id="sub_conc")

    errors = []

    def worker(idx):
        res = toolset.execute_tool_call(
            "File_edit",
            {"file_path": str(target), "content": f"writer_{idx}\n"},
        )
        if res.startswith("Error:"):
            errors.append(res)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    final_content = target.read_text(encoding="utf-8")
    assert final_content.startswith("writer_") and final_content.endswith("\n")


def test_subagent_worker_declares_file_tools_only_when_permitted(isolated_env):
    allowed = isolated_env["docs"] / "notes.md"
    allowed.write_text("hello\n", encoding="utf-8")

    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"), \
         patch("google.genai.Client"):
        orch = MagicMock()
        orch.agent_id = "test_subagent_worker"
        fake_web = MagicMock()
        fake_web.get_tool_declarations.return_value = [
            {"name": "WebSearch_search", "description": "search", "parameters": {"type": "OBJECT", "properties": {}}}
        ]
        fake_term = MagicMock()
        fake_term.get_tool_declarations.return_value = [
            {"name": "Terminal_run", "description": "shell", "parameters": {"type": "OBJECT", "properties": {}}}
        ]
        orch.cerebrum.tools = {"WebSearch": fake_web, "Terminal": fake_term}

        # 1. Without file permissions -> no File_read / File_edit declared
        w_none = SubagentWorker("sub_none", orch)
        decls_none = [
            fd.name
            for tool in (w_none.thinker_config.tools or [])
            for fd in (tool.function_declarations or [])
        ]
        assert "WebSearch_search" in decls_none
        assert "File_read" not in decls_none
        assert "File_edit" not in decls_none
        assert "Terminal_run" not in decls_none
        assert "do NOT have permission" in w_none.thinker_config.system_instruction

        # 2. With file permissions -> File_read & File_edit declared and listed in prompt
        policy = SubagentFilePolicy.from_paths([str(allowed)])
        w_files = SubagentWorker("sub_files", orch, file_policy=policy)
        decls_files = [
            fd.name
            for tool in (w_files.thinker_config.tools or [])
            for fd in (tool.function_declarations or [])
        ]
        assert "WebSearch_search" in decls_files
        assert "File_read" in decls_files
        assert "File_edit" in decls_files
        assert "Terminal_run" not in decls_files
        assert str(allowed.resolve()) in w_files.thinker_config.system_instruction


def test_orchestrator_spawn_and_execution_time_sandbox(isolated_env):
    orch = _build_minimal_orchestrator(isolated_env["tmp"])
    docs = isolated_env["docs"]
    allowed = docs / "task.txt"
    allowed.write_text("status=pending\n", encoding="utf-8")
    forbidden = docs / "forbidden.txt"
    forbidden.write_text("private\n", encoding="utf-8")

    # Invalid allowed_files fails cleanly before spawning
    bad_spawn = orch.spawn_subagent("Do work", allowed_files="task.txt")
    assert bad_spawn.startswith("Error:")
    assert not orch.active_subagents

    with patch.object(SettingsManager, "get_env", return_value="test_key_12345"), \
         patch("google.genai.Client"), \
         patch.object(SubagentWorker, "send_prompt"):
        spawn_msg = orch.spawn_subagent("Update task", allowed_files=[str(allowed)])
        assert "spawned" in spawn_msg
        assert len(orch.active_subagents) == 1
        sid = next(iter(orch.active_subagents.keys()))
        subagent = orch.active_subagents[sid]
        subagent.send_function_responses = MagicMock()

        status = orch.list_subagents()
        assert sid in status
        assert "task.txt" in status

        orch.cerebrum.execute_tool_call.return_value = "Search OK"

        calls = [
            SimpleNamespace(name="File_read", args={"file_path": str(allowed)}),
            SimpleNamespace(
                name="File_edit",
                args={"file_path": str(allowed), "old_text": "status=pending", "new_text": "status=done"},
            ),
            SimpleNamespace(name="File_read", args={"file_path": str(forbidden)}),
            SimpleNamespace(name="File_edit", args={"file_path": str(forbidden), "content": "pwned"}),
            SimpleNamespace(name="Terminal_run", args={"command_str": "cat forbidden.txt"}),
            SimpleNamespace(name="Terminal_read_file", args={"file_path": str(forbidden)}),
            SimpleNamespace(name="System_sleep", args={"reason": "bypass"}),
            SimpleNamespace(name="WebSearch_search", args={"query": "open amity"}),
        ]

        orch._async_subagent_tool_execution(sid, calls)

        assert subagent.send_function_responses.called
        responses = subagent.send_function_responses.call_args[0][0]
        assert len(responses) == len(calls)

        # Authorized File_read & File_edit succeed
        assert responses[0][0] == "File_read"
        assert "1 | status=pending" in responses[0][1]["result"]
        assert responses[1][0] == "File_edit"
        assert "Successfully edited" in responses[1][1]["result"]
        assert allowed.read_text(encoding="utf-8") == "status=done\n"

        # Unauthorized File_read & File_edit blocked
        assert responses[2][1]["result"].startswith("Error: Access denied.")
        assert responses[3][1]["result"].startswith("Error: Access denied.")
        assert forbidden.read_text(encoding="utf-8") == "private\n"

        # Disallowed Cerebrum tools blocked before reaching Cerebrum
        assert "Permission denied" in responses[4][1]["error"]
        assert "Permission denied" in responses[5][1]["error"]
        assert "Permission denied" in responses[6][1]["error"]

        # WebSearch allowed and routed to Cerebrum
        assert responses[7] == ("WebSearch_search", {"result": "Search OK"})
        orch.cerebrum.execute_tool_call.assert_called_once_with(
            "WebSearch_search", {"query": "open amity"}
        )
