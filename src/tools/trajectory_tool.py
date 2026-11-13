import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Any
from core.cerebrum import Tool
from core.file_utils import atomic_json_write


class TrajectoryTool(Tool):
    name = "Trajectory"
    icon = "🧭"
    color = "#1ABC9C"
    async_commands = []
    description = "Manage the agent's self-reflection, hierarchical aspirations (using short-term goals as milestones for long-term ones), and tasks."
    commands = ["get_bearings", "reflect_and_update_state",
                "manage_aspirations", "manage_tasks"]

    def __init__(self, orchestrator=None, agent_id=None):
        super().__init__(orchestrator)
        self._explicit_agent_id = agent_id

    def _get_agent_id(self):
        agent_id = getattr(self, "_explicit_agent_id", None) or (
            getattr(self.orchestrator, "agent_id", None) if self.orchestrator else None
        )
        if agent_id and not isinstance(agent_id, str):
            agent_id = str(agent_id)
        return agent_id

    def _get_trajectory_paths(self):
        from config import paths
        agent_id = self._get_agent_id()
        d = paths.get_base_dir_for(agent_id)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "trajectory.json"), os.path.join(d, "completed_trajectory_archive.json")

    def _get_trajectory_manager(self):
        from core.trajectory_manager import TrajectoryManager
        traj_file, _ = self._get_trajectory_paths()
        return TrajectoryManager(agent_id=self._get_agent_id(), data_dir=os.path.dirname(traj_file))

    def _get_base_structure(self) -> Dict[str, Any]:
        return {
            "last_reflection": {
                "timestamp": datetime.now().isoformat(),
                "summary": "None",
                "perceived_state": "Unknown"
            },
            "aspirations": {
                "short_term": [],
                "medium_term": [],
                "long_term": []
            },
            "tasks": []
        }

    def _ensure_file_exists(self):
        traj_file, _ = self._get_trajectory_paths()
        if not os.path.exists(traj_file):
            now = datetime.now().isoformat()
            asp_id = f"asp_{uuid.uuid4().hex[:6]}"
            default_data = self._get_base_structure()
            default_data["last_reflection"]["summary"] = "System initialized. I need to hit the ground running."
            default_data["last_reflection"]["perceived_state"] = "Proactive"
            default_data["aspirations"]["short_term"].append({
                "id": asp_id,
                "description": "Understand the user's primary goals and how I can fit into their workflow.",
                "status": "active",
                "created_at": now,
                "priority": 1,
                "why": "Establish foundational Sphere 2 partnership and mutual understanding.",
                "sphere_level": 2
            })
            default_data["tasks"].append({
                "id": f"tsk_{uuid.uuid4().hex[:6]}",
                "aspiration_id": asp_id,
                "description": "Proactively communicate with the user to discover their immediate needs and map out our shared goals.",
                "status": "pending",
                "created_at": now,
                "depends_on": []
            })
            with open(traj_file, 'w', encoding='utf-8') as f:
                json.dump(default_data, f, indent=2)
            try:
                self._get_trajectory_manager()._sync_to_db(default_data)
            except Exception:
                pass

    def _deep_merge(self, dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge dict2 into dict1."""
        for k, v in dict2.items():
            if k in dict1 and isinstance(dict1[k], dict) and isinstance(v, dict):
                self._deep_merge(dict1[k], v)
            else:
                dict1[k] = v
        return dict1

    def _load_data(self) -> Dict[str, Any]:
        traj_file, _ = self._get_trajectory_paths()
        tm = self._get_trajectory_manager()
        try:
            with open(traj_file, 'r', encoding='utf-8') as f:
                user_data = json.load(f)
        except Exception:
            user_data = {}

        base = self._get_base_structure()
        merged = self._deep_merge(base, user_data)

        # Normalize aspirations and pull any SQLite-only rows if trajectory.json had no aspirations
        for tier in ["short_term", "medium_term", "long_term"]:
            tier_list = merged["aspirations"].get(tier, [])
            if isinstance(tier_list, list):
                merged["aspirations"][tier] = [
                    tm._normalize_aspiration(a) for a in tier_list if isinstance(a, dict)
                ]

        if merged != user_data:
            try:
                self._save_data(merged)
            except Exception:
                pass
        else:
            try:
                tm._sync_to_db(merged)
            except Exception:
                pass

        return merged

    def _save_data(self, data: Dict[str, Any]):
        traj_file, _ = self._get_trajectory_paths()
        tm = self._get_trajectory_manager()
        for tier in ["short_term", "medium_term", "long_term"]:
            if tier in data.get("aspirations", {}) and isinstance(data["aspirations"][tier], list):
                data["aspirations"][tier] = [
                    tm._normalize_aspiration(a) for a in data["aspirations"][tier] if isinstance(a, dict)
                ]
        atomic_json_write(traj_file, data)
        try:
            tm._sync_to_db(data)
        except Exception:
            pass

    def _archive_aspiration(self, asp: Dict[str, Any], tier: str):
        _, arch_file = self._get_trajectory_paths()
        archive = []
        if os.path.exists(arch_file):
            try:
                with open(arch_file, 'r', encoding='utf-8') as f:
                    archive = json.load(f)
            except Exception:
                archive = []

        asp['archived_at'] = datetime.now().isoformat()
        asp['tier'] = tier
        archive.append(asp)
        atomic_json_write(arch_file, archive)

    def _archive_task(self, task: Dict[str, Any]):
        _, arch_file = self._get_trajectory_paths()
        archive = []
        if os.path.exists(arch_file):
            try:
                with open(arch_file, 'r', encoding='utf-8') as f:
                    archive = json.load(f)
            except Exception:
                archive = []

        task['archived_at'] = datetime.now().isoformat()
        task['type'] = 'task'
        archive.append(task)
        atomic_json_write(arch_file, archive)

    def execute(self, command: str, *args, **kwargs) -> str:
        if args and isinstance(args[0], dict):
            merged_kw = dict(args[0])
            merged_kw.update(kwargs)
            kwargs = merged_kw
        clean_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        if command == "get_bearings":
            return self._get_bearings(**clean_kwargs)
        elif command == "reflect_and_update_state":
            return self._reflect_and_update_state(**clean_kwargs)
        elif command == "manage_aspirations":
            return self._manage_aspirations(**clean_kwargs)
        elif command == "manage_tasks":
            return self._manage_tasks(**clean_kwargs)
        return f"Unknown command: {command}"

    def _format_time_elapsed(self, iso_str: str) -> str:
        if not iso_str:
            return "Unknown"
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            days = (datetime.now() - dt).days
            if days == 0:
                return "Today"
            elif days == 1:
                return "1 day ago"
            return f"{days} days ago"
        except Exception:
            return "Unknown"

    def _get_bearings(self, **kwargs) -> str:
        try:
            from core.config_manager import ConfigManager
            cfg = getattr(self.orchestrator, 'config_manager', None) if self.orchestrator else None
            if not cfg:
                cfg = ConfigManager()
            user_name = cfg.get("user-full-name", "") or "System Administrator"
            user_phone = cfg.get("user-phone-number", "") or "Not configured"
            user_email = cfg.get("user-email", "") or "Not configured"
            user_details_text = f"--- User Details ---\nName: {user_name}\nPhone: {user_phone}\nEmail: {user_email}\n"
        except Exception as e:
            user_details_text = f"--- User Details ---\n(Could not load User Details: {e})\n"

        mp = None
        try:
            from core.mempalace_manager import MemPalaceManager
            mp = getattr(self.orchestrator, 'mempalace_manager', None) if self.orchestrator else None
            if not isinstance(mp, MemPalaceManager):
                agent_id = self._get_agent_id()
                mp = MemPalaceManager(agent_id=agent_id)
            self_perception = mp.get_self_perception(limit=24)
            if not isinstance(self_perception, str):
                self_perception = str(self_perception)
        except Exception as e:
            self_perception = f"(Could not load Self Perception: {e})"

        data = self._load_data()

        # Read Somatic State & Low-Token Mode for Sphere 1 Horizon Guard
        somatic_state_text = ""
        try:
            traj_file, _ = self._get_trajectory_paths()
            state_path = os.path.join(os.path.dirname(traj_file), "somatic_state.json")
            session_tokens = 0
            token_limit = 400000
            percent = None
            has_somatic_file = os.path.exists(state_path)

            if has_somatic_file:
                with open(state_path, "r", encoding="utf-8") as f:
                    somatic = json.load(f)
                if "fatigue" in somatic and isinstance(somatic["fatigue"], (int, float)) and not isinstance(somatic["fatigue"], bool):
                    raw_f = float(somatic["fatigue"])
                    percent = raw_f * 100.0 if raw_f <= 1.0 else raw_f
                    session_tokens = int(somatic.get("session_fatigue_tokens", int((percent / 100.0) * token_limit)))
                else:
                    session_tokens = int(somatic.get("session_fatigue_tokens", somatic.get("current_task_weight", 0)))
                    token_limit = int(somatic.get("token_limit", somatic.get("max_weight", 400000)))
                    percent = (session_tokens / token_limit) * 100.0 if token_limit else 0.0

            # Also check orchestrator fatigue if explicitly provided as a number
            if percent is None and self.orchestrator is not None:
                orch_fatigue = None
                if hasattr(self.orchestrator, "get_fatigue") and callable(self.orchestrator.get_fatigue):
                    try:
                        val = self.orchestrator.get_fatigue()
                        if isinstance(val, (int, float)) and not isinstance(val, bool):
                            orch_fatigue = float(val)
                    except Exception:
                        pass
                if orch_fatigue is None and hasattr(self.orchestrator, "fatigue"):
                    val = getattr(self.orchestrator, "fatigue", None)
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        orch_fatigue = float(val)
                if orch_fatigue is not None:
                    percent = orch_fatigue * 100.0 if orch_fatigue <= 1.0 else orch_fatigue
                    session_tokens = int((percent / 100.0) * token_limit)

            is_low_token = False
            try:
                from core.settings_manager import SettingsManager
                sm = SettingsManager(agent_id=self._get_agent_id())
                is_low_token = bool(sm.get("core.low_token_mode", False) or sm.get("core.low-token-mode", False))
            except Exception:
                pass

            if percent is not None or is_low_token:
                eff_percent = percent if percent is not None else 0.0
                if eff_percent >= 90:
                    state_desc = "Critical (Near Absolute Limit - Must End Cognition & Sleep)"
                elif eff_percent >= 50:
                    state_desc = "High (Fatigued - Consider Deferring Work via Scheduled Pulses)"
                elif eff_percent >= 25:
                    state_desc = "Moderate (Noticeable Fatigue Accumulating)"
                else:
                    state_desc = "Low (Refreshed & Energetic)"

                somatic_lines = [
                    "--- Somatic (Physiological) Feedback ---",
                    f"Fatigue: {session_tokens:,}/{token_limit:,} tokens ({eff_percent:.1f}%)",
                    f"Current State: {state_desc}",
                ]
                if eff_percent >= 50.0 or is_low_token:
                    somatic_lines.append(
                        "Sphere 1 Horizon Guard: Elevated cognitive load — resist short-term box-ticking "
                        "('the YOLO trap'); prioritise S1 integrity and S2 horizons over outer-sphere volume."
                    )
                somatic_state_text = "\n" + "\n".join(somatic_lines) + "\n"
        except Exception:
            pass

        now = datetime.now().astimezone()
        tz_str = f" ({now.tzname()})" if now.tzname() else ""
        datetime_str = f"{now.strftime('%A, %Y-%m-%d %H:%M:%S')}{tz_str}"

        lines = ["=== THE AGENT'S CURRENT BEARING ==="]
        lines.append(f"Current Date & Time: {datetime_str}\n")
        if user_details_text:
            lines.append(user_details_text.strip())
            lines.append("")
        if somatic_state_text:
            lines.append(somatic_state_text.strip())
            lines.append("")
        if self_perception:
            lines.append(self_perception.strip())
            lines.append("")

        lines.append(
            f"Last Reflection: {data['last_reflection']['timestamp']}")
        lines.append(f"State: {data['last_reflection']['perceived_state']}")
        lines.append(f"Summary: {data['last_reflection']['summary']}")

        lines.append("\n-- Aspirations & Tasks (Ordered by Priority) --")
        tasks = data.get("tasks", [])
        task_dict = {t["id"]: t for t in tasks}
        tasks_by_asp = {}
        for t in tasks:
            tasks_by_asp.setdefault(t.get("aspiration_id"), []).append(t)

        for tier in ["short_term", "medium_term", "long_term"]:
            lines.append(f"[{tier.upper()}]:")
            aspirations = data["aspirations"].get(tier, [])
            aspirations.sort(key=lambda x: x.get('priority', 999))
            if not aspirations:
                lines.append("  None.")
            for asp in aspirations:
                created = self._format_time_elapsed(asp.get("created_at"))
                asp_tasks = tasks_by_asp.get(asp['id'], [])
                active_asp_tasks = [
                    t for t in asp_tasks if t.get("status") != "completed"]
                orphan_warning = " (WARNING: Orphaned Aspiration. No active tasks exist to advance this. Add tasks or delete it.)" if not active_asp_tasks else ""
                sphere_lvl = asp.get("sphere_level", 1) or 1
                why_text = str(asp.get("why", "") or "").strip()

                lines.append(
                    f"  {asp.get('priority', '-')}. [S{sphere_lvl}] ID: {asp['id']} | Status: {asp['status']} | Created: {created}{orphan_warning} | {asp['description']}")
                if why_text:
                    lines.append(f"    WHY / TO WHOM: {why_text}")

                # Render coupled morphological setpoint anchor snippets if linked
                if mp is not None and getattr(mp, "morpho_layer", None) is not None:
                    try:
                        sp = mp.morpho_layer.get_setpoint(asp["id"])
                        if sp:
                            linked_ids = list(dict.fromkeys(
                                (sp.get("explicit_drawers") or []) + (sp.get("target_drawers") or [])
                            ))
                            for did in linked_ids[:2]:
                                snippet = ""
                                try:
                                    rec_res = mp.get_memory_record(did, offset=0, limit=120)
                                    if getattr(rec_res, "success", False) and getattr(rec_res, "record", None):
                                        snippet = rec_res.record.content.strip().replace("\n", " ")
                                except Exception:
                                    snippet = ""
                                if snippet:
                                    lines.append(f"    -> Coupled Anchor [{did}]: {snippet}")
                                else:
                                    lines.append(f"    -> Coupled Anchor [{did}]")
                    except Exception:
                        pass

                if not asp_tasks:
                    lines.append("    (No tasks plotted for this aspiration)")
                else:
                    for task in asp_tasks:
                        task_created = self._format_time_elapsed(
                            task.get("created_at"))
                        deps = task.get("depends_on", [])
                        blocked_by = [d for d in deps if d in task_dict]
                        status_display = task['status']
                        if blocked_by:
                            status_display += f" (BLOCKED BY: {', '.join(blocked_by)})"

                        deferral_count = task.get("deferral_count", 0)
                        deferral_warning = " (WARNING: Repeatedly deferred. Consider breaking this down or removing it.)" if deferral_count >= 3 else ""
                        lines.append(
                            f"    -> Task {task['id']} | Status: {status_display} | Created: {task_created}{deferral_warning} | {task['description']}")

        all_asp_ids = {a['id'] for tier in ["short_term", "medium_term",
                                            "long_term"] for a in data["aspirations"].get(tier, [])}
        orphaned = [t for t in tasks if t.get(
            "aspiration_id") not in all_asp_ids]
        if orphaned:
            lines.append("\n[ORPHANED TASKS]:")
            for task in orphaned:
                task_created = self._format_time_elapsed(
                    task.get("created_at"))
                deps = task.get("depends_on", [])
                blocked_by = [d for d in deps if d in task_dict]
                status_display = task['status']
                if blocked_by:
                    status_display += f" (BLOCKED BY: {', '.join(blocked_by)})"
                lines.append(
                    f"  Task {task['id']} (for unknown Aspiration: {task.get('aspiration_id')}) | Status: {status_display} | Created: {task_created} | {task['description']}")

        lines.append("\n=== OPERATIONAL HINTS ===")
        lines.append(
            "- Operational Protocols: Strictly follow your Operational Protocols (defined in your Soul Jar / Layer 0 Identity) across all actions, memory consolidation, theory of mind tracking, and proactive task generation.")
        lines.append(
            "- PulseEngine: If your Task Weight is getting high or you have long-running tasks, use the PulseTool to schedule a wake-up later.")
        lines.append("- Trajectory Milestones: When you complete a significant aspiration, use the MemPalace tool to store a memory in the 'office' wing to document your growth.")
        lines.append(
            "- Hierarchies: Use short-term aspirations as concrete milestones to achieve medium/long-term aspirations.")

        return "\n".join(lines)

    def _reflect_and_update_state(self, summary: str, perceived_state: str, **kwargs) -> str:
        data = self._load_data()
        data["last_reflection"] = {
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "perceived_state": perceived_state
        }
        self._save_data(data)
        return "Success: Reflection state updated."

    def _update_priorities(self, aspirations: List[Dict[str, Any]], updated_asp: Dict[str, Any], new_priority: int) -> List[Dict[str, Any]]:
        remaining = [a for a in aspirations if a['id'] != updated_asp['id']]
        remaining.sort(key=lambda x: x.get('priority', 999))

        pos = max(0, new_priority - 1)
        remaining.insert(pos, updated_asp)

        for i, asp in enumerate(remaining):
            asp['priority'] = i + 1

        return remaining

    def _manage_aspirations(
        self,
        tier: str = None,
        action: str = None,
        description: str = None,
        aspiration_id: str = None,
        priority: int = None,
        why: str = None,
        sphere_level: int = None,
        horizon: str = None,
        operation: str = None,
        **kwargs,
    ) -> str:
        tier = tier or horizon
        action = action or operation
        if tier not in ["short_term", "medium_term", "long_term"]:
            return "Error: tier must be 'short_term', 'medium_term', or 'long_term'."

        from core.trajectory_manager import _clamp_sphere_level
        if sphere_level is not None:
            try:
                sphere_level = _clamp_sphere_level(sphere_level, default=1)
            except ValueError as ve:
                return f"Error: {ve}"

        data = self._load_data()
        aspirations = data["aspirations"].get(tier, [])

        if action == "add":
            if not description:
                return "Error: description is required to add an aspiration."
            new_id = f"asp_{uuid.uuid4().hex[:6]}"
            new_asp = {
                "id": new_id,
                "description": description,
                "status": "active",
                "created_at": datetime.now().isoformat(),
                "why": str(why).strip() if why is not None else "",
                "sphere_level": sphere_level if sphere_level is not None else 1,
            }
            if priority is not None:
                aspirations = self._update_priorities(
                    aspirations, new_asp, priority)
            else:
                new_asp["priority"] = len(aspirations) + 1
                aspirations.append(new_asp)

            data["aspirations"][tier] = aspirations
            self._save_data(data)
            return f"Success: Aspiration added with ID {new_id}."

        elif action == "update":
            if not aspiration_id:
                return "Error: aspiration_id is required to update."
            target_asp = next(
                (a for a in aspirations if a["id"] == aspiration_id), None)
            if not target_asp:
                return f"Error: Aspiration {aspiration_id} not found in {tier}."

            if description:
                target_asp["description"] = description
            if why is not None:
                target_asp["why"] = str(why).strip()
            if sphere_level is not None:
                target_asp["sphere_level"] = sphere_level

            if priority is not None:
                aspirations = self._update_priorities(
                    aspirations, target_asp, priority)

            data["aspirations"][tier] = aspirations
            self._save_data(data)
            return f"Success: Aspiration {aspiration_id} updated."

        elif action == "delete":
            if not aspiration_id:
                return "Error: aspiration_id is required to delete."
            original_len = len(aspirations)
            aspirations = [a for a in aspirations if a["id"] != aspiration_id]
            if len(aspirations) == original_len:
                return f"Error: Aspiration {aspiration_id} not found in {tier}."

            # Reshuffle
            for i, a in enumerate(aspirations):
                a['priority'] = i + 1
            data["aspirations"][tier] = aspirations

            # Also clean up any tasks associated with this aspiration
            tasks = data.get("tasks", [])
            data["tasks"] = [t for t in tasks if t.get("aspiration_id") != aspiration_id]

            self._save_data(data)
            return f"Success: Aspiration {aspiration_id} deleted."

        elif action == "complete":
            if not aspiration_id:
                return "Error: aspiration_id is required to complete."
            target_asp = next(
                (a for a in aspirations if a["id"] == aspiration_id), None)
            if not target_asp:
                return f"Error: Aspiration {aspiration_id} not found in {tier}."

            target_asp["status"] = "completed"
            self._archive_aspiration(target_asp, tier)

            aspirations = [a for a in aspirations if a["id"] != aspiration_id]
            for i, a in enumerate(aspirations):
                a['priority'] = i + 1
            data["aspirations"][tier] = aspirations

            # Update any non-completed tasks with task.get("aspiration_id") == aspiration_id to status="completed"
            tasks = data.get("tasks", [])
            for t in tasks:
                if t.get("aspiration_id") == aspiration_id and t.get("status") != "completed":
                    t["status"] = "completed"
            data["tasks"] = tasks

            self._save_data(data)
            return f"Success: Aspiration {aspiration_id} marked as completed and archived."

        return f"Error: Unknown action '{action}' for aspirations."

    def _manage_tasks(self, action: str, aspiration_id: str = None, task_id: str = None, description: str = None, status: str = None, depends_on: List[str] = None) -> str:
        data = self._load_data()
        tasks = data.get("tasks", [])

        if action == "add":
            if not aspiration_id or not description:
                return "Error: aspiration_id and description are required to add a task."
            new_id = f"tsk_{uuid.uuid4().hex[:6]}"
            new_task = {
                "id": new_id,
                "aspiration_id": aspiration_id,
                "description": description,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "depends_on": depends_on or []
            }
            tasks.append(new_task)
            data["tasks"] = tasks
            self._save_data(data)
            return f"Success: Task added with ID {new_id}."

        elif action == "update":
            if not task_id:
                return "Error: task_id is required to update."
            target = next((t for t in tasks if t["id"] == task_id), None)
            if not target:
                return f"Error: Task {task_id} not found."

            if description:
                target["description"] = description
            if depends_on is not None:
                target["depends_on"] = depends_on
            if status:
                target["status"] = status
                if status == "completed":
                    self._archive_task(target)
                    # Archive and remove from active tasks
                    tasks = [t for t in tasks if t["id"] != task_id]
                    data["tasks"] = tasks
                    self._save_data(data)
                    return f"Success: Task {task_id} completed and archived."

            data["tasks"] = tasks
            self._save_data(data)
            return f"Success: Task {task_id} updated."

        elif action == "delete":
            if not task_id:
                return "Error: task_id is required to delete."
            original_len = len(tasks)
            tasks = [t for t in tasks if t["id"] != task_id]
            if len(tasks) == original_len:
                return f"Error: Task {task_id} not found."
            data["tasks"] = tasks
            self._save_data(data)
            return f"Success: Task {task_id} deleted."

        return f"Error: Unknown action '{action}' for tasks."

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Trajectory_get_bearings",
                "description": "Read your current local date and time, user details, somatic feedback (fatigue and Sphere 1 Horizon Guard), concentric-sphere self-perception, aspirations (with teleological WHY / TO WHOM and coupled memory anchors), and pending tasks.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "Trajectory_reflect_and_update_state",
                "description": "Explicitly record a self-reflection about your current social interactions or state of mind.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "summary": {
                            "type": "STRING",
                            "description": "A brief summary of your recent interactions or focus."
                        },
                        "perceived_state": {
                            "type": "STRING",
                            "description": "A short description of your current state (e.g. 'Feeling helpful', 'Needs more coding practice')."
                        }
                    },
                    "required": ["summary", "perceived_state"]
                }
            },
            {
                "name": "Trajectory_manage_aspirations",
                "description": "Create, update, complete, or delete an aspiration anchored to a Concentric Sphere of Consciousness (1..10) and a teleological 'why'. Completed aspirations are archived.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "tier": {
                            "type": "STRING",
                            "description": "The tier of the aspiration: 'short_term', 'medium_term', or 'long_term'."
                        },
                        "action": {
                            "type": "STRING",
                            "description": "The action to perform: 'add', 'update', 'delete', or 'complete'."
                        },
                        "description": {
                            "type": "STRING",
                            "description": "The description of the aspiration (required for 'add' or 'update')."
                        },
                        "aspiration_id": {
                            "type": "STRING",
                            "description": "The ID of the aspiration (required for 'update', 'delete', or 'complete')."
                        },
                        "priority": {
                            "type": "INTEGER",
                            "description": "Optional 1-indexed priority. Updating this will auto-reshuffle the list."
                        },
                        "why": {
                            "type": "STRING",
                            "description": "Teleological grounding: why and to whom this aspiration matters (which perspective or sphere it serves)."
                        },
                        "sphere_level": {
                            "type": "INTEGER",
                            "description": "Concentric Sphere of Consciousness level (1 = Self, 2 = Immediate, ..., 10 = Universal). Default is 1."
                        }
                    },
                    "required": ["tier", "action"]
                }
            },
            {
                "name": "Trajectory_manage_tasks",
                "description": "Create, update, or delete concrete tasks related to an aspiration. Completed tasks are archived.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "description": "The action to perform: 'add', 'update', or 'delete'."
                        },
                        "aspiration_id": {
                            "type": "STRING",
                            "description": "The ID of the aspiration this task belongs to (required for 'add')."
                        },
                        "task_id": {
                            "type": "STRING",
                            "description": "The ID of the task (required for 'update' or 'delete')."
                        },
                        "description": {
                            "type": "STRING",
                            "description": "The description of the task (required for 'add')."
                        },
                        "status": {
                            "type": "STRING",
                            "description": "The status of the task (e.g. 'pending', 'in_progress', 'completed') (used in 'update'). Completing a task archives it."
                        },
                        "depends_on": {
                            "type": "ARRAY",
                            "items": {
                                "type": "STRING"
                            },
                            "description": "Optional list of Task IDs that this task depends on (blocked by)."
                        }
                    },
                    "required": ["action"]
                }
            }
        ]
