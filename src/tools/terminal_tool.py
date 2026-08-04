import subprocess
import os
import logging
import threading
import time
import tempfile
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.settings_manager import SettingsManager

class TerminalSkill(Tool):
    name = "Terminal"
    description = "Allows the agent to execute bash commands on the local system."
    commands = ["run", "run_async", "check_status", "kill_task"]

    def __init__(self):
        super().__init__()
        self.tasks = {}
        self.task_counter = 0
        self.lock = threading.Lock()

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Terminal_run",
                "description": "Executes a short bash command synchronously (max 30s timeout). Use this for quick file reads, listing dirs, etc. IMPORTANT: Due to Flatpak sandboxing, you only have host access to ~/Documents, ~/Pictures, ~/Downloads, and ~/Desktop.",
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
            }
        ]

    def _format_output(self, stdout: str, stderr: str) -> str:
        output = stdout if stdout else ""
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
            
        is_low_token = SettingsManager().get("core.low-token-mode", False)
        max_length = 500 if is_low_token else 4000
        
        if len(output) > max_length:
            output = output[:max_length] + f"\n... [Output truncated to {max_length} characters]"
            
        if not output.strip():
            output = "[Command executed successfully with no output]"
            
        return output

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "run":
            return self._run_sync(**kwargs)
        elif command == "run_async":
            return self._run_async(**kwargs)
        elif command == "check_status":
            return self._check_status(**kwargs)
        elif command == "kill_task":
            return self._kill_task(**kwargs)
        return f"Unknown command: {command}"

    def _get_command_args(self, command_str, as_sudo):
        env_sudo_pass = os.environ.get("SUDO_PASSWORD", "")
        cwd_path = os.path.expanduser("~/Documents/OpenAmity")
        os.makedirs(cwd_path, exist_ok=True)
        
        if as_sudo:
            if not env_sudo_pass or env_sudo_pass == "your_password_here":
                raise ValueError("SUDO_PASSWORD is not configured in the environment. Please add it to the .env file.")
            return ["sudo", "-S", "bash", "-c", command_str], f"{env_sudo_pass}\n", cwd_path
        else:
            return ["bash", "-c", command_str], None, cwd_path

    def _run_sync(self, command_str=None, as_sudo=False):
        if not command_str: return "Error: Missing command_str parameter."
        try:
            full_command, stdin_input, cwd_path = self._get_command_args(command_str, as_sudo)
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

    def _run_async(self, command_str=None, reminder_minutes=2, as_sudo=False):
        if not command_str: return "Error: Missing command_str parameter."
        try:
            full_command, stdin_input, cwd_path = self._get_command_args(command_str, as_sudo)
            
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
                    "completed": False
                }
                
            threading.Thread(target=self._monitor_task, args=(task_id,), daemon=True).start()
            return f"Task started in background with ID: {task_id}. You will be notified when it completes."
            
        except Exception as e:
            logging.error(f"Terminal async execution error: {e}", exc_info=True)
            return f"Error starting async command: {e}"

    def _monitor_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
        if not task: return
        
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
                    self.orchestrator.pulse_engine.trigger_pulse.emit(prompt)
                    
        with self.lock:
            task["completed"] = True
            
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
            self.orchestrator.pulse_engine.trigger_pulse.emit(prompt)

    def _check_status(self, task_id=None):
        if task_id is None: return "Error: Missing task_id parameter."
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
        if task_id is None: return "Error: Missing task_id parameter."
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

    def shutdown(self):
        logging.info("TerminalSkill: Shutting down background tasks...")
        with self.lock:
            for task_id, task in self.tasks.items():
                if not task["completed"]:
                    try:
                        task["process"].terminate()
                        task["process"].kill()
                    except Exception as e:
                        logging.error(f"Error killing task {task_id} on shutdown: {e}")
                
                try:
                    task["out_file"].close()
                    task["err_file"].close()
                    os.unlink(task["out_file"].name)
                    os.unlink(task["err_file"].name)
                except:
                    pass
            self.tasks.clear()
