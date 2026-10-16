import time
import pytest
from unittest.mock import MagicMock
from core.pulse_engine import PulseEngine
from core.settings_manager import SettingsManager
from core.address_book import AddressBookManager


@pytest.fixture
def pulse_env(tmp_path, monkeypatch):
    agent_id = "test-wa-routing-agent"
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.paths.get_base_dir_for", lambda aid: str(agent_dir))

    orch = MagicMock()
    orch.agent_id = agent_id
    orch.is_paused = False
    orch.is_busy = False
    orch.has_queued_pulse = MagicMock(return_value=False)

    sm = SettingsManager(agent_id=agent_id)
    sm.set("core.auto-pulse.whitelist", [])
    sm.set("core.auto-pulse.group-whitelist", [])
    sm.set("core.auto-pulse.pulse-all-dms", False)
    sm.set("core.auto-pulse.pulse-all-groups", False)
    sm.set("core.auto-pulse.pulse-on-mentions", False)
    sm.set("core.auto-pulse.buffer-seconds", 0.05)
    sm.save()

    engine = PulseEngine(orch)
    engine.settings_manager = sm
    yield orch, sm, engine
    engine.stop()


def test_routing_default_ignores_all(pulse_env):
    orch, sm, engine = pulse_env

    # DM from unwhitelisted sender ignored
    engine.handle_whatsapp_message("27112223333@c.us", "Stranger")
    assert engine.whatsapp_timer is None

    # Group message ignored
    engine.handle_whatsapp_message("12345-67890@g.us", "Group Chat", is_group=True, group_name="Devs")
    assert engine.whatsapp_timer is None

    # Broadcast ignored
    engine.handle_whatsapp_message("status@broadcast", "Status")
    assert engine.whatsapp_timer is None


def test_routing_pulse_all_dms(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.save()

    # Any DM qualifies
    engine.handle_whatsapp_message("27999888777@c.us", "Random Contact")
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # Group still ignored
    engine.handle_whatsapp_message("12345-67890@g.us", "Group", is_group=True, group_name="Devs")
    assert engine.whatsapp_timer is None


def test_routing_pulse_all_groups(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-groups", True)
    sm.save()

    # Any group qualifies
    engine.handle_whatsapp_message("12345-67890@g.us", "Group", is_group=True, group_name="Random Group")
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # DM from unwhitelisted sender still ignored
    engine.handle_whatsapp_message("27999888777@c.us", "Stranger")
    assert engine.whatsapp_timer is None


def test_routing_group_whitelist_verbatim_matching(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.group-whitelist", ["Project Amity", "Dev Team"])
    sm.save()

    # 1. Exact match (case-insensitive & trimmed) qualifies
    engine.handle_whatsapp_message("111@g.us", "Alice", is_group=True, group_name="  project amity  ")
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # 2. Substring does NOT qualify
    engine.handle_whatsapp_message("222@g.us", "Bob", is_group=True, group_name="Project Amity Discussion")
    assert engine.whatsapp_timer is None

    # 3. Non-whitelisted group ignored
    engine.handle_whatsapp_message("333@g.us", "Charlie", is_group=True, group_name="Marketing")
    assert engine.whatsapp_timer is None


def test_routing_pulse_on_mentions(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-on-mentions", True)
    sm.save()

    # 1. Mention in an unwhitelisted group qualifies
    engine.handle_whatsapp_message("111@g.us", "Alice", is_group=True, group_name="Random Group", mentioned=True)
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()
    engine.whatsapp_timer = None

    # 2. Non-mention in that same group is ignored
    engine.handle_whatsapp_message("111@g.us", "Alice", is_group=True, group_name="Random Group", mentioned=False)
    assert engine.whatsapp_timer is None

    # 3. Mention in an unwhitelisted DM qualifies
    engine.handle_whatsapp_message("27111222333@c.us", "Stranger", is_group=False, mentioned=True)
    assert engine.whatsapp_timer is not None
    engine.whatsapp_timer.cancel()


def test_fixed_window_buffer_behavior(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.5)
    sm.save()

    # First message starts timer
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    initial_timer = engine.whatsapp_timer
    assert initial_timer is not None

    # Second message arrives while timer is running; timer must NOT be replaced/restarted
    engine.handle_whatsapp_message("27222222222@c.us", "Contact 2")
    assert engine.whatsapp_timer is initial_timer

    initial_timer.cancel()


def test_cumulative_buffer_slowdown_and_reset(pulse_env, monkeypatch):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.set("core.auto-pulse.buffer-seconds", 0.05)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    current_fake_time = 1000.0
    monkeypatch.setattr(time, "time", lambda: current_fake_time)

    assert engine._wa_extra_delay == 0.0

    # 1. Fire first pulse
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    assert engine.whatsapp_timer is not None
    engine.execute_whatsapp_pulse()

    assert len(pulses_emitted) == 1
    # Check that privacy is strictly maintained (no sender or number leaked)
    assert "27111111111" not in pulses_emitted[0][0]
    assert "Contact 1" not in pulses_emitted[0][0]
    assert "New WhatsApp messages" in pulses_emitted[0][0]
    assert pulses_emitted[0][1] == "WhatsApp message received"

    # Extra delay increased by 10 seconds
    assert engine._wa_extra_delay == 10.0
    assert engine._wa_last_pulse_time == 1000.0

    # 2. Fire second pulse 2 minutes later
    current_fake_time = 1120.0
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    engine.execute_whatsapp_pulse()
    assert engine._wa_extra_delay == 20.0

    # 3. Simulate 30 minutes (1800s) of silence
    current_fake_time = 1120.0 + 1801.0
    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    # Buffer delay should reset back to base (extra_delay = 0.0)
    assert engine._wa_extra_delay == 0.0
    if engine.whatsapp_timer:
        engine.whatsapp_timer.cancel()


def test_coalescing_when_pulse_already_queued(pulse_env):
    orch, sm, engine = pulse_env
    sm.set("core.auto-pulse.pulse-all-dms", True)
    sm.save()

    pulses_emitted = []
    engine.trigger_pulse.connect(lambda p, purpose=None: pulses_emitted.append((p, purpose)))

    # Simulate orchestrator already having a WhatsApp pulse queued
    orch.has_queued_pulse.return_value = True

    engine.handle_whatsapp_message("27111111111@c.us", "Contact 1")
    engine.execute_whatsapp_pulse()

    # Nothing should be emitted because it coalesces
    assert len(pulses_emitted) == 0
    # And extra delay must NOT escalate on coalesced pulses
    assert engine._wa_extra_delay == 0.0


def test_reset_whatsapp_slowdown(pulse_env):
    orch, sm, engine = pulse_env
    engine._wa_extra_delay = 30.0
    engine._wa_last_pulse_time = 500.0

    engine.reset_whatsapp_slowdown()
    assert engine._wa_extra_delay == 0.0
    assert engine._wa_last_pulse_time == 0.0
