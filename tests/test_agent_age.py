import os
import tempfile
import pytest
from core.agent_manager import AgentManager
from core.settings_manager import SettingsManager


@pytest.fixture
def temp_agent_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        class PatchedAgentManager(AgentManager):
            def __init__(self):
                self.agents_dir = os.path.join(tmpdir, "agents")
                self.orchestrators = {}
                os.makedirs(self.agents_dir, exist_ok=True)
                self.migrate_legacy_data()
                self._backfill_creation_dates()
                self._complete_legacy_chatroom_migration()
                self.hook_server = None

        mgr = PatchedAgentManager()
        yield mgr


def test_agent_creation_date_stamped_and_ordered(temp_agent_manager, monkeypatch):
    mgr = temp_agent_manager

    # Create agent 1
    aid1 = mgr.create_agent("agent_1")
    s1 = SettingsManager(agent_id=aid1)
    s1.set("core.agent.creation-date", "2026-01-15")
    s1.save()

    # Create agent 2
    aid2 = mgr.create_agent("agent_2")
    s2 = SettingsManager(agent_id=aid2)
    s2.set("core.agent.creation-date", "2026-03-20")
    s2.save()

    # Create agent 3 (missing creation date initially, should backfill)
    aid3 = mgr.create_agent("agent_3")
    s3 = SettingsManager(agent_id=aid3)
    s3.set("core.agent.creation-date", "")
    s3.save()

    # Run backfill
    mgr._backfill_creation_dates()
    assert len(s3.get("core.agent.creation-date")) == 10  # YYYY-MM-DD

    # Check age sorting: agent_1 (Jan) -> agent_2 (Mar) -> agent_3 (today)
    sorted_agents = mgr.get_agents_by_age()
    assert sorted_agents[0] == "agent_1"
    assert sorted_agents[1] == "agent_2"
    assert sorted_agents[2] == "agent_3"
