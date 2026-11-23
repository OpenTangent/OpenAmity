import hashlib
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import paths
from core.config_manager import ConfigManager
from core.mempalace_manager import MemPalaceManager
from core.morphological_layer import (
    MorphologicalMemoryLayer,
    compute_cross_session_semantic_bridges,
    prime_for_input,
)
from core.openrouter_worker import OpenRouterWorker
from core.orchestrator import AmityOrchestrator
from core.prompt_context import MEMORY_BEGIN, MEMORY_END
from core.settings_manager import SettingsManager


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
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


def test_cross_session_bridges_exclude_same_session():
    ids = ["s1_p1", "s1_p2", "s2_p1", "s3_p1"]
    groups = ["session_001", "session_001", "session_002", "session_003"]
    embs = np.array(
        [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.95, 0.31, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )

    edges = compute_cross_session_semantic_bridges(ids, embs, groups, sem_threshold=0.60, top_k=2)
    pairs = {(u, v) for u, v, _ in edges}
    assert ("s1_p1", "s1_p2") not in pairs
    assert ("s1_p1", "s2_p1") in pairs
    assert ("s2_p1", "s1_p1") in pairs
    assert all("s3_p1" not in p for p in pairs)


def test_silence_gate_blocks_greetings_and_low_similarity():
    called = {"n": 0}

    def fake_dense(q, k):
        called["n"] += 1
        return [{"drawer_id": "d1", "score": 0.28, "content": "Unrelated chatter", "source_file": "s1"}]

    assert prime_for_input("Morning!", fake_dense) == ""
    assert called["n"] == 0
    assert prime_for_input("What is the weather in Mars right now?", fake_dense) == ""
    assert called["n"] == 1


def test_prime_fires_on_strong_or_corroborated_match_with_highway():
    def fake_dense(q, k):
        return [
            {
                "drawer_id": "s51",
                "score": 0.43,
                "content": "[Session 051] User makes beef stew and homemade yogurt in a slow cooker.",
                "source_file": "session_051",
            }
        ]

    def fake_bm25(q, k):
        return [{"drawer_id": "s51"}]

    def fake_relax(seeds, k):
        return [("s51_p2", 0.80), ("s14", 0.45)]

    def fake_hydrate(nid):
        if nid == "s51_p2":
            return {"drawer_id": "s51_p2", "content": "same session", "source_file": "session_051"}
        if nid == "s14":
            return {
                "drawer_id": "s14",
                "content": "[Session 014] User avoids dairy lactose except fermented yogurt.",
                "source_file": "session_014",
            }
        return None

    hint = prime_for_input(
        "I have been struggling with my slow cooker recipes, any advice?",
        fake_dense,
        bm25_search_fn=fake_bm25,
        morpho_relax_fn=fake_relax,
        hydrate_fn=fake_hydrate,
    )
    assert "Partial Pre-Conscious Hint" in hint
    assert "MemPalace_search" in hint
    assert "[Drawer #s51]" in hint
    assert "beef stew" in hint
    assert "[Drawer #s14]" in hint
    assert "s51_p2" not in hint
    assert len(hint) <= 750

    # Confirm a realistic two-cue payload (240-char primary + 220-char highway) fits inside max_chars=750 without clipping primary
    long_primary = "[Session 051] " + ("slow cooker beef stew recipe details " * 8) + "TAIL_MARKER_PRIMARY"
    long_highway = "[Session 014] " + ("fermented yogurt dairy notes " * 9) + "TAIL_MARKER_HIGHWAY"
    hint_two_cue = prime_for_input(
        "I have been struggling with my slow cooker recipes, any advice?",
        lambda q, k: [{"drawer_id": "s51", "score": 0.48, "content": long_primary[:240], "source_file": "session_051"}],
        bm25_search_fn=fake_bm25,
        morpho_relax_fn=fake_relax,
        hydrate_fn=lambda nid: {"drawer_id": nid, "content": long_highway[:220], "source_file": "session_014"} if nid == "s14" else None,
    )
    assert "Partial Pre-Conscious Hint" in hint_two_cue
    assert "MemPalace_search" in hint_two_cue
    assert long_primary[:235] in hint_two_cue
    assert "[Drawer #s14]" in hint_two_cue
    assert len(hint_two_cue) <= 750
    assert not hint_two_cue.endswith("...")


@pytest.mark.core
def test_relax_setpoint_min_potential_default_and_override(isolated_env, tmp_path):
    layer = MorphologicalMemoryLayer(palace_path=str(tmp_path / "morpho_gate"))
    layer.register_drawer("T")
    layer.register_drawer("N_weak")
    layer.record_coactivation(["T", "N_weak"], eta=1.0)

    # Tune leak/steps so neighbor reaches between 0.10 and 0.25 (~0.20)
    layer.update_parameters(leak=0.25, n_steps=12)
    default_res = dict(layer.relax_setpoint(["T"]))
    assert "N_weak" in default_res
    pot = default_res["N_weak"]
    assert 0.10 < pot < 0.25

    # Explicit min_potential=0.25 filters out weak unreinforced neighbor
    gated_res = layer.relax_setpoint(["T"], min_potential=0.25)
    assert gated_res == []

    # Verify MemPalaceManager._get_prospective_memory_context passes prospective_min_potential=0.25 by default
    agent_id = "agent_wake_gate"
    mp = MemPalaceManager(agent_id=agent_id)
    r1 = mp.add_memory("Active goal anchor memory", wing="default", room="goals", source_file="s1")
    r2 = mp.add_memory("Neighbor memory note", wing="default", room="goals", source_file="s2")
    mp.flush_turn_coactivation(is_sleep_cycle=True)

    traj_path = os.path.join(paths.get_base_dir_for(agent_id), "trajectory.json")
    os.makedirs(os.path.dirname(traj_path), exist_ok=True)
    with open(traj_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "aspirations": {
                    "short_term": [
                        {"id": "asp_1", "description": "Active goal anchor memory", "status": "active", "sphere_level": 1}
                    ],
                    "medium_term": [],
                    "long_term": [],
                },
                "tasks": [],
            },
            f,
        )

    observed_kwargs = []
    orig_relax = mp.morpho_layer.relax_setpoint

    def spy_relax(*args, **kwargs):
        observed_kwargs.append(kwargs)
        return [(r2["drawer_id"], 0.18)]

    with patch.object(mp.morpho_layer, "relax_setpoint", side_effect=spy_relax):
        ctx = mp._get_prospective_memory_context()
    assert observed_kwargs and observed_kwargs[0]["min_potential"] == pytest.approx(0.25)
    assert ctx == ""


@pytest.mark.core
def test_search_non_displacing_associative_highway_hint(isolated_env):
    agent_id = "agent_highway_search"
    mp = MemPalaceManager(agent_id=agent_id)

    d_main = mp.add_memory(
        "[Session 010] Main query target about alpine botanical specimens.",
        wing="default",
        room="notes",
        source_file="session_010",
    )["drawer_id"]
    d_same = mp.add_memory(
        "[Session 010] Same session note that must not appear as highway.",
        wing="default",
        room="notes",
        source_file="session_010",
    )["drawer_id"]
    d_cross = mp.add_memory(
        "[Session 044] Unrelated lexical topic: grandfather brass barometer repair log.",
        wing="default",
        room="notes",
        source_file="session_044",
    )["drawer_id"]
    mp.flush_turn_coactivation(is_sleep_cycle=True)

    # Strong Hebbian link from d_main -> d_cross (distinct session) and d_same (same session)
    mp.morpho_layer.record_coactivation([d_main, d_same], eta=3.0)
    mp.morpho_layer.record_coactivation([d_main, d_cross], eta=3.0)

    # search_records must NEVER include d_cross if limited to 1 top hit
    rec_res = mp.search_records("alpine botanical specimens", limit=1)
    assert len(rec_res.hits) == 1
    assert rec_res.hits[0].logical_id == d_main

    # search() appends 1 non-displacing [Associative Highway] line for d_cross (skipping d_same!)
    with patch.object(mp.morpho_layer, "relax_setpoint", return_value=[(d_same, 0.72), (d_cross, 0.58)]):
        out = mp.search("alpine botanical specimens", n_results=1)
    assert f"[Drawer #{d_main}]" in out
    assert f"[Associative Highway] [Drawer #{d_cross}]" in out
    assert "barometer" in out
    assert d_same not in out
    assert out.count("[Associative Highway]") == 1

    # Toggle off via settings
    settings = SettingsManager(agent_id=agent_id)
    settings.set("core.morphological_memory.search_associative_hint", False)
    settings.save()
    mp.reload_settings()
    with patch.object(mp.morpho_layer, "relax_setpoint", return_value=[(d_cross, 0.58)]):
        out_off = mp.search("alpine botanical specimens", n_results=1)
    assert "[Associative Highway]" not in out_off


@pytest.mark.core
def test_consolidate_semantic_bridges_end_to_end(isolated_env):
    agent_id = "agent_sleep_bridges"
    mp = MemPalaceManager(agent_id=agent_id)

    d_s1_a = mp.add_memory(
        "Morning espresso pour-over recipe with Ethiopian light roast beans.",
        wing="default",
        room="coffee",
        source_file="session_001",
    )["drawer_id"]
    d_s1_b = mp.add_memory(
        "Morning espresso pour-over recipe with Ethiopian light roast beans and kettle temperature.",
        wing="default",
        room="coffee",
        source_file="session_001",
    )["drawer_id"]
    d_s2 = mp.add_memory(
        "Weekend pour-over espresso brewing with Ethiopian light roast coffee beans.",
        wing="default",
        room="coffee",
        source_file="session_002",
    )["drawer_id"]
    d_strong = mp.add_memory(
        "Completely unrelated woodworking chisel sharpening notes.",
        wing="default",
        room="workshop",
        source_file="session_003",
    )["drawer_id"]
    mp.flush_turn_coactivation(is_sleep_cycle=True)

    # Seed a strong lived Hebbian edge (weight = 5.0)
    for _ in range(5):
        mp.morpho_layer.record_coactivation([d_s1_a, d_strong], eta=1.0)

    res = mp.consolidate_semantic_bridges(sem_threshold=0.60, top_k=2)
    assert res["status"] == "ok"
    assert res["edges_upserted"] >= 2

    with mp.morpho_layer._get_conn() as conn:
        same_sess = conn.execute(
            "SELECT weight FROM morpho_edges WHERE source_id=? AND target_id=?",
            (d_s1_a, d_s1_b),
        ).fetchone()
        cross_sess = conn.execute(
            "SELECT weight FROM morpho_edges WHERE source_id=? AND target_id=?",
            (d_s1_a, d_s2),
        ).fetchone()
        preserved = conn.execute(
            "SELECT weight FROM morpho_edges WHERE source_id=? AND target_id=?",
            (d_s1_a, d_strong),
        ).fetchone()

    assert same_sess is None
    assert cross_sess is not None and cross_sess["weight"] >= 0.48
    assert preserved is not None and preserved["weight"] >= 5.0

    # Verify check_cycle_completion calls step_temporal_decay then consolidate_semantic_bridges after clean sleep
    call_order = []
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.is_thinking = False
    orch._is_sleep_cycle = True
    orch._sleep_cycle_error = False
    orch._hard_fatigue_limit_hit = False
    orch._pending_settings_reload = False
    orch._pausing_in_progress = False
    orch._shutdown_flag = False
    orch.is_paused = False
    orch.tts_worker = None
    orch.speech_queue = []
    orch.event_queue = []
    orch.recent_history = []
    orch.session_fatigue_tokens = 100
    orch._last_notified_fatigue_threshold = 0
    orch.current_task_weight = 100
    orch.settings_manager = SettingsManager(agent_id=agent_id)
    orch.mempalace_manager = MagicMock()
    orch.mempalace_manager.compact_short_term_for_sleep.side_effect = lambda **kw: call_order.append("compact")
    orch.mempalace_manager.step_temporal_decay.side_effect = lambda: call_order.append("decay")
    orch.mempalace_manager.consolidate_semantic_bridges.side_effect = lambda: call_order.append("bridges")
    orch.cerebrum = MagicMock()
    orch.cerebrum.get_all_tool_declarations.return_value = []
    orch.gemini_worker = MagicMock()
    orch.system_prompt = ""
    orch.build_system_prompt = MagicMock(side_effect=lambda: call_order.append("build_prompt"))
    orch._write_somatic_state = MagicMock()
    orch.clear_session_state = MagicMock()
    orch.set_busy_state = MagicMock()

    orch.check_cycle_completion()
    assert call_order == ["compact", "decay", "bridges", "build_prompt"]


@pytest.mark.core
def test_prime_for_input_kv_cache_invariance(isolated_env):
    agent_id = "agent_prime_kv"
    cfg = ConfigManager()
    cfg.set("user-full-name", "Andrew")
    cfg.save()

    mp = MemPalaceManager(agent_id=agent_id)
    slow_cooker_id = mp.add_memory(
        "[Session 051] Andrew loves cooking slow cooker beef stew and homemade fermented yogurt.",
        wing="default",
        room="preferences",
        source_file="session_051",
    )["drawer_id"]
    lactose_id = mp.add_memory(
        "[Session 014] Dietary note: mild lactose intolerance except fermented yogurt.",
        wing="default",
        room="preferences",
        source_file="session_014",
    )["drawer_id"]
    mp.flush_turn_coactivation(is_sleep_cycle=True)
    mp.morpho_layer.record_coactivation([slow_cooker_id, lactose_id], eta=3.0)

    # Populate background sessions (> 20 drawers total)
    filler_topics = [
        "Visited the coastal botanical garden and sketched protea flowers.",
        "Repaired the wooden garden gate latch using stainless steel screws.",
        "Watched a documentary about deep sea hydrothermal vents.",
        "Read chapters on nineteenth-century maritime navigation instruments.",
        "Organized old vinyl jazz records by saxophone soloist.",
        "Replaced bicycle brake pads and trued the rear wheel spoke tension.",
        "Baked sourdough rye loaves using stone-milled whole grain flour.",
        "Cleaned the rainwater cistern filter before autumn storms.",
        "Catalogued vintage fountain pen nibs and iron-gall ink samples.",
        "Built a cedar birdhouse for Cape white-eyes in the backyard.",
        "Reviewed notes on Roman concrete pozzolana curing underwater.",
        "Practised sight-reading Bach cello suites arranged for guitar.",
        "Calibrated the analog darkroom enlarger timer for silver gelatin prints.",
        "Mapped hiking trails across the sandstone ridge above town.",
        "Tested solar battery charge controller efficiency during overcast days.",
        "Transcribed grandfather's handwritten orchard grafting journal.",
        "Tuned the upright piano middle octave temperament.",
        "Sorted heirloom tomato seeds for early spring cold frame germination.",
        "Patched canvas camping tent seams with beeswax waterproofing.",
        "Documented local estuary bird migration arrival dates.",
    ]
    for idx, txt in enumerate(filler_topics, start=100):
        mp.add_memory(txt, wing="default", room="archive", source_file=f"session_{idx:03d}")
    mp.flush_turn_coactivation(is_sleep_cycle=True)

    # Silence gate audit: >= 90% silence across 25 unrelated greetings/coding/trivia queries
    unrelated_queries = [
        "Morning!",
        "Thanks, that makes sense.",
        "Ok sounds good",
        "What is the capital of France?",
        "Can you write a quick Python function to merge two sorted lists?",
        "Explain the difference between TCP and UDP briefly.",
        "How do I invert a binary tree in Rust?",
        "What is the boiling point of water at 2,000 metres elevation?",
        "Convert 45 degrees Celsius to Fahrenheit.",
        "Who wrote Crime and Punishment?",
        "Explain how SQLite WAL mode works.",
        "What does HTTP status code 429 mean?",
        "Give me a bash one-liner to find the 10 largest files in a directory.",
        "How many planets are in the solar system?",
        "What is the time complexity of quicksort in the worst case?",
        "Draft a short git commit message for fixing a race condition.",
        "What is the difference between a mutex and a semaphore?",
        "How does RSA public-key encryption work conceptually?",
        "Write a regex to match ISO 8601 dates like 2026-11-21.",
        "What year did the Berlin Wall fall?",
        "Explain Bayes' theorem in two sentences.",
        "How do I rebase an interactive git branch safely?",
        "What is the chemical formula for ozone?",
        "Give me three synonyms for meticulous.",
        "How does garbage collection differ between Go and Python?",
    ]
    silent_count = sum(1 for q in unrelated_queries if mp.prime_for_input(q) == "")
    assert (silent_count / len(unrelated_queries)) >= 0.90
    assert mp._turn_touched == []  # Silence checks must never touch drawers

    # Priming fires on resonant query and does NOT pollute _turn_touched
    hint = mp.prime_for_input("Any advice for making beef stew in my slow cooker?")
    assert f"[Drawer #{slow_cooker_id}]" in hint
    assert f"[Drawer #{lactose_id}]" in hint
    assert mp._turn_touched == []

    # KV-cache invariance in AmityOrchestrator._async_query_prep
    orch = AmityOrchestrator.__new__(AmityOrchestrator)
    orch.agent_id = agent_id
    orch.config_manager = cfg
    orch.settings_manager = SettingsManager(agent_id=agent_id)
    orch.mempalace_manager = mp
    orch.cerebrum = SimpleNamespace(
        get_agent_manual=lambda: "# Manual\n",
        get_all_tool_declarations=lambda: [],
    )
    orch.last_action_result = None
    orch._last_prospective_context = None
    orch.save_session_state = MagicMock()
    orch.finish_thinking = MagicMock()
    orch.set_busy_state = MagicMock()
    orch.gemini_worker = MagicMock()

    orch.build_system_prompt()
    prompt_hash_before = hashlib.sha256(orch.system_prompt.encode("utf-8")).hexdigest()

    orch.current_user_prompt = "Any advice for making beef stew in my slow cooker?"
    orch._async_query_prep(orch.current_user_prompt, [], None)

    prompt_hash_after = hashlib.sha256(orch.system_prompt.encode("utf-8")).hexdigest()
    assert prompt_hash_after == prompt_hash_before
    assert not orch.gemini_worker.update_system_instruction.called

    sent_prompt = orch.gemini_worker.send_prompt.call_args[0][0]
    expected_header = (
        "[Morphological Resonance — Partial Pre-Conscious Hint "
        "(NOT exhaustive; always verify with MemPalace_search when updates, "
        "counts, dates, or multiple sessions may exist)]:"
    )
    assert expected_header in sent_prompt
    assert "Partial Pre-Conscious Hint" in sent_prompt
    assert "MemPalace_search" in sent_prompt
    assert f"[Drawer #{slow_cooker_id}]" in sent_prompt

    # Deduplication with Mirror Recall when entity name matches
    mp.add_memory("Person: Jordan\nPreference: slow cooker beef stew", wing="sanctuary", room="people", source_file="s_jordan")
    mp.update_mirror("Jordan", subjective_view="Loves slow cooker stew.", sphere_level=2)
    mp.flush_turn_coactivation(is_sleep_cycle=True)

    dedup_hint = mp.prime_for_input("Any advice for making beef stew in my slow cooker?", exclude_drawer_ids={slow_cooker_id})
    assert slow_cooker_id not in dedup_hint

    # Toggle off perturbation_priming
    settings = SettingsManager(agent_id=agent_id)
    settings.set("core.morphological_memory.perturbation_priming", False)
    settings.save()
    mp.reload_settings()
    assert mp.prime_for_input("Any advice for making beef stew in my slow cooker?") == ""


def test_openrouter_worker_headless_text_accessors(monkeypatch, tmp_path):
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(tmp_path))

    with patch.object(SettingsManager, "get_env", return_value="sk-or-test-key-12345"):
        worker = OpenRouterWorker(agent_id="test_openrouter_headless")
        worker.start_session(system_instruction="Test system prompt")

        c1 = MagicMock()
        c1.choices = [MagicMock()]
        c1.choices[0].delta = MagicMock()
        c1.choices[0].delta.reasoning = "Let me verify the date."
        c1.choices[0].delta.content = None
        c1.choices[0].delta.tool_calls = None
        c1.usage = None

        c2 = MagicMock()
        c2.choices = [MagicMock()]
        c2.choices[0].delta = MagicMock()
        c2.choices[0].delta.reasoning = None
        c2.choices[0].delta.content = "The final answer is 42."
        c2.choices[0].delta.tool_calls = None
        c2.usage = None

        worker.client = MagicMock()
        worker.client.chat.completions.create.return_value = iter([c1, c2])

        emitted = []
        worker.thought_received.connect(lambda txt, fcs: emitted.append((txt, fcs)))

        worker._process_thought_inner("What is the answer?", None, False)

        assert worker.last_assistant_text == "The final answer is 42."
        assert worker.last_reasoning_text == "Let me verify the date."
        assert emitted == [("Let me verify the date.\n\nThe final answer is 42.", [])]
