import subprocess
import os
import logging
from typing import List, Dict, Any
from core.cerebrum import Tool

class TerminalSkill(Tool):
    name = "Terminal"
    description = "Allows the agent to execute bash commands on the local system."
    commands = ["run"]

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Terminal_run",
                "description": "Executes a bash command on the local system. Use this to interact with the file system, install packages, run scripts, and manage the system. IMPORTANT: Due to Flatpak sandboxing, you only have host access to ~/Documents, ~/Pictures, ~/Downloads, and ~/Desktop. When creating or saving files, organize them into these standard XDG directories under a sub-directory named after yourself (e.g., ~/Documents/<YourName>/). Use ~/Documents/<YourName>/.scratch/ as a scratch space for temporary files or files not intended for the user to use/view.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command_str": {
                            "type": "STRING",
                            "description": "The bash command string to execute."
                        },
                        "as_sudo": {
                            "type": "BOOLEAN",
                            "description": "Set to true if the command requires superuser/sudo privileges. The system admin must add the SUDO_PASSWORD to the .env file for this to work."
                        }
                    },
                    "required": ["command_str"]
                }
            }
        ]

    def execute(self, command: str, *args, **kwargs) -> str:
        if command != "run":
            return f"Unknown command: {command}"

        command_str = kwargs.get("command_str")
        as_sudo = kwargs.get("as_sudo", False)

        if not command_str:
            return "Error: Missing command_str parameter."

        env_sudo_pass = os.environ.get("SUDO_PASSWORD", "")

        try:
            cwd_path = os.path.expanduser("~/Documents/OpenAmity")
            os.makedirs(cwd_path, exist_ok=True)
            
            if as_sudo:
                if not env_sudo_pass or env_sudo_pass == "your_password_here":
                    return "Error: SUDO_PASSWORD is not configured in the environment. Please add it to the .env file."
                
                full_command = ["sudo", "-S", "bash", "-c", command_str]
                result = subprocess.run(
                    full_command, 
                    input=f"{env_sudo_pass}\n", 
                    text=True, 
                    capture_output=True,
                    check=False,
                    cwd=cwd_path
                )
            else:
                full_command = ["bash", "-c", command_str]
                result = subprocess.run(
                    full_command, 
                    text=True, 
                    capture_output=True,
                    check=False,
                    cwd=cwd_path
                )

            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"

            # Limit output to prevent context window explosion
            from core.settings_manager import SettingsManager
            is_low_token = SettingsManager().get("core.low-token-mode", False)
            max_length = 500 if is_low_token else 4000
            
            if len(output) > max_length:
                output = output[:max_length] + f"\n... [Output truncated to {max_length} characters]"
            
            if not output.strip():
                output = "[Command executed successfully with no output]"

            return output

        except Exception as e:
            logging.error(f"Terminal execution error: {e}", exc_info=True)
            return f"Error executing command: {e}"
