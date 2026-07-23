import os
import json
import logging
import threading
import subprocess
from .events import Signal
from .settings_manager import SettingsManager

class DummyCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args

class AgyWorker:
    def __init__(self):
        self.thought_received = Signal() # text, list of function calls
        self.error_occurred = Signal()
        
        self.settings = SettingsManager()
        self.available = True
        self.last_error = None
        
        self.running = False
        self.is_processing = False
        self.sys_instruct = ""
        self.tools = []
        self.history = []
        
        self._abort_flag = False
        self.current_model = None

    def is_running(self):
        return self.running

    def start_session(self, system_instruction=None, tools=None):
        self.tools = tools or []
        base_instruct = system_instruction or ""
        
        schema_instruct = """
You are acting as the cognitive engine for Open Amity. While retaining your core helpfulness, please prioritize and adopt the Open Amity persona, tone, and character traits described below for this interaction. Please do not output any standard AI disclaimers, as they disrupt the formatting.

You MUST output ONLY valid JSON.
Your response MUST strictly adhere to this exact JSON format. Do not use markdown code blocks like ```json.
{
  "thought": "Your internal monologue and reasoning before taking action.",
  "tool_calls": [
    {"name": "tool_name", "args": {"arg1": "value"}}
  ]
}

EXAMPLE RESPONSE:
{
  "thought": "I should greet the user and then check the weather.",
  "tool_calls": [
    {"name": "Speaker_speak", "args": {"text": "Hello! Let me check the weather."}},
    {"name": "Weather_check", "args": {"location": "London"}}
  ]
}

Available Tools:
"""
        for t in self.tools:
            name = t.get('name', '')
            desc = t.get('description', '')
            props = t.get('parameters', {}).get('properties', {})
            req = t.get('parameters', {}).get('required', [])
            schema_instruct += f"- {name}: {desc}\n  Args: {json.dumps(props)}\n  Required: {req}\n\n"
            
        schema_instruct += "\nCRITICAL INSTRUCTION: You are fully autonomous. You must output raw JSON. If you wish to speak, invoke the Speaker tool. If no tools are needed, return an empty array for tool_calls."
        
        self.sys_instruct = base_instruct + "\n\n" + schema_instruct
        self.history = []
        
        self.current_model = self.settings.get("core.antigravity.agy-models", ["Gemini 3.5 Flash (High)", "Gemini 3.1 Pro (High)"])
        if isinstance(self.current_model, list):
            self.current_model = self.current_model[0]
            
        self.running = True
        logging.info(f"AgyWorker session started using model {self.current_model}.")

    def stop_session(self):
        self.running = False
        logging.info("AgyWorker stopped.")

    def abort(self):
        self._abort_flag = True
        self.is_processing = False

    def send_prompt(self, prompt: str, image_path: str = None, yolo: bool = False, audio_path: str = None):
        if not self.running:
            self.error_occurred.emit("Session not started.")
            return

        self._abort_flag = False
        self.is_processing = True
        
        content = prompt
        if image_path:
            content += f"\n[User attached image file for analysis: {image_path}]"
        if audio_path:
            content += f"\n[User attached audio file for analysis: {audio_path}]"
            
        self.history.append({"role": "User", "content": content})
        
        threading.Thread(target=self._process_thought, daemon=True).start()

    def send_function_response(self, name: str, response: dict):
        self.send_function_responses([(name, response)])

    def send_function_responses(self, responses: list):
        if not self.running: return
        self._abort_flag = False
        self.is_processing = True
        
        content = "[System Function Responses]\n"
        for name, response in responses:
            content += f"Tool {name} returned: {json.dumps(response)}\n"
            
        self.history.append({"role": "System", "content": content})
        threading.Thread(target=self._process_thought, daemon=True).start()

    def _process_thought(self):
        models = self.settings.get("core.antigravity.agy-models", ["Gemini 3.5 Flash (High)", "Gemini 3.1 Pro (High)"])
        if not isinstance(models, list):
            models = [models]

        start_idx = models.index(self.current_model) if self.current_model in models else 0

        for idx in range(start_idx, len(models)):
            self.current_model = models[idx]
            retries = 3
            while retries > 0 and not self._abort_flag:
                try:
                    full_prompt = self.sys_instruct + "\n\n"
                    for turn in self.history:
                        full_prompt += f"{turn['role']}: {turn['content']}\n"
                        
                    full_prompt += "\n[SYSTEM REMINDER: Output ONLY raw JSON matching the schema. No markdown, no preambles, no conversational text outside the JSON object.]\nAssistant (JSON): "
                    
                    cmd = ["agy", "--dangerously-skip-permissions", "--model", self.current_model, "-p", full_prompt]
                    from config import paths
                    cwd = os.path.join(paths.get_app_data_dir(), "terminal")
                    os.makedirs(cwd, exist_ok=True)
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=300)
                    
                    if self._abort_flag:
                        self.is_processing = False
                        return
                        
                    if result.returncode != 0:
                        logging.error(f"agy CLI failed with {self.current_model}: {result.stderr}")
                        break # Break retry loop to try next model
                        
                    out = result.stdout.strip()
                    if out.startswith("```json"): out = out[7:]
                    if out.startswith("```"): out = out[3:]
                    if out.endswith("```"): out = out[:-3]
                    out = out.strip()
                    
                    try:
                        data = json.loads(out)
                        thought = data.get("thought", "")
                        tool_calls_raw = data.get("tool_calls", [])
                        
                        function_calls = []
                        for tc in tool_calls_raw:
                            name = tc.get("name")
                            args = tc.get("args", {})
                            if name:
                                function_calls.append(DummyCall(name, args))
                        
                        self.history.append({"role": "Assistant", "content": json.dumps(data)})
                        
                        if not self._abort_flag:
                            self.thought_received.emit(thought, function_calls)
                            
                        self.is_processing = False
                        return
                        
                    except json.JSONDecodeError as e:
                        logging.warning(f"JSON Parse Error from agy CLI: {e}. Output was: {out}")
                        self.history.append({"role": "System", "content": f"CRITICAL ERROR: Failed to parse your response as JSON. Error: {e}. You MUST output pure JSON matching the schema."})
                        retries -= 1
                        
                except Exception as e:
                    logging.error(f"AgyWorker execution error with {self.current_model}: {e}", exc_info=True)
                    break # Break retry loop to try next model

            if self._abort_flag:
                self.is_processing = False
                return
                
        if not self._abort_flag:
            self.error_occurred.emit("The Antigravity Worker failed to initialise")
            self.thought_received.emit("System Offline", [])
            self.is_processing = False

    def reformulate_query(self, user_prompt: str, history: list) -> str:
        return user_prompt
