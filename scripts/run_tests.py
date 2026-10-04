#!/usr/bin/env python3
"""Open Amity Smart Test Runner

Executes targeted, compartmentalised tests based on modified code or domain filters,
drastically reducing development test run times and context token usage.

Usage:
    python3 scripts/run_tests.py --changed      # (Default) Run only tests relevant to git diff
    python3 scripts/run_tests.py --quick        # Run all fast unit tests (skip slow/integration)
    python3 scripts/run_tests.py --domain core  # Run only core tests (core | tools | workers | gui)
    python3 scripts/run_tests.py --all          # Run the complete test suite
    python3 scripts/run_tests.py tests/test_file_utils.py  # Run specific test file(s)
"""

import sys
import os
import subprocess
import argparse
from typing import List, Set

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Explicit mapping from source files/directories to corresponding test files
FILE_TO_TEST_MAP = {
    "src/core/agent_manager.py": ["tests/test_agent_refresh.py"],
    "src/core/api_key_manager.py": ["tests/test_api_key_manager.py"],
    "src/core/audio_output.py": ["tests/test_tts_decoupling.py", "tests/test_speaker_speak_to_file.py"],
    "src/core/backup_manager.py": ["tests/test_backup_manager.py"],
    "src/core/browser_manager.py": ["tests/test_browser_tool.py", "tests/test_browser_accessibility.py"],
    "src/core/cerebrum.py": ["tests/test_tool_pips.py", "tests/test_terminal_tool.py"],
    "src/core/chatgpt_worker.py": ["tests/test_chatgpt_worker.py"],
    "src/core/chatroom_manager.py": ["tests/test_chatroom.py"],
    "src/core/claude_worker.py": ["tests/test_claude_worker.py"],
    "src/core/config_manager.py": ["tests/test_config_manager.py"],
    "src/core/deepseek_worker.py": ["tests/test_deepseek_worker.py"],
    "src/core/events.py": ["tests/test_events.py"],
    "src/core/file_utils.py": ["tests/test_file_utils.py"],
    "src/core/gemini_worker.py": ["tests/test_gemini_worker.py"],
    "src/core/hook_server.py": ["tests/test_pulse_hook_server.py"],
    "src/core/mempalace_manager.py": ["tests/test_mempalace_tool.py", "tests/test_morphological_mempalace.py", "tests/test_mirror_epistemics.py"],
    "src/core/morphological_layer.py": ["tests/test_morphological_mempalace.py"],
    "src/core/openrouter_worker.py": ["tests/test_openrouter_worker.py"],
    "src/core/orchestrator.py": ["tests/test_orchestrator_prompt.py", "tests/test_pause_agent.py"],
    "src/core/pulse_engine.py": ["tests/test_pulse_engine_improvements.py"],
    "src/core/settings_manager.py": ["tests/test_agent_refresh.py", "tests/test_pause_agent.py"],
    "src/core/subagent_worker.py": ["tests/test_gemini_worker.py"],
    "src/core/uid_generator.py": ["tests/test_uid_generator.py"],
    "src/core/whatsapp_daemon.py": ["tests/test_whatsapp_daemon.py"],

    # Tools
    "src/tools/browser_tool.py": ["tests/test_browser_tool.py", "tests/test_browser_accessibility.py"],
    "src/tools/mempalace_tool.py": ["tests/test_mempalace_tool.py"],
    "src/tools/moltbook_tool.py": ["tests/test_moltbook_tool.py"],
    "src/tools/pulse_tool.py": ["tests/test_tool_pips.py", "tests/test_pulse_engine_improvements.py"],
    "src/tools/speaker_tool.py": ["tests/test_tts_decoupling.py", "tests/test_speaker_speak_to_file.py"],
    "src/tools/system_tool.py": ["tests/test_system_tool.py"],
    "src/tools/terminal_tool.py": ["tests/test_terminal_tool.py"],
    "src/tools/trajectory_tool.py": ["tests/test_trajectory_tool.py"],
    "src/tools/whatsapp_tool.py": ["tests/test_whatsapp_daemon.py"],

    # GUI
    "src/gui/chat_formatter.py": ["tests/test_chat_formatter.py"],
    "src/gui/editable_list_widget.py": ["tests/test_values_goals_gui.py"],
    "src/gui/main_window.py": ["tests/test_main_window_menu.py", "tests/test_prompt_text_edit.py"],
    "src/gui/settings_panel.py": [
        "tests/test_provider_settings_gui.py",
        "tests/test_system_settings_gui.py",
        "tests/test_provider_accordion.py",
        "tests/test_voice_accordion.py",
        "tests/test_social_accordion.py",
        "tests/test_email_settings_gui.py"
    ],
    "src/gui/tool_pips.py": ["tests/test_tool_pips.py"],
    "src/gui/theme.py": ["tests/test_main_window_menu.py"],

    # Config
    "src/config/presets.py": ["tests/test_agent_presets.py"],
    "src/config/paths.py": ["tests/test_file_utils.py", "tests/test_config_manager.py"],
}

# Domain to test file mapping
DOMAIN_MAP = {
    "core": [
        "tests/test_agent_refresh.py",
        "tests/test_api_key_manager.py",
        "tests/test_backup_manager.py",
        "tests/test_chatroom.py",
        "tests/test_config_manager.py",
        "tests/test_events.py",
        "tests/test_file_utils.py",
        "tests/test_identity_evolution.py",
        "tests/test_mirror_epistemics.py",
        "tests/test_morphological_mempalace.py",
        "tests/test_orchestrator_prompt.py",
        "tests/test_pause_agent.py",
        "tests/test_pulse_engine_improvements.py",
        "tests/test_uid_generator.py",
    ],
    "tools": [
        "tests/test_mempalace_tool.py",
        "tests/test_moltbook_tool.py",
        "tests/test_speaker_speak_to_file.py",
        "tests/test_system_tool.py",
        "tests/test_terminal_tool.py",
        "tests/test_trajectory_tool.py",
    ],
    "workers": [
        "tests/test_chatgpt_worker.py",
        "tests/test_claude_worker.py",
        "tests/test_deepseek_worker.py",
        "tests/test_gemini_worker.py",
        "tests/test_openrouter_worker.py",
    ],
    "gui": [
        "tests/test_agent_presets.py",
        "tests/test_chat_formatter.py",
        "tests/test_email_settings_gui.py",
        "tests/test_main_window_menu.py",
        "tests/test_prompt_text_edit.py",
        "tests/test_provider_accordion.py",
        "tests/test_provider_settings_gui.py",
        "tests/test_social_accordion.py",
        "tests/test_system_settings_gui.py",
        "tests/test_tool_pips.py",
        "tests/test_tts_decoupling.py",
        "tests/test_values_goals_gui.py",
        "tests/test_voice_accordion.py",
    ],
    "integration": [
        "tests/test_browser_tool.py",
        "tests/test_browser_accessibility.py",
        "tests/test_whatsapp_daemon.py",
        "tests/test_pulse_hook_server.py",
    ],
}


def get_git_changed_files() -> List[str]:
    """Retrieves list of modified and untracked files relative to REPO_ROOT."""
    changed = set()
    try:
        # Check unstaged and staged modified files
        res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            changed.update(line.strip() for line in res.stdout.splitlines() if line.strip())

        # Check untracked files
        res_untracked = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )
        if res_untracked.returncode == 0:
            for line in res_untracked.stdout.splitlines():
                line = line.strip()
                if line.startswith("?? ") or line.startswith("M "):
                    parts = line.split(maxsplit=1)
                    if len(parts) == 2:
                        changed.add(parts[1])
    except Exception as e:
        print(f"[Warning] Failed to query git status: {e}", file=sys.stderr)

    return sorted(list(changed))


def resolve_tests_for_changed_files(changed_files: List[str]) -> Set[str]:
    """Maps changed source and test files to the specific test files that need running."""
    tests_to_run = set()

    for path in changed_files:
        path = path.replace("\\", "/")

        # Direct test file modified
        if path.startswith("tests/") and path.endswith(".py"):
            full_test_path = os.path.join(REPO_ROOT, path)
            if os.path.exists(full_test_path):
                tests_to_run.add(path)
            continue

        # Exact file mapping
        if path in FILE_TO_TEST_MAP:
            for test_file in FILE_TO_TEST_MAP[path]:
                if os.path.exists(os.path.join(REPO_ROOT, test_file)):
                    tests_to_run.add(test_file)
            continue

        # Heuristic matching: src/<dir>/<name>.py -> tests/test_<name>.py
        if path.startswith("src/") and path.endswith(".py"):
            base_name = os.path.basename(path)
            guessed_test = f"tests/test_{base_name}"
            if os.path.exists(os.path.join(REPO_ROOT, guessed_test)):
                tests_to_run.add(guessed_test)
                continue

            # Fallback by directory
            if "/gui/" in path:
                tests_to_run.update(DOMAIN_MAP["gui"][:3])
            elif "/core/" in path:
                tests_to_run.update(DOMAIN_MAP["core"][:3])
            elif "/tools/" in path:
                tests_to_run.update(DOMAIN_MAP["tools"][:3])

    return tests_to_run


def main():
    parser = argparse.ArgumentParser(
        description="Open Amity Smart Test Runner",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("tests", nargs="*", help="Specific test files or expressions to run")
    parser.add_argument("--changed", action="store_true", help="Run tests mapped to modified git files (default if changes exist)")
    parser.add_argument("--quick", action="store_true", help="Run fast unit tests (skip slow and integration)")
    parser.add_argument("--domain", choices=["core", "tools", "workers", "gui", "integration"], help="Run tests for a specific domain")
    parser.add_argument("--all", action="store_true", help="Run the entire test suite")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose pytest output")
    parser.add_argument("-k", type=str, help="Pytest expression filter (-k)")

    args, extra_pytest_args = parser.parse_known_args()

    pytest_bin = os.path.join(REPO_ROOT, ".venv", "bin", "pytest")
    if not os.path.exists(pytest_bin):
        pytest_bin = "pytest"

    cmd = [pytest_bin, "-ra", "--tb=short"]
    if args.verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")

    if args.k:
        cmd.extend(["-k", args.k])

    if extra_pytest_args:
        cmd.extend(extra_pytest_args)

    # 1. User specified explicit test files
    if args.tests:
        cmd.extend(args.tests)
        print(f"[*] Running specified tests: {' '.join(args.tests)}")
        sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)

    # 2. Domain filter
    if args.domain:
        test_files = DOMAIN_MAP.get(args.domain, [])
        valid_files = [f for f in test_files if os.path.exists(os.path.join(REPO_ROOT, f))]
        cmd.extend(valid_files)
        print(f"[*] Running {args.domain} domain tests ({len(valid_files)} test files)...")
        sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)

    # 3. Quick mode (not slow and not integration)
    if args.quick:
        cmd.extend(["-m", "not slow and not integration"])
        print("[*] Running quick unit tests (skipping slow and integration)...")
        sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)

    # 4. Explicit --all requested
    if args.all:
        cmd.append("tests")
        print("[*] Running entire test suite...")
        sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)

    # 5. Default: --changed mode
    changed_files = get_git_changed_files()
    matched_tests = resolve_tests_for_changed_files(changed_files)

    if not matched_tests:
        if changed_files:
            print(f"[*] Git changes detected ({len(changed_files)} files), but none affect testable Python code (e.g. docs/configs).")
            print("    Skipping test execution. Use --all or --quick if you wish to run tests manually.")
            sys.exit(0)
        else:
            print("[*] No working tree changes detected.")
            print("[*] Defaulting to quick unit tests (use --all to run full suite)...")
            cmd.extend(["-m", "not slow and not integration"])
            sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)

    selected_tests = sorted(list(matched_tests))
    print(f"[*] Detected {len(changed_files)} changed files. Running {len(selected_tests)} mapped test files:")
    for t in selected_tests:
        print(f"    - {t}")

    cmd.extend(selected_tests)
    sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)


if __name__ == "__main__":
    main()
