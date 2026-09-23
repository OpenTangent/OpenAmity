import subprocess
import os
import logging
import threading
import time
import tempfile
from typing import List, Dict, Any
from core.cerebrum import Tool


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
                "description": "Executes a short bash command synchronously (max 30s timeout). Use this for executing commands, running scripts, listing dirs, etc. NOTE: For inspecting text files, use the dedicated Terminal_read_file tool instead. IMPORTANT: Due to Flatpak sandboxing, you generally only have host access to ~/Documents, ~/Pictures, ~/Downloads, and ~/Desktop.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command_str": {
                            "type": "STRING",
                            "description": "The bash command string to execute."
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
                "name": "Terminal_read_file",
                "description": (
                    "Reads the text content of a file on the local file system with line numbers, line slicing/pagination, "
                    "search/grep queries with context lines, line length limits, and file metadata. This is the preferred "
                    "method for inspecting source code, configuration files, logs, and text documents instead of 'cat' via Terminal_run."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "The path to the file to inspect. Can be absolute, relative to ~/Documents, or start with '~'."
                        },
                        "start_line": {
                            "type": "INTEGER",
                            "description": "The 1-indexed line number to start reading from. Defaults to 1."
                        },
                        "end_line": {
                            "type": "INTEGER",
                            "description": "The 1-indexed line number to stop reading at (inclusive). If omitted, reads up to max_lines."
                        },
                        "max_lines": {
                            "type": "INTEGER",
                            "description": "The maximum number of lines to return in a single call (default: 500, or 100 in Low Token Mode; max: 1000)."
                        },
                        "show_line_numbers": {
                            "type": "BOOLEAN",
                            "description": "Whether to prefix each line with its 1-indexed line number (e.g. '   1 | ...'). Defaults to true."
                        },
                        "query": {
                            "type": "STRING",
                            "description": "Optional search pattern to locate within the file (case-insensitive). When provided, returns matching lines with context lines, or jumps to the first match if jump_to_match is true."
                        },
                        "context_lines": {
                            "type": "INTEGER",
                            "description": "Number of surrounding context lines to display before and after each match when 'query' is provided (default: 3)."
                        },
                        "jump_to_match": {
                            "type": "BOOLEAN",
                            "description": "When true and 'query' is provided, jumps start_line directly to the first match and reads continuously for max_lines instead of showing isolated match hunks. Defaults to false."
                        },
                        "max_line_length": {
                            "type": "INTEGER",
                            "description": "Optional maximum character length for individual lines. Lines exceeding this limit are truncated with ' ... [line truncated]' to prevent token spikes (defaults to None, or 300 in Low Token Mode)."
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
                    "Flags a file or directory path to be included in .oaa snapshots. When an .oaa snapshot is created, all flagged files and full directories are included alongside your state files, and restored to their appropriate locations upon restore. Every time this function is called, it automatically checks all paths in the list: missing paths are removed (reason 'missing'), and individual files or subdirectories within an already flagged directory are removed (reason 'redundant'). You will be informed of any removals."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "The file or directory path to flag (e.g. '~/Documents/Nova/Code/MyProject' or '~/Documents/Nova/notes.txt'). Required for action='add' or 'remove'."
                        },
                        "action": {
                            "type": "STRING",
                            "description": "Action to perform: 'add' (default, flags a path for backup), 'remove' (unflags a path), or 'list' (verifies and lists currently flagged paths).",
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
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if num_bytes < 1024.0 or unit == 'TB':
                return f"{num_bytes:.1f} {unit}" if unit != 'B' else f"{num_bytes} B"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} PB"

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
        if not file_path:
            return "Error: Missing file_path parameter."

        file_path_str = str(file_path).strip()
        if not file_path_str:
            return "Error: file_path parameter cannot be empty."

        expanded = os.path.expanduser(file_path_str)
        if not os.path.isabs(expanded):
            full_path = os.path.abspath(os.path.join(os.path.expanduser("~/Documents"), expanded))
        else:
            full_path = os.path.abspath(expanded)

        if not os.path.exists(full_path):
            return f"Error: File not found at '{full_path}'."

        if os.path.isdir(full_path):
            return f"Error: Path '{full_path}' is a directory, not a regular file. To inspect directory contents, run 'ls -la' using Terminal_run."

        if not os.path.isfile(full_path):
            return f"Error: Path '{full_path}' is not a regular file."

        try:
            file_size = os.path.getsize(full_path)
        except Exception as e:
            return f"Error determining size of '{full_path}': {e}"

        if file_size == 0:
            return f"=== File: {file_path_str} (Empty file, 0 B) ==="

        # Binary check: inspect first 8192 bytes
        try:
            with open(full_path, "rb") as f:
                probe = f.read(8192)
                if b"\x00" in probe:
                    return (
                        f"Error: File '{full_path}' appears to be a binary file ({self._format_size(file_size)}). "
                        "For multimedia assets (images, audio, PDF), use Media_read."
                    )
        except PermissionError:
            return f"Error: Permission denied accessing '{full_path}'. If superuser privileges are required, use Terminal_run(as_sudo=True)."
        except Exception as e:
            return f"Error checking file '{full_path}': {e}"

        # Low Token Mode handling
        is_low_token = False
        if self.orchestrator and hasattr(self.orchestrator, "settings_manager"):
            is_low_token = self.orchestrator.settings_manager.get("core.low-token-mode", False)

        default_max_lines = 100 if is_low_token else 500
        max_line_cap = 250 if is_low_token else 1000
        char_limit = 5000 if is_low_token else 30000

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except PermissionError:
            return f"Error: Permission denied reading '{full_path}'. If superuser privileges are required, use Terminal_run(as_sudo=True)."
        except Exception as e:
            return f"Error reading file '{full_path}': {e}"

        total_lines = len(lines)
        if total_lines == 0:
            return f"=== File: {file_path_str} (Empty file, 0 B) ==="

        # Line length truncation helper
        max_len = None
        if max_line_length is not None:
            try:
                max_len = max(1, int(max_line_length))
            except (ValueError, TypeError):
                max_len = None
        elif is_low_token:
            max_len = 300

        def _truncate_line(raw_text: str) -> str:
            clean = raw_text.rstrip("\r\n")
            if max_len is not None and len(clean) > max_len:
                return clean[:max_len] + " ... [line truncated]"
            return clean

        # Normalize show_line_numbers
        if show_line_numbers is None:
            show_numbers = True
        elif isinstance(show_line_numbers, str):
            show_numbers = show_line_numbers.lower() not in ("false", "0", "no")
        else:
            show_numbers = bool(show_line_numbers)

        # Handle search query if provided
        query_str = str(query).strip() if query is not None else ""
        if isinstance(jump_to_match, str):
            jump_to_match = jump_to_match.lower() in ("true", "1", "yes")
        else:
            jump_to_match = bool(jump_to_match)

        jumped_note = ""
        if query_str:
            query_lower = query_str.lower()
            matching_line_nums = [
                idx for idx, line in enumerate(lines, start=1)
                if query_lower in line.lower()
            ]

            if not matching_line_nums:
                return (
                    f"=== File: {file_path_str} (Size: {self._format_size(file_size)}) ===\n"
                    f"Notice: No matches found for query '{query_str}' in {total_lines} lines."
                )

            if jump_to_match:
                start_line = matching_line_nums[0]
                jumped_note = f"Jumped to first match for '{query_str}' at line {start_line}; "
            else:
                # Return match hunks with context lines
                try:
                    ctx_val = max(0, int(context_lines)) if context_lines is not None else 3
                except (ValueError, TypeError):
                    ctx_val = 3

                # Build and merge contiguous / overlapping hunks
                hunks = []
                for m in matching_line_nums:
                    h_start = max(1, m - ctx_val)
                    h_end = min(total_lines, m + ctx_val)
                    if hunks and h_start <= hunks[-1][1] + 1:
                        hunks[-1] = (hunks[-1][0], max(hunks[-1][1], h_end))
                    else:
                        hunks.append((h_start, h_end))

                pad_width = len(str(total_lines))
                match_set = set(matching_line_nums)

                match_summary_lines = ", ".join(map(str, matching_line_nums[:15]))
                if len(matching_line_nums) > 15:
                    match_summary_lines += f", ... ({len(matching_line_nums)} total)"

                header = (
                    f"=== File: {file_path_str} (Found {len(matching_line_nums)} "
                    f"match{'es' if len(matching_line_nums) != 1 else ''} for query '{query_str}' "
                    f"at line{'s' if len(matching_line_nums) != 1 else ''} [{match_summary_lines}], "
                    f"Size: {self._format_size(file_size)}) ===\n"
                )

                current_char_count = len(header)
                truncated_due_to_chars = False
                last_rendered_line = 0
                formatted_hunk_blocks = []

                for h_idx, (h_start, h_end) in enumerate(hunks):
                    if current_char_count >= char_limit:
                        truncated_due_to_chars = True
                        break

                    hunk_lines = []
                    if h_idx > 0:
                        sep = f"{' ' * pad_width}   ---\n"
                        hunk_lines.append(sep)
                        current_char_count += len(sep)

                    for line_num in range(h_start, h_end + 1):
                        clean_line = _truncate_line(lines[line_num - 1])
                        is_match = line_num in match_set
                        marker = ">" if is_match else "|"

                        if show_numbers:
                            rendered_line = f"{line_num:>{pad_width}} {marker} {clean_line}\n"
                        else:
                            prefix = "> " if is_match else "  "
                            rendered_line = f"{prefix}{clean_line}\n"

                        if current_char_count + len(rendered_line) > char_limit:
                            truncated_due_to_chars = True
                            break

                        hunk_lines.append(rendered_line)
                        current_char_count += len(rendered_line)
                        last_rendered_line = line_num

                    formatted_hunk_blocks.append("".join(hunk_lines))
                    if truncated_due_to_chars:
                        break

                output = header + "".join(formatted_hunk_blocks)
                if truncated_due_to_chars:
                    output += f"\n... [Output truncated at {char_limit} characters limit. Call Terminal_read_file with start_line={last_rendered_line + 1} to inspect further]"
                else:
                    output += f"\n... [To read continuously around any match, call Terminal_read_file with start_line=<line_number>]"

                return output.strip()

        # Sequential file slicing
        try:
            start_line_val = int(start_line) if start_line is not None else 1
        except (ValueError, TypeError):
            start_line_val = 1

        if start_line_val < 1:
            start_line_val = 1

        # Determine end_line
        if end_line is not None:
            try:
                end_line_val = int(end_line)
            except (ValueError, TypeError):
                end_line_val = None
        else:
            end_line_val = None

        if end_line_val is not None and end_line_val < start_line_val:
            return f"Error: end_line ({end_line_val}) cannot be less than start_line ({start_line_val})."

        if start_line_val > total_lines:
            return f"Notice: start_line {start_line_val} exceeds total file lines ({total_lines}). File has {total_lines} lines in total."

        # Determine effective limit
        if max_lines is not None:
            try:
                effective_limit = min(max(1, int(max_lines)), max_line_cap)
            except (ValueError, TypeError):
                effective_limit = default_max_lines
        else:
            effective_limit = default_max_lines

        if end_line_val is not None:
            if (end_line_val - start_line_val + 1) > max_line_cap:
                end_line_val = start_line_val + max_line_cap - 1
            end_line_val = min(end_line_val, total_lines)
        else:
            end_line_val = min(start_line_val + effective_limit - 1, total_lines)

        selected_lines = lines[start_line_val - 1 : end_line_val]

        pad_width = len(str(end_line_val))
        formatted_lines = []
        header = f"=== File: {file_path_str} ({jumped_note}Lines {start_line_val}-{end_line_val} of {total_lines}, Size: {self._format_size(file_size)}) ===\n"
        current_char_count = len(header)
        truncated_due_to_chars = False
        last_rendered_line = start_line_val - 1

        for idx, line_text in enumerate(selected_lines):
            current_line_num = start_line_val + idx
            clean_line = _truncate_line(line_text)
            marker = ">" if (jump_to_match and current_line_num == start_line_val) else "|"
            if show_numbers:
                rendered_line = f"{current_line_num:>{pad_width}} {marker} {clean_line}\n"
            else:
                prefix = "> " if (jump_to_match and current_line_num == start_line_val) else ""
                rendered_line = f"{prefix}{clean_line}\n"

            if current_char_count + len(rendered_line) > char_limit and idx > 0:
                truncated_due_to_chars = True
                break

            formatted_lines.append(rendered_line)
            current_char_count += len(rendered_line)
            last_rendered_line = current_line_num

        output = header + "".join(formatted_lines)

        if truncated_due_to_chars:
            next_line = last_rendered_line + 1
            output += f"\n... [Output truncated at {char_limit} characters limit. Call Terminal_read_file with start_line={next_line} to continue reading]"
        elif end_line_val < total_lines:
            remaining = total_lines - end_line_val
            next_line = end_line_val + 1
            output += f"\n... [{remaining} more lines in file. Call Terminal_read_file with start_line={next_line} to continue reading]"
        else:
            output += f"\n=== End of File ({total_lines} lines) ==="

        return output.strip()

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
