import subprocess
import os
import logging
import threading
import time
import tempfile
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.file_ops import format_file_size, read_text_file


class TerminalSkill(Tool):
    name = "Terminal"
    icon = "💻"
    color = "#F39C12"
    async_commands = ["run_async"]
    description = "Allows the agent to execute bash commands on the local system."
    commands = ["run", "run_async", "check_status", "kill_task", "flag_for_backup", "read_file"]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        self.persistent_sessions = {}
        self.tasks = {}
        self.task_counter = 0
        self.lock = threading.Lock()

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Terminal_run",
                "description": "Runs a bash command synchronously (30s timeout). Prefer Terminal_read_file for reading files. Sandbox allows ~/Documents, ~/Pictures, ~/Downloads, ~/Desktop.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command_str": {
                            "type": "STRING",
                            "description": "Bash command string to execute."
                        },
                        "as_sudo": {
                            "type": "BOOLEAN",
                            "description": "Run with superuser/sudo privileges."
                        }
                    },
                    "required": ["command_str"]
                }
            },
            {
                "name": "Terminal_read_file",
                "description": (
                    "Reads text files with line numbers, range slicing, case-insensitive search queries, and line-length guards. "
                    "Use instead of 'cat' via Terminal_run."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "Path to file (absolute, ~/..., or relative to ~/Documents)."
                        },
                        "start_line": {
                            "type": "INTEGER",
                            "description": "1-indexed start line (default: 1)."
                        },
                        "end_line": {
                            "type": "INTEGER",
                            "description": "1-indexed inclusive end line; defaults to start_line + max_lines - 1."
                        },
                        "max_lines": {
                            "type": "INTEGER",
                            "description": "Max lines returned per call (default: 500, or 100 in Low Token Mode; max: 1000)."
                        },
                        "show_line_numbers": {
                            "type": "BOOLEAN",
                            "description": "Prefix 1-indexed line numbers (default: true)."
                        },
                        "query": {
                            "type": "STRING",
                            "description": "Optional case-insensitive substring to locate with context lines (or jump via jump_to_match)."
                        },
                        "context_lines": {
                            "type": "INTEGER",
                            "description": "Context lines before/after each match when 'query' is set (default: 3)."
                        },
                        "jump_to_match": {
                            "type": "BOOLEAN",
                            "description": "Jump start_line to first match and read sequentially for max_lines (default: false)."
                        },
                        "max_line_length": {
                            "type": "INTEGER",
                            "description": "Truncate lines past N chars (default: None, or 300 in Low Token Mode)."
                        }
                    },
                    "required": ["file_path"]
                }
            },
            {
                "name": "Terminal_run_async",
                "description": "Executes a long-running bash command asynchronously. Returns a task_id immediately. You will receive a system pulse when it completes.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command_str": {
                            "type": "STRING",
                            "description": "The bash command string to execute."
                        },
                        "reminder_minutes": {
                            "type": "INTEGER",
                            "description": "Minutes before the system reminds you the task is still running. Default 2."
                        },
                        "as_sudo": {
                            "type": "BOOLEAN",
                            "description": "Set to true if the command requires superuser/sudo privileges."
                        }
                    },
                    "required": ["command_str"]
                }
            },
            {
                "name": "Terminal_check_status",
                "description": "Checks the status and output of an asynchronous background task.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task_id": {
                            "type": "INTEGER",
                            "description": "The ID of the task to check."
                        }
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "Terminal_kill_task",
                "description": "Terminates a running asynchronous background task.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task_id": {
                            "type": "INTEGER",
                            "description": "The ID of the task to kill."
                        }
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "Terminal_flag_for_backup",
                "description": (
                    "Manages external files/directories included in .oaa snapshots. Auto-prunes missing and redundant paths on each call."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "File or directory path (e.g. '~/Documents/Nova/Code/MyProject'); required for 'add'/'remove'."
                        },
                        "action": {
                            "type": "STRING",
                            "description": "'add' (default), 'remove', or 'list'.",
                            "enum": ["add", "remove", "list"]
                        }
                    }
                }
            }
        ]

    def _format_output(self, stdout: str, stderr: str) -> str:
        output = stdout if stdout else ""
        if stderr:
            output += f"\nSTDERR:\n{stderr}"

        is_low_token = self.orchestrator.settings_manager.get(
            "core.low-token-mode", False) if self.orchestrator else False
        max_length = 500 if is_low_token else 30000

        if len(output) > max_length:
            output = output[:max_length] + \
                f"\n... [Output truncated to {max_length} characters]"

        if not output.strip():
            output = "[Command executed successfully with no output]"

        return output

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "run_async":
            return self._run_async(**kwargs)
        clean_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        if command == "run":
            return self._run_sync(**clean_kwargs)
        elif command == "check_status":
            return self._check_status(**clean_kwargs)
        elif command == "kill_task":
            return self._kill_task(**clean_kwargs)
        elif command == "flag_for_backup":
            return self._flag_for_backup(*args, **clean_kwargs)
        elif command == "read_file":
            return self._read_file(*args, **clean_kwargs)
        return f"Unknown command: {command}"

    def _get_command_args(self, command_str, as_sudo):
        env_sudo_pass = ""
        if self.orchestrator and hasattr(self.orchestrator, 'settings_manager'):
            env_sudo_pass = self.orchestrator.settings_manager.get_env(
                "SUDO_PASSWORD") or ""
        cwd_path = os.path.expanduser("~/Documents")

        if as_sudo:
            if not env_sudo_pass or env_sudo_pass == "your_password_here":
                raise ValueError(
                    "SUDO_PASSWORD is not configured in the environment. Please add it to the .env file.")
            return ["sudo", "-S", "bash", "-c", command_str], f"{env_sudo_pass}\n", cwd_path
        else:
            return ["bash", "-c", command_str], None, cwd_path

    def _run_sync(self, command_str=None, as_sudo=False, **kwargs):
        if not command_str:
            return "Error: Missing command_str parameter."
        try:
            full_command, stdin_input, cwd_path = self._get_command_args(
                command_str, as_sudo)
            result = subprocess.run(
                full_command,
                input=stdin_input,
                text=True,
                capture_output=True,
                check=False,
                cwd=cwd_path,
                timeout=30
            )
            return self._format_output(result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return "Error: Command execution timed out after 30 seconds. For long-running tasks, use Terminal_run_async."
        except Exception as e:
            logging.error(f"Terminal execution error: {e}", exc_info=True)
            return f"Error executing command: {e}"

    def _run_async(self, command_str=None, reminder_minutes=2, as_sudo=False, _call_id=None, **kwargs):
        if not command_str:
            return "Error: Missing command_str parameter."
        try:
            full_command, stdin_input, cwd_path = self._get_command_args(
                command_str, as_sudo)

            out_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
            err_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)

            process = subprocess.Popen(
                full_command,
                stdin=subprocess.PIPE if stdin_input else None,
                stdout=out_file,
                stderr=err_file,
                cwd=cwd_path,
                text=True
            )

            if stdin_input:
                process.stdin.write(stdin_input)
                process.stdin.flush()
                process.stdin.close()

            with self.lock:
                self.task_counter += 1
                task_id = self.task_counter
                self.tasks[task_id] = {
                    "process": process,
                    "command_str": command_str,
                    "out_file": out_file,
                    "err_file": err_file,
                    "start_time": time.time(),
                    "reminder_minutes": reminder_minutes,
                    "completed": False,
                    "call_id": _call_id
                }

            threading.Thread(target=self._monitor_task,
                             args=(task_id,), daemon=True).start()
            return f"Task started in background with ID: {task_id}. You will be notified when it completes."

        except Exception as e:
            logging.error(
                f"Terminal async execution error: {e}", exc_info=True)
            return f"Error starting async command: {e}"

    def _monitor_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
        if not task:
            return

        process = task["process"]
        reminder_seconds = task["reminder_minutes"] * 60
        start_time = task["start_time"]

        reminded = False
        while process.poll() is None:
            time.sleep(1)
            if not reminded and (time.time() - start_time) > reminder_seconds:
                reminded = True
                prompt = f"[SYSTEM_NOTIFICATION] Background Task {task_id} ('{task['command_str']}') is still running. You can check its status using Terminal_check_status or let it continue."
                if hasattr(self, 'orchestrator') and self.orchestrator and hasattr(self.orchestrator, 'pulse_engine'):
                    self.orchestrator.pulse_engine.trigger_pulse.emit(prompt, purpose="Terminal command in progress")

        with self.lock:
            task["completed"] = True
            call_id = task.get("call_id")

        if call_id and hasattr(self, 'orchestrator') and self.orchestrator and hasattr(self.orchestrator, 'on_tool_finished'):
            self.orchestrator.on_tool_finished.emit(call_id)

        try:
            task["out_file"].seek(0)
            task["err_file"].seek(0)
            stdout = task["out_file"].read()
            stderr = task["err_file"].read()
            output = self._format_output(stdout, stderr)
        except Exception as e:
            output = f"Error reading output: {e}"

        prompt = f"[SYSTEM_NOTIFICATION] Background Task {task_id} ('{task['command_str']}') has completed.\n\nOutput:\n{output}"

        if hasattr(self, 'orchestrator') and self.orchestrator and hasattr(self.orchestrator, 'pulse_engine'):
            self.orchestrator.pulse_engine.trigger_pulse.emit(prompt, purpose="Terminal command completed")

    def _check_status(self, task_id=None):
        if task_id is None:
            return "Error: Missing task_id parameter."
        with self.lock:
            task = self.tasks.get(task_id)

        if not task:
            return f"Error: No task found with ID {task_id}."

        try:
            task["out_file"].seek(0)
            task["err_file"].seek(0)
            stdout = task["out_file"].read()
            stderr = task["err_file"].read()
            output = self._format_output(stdout, stderr)
        except Exception as e:
            output = f"Error reading output: {e}"

        if task["completed"]:
            return f"Task {task_id} is COMPLETE.\nOutput:\n{output}"
        else:
            return f"Task {task_id} is STILL RUNNING.\nPartial Output:\n{output}"

    def _kill_task(self, task_id=None):
        if task_id is None:
            return "Error: Missing task_id parameter."
        with self.lock:
            task = self.tasks.get(task_id)

        if not task:
            return f"Error: No task found with ID {task_id}."

        if task["completed"]:
            return f"Task {task_id} has already completed."

        try:
            task["process"].terminate()
            for _ in range(10):
                if task["process"].poll() is not None:
                    break
                time.sleep(0.1)
            if task["process"].poll() is None:
                task["process"].kill()

            return f"Task {task_id} has been terminated."
        except Exception as e:
            return f"Error terminating task: {e}"

    def _flag_for_backup(self, path=None, action="add", **kwargs) -> str:
        agent_id = None
        if self.orchestrator:
            agent_id = getattr(self.orchestrator, "agent_id", None)

        if not agent_id:
            return "Error: Agent ID is not available from orchestrator to manage backup targets."

        from core.backup_manager import sync_and_clean_backup_targets

        success, msg, removals, current_targets = sync_and_clean_backup_targets(
            agent_id=agent_id,
            candidate_path=path,
            action=action
        )

        lines = [f"=== Backup Flagging Result: {'SUCCESS' if success else 'NOTICE'} ==="]
        lines.append(msg)

        if removals:
            lines.append("\nPruned from backup targets:")
            for r in removals:
                reason = r.get("reason", "unknown")
                detail = f" ({r['detail']})" if r.get("detail") else ""
                lines.append(f"- '{r['path']}': Removed [{reason}]{detail}")

        if current_targets:
            lines.append(f"\nCurrent active flagged backup targets ({len(current_targets)}):")
            for t in current_targets:
                tag = "[DIR]" if t.get("type") == "directory" else "[FILE]"
                lines.append(f"- {tag} {t['path']}")
        else:
            lines.append("\nNo external files or directories are currently flagged for backup.")

        return "\n".join(lines)

    def _format_size(self, num_bytes: int) -> str:
        return format_file_size(num_bytes)

    def _read_file(
        self,
        file_path: str = None,
        start_line: int = 1,
        end_line: int = None,
        max_lines: int = None,
        show_line_numbers: bool = True,
        query: str = None,
        context_lines: int = 3,
        jump_to_match: bool = False,
        max_line_length: int = None,
        **kwargs
    ) -> str:
        is_low_token = False
        if self.orchestrator and hasattr(self.orchestrator, "settings_manager"):
            is_low_token = self.orchestrator.settings_manager.get("core.low-token-mode", False) is True

        return read_text_file(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
            show_line_numbers=show_line_numbers,
            query=query,
            context_lines=context_lines,
            jump_to_match=jump_to_match,
            max_line_length=max_line_length,
            is_low_token=is_low_token,
            tool_name="Terminal_read_file",
            dir_hint="To inspect directory contents, run 'ls -la' using Terminal_run.",
            binary_hint="For multimedia assets (images, audio, PDF), use Media_read.",
            permission_hint="If superuser privileges are required, use Terminal_run(as_sudo=True).",
        )

    def shutdown(self):
        logging.info("TerminalSkill: Shutting down background tasks...")
        with self.lock:
            for task_id, task in self.tasks.items():
                if not task["completed"]:
                    try:
                        task["process"].terminate()
                        task["process"].kill()
                    except Exception as e:
                        logging.error(
                            f"Error killing task {task_id} on shutdown: {e}")

                try:
                    task["out_file"].close()
                    task["err_file"].close()
                    os.unlink(task["out_file"].name)
                    os.unlink(task["err_file"].name)
                except:
                    pass
            self.tasks.clear()
