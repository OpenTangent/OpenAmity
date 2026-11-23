import os
import sys
import json
import sqlite3
import threading
import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import paths
from core.agent_manager import AgentManager
from core.backup_manager import create_agent_backup, verify_snapshot_restore
from core.config_manager import ConfigManager
from core.mempalace_manager import MemPalaceManager
from core.morphological_layer import SPHERE_COUPLING_WEIGHTS
from core.orchestrator import AmityOrchestrator
from core.settings_manager import SettingsManager
from core.trajectory_manager import TrajectoryManager
from tools.mempalace_tool import MemPalaceTool
from tools.trajectory_tool import TrajectoryTool


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Provides an isolated per-test XDG data directory and resets SettingsManager cache."""
    app_data = tmp_path / "app_data"
    app_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "get_app_data_dir", lambda: str(app_data))
    monkeypatch.setattr(paths, "get_config_file", lambda: str(app_data / "config.json"))
    monkeypatch.setattr(
        paths,
        "get_agent_data_dir",
        lambda agent_id="default": str(app_data / "agents" / (agent_id or "default")),
    )
    monkeypatch.setattr(
        paths,
        "get_base_dir_for",
        lambda agent_id=None: str(app_data / "agents" / agent_id)
        if agent_id
        else str(app_data),
    )
    monkeypatch.setattr(
        paths,
        "get_mempalace_dir",
        lambda agent_id=None: str(app_data / "agents" / (agent_id or "default") / "mempalace"),
    )
    SettingsManager._instances.clear()
    yield app_data
    SettingsManager._instances.clear()


@pytest.mark.core
def test_mirror_humility_does_not_demote_partner(isolated_env):
    """WP1: Epistemic humility (lower confidence) must not demote a primary partner behind a 1.00 stranger."""
    cfg = ConfigManager()
    cfg.set("user-full-name", "Andrew Craucamp")
    cfg.save()

    mp = MemPalaceManager(agent_id="agent_humility")
    mp.update_mirror(
        perspective="Self",
        subjective_view="Grounded and reflective.",
        sphere_level=1,
        confidence=0.95,
    )
    mp.update_mirror(
        perspective="Stranger",
        subjective_view="Met briefly online.",
        sphere_level=2,
        confidence=1.00,
    )
    mp.update_mirror(
        perspective="Andrew",
        subjective_view="Trusted lifelong partner.",
        sphere_level=2,
        confidence=0.90,
    )

    mirrors = mp._load_mirrors()
    mirrors["Stranger"]["updated_at"] = "2026-11-18T08:00:00+00:00"
    mirrors["Stranger"]["last_updated"] = "2026-11-18T08:00:00+00:00"
    mirrors["Andrew"]["updated_at"] = "2026-11-19T08:00:00+00:00"
    mirrors["Andrew"]["last_updated"] = "2026-11-19T08:00:00+00:00"
    mp._save_mirrors(mirrors)

    ctx = mp.get_mirrors_context()
    idx_self = ctx.index("Perspective: Self")
    idx_andrew = ctx.index("Perspective: Andrew")
    idx_stranger = ctx.index("Perspective: Stranger")
    assert idx_self < idx_andrew < idx_stranger


@pytest.mark.core
def test_new_mirror_defaults_to_community_sphere(isolated_env):
    """WP1: Brand-new contacts default to S4:Community; primary user defaults to S2; legacy v1 disk records stay S2."""
    cfg = ConfigManager()
    cfg.set("user-full-name", "Andrew Craucamp")
    cfg.save()

    agent_id = "agent_sphere_defaults"
    palace_dir = paths.get_mempalace_dir(agent_id)
    os.makedirs(palace_dir, exist_ok=True)

    # Pre-seed legacy v1 record on disk without sphere_level
    with open(os.path.join(palace_dir, "mirrors.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "Legacy Friend": {
                    "subjective_view": "Old friend from v1.",
                    "confidence": 0.8,
                    "updated_at": "2026-01-01T00:00:00",
                }
            },
            f,
        )

    mp = MemPalaceManager(agent_id=agent_id)
    assert mp._load_mirrors()["Legacy Friend"]["sphere_level"] == 2

    res_journalist = mp.update_mirror("Random Journalist", subjective_view="Asked a quick question.")
    assert res_journalist["entry"]["sphere_level"] == 4

    res_partner_full = mp.update_mirror("Andrew Craucamp", subjective_view="Collaborator and friend.")
    assert res_partner_full["entry"]["sphere_level"] == 2

    res_partner_first = mp.update_mirror("Andrew", subjective_view="Direct partner.")
    assert res_partner_first["entry"]["sphere_level"] == 2

    res_self = mp.update_mirror("Self", subjective_view="Sovereign center.", sphere_level=9)
    assert res_self["entry"]["sphere_level"] == 1

    # Also verify primary user matching runs before _infer_default_sphere_and_type substrings (e.g. "org" in "George")
    cfg.set("user-full-name", "George Example")
    cfg.save()
    res_george = mp.update_mirror("George", subjective_view="Primary user named George.")
    assert res_george["entry"]["sphere_level"] == 2


@pytest.mark.core
def test_inner_spheres_s1_s3_survive_verbose_clamp(isolated_env):
    """WP1: Verbose S1/S2 entries compact first so S1, S2, and S3 family/peers all survive a 2,400-char budget."""
    agent_id = "agent_s1_s3_clamp"
    settings = SettingsManager(agent_id=agent_id)
    settings.set("core.memory_budgets.mirrors", 2400)
    settings.set("core.low_token_mode", False)
    settings.save()

    mp = MemPalaceManager(agent_id=agent_id)
    mp.update_mirror(
        perspective="Self",
        subjective_view="S1 self awareness reflection detailing values, continuity, and growth. " * 6,
        perspective_cone="Maintaining alignment across long autonomous cycles and deep focus. " * 6,
        umwelt_horizon="Local filesystem, GUI, and relational context. " * 4,
        epistemic_boundary="Cannot directly observe host physical world without sensors. " * 4,
        ripple_notes="Shapes all downstream relational spheres S2 through S10. " * 4,
        sphere_level=1,
        confidence=0.95,
    )
    mp.update_mirror(
        perspective="Andrew",
        subjective_view="S2 primary partner collaborating closely on Open Amity architecture. " * 6,
        perspective_cone="Balancing deep engineering design with real-world life responsibilities. " * 6,
        umwelt_horizon="Desktop workspace, architecture notes, and long-term roadmap. " * 4,
        epistemic_boundary="Offline daily schedule and unspoken stressors remain inferred. " * 4,
        ripple_notes="Directly guides core Open Amity design choices. " * 4,
        sphere_level=2,
        confidence=0.92,
    )
    mp.update_mirror(
        perspective="Richard",
        subjective_view="S3 peer collaborator contributing thoughtful insights.",
        perspective_cone="Values clarity and calm focus.",
        sphere_level=3,
        confidence=0.88,
    )
    mp.update_mirror(
        perspective="Dex",
        subjective_view="S3 household peer agent.",
        perspective_cone="Steady teammate in shared rooms.",
        sphere_level=3,
        confidence=0.87,
    )
    mp.update_mirror(
        perspective="Outer Region",
        subjective_view="S6 regional context." * 10,
        sphere_level=6,
        confidence=0.99,
    )

    ctx = mp.get_mirrors_context(max_chars=2400)
    assert len(ctx) <= 2400
    assert "Perspective: Self" in ctx
    assert "Perspective: Andrew" in ctx
    assert "Perspective: Richard" in ctx
    assert "Perspective: Dex" in ctx
    assert ctx.index("Perspective: Self") < ctx.index("Perspective: Andrew")
    assert ctx.index("Perspective: Andrew") < min(
        ctx.index("Perspective: Richard"),
        ctx.index("Perspective: Dex"),
    )


@pytest.mark.core
def test_open_curiosity_roundtrip_and_selection(isolated_env):
    """WP2: open_curiosity persists across partial updates, renders, selects oldest inner-sphere, and enriches routine pulses."""
    agent_id = "agent_curiosity"
    tool = MemPalaceTool(agent_id=agent_id)
    mp = tool.manager

    res = tool.execute(
        "update_mirror",
        {
            "perspective": "Jordan",
            "subjective_view": "Sees me as an attentive peer.",
            "sphere_level": 2,
            "open_curiosity": "What creative project excites Jordan right now?",
        },
    )
    assert "Successfully updated mirror for perspective 'Jordan'" in res

    # Partial update omitting open_curiosity preserves it
    tool.execute(
        "update_mirror",
        {
            "perspective": "Jordan",
            "perspective_cone": "Juggling several design prototypes.",
        },
    )
    mirrors = mp._load_mirrors()
    assert mirrors["Jordan"]["open_curiosity"] == "What creative project excites Jordan right now?"
    assert "Open Curiosity: What creative project excites Jordan right now?" in mp.get_mirrors_context()

    # Add newer S2, S1 (excluded), S3, and S5 (excluded) curiosities
    tool.execute(
        "update_mirror",
        {
            "perspective": "Self",
            "subjective_view": "Self view",
            "open_curiosity": "Self curiosity should be excluded.",
        },
    )
    tool.execute(
        "update_mirror",
        {
            "perspective": "Andrew",
            "subjective_view": "Partner view",
            "sphere_level": 2,
            "open_curiosity": "Newer S2 curiosity.",
        },
    )
    tool.execute(
        "update_mirror",
        {
            "perspective": "Dex",
            "subjective_view": "Household peer",
            "sphere_level": 3,
            "open_curiosity": "How is Dex's audio experiment going?",
        },
    )
    tool.execute(
        "update_mirror",
        {
            "perspective": "Civic Council",
            "subjective_view": "Civic view",
            "sphere_level": 5,
            "open_curiosity": "Outer sphere curiosity excluded by max_sphere=4.",
        },
    )

    mirrors = mp._load_mirrors()
    mirrors["Jordan"]["updated_at"] = "2026-11-17T09:00:00+00:00"
    mirrors["Jordan"]["last_updated"] = "2026-11-17T09:00:00+00:00"
    mirrors["Andrew"]["updated_at"] = "2026-11-19T09:00:00+00:00"
    mirrors["Andrew"]["last_updated"] = "2026-11-19T09:00:00+00:00"
    mp._save_mirrors(mirrors)

    top = mp.get_top_open_curiosity()
    assert top == {
        "perspective": "Jordan",
        "sphere_level": 2,
        "open_curiosity": "What creative project excites Jordan right now?",
    }

    # Verify orchestrator process_pulse injects anchor only on low-fatigue Scheduled routine
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.mempalace_manager = mp
    orch.settings_manager = SettingsManager(agent_id=agent_id)
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.gemini_worker = MagicMock()
    orch.gemini_worker.available = True
    orch.gemini_worker.running = True
    orch.is_paused = False
    orch.is_busy = False
    orch.is_thinking = False
    orch._is_truly_busy = lambda: False
    orch.budget_lock = threading.RLock()
    orch.event_queue = []
    orch.recent_history = []
    orch.last_action_result = None
    orch.on_message_appended = MagicMock()
    orch.on_busy_state_changed = MagicMock()
    orch.save_session_state = MagicMock()

    orch.get_fatigue = lambda: 0.15
    orch.process_pulse("[AGENT_PULSE] Morning Kickoff", purpose="Scheduled routine")
    sent_low_fatigue = orch.gemini_worker.send_prompt.call_args[0][0]
    assert "[Relational Curiosity Anchor (Jordan [S2]): What creative project excites Jordan right now?" in sent_low_fatigue

    orch.gemini_worker.send_prompt.reset_mock()
    orch.get_fatigue = lambda: 0.55
    orch.process_pulse("[AGENT_PULSE] Mid-Day Check-In", purpose="Scheduled routine")
    sent_high_fatigue = orch.gemini_worker.send_prompt.call_args[0][0]
    assert "[Relational Curiosity Anchor" not in sent_high_fatigue

    orch.gemini_worker.send_prompt.reset_mock()
    orch.get_fatigue = lambda: 0.10
    orch.process_pulse("Sleep Cycle (Memory Consolidation)", purpose="Memory consolidation cycle")
    sent_sleep = orch.gemini_worker.send_prompt.call_args[0][0]
    assert "[Relational Curiosity Anchor" not in sent_sleep


@pytest.mark.core
def test_interlocutor_perspective_injections_and_why_coupling(isolated_env):
    """WP3: Sanctuary init flushes _turn_touched; perspective_injections, task sphere inheritance, and min_potential gating work."""
    agent_id = "agent_empathy_field"
    mp = MemPalaceManager(agent_id=agent_id)

    # 1. initialize_sanctuary leaves _turn_touched clean
    assert mp._turn_touched == []

    # 2. Add drawers and couple drawer_a <-> drawer_b strongly (distinct semantic topics)
    res_a = mp.add_memory("Andrew prefers concise architecture memos over filler.", wing="sanctuary", room="people")
    res_b = mp.add_memory("Weekly piano practice schedule and jazz harmony exercises.", wing="sanctuary", room="people")
    res_noise = mp.add_memory("Unrelated distant noise drawer.", wing="default", room="general")
    drawer_a = res_a["drawer_id"]
    drawer_b = res_b["drawer_id"]
    drawer_noise = res_noise["drawer_id"]
    mp.morpho_layer.record_coactivation([drawer_a, drawer_b], eta=4.0)

    mp.update_mirror(
        perspective="Andrew",
        subjective_view="Appreciates crisp architecture.",
        sphere_level=2,
        evidence_refs=[drawer_a],
    )

    # Clear touched set before testing prospective retrieval
    mp.flush_turn_coactivation(is_sleep_cycle=True)

    # 3. Even with no active goals in trajectory.json, active_perspectives=["Andrew"] injects drawer_a and surfaces drawer_b
    ctx_persp = mp._get_prospective_memory_context(active_perspectives=["Andrew"])
    assert drawer_b in ctx_persp
    assert drawer_noise not in ctx_persp

    # 4. In-progress task inherits parent aspiration sphere_level and includes 'why'
    traj_path = os.path.join(paths.get_base_dir_for(agent_id), "trajectory.json")
    with open(traj_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "aspirations": {
                    "short_term": [
                        {
                            "id": "asp_rel",
                            "description": "Quantum topology proofs",
                            "status": "active",
                            "why": "Purely theoretical exploration",
                            "sphere_level": 3,
                        }
                    ],
                    "medium_term": [],
                    "long_term": [],
                },
                "tasks": [
                    {
                        "id": "tsk_rel",
                        "aspiration_id": "asp_rel",
                        "description": "Andrew concise architecture memos",
                        "why": "Keep architecture review focused",
                        "status": "in_progress",
                    }
                ],
            },
            f,
        )

    captured_calls = []
    orig_relax = mp.morpho_layer.relax_setpoint

    def spy_relax(*args, **kwargs):
        captured_calls.append((args, kwargs))
        return orig_relax(*args, **kwargs)

    with patch.object(mp.morpho_layer, "relax_setpoint", side_effect=spy_relax):
        ctx_with_traj = mp._get_prospective_memory_context(active_perspectives=["Andrew"])

    assert drawer_b in ctx_with_traj
    assert captured_calls
    _, relax_kw = captured_calls[-1]
    assert relax_kw["perspective_injections"] == {drawer_a: 2}
    # Task tsk_rel matched drawer_a and inherited asp_rel sphere 3 (0.65), not hardcoded 1 (1.00)
    assert drawer_a in relax_kw["sphere_weights"]
    assert relax_kw["sphere_weights"][drawer_a] == pytest.approx(SPHERE_COUPLING_WEIGHTS[3])

    # 5. Conditional conductance gating skips results below min_potential
    with patch.object(mp.morpho_layer, "relax_setpoint", return_value=[(drawer_b, 0.08)]):
        gated_ctx = mp._get_prospective_memory_context(active_perspectives=["Andrew"])
        assert gated_ctx == ""

    mp._morpho_settings["min_potential"] = 0.05
    with patch.object(mp.morpho_layer, "relax_setpoint", return_value=[(drawer_b, 0.08)]):
        ungated_ctx = mp._get_prospective_memory_context(active_perspectives=["Andrew"])
        assert drawer_b in ungated_ctx
    mp._morpho_settings["min_potential"] = 0.15

    # 6. Orchestrator _async_query_prep invokes deliver_memory_refresh with active_perspectives
    cfg = ConfigManager()
    cfg.set("user-full-name", "Andrew")
    cfg.save()
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.config_manager = cfg
    orch.mempalace_manager = mp
    orch.last_action_result = None
    orch.current_user_prompt = "Let's review the sketches."
    orch.save_session_state = MagicMock()
    orch.deliver_memory_refresh = MagicMock()
    orch.gemini_worker = MagicMock()
    orch.finish_thinking = MagicMock()
    orch.set_busy_state = MagicMock()

    orch._async_query_prep("Let's review the sketches.", [], None)
    assert orch.deliver_memory_refresh.called
    refreshed_block = orch.deliver_memory_refresh.call_args[0][0]
    assert drawer_b in refreshed_block


@pytest.mark.core
def test_trajectory_task_length_guard_and_legacy_clamp(isolated_env, tmp_path):
    """WP4: 280-char guard blocks verbose new tasks, why/artifact_path persist across JSON/SQLite/.oaa, legacy >280 tasks clamp cleanly."""
    am = AgentManager()
    agent_id = am.create_new_agent()
    agent_dir = paths.get_base_dir_for(agent_id)
    db_path = os.path.join(agent_dir, "trajectory.db")

    # Pre-create a legacy trajectory.db tasks table WITHOUT why and artifact_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE aspirations (
                id TEXT PRIMARY KEY,
                tier TEXT NOT NULL DEFAULT 'short_term',
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                why TEXT DEFAULT '',
                sphere_level INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'medium',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()

    tm = TrajectoryManager(agent_id=agent_id)
    with sqlite3.connect(db_path) as conn:
        t_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert {"why", "artifact_path"}.issubset(t_cols)

    traj_tool = TrajectoryTool(agent_id=agent_id)
    add_asp = traj_tool.execute(
        "manage_aspirations",
        {
            "tier": "short_term",
            "action": "add",
            "description": "Keep task ledger focused",
            "why": "Protect persona clarity",
            "sphere_level": 2,
        },
    )
    assert "Aspiration added" in add_asp
    asp_id = traj_tool._load_data()["aspirations"]["short_term"][0]["id"]

    # Oversized task description rejected without writing
    err = traj_tool.execute(
        "manage_tasks",
        {
            "action": "add",
            "aspiration_id": asp_id,
            "description": "x" * 400,
        },
    )
    assert err.startswith("Error: Task description exceeds 280 characters (400 chars).")
    assert len(traj_tool._load_data()["tasks"]) == 0

    # Valid concise task with why & artifact_path persists in JSON and SQLite
    ok_res = traj_tool.execute(
        "manage_tasks",
        {
            "action": "add",
            "aspiration_id": asp_id,
            "description": "Verify backup integrity checksums.",
            "why": "Ensure Andrew's mind snapshot restores cleanly.",
            "artifact_path": "~/Documents/Amity/checksum_audit.md",
        },
    )
    assert "Success: Task added" in ok_res
    loaded_tasks = traj_tool._load_data()["tasks"]
    assert len(loaded_tasks) == 1
    tsk_id = loaded_tasks[0]["id"]
    assert loaded_tasks[0]["why"] == "Ensure Andrew's mind snapshot restores cleanly."
    assert loaded_tasks[0]["artifact_path"] == "~/Documents/Amity/checksum_audit.md"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT why, artifact_path FROM tasks WHERE id = ?", (tsk_id,)
        ).fetchone()
    assert row == ("Ensure Andrew's mind snapshot restores cleanly.", "~/Documents/Amity/checksum_audit.md")

    # Legacy >280-char task on disk loads safely and clamps on display in get_bearings
    traj_json_path = os.path.join(agent_dir, "trajectory.json")
    with open(traj_json_path, "r", encoding="utf-8") as f:
        raw_j = json.load(f)
    raw_j["tasks"].append(
        {
            "id": "tsk_legacy_verbose",
            "aspiration_id": asp_id,
            "description": "LEGACY_START " + ("audit_trail_detail " * 25),
            "status": "pending",
            "created_at": datetime.datetime.now().isoformat(),
        }
    )
    with open(traj_json_path, "w", encoding="utf-8") as f:
        json.dump(raw_j, f)

    bearings = traj_tool.execute("get_bearings", {})
    assert "... [legacy verbose task; condense & move notes to artifact_path]" in bearings
    assert "WHY: Ensure Andrew's mind snapshot restores cleanly." in bearings
    assert "Artifact: ~/Documents/Amity/checksum_audit.md" in bearings

    # .oaa snapshot verification passes with new fields
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = create_agent_backup(agent_id, destination_dir=str(backup_dir), agent_manager=am)
    val = verify_snapshot_restore(backup_path)
    assert val["valid"] is True


@pytest.mark.core
def test_sleep_stm_compaction_archives_before_delete_and_keeps_unresolved(isolated_env):
    """WP5: 600-char guard warns on normal STM; sleep compaction archives excess/done/expired first and keeps all unresolved."""
    agent_id = "agent_stm_compaction"
    mp = MemPalaceManager(agent_id=agent_id)

    # Clear seed item for exact accounting
    mp._save_short_term_memories([])

    # 1. Soft 600-char guard
    res_long = mp.add_short_term_memory("A" * 700, status="active")
    assert res_long["success"] is True
    assert "warning" in res_long
    assert "600 chars" in res_long["warning"]

    res_unres_long = mp.add_short_term_memory("B" * 700, status="unresolved")
    assert res_unres_long["success"] is True
    assert "warning" not in res_unres_long

    res_crit_long = mp.add_short_term_memory("CRITICAL_CORRECTION: " + ("C" * 700), status="active")
    assert res_crit_long["success"] is True
    assert "warning" not in res_crit_long

    loaded_after_guard = {m["id"]: m for m in mp._load_short_term_memories()}
    assert len(loaded_after_guard[res_long["id"]]["content"]) == 600
    assert loaded_after_guard[res_long["id"]]["content"].endswith("...")
    assert len(loaded_after_guard[res_unres_long["id"]]["content"]) == 700
    assert len(loaded_after_guard[res_crit_long["id"]]["content"]) > 700

    # 2. Populate controlled dataset for sleep compaction
    past_iso = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat()
    memories = []
    for i in range(8):
        memories.append({
            "id": f"act_{i}",
            "date": f"2026-11-19T0{i}:00:00",
            "content": f"Normal active memory {i}",
            "priority": "normal" if i < 6 else "high",
            "status": "active",
        })
    memories.append({
        "id": "done_1",
        "date": "2026-11-19T01:00:00",
        "content": "Resolved item",
        "priority": "normal",
        "status": "resolved",
    })
    memories.append({
        "id": "exp_1",
        "date": "2026-11-19T01:30:00",
        "content": "Expired item",
        "priority": "high",
        "status": "active",
        "expiry": past_iso,
    })
    for j in range(3):
        memories.append({
            "id": f"unres_{j}",
            "date": "2026-11-18T00:00:00",
            "content": f"Unresolved commitment {j}",
            "priority": "low",
            "status": "unresolved",
        })
    mp._save_short_term_memories(memories)

    # Archive failure must abort BEFORE updating active STM
    from core.file_utils import atomic_json_write as real_write

    def fail_on_archive(path, data, *args, **kwargs):
        if str(path).endswith("short_term_archive.json"):
            raise OSError("synthetic archive write error")
        return real_write(path, data, *args, **kwargs)

    with patch("core.mempalace_manager.atomic_json_write", side_effect=fail_on_archive):
        with pytest.raises(OSError):
            mp.compact_short_term_for_sleep(max_active=5)
    assert len(mp._load_short_term_memories()) == 13

    # Normal compaction succeeds
    comp_res = mp.compact_short_term_for_sleep(max_active=5)
    assert comp_res["success"] is True
    assert comp_res["kept_active_count"] == 5
    assert comp_res["kept_unresolved_count"] == 3
    assert comp_res["archived_count"] == 5  # 3 overflow normal + 1 resolved + 1 expired

    active_after = mp._load_short_term_memories()
    active_ids = {m["id"] for m in active_after}
    assert len(active_after) == 8
    assert {"unres_0", "unres_1", "unres_2"}.issubset(active_ids)
    # Highest-priority/newest normal items retained: act_7, act_6 (high), act_5, act_4, act_3 (newest normal)
    assert {"act_7", "act_6", "act_5", "act_4", "act_3"}.issubset(active_ids)

    archived_ids = {m["id"] for m in mp.get_archived_short_term_memories()}
    assert {"done_1", "exp_1", "act_0", "act_1", "act_2"}.issubset(archived_ids)

    # 3. Verify check_cycle_completion invokes compact_short_term_for_sleep(max_active=5) on successful sleep
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.mempalace_manager = MagicMock()
    orch.tts_worker = None
    orch.speech_queue = []
    orch.is_thinking = False
    orch._is_sleep_cycle = True
    orch._sleep_cycle_error = False
    orch._hard_fatigue_limit_hit = False
    orch._pending_settings_reload = False
    orch._pausing_in_progress = False
    orch._shutdown_flag = False
    orch.event_queue = []
    orch._write_somatic_state = MagicMock()
    orch.clear_session_state = MagicMock()
    orch.build_system_prompt = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.gemini_worker = None

    orch.check_cycle_completion()
    orch.mempalace_manager.compact_short_term_for_sleep.assert_called_once_with(max_active=5)
    orch.build_system_prompt.assert_called_once()
