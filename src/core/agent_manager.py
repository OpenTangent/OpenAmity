import os
import shutil
import uuid
import logging
from datetime import datetime
from config import paths


class AgentManager:
    def __init__(self):
        self.agents_dir = os.path.join(paths.get_app_data_dir(), "agents")
        self.orchestrators = {}
        os.makedirs(self.agents_dir, exist_ok=True)
        self.migrate_legacy_data()

        # Enforce creation-date backfill on existing agents
        self._backfill_creation_dates()

        # Complete legacy chatroom migration if needed
        self._complete_legacy_chatroom_migration()

        # Start watch notifier
        from core.chatroom_notifier import ChatroomWatchNotifier
        self.chatroom_notifier = ChatroomWatchNotifier(self)

        self.hook_server = None
        self.start_hook_server()

    def _backfill_creation_dates(self):
        """Ensures all existing agents have core.agent.creation-date set, falling back to current date."""
        from core.settings_manager import SettingsManager
        today_str = datetime.now().strftime("%Y-%m-%d")
        for aid in self.get_all_agents():
            try:
                settings = SettingsManager(agent_id=aid)
                cdate = settings.get("core.agent.creation-date", "")
                if not cdate or str(cdate).strip().lower() in ("", "unknown"):
                    settings.set("core.agent.creation-date", today_str)
                    settings.save()
                    logging.info(f"AgentManager: Backfilled creation-date '{today_str}' for agent {aid}")
            except Exception as e:
                logging.warning(f"AgentManager: Error checking creation-date for agent {aid}: {e}")

    def _complete_legacy_chatroom_migration(self):
        try:
            from core.chatroom_manager import ChatroomManager
            cm = ChatroomManager()
            all_uids = [self.get_agent_uid(aid) for aid in self.get_all_agents()]
            cm.complete_legacy_migration(all_uids)
        except Exception as e:
            logging.warning(f"AgentManager: Error completing legacy chatroom migration: {e}")

    def migrate_legacy_data(self):
        legacy_files = [
            "settings.json",
            "somatic_state.json",
            "soul_jar.json",
            "identity.txt",
            "short_term_mem.json",
            "trajectory.json",
            "completed_trajectory_archive.json",
            "pulses.db",
            ".env",
            "address_book.json"
        ]
        legacy_dirs = [
            "mempalace",
            "whatsapp_bridge",
            "whatsapp_data",
            "terminal"
        ]

        app_data_dir = paths.get_app_data_dir()
        needs_migration = False

        for f in legacy_files:
            if os.path.exists(os.path.join(app_data_dir, f)):
                needs_migration = True
                break
        if not needs_migration:
            for d in legacy_dirs:
                if os.path.exists(os.path.join(app_data_dir, d)):
                    needs_migration = True
                    break

        if needs_migration:
            default_agent_dir = os.path.join(self.agents_dir, "default")
            migrated_agent_id = str(uuid.uuid4())
            migrated_agent_dir = os.path.join(
                self.agents_dir, migrated_agent_id)

            if os.path.exists(default_agent_dir):
                shutil.move(default_agent_dir, migrated_agent_dir)
                logging.info(
                    f"Renamed existing 'default' agent to '{migrated_agent_id}'.")
            else:
                os.makedirs(migrated_agent_dir, exist_ok=True)
                logging.info(
                    f"Migrating legacy single-agent data to '{migrated_agent_id}' agent.")

            for f in legacy_files:
                src = os.path.join(app_data_dir, f)
                if os.path.exists(src):
                    dst = os.path.join(migrated_agent_dir, f)
                    if not os.path.exists(dst):
                        try:
                            shutil.move(src, dst)
                        except Exception as e:
                            logging.error(f"Failed to migrate file {f}: {e}")
                    else:
                        os.remove(src)

            for d in legacy_dirs:
                src = os.path.join(app_data_dir, d)
                if os.path.exists(src):
                    dst = os.path.join(migrated_agent_dir, d)
                    if not os.path.exists(dst):
                        try:
                            shutil.move(src, dst)
                        except Exception as e:
                            logging.error(f"Failed to migrate dir {d}: {e}")
                    else:
                        shutil.rmtree(src, ignore_errors=True)

    def get_all_agents(self):
        agents = []
        if os.path.exists(self.agents_dir):
            for entry in os.listdir(self.agents_dir):
                full_path = os.path.join(self.agents_dir, entry)
                if os.path.isdir(full_path):
                    agents.append(entry)
        return agents

    def get_agent_creation_date(self, agent_id: str) -> str:
        from core.settings_manager import SettingsManager
        settings = SettingsManager(agent_id=agent_id)
        cdate = settings.get("core.agent.creation-date", "")
        if cdate and str(cdate).strip().lower() not in ("", "unknown"):
            return str(cdate).strip()
        return datetime.now().strftime("%Y-%m-%d")

    def get_agents_by_age(self):
        """
        Returns all agent IDs sorted from oldest to newest based on core.agent.creation-date.
        Ties are broken by agent directory mtime or ID.
        """
        all_agents = self.get_all_agents()

        def _sort_key(aid):
            cdate = self.get_agent_creation_date(aid)
            agent_dir = os.path.join(self.agents_dir, aid)
            mtime = os.path.getmtime(agent_dir) if os.path.exists(agent_dir) else 0.0
            return (cdate, mtime, aid)

        return sorted(all_agents, key=_sort_key)

    def create_agent(self, agent_id: str) -> str:
        agent_dir = os.path.join(self.agents_dir, agent_id)
        os.makedirs(agent_dir, exist_ok=True)
        env_path = os.path.join(agent_dir, ".env")
        if not os.path.exists(env_path):
            with open(env_path, "w") as f:
                f.write("")

        # Enforce creation date
        from core.settings_manager import SettingsManager
        settings = SettingsManager(agent_id=agent_id)
        if not settings.get("core.agent.creation-date"):
            settings.set("core.agent.creation-date", datetime.now().strftime("%Y-%m-%d"))
            settings.save()

        self.get_agent_uid(agent_id)
        return agent_id

    def create_new_agent(self) -> str:
        agent_id = str(uuid.uuid4())
        return self.create_agent(agent_id)

    def delete_agent(self, agent_id: str):
        uid = self.get_agent_uid(agent_id)
        if hasattr(self, 'chatroom_notifier') and self.chatroom_notifier and uid:
            self.chatroom_notifier.cancel_room_notifications_for_agent(None, uid)

        try:
            from core.chatroom_manager import ChatroomManager
            ChatroomManager().remove_agent_everywhere(uid)
        except Exception as e:
            logging.warning(f"AgentManager: Error removing agent {uid} from chatrooms: {e}")

        if agent_id in self.orchestrators:
            orchestrator = self.orchestrators[agent_id]
            try:
                if hasattr(orchestrator, 'shutdown'):
                    orchestrator.shutdown(force_sleep=False)
            except Exception as e:
                logging.error(
                    f"AgentManager: Error during shutdown while deleting agent {agent_id}: {e}", exc_info=True)
            self.orchestrators.pop(agent_id, None)

        agent_dir = os.path.join(self.agents_dir, agent_id)
        if os.path.exists(agent_dir):
            try:
                shutil.rmtree(agent_dir, ignore_errors=True)
            except Exception as e:
                logging.error(
                    f"AgentManager: Error deleting agent directory {agent_dir}: {e}", exc_info=True)
        from core.settings_manager import SettingsManager
        SettingsManager.remove_instance(agent_id)

    def get_orchestrator(self, agent_id: str):
        return self.orchestrators.get(agent_id)

    def stop_agent(self, agent_id: str):
        uid = self.get_agent_uid(agent_id)
        if hasattr(self, 'chatroom_notifier') and self.chatroom_notifier and uid:
            self.chatroom_notifier.cancel_room_notifications_for_agent(None, uid)

        if agent_id in self.orchestrators:
            orchestrator = self.orchestrators.pop(agent_id)
            try:
                if hasattr(orchestrator, 'shutdown'):
                    orchestrator.shutdown(force_sleep=False)
            except Exception as e:
                logging.error(
                    f"AgentManager: Error stopping agent {agent_id}: {e}", exc_info=True)

    def start_agent(self, agent_id: str):
        if agent_id in self.orchestrators:
            return self.orchestrators[agent_id]

        from core.orchestrator import AmityOrchestrator
        orchestrator = AmityOrchestrator(agent_id=agent_id)
        self.orchestrators[agent_id] = orchestrator
        return orchestrator

    def get_agent_name(self, agent_id: str) -> str:
        from core.settings_manager import SettingsManager
        settings = SettingsManager(agent_id=agent_id)
        name = settings.get("core.agent.name", "")
        if name:
            return name
        return agent_id

    def get_agent_uid(self, agent_id: str) -> str:
        from core.settings_manager import SettingsManager
        from core.uid_generator import generate_agent_uid, is_valid_agent_uid
        settings = SettingsManager(agent_id=agent_id)
        uid = settings.get("core.agent.uid", "")
        if not uid or not is_valid_agent_uid(uid):
            uid = generate_agent_uid()
            settings.set("core.agent.uid", uid)
            settings.save()
            logging.info(f"Assigned new agent UID {uid} to agent {agent_id}")
        return uid

    def get_agent_id_by_uid(self, uid: str):
        if not uid:
            return None
        clean_uid = uid.strip().upper()
        for aid in self.get_all_agents():
            agent_uid = self.get_agent_uid(aid)
            if agent_uid and agent_uid.strip().upper() == clean_uid:
                return aid
        return None

    def start_hook_server(self):
        if self.hook_server and getattr(self.hook_server, 'is_running', False):
            return
        try:
            from core.config_manager import ConfigManager
            from core.hook_server import HookServer
            cm = ConfigManager()
            if cm.get("pulse-hooks.enabled", True):
                host = cm.get("pulse-hooks.host", "127.0.0.1")
                port = cm.get("pulse-hooks.port", 7965)
                self.hook_server = HookServer(agent_manager=self, host=host, port=port)
                self.hook_server.start()
        except Exception as e:
            logging.error(f"AgentManager: Error starting HookServer: {e}", exc_info=True)

    def stop_hook_server(self):
        if self.hook_server:
            try:
                self.hook_server.stop()
            except Exception as e:
                logging.debug(f"AgentManager: Error stopping HookServer: {e}")
            self.hook_server = None
        if hasattr(self, 'chatroom_notifier') and self.chatroom_notifier:
            self.chatroom_notifier.stop()
