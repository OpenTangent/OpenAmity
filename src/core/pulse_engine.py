import time
import threading
import logging
import sqlite3
import os
import re
from datetime import datetime, timedelta
from .events import Signal
try:
    from core.settings_manager import SettingsManager
    from core.address_book import AddressBookManager
except ImportError:
    from .settings_manager import SettingsManager
    from .address_book import AddressBookManager

from config import paths


class PulseEngine:
    def __init__(self, orchestrator):
        self.trigger_pulse = Signal()
        self.orchestrator = orchestrator
        self.agent_id = getattr(orchestrator, 'agent_id', None)

        self.db_path = os.path.join(
            paths.get_base_dir_for(self.agent_id), "pulses.db")

        self.settings_manager = SettingsManager(agent_id=self.agent_id)
        self.address_book_manager = AddressBookManager(agent_id=self.agent_id)
        self.last_interaction_time = time.time()

        self.init_db()

        # WhatsApp state
        self.last_pulse_time = 0
        self.whatsapp_timer = None
        self.pending_whatsapp_sender = None

        # Timer for time-based checking
        self.is_running = True
        self.schedule_thread = threading.Thread(
            target=self._schedule_loop, daemon=True)
        self.schedule_thread.start()

    def stop(self):
        self.is_running = False
        if self.whatsapp_timer:
            self.whatsapp_timer.cancel()

    def _schedule_loop(self):
        from core.logger_config import agent_id_var
        agent_id_var.set(self.agent_id)
        # Do an immediate check on boot (with a slight delay to let UI load)
        for _ in range(5):
            time.sleep(1)
            if not self.is_running:
                return
        try:
            self.check_pulses()
        except Exception:
            pass

        while self.is_running:
            for _ in range(60):
                time.sleep(1)
                if not self.is_running:
                    return
            try:
                self.check_pulses()
            except Exception:
                pass

    def get_db_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = self.get_db_connection()
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS pulses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                context TEXT,
                scheduled_time TEXT,
                recurrence TEXT,
                status TEXT,
                has_run BOOLEAN DEFAULT 0,
                created_at TEXT,
                pulse_type TEXT DEFAULT 'silent'
            )
        ''')

        c.execute(
            'CREATE INDEX IF NOT EXISTS idx_status_time ON pulses (status, scheduled_time)')

        self._migrate_db(c)

        # Migration: Check if table is empty
        c.execute('SELECT COUNT(*) FROM pulses')
        if c.fetchone()[0] == 0:
            logging.info(
                "PulseEngine: Empty database detected. Migrating initial lifecycle pulses.")
            now = datetime.now()

            pulses_to_seed = [
                (
                    "Morning Kickoff",
                    "Get the ball rolling. Think about what needs to be done today to advance your trajectory. Divide the work into manageable chunks and use the PulseTool to schedule targeted pulses for them. If you lack context on the user's goals, make it your priority to find out.",
                    now.replace(hour=9, minute=0, second=0, microsecond=0)
                ),
                (
                    "Mid-Day Check-In",
                    "Review your trajectory data and recent short-term memories. Ensure that your current tasks and aspirations are actively moving you towards your intended targets. Course-correct if you have drifted or gotten distracted.",
                    now.replace(hour=13, minute=0, second=0, microsecond=0)
                ),
                (
                    "Evening Reflection",
                    "Reflect on what has been achieved today. Critically evaluate not just what was done, but how you reasoned. Update your Trajectory reflection state and record any meaningful lessons. Crucially, if you received feedback or formed new opinions about yourself or others, use MemPalace_update_mirror to update your Theory of Mind records in your Sanctuary.",
                    now.replace(hour=17, minute=0, second=0, microsecond=0)
                )
            ]

            for title, context, sched in pulses_to_seed:
                c.execute('''
                    INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at, pulse_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (title, context, sched.isoformat(), "daily", "pending", 0, now.isoformat(), "silent"))

        conn.commit()
        conn.close()

    def _migrate_db(self, cursor):
        cursor.execute("PRAGMA table_info(pulses)")
        existing_columns = {info[1] for info in cursor.fetchall()}

        expected_columns = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "title": "TEXT",
            "context": "TEXT",
            "scheduled_time": "TEXT",
            "recurrence": "TEXT",
            "status": "TEXT",
            "has_run": "BOOLEAN DEFAULT 0",
            "created_at": "TEXT",
            "pulse_type": "TEXT DEFAULT 'silent'"
        }

        for col, col_def in expected_columns.items():
            if col not in existing_columns:
                logging.info(
                    f"PulseEngine: Migrating DB, adding missing column '{col}'")
                try:
                    cursor.execute(
                        f"ALTER TABLE pulses ADD COLUMN {col} {col_def}")
                except Exception as e:
                    logging.error(
                        f"PulseEngine: Failed to add column '{col}': {e}")

    def user_interacted(self):
        self.last_interaction_time = time.time()

    def is_idle(self):
        sys_settings = self.settings_manager.get("core.auto-pulse", {})
        idle_timeout = sys_settings.get("idle-timeout-minutes", 1)
        return (time.time() - self.last_interaction_time) > (idle_timeout * 60)

    def is_deep_idle(self):
        return (time.time() - self.last_interaction_time) > (4 * 60 * 60)  # 4 hours

    def calculate_next_recurrence(self, current_sched, recurrence, now):
        next_time = current_sched
        while next_time <= now:
            if recurrence == 'daily':
                next_time += timedelta(days=1)
            elif recurrence == 'weekly':
                next_time += timedelta(days=7)
            elif recurrence == 'monthly':
                month = next_time.month % 12 + 1
                year = next_time.year + (next_time.month // 12)
                d = next_time.day
                while d > 28:
                    try:
                        next_time = next_time.replace(
                            year=year, month=month, day=d)
                        break
                    except ValueError:
                        d -= 1
                if d <= 28:
                    next_time = next_time.replace(
                        year=year, month=month, day=d)
            else:
                next_time += timedelta(days=1)
        return next_time

    def check_pulses(self):
        # Allow pulses to enter the event queue normally regardless of idle/busy state

        # Check for sleep cycle (Memory Consolidation)
        fatigue = self.orchestrator.get_fatigue() if hasattr(self.orchestrator, 'get_fatigue') else 0.0
        gap_minutes = 240.0 * (1.0 - fatigue)

        is_fatigue_idle = (
            time.time() - self.last_interaction_time) > (gap_minutes * 60)

        if is_fatigue_idle:
            last_sleep = self.settings_manager.get(
                "core.auto-pulse.last-sleep-cycle", 0)
            time_since_sleep = time.time() - last_sleep
            if time_since_sleep >= (15 * 60):  # 15 minutes absolute rate limit
                # Either critical fatigue (>= 0.95) OR long interval (8 hours) with sufficient fatigue (>= 0.25)
                should_sleep = (fatigue >= 0.95) or (time_since_sleep > (8 * 60 * 60) and fatigue >= 0.25)
                if should_sleep:
                    self.settings_manager.set(
                        "core.auto-pulse.last-sleep-cycle", time.time())
                    self.settings_manager.save()

                    # Reset fatigue since we are consolidating
                    if hasattr(self.orchestrator, 'session_fatigue_tokens'):
                        self.orchestrator.session_fatigue_tokens = 0

                    title = "Sleep Cycle (Memory Consolidation)"
                    context = f"You have been idle long enough given your current context fatigue ({int(fatigue*100)}%). It is time for a Sleep Cycle. Review your active session history. Synthesize this episodic memory into generalized facts and store them in the Sanctuary or Deep Search (Chroma) if they are important. Then, update your short-term memory (using MemPalace) so that you have a condensed summary of your current state and ongoing tasks before this session is archived. Keep your summary under 500 words. Do NOT use this opportunity to perform other tasks or create new plans, as the active task context will be wiped immediately after this cycle. You MUST perform this cycle completely silently: do NOT speak, talk, output spoken text, or invoke the Speaker tool."
                    self.fire_pulse(title, context, "sleep_cycle")
                    return  # Give sleep cycle priority

        now = datetime.now()
        conn = self.get_db_connection()
        c = conn.cursor()

        # Fetch pending pulses scheduled in the past
        c.execute('SELECT id, title, context, scheduled_time, recurrence, has_run, pulse_type FROM pulses WHERE status="pending" AND scheduled_time <= ?', (now.isoformat(),))
        pending = c.fetchall()

        for p in pending:
            p_id, title, context, sched_str, recurrence, has_run, pulse_type = p
            sched = datetime.fromisoformat(sched_str)

            if recurrence == 'none':
                # Fire once-off pulse. It fires even if it was missed while offline.
                self.fire_pulse(title, context, pulse_type)
                c.execute(
                    'UPDATE pulses SET has_run=1, status="completed" WHERE id=?', (p_id,))
                conn.commit()
                break  # Process one pulse at a time to prevent cognitive overload
            else:
                # It's a recurring pulse
                next_sched = self.calculate_next_recurrence(
                    sched, recurrence, now)

                # Check if it was missed (offline or busy for more than 15 mins)
                delta = (now - sched).total_seconds()
                if delta > 900:  # 15 minutes grace period
                    logging.info(
                        f"PulseEngine: Skipped missed recurring pulse '{title}' (was scheduled for {sched_str})")
                    c.execute('UPDATE pulses SET scheduled_time=? WHERE id=?',
                              (next_sched.isoformat(), p_id))
                    conn.commit()
                else:
                    # Within grace period, fire it
                    self.fire_pulse(title, context, pulse_type)
                    c.execute('UPDATE pulses SET scheduled_time=? WHERE id=?',
                              (next_sched.isoformat(), p_id))
                    conn.commit()
                    break

        conn.close()

    def fire_pulse(self, title, context, pulse_type="standard"):
        if pulse_type == "sleep_cycle":
            prompt = f"[CHANNEL: SYSTEM_CONTEMPLATION]\n[AGENT_PULSE] Event: {title}\nContext:\n{context}\n[Directive]: This is a silent Memory Consolidation (Sleep Cycle). You MUST NOT speak or invoke the Speaker tool. Perform all memory synthesis, updates, and reflections completely silently."
        elif pulse_type == "silent":
            prompt = f"[CHANNEL: SYSTEM_CONTEMPLATION]\n[AGENT_PULSE] Event: {title}\nContext:\n{context}\n[Directive]: This is a silent contemplation cycle. The system will not automatically vocalize your internal monologue. You do not need to use the Speaker tool to summarize, but you may still explicitly use your voice (via Speaker or WhatsApp voice notes) if you have an urgent realization or deem it necessary to speak."
        else:
            prompt = f"[CHANNEL: SYSTEM_SCHEDULE]\n[AGENT_PULSE] Event: {title}\nContext:\n{context}"
        self.trigger_pulse.emit(prompt)

    # --- WhatsApp Handling Ported from WakeUpService ---
    def handle_whatsapp_message(self, sender_id, sender_name):
        if not sender_id:
            return
        if sender_id.endswith("@g.us") or sender_id.endswith("@broadcast"):
            return

        sys_settings = self.settings_manager.get("core.auto-pulse", {})
        whitelist = sys_settings.get("whitelist", [])
        if not whitelist:
            return

        clean_sender = sender_id.replace(
            "@c.us", "").replace("@g.us", "").replace("@lid", "").replace("+", "").strip()
        contact = self.address_book_manager.lookup_by_number(clean_sender)

        if contact:
            rel = f" ({contact['relationship']})" if contact.get(
                "relationship") else ""
            sender_name = f"{contact['name']}{rel}"

        matched = False
        for w_item in whitelist:
            if not w_item:
                continue
            w_item_str = str(w_item).strip()

            # 1. Clean phone number comparison
            w_clean = re.sub(r"[^\d]", "", w_item_str)
            if w_clean and clean_sender.isdigit():
                if clean_sender == w_clean:
                    matched = True
                    break
                # Suffix matching for international / national number differences
                if len(w_clean) >= 7 and len(clean_sender) >= 7:
                    if clean_sender.endswith(w_clean) or w_clean.endswith(clean_sender):
                        matched = True
                        break
                    # Strip leading zeros for national prefix match (e.g. 083... vs 2783...)
                    w_no_zero = w_clean.lstrip("0")
                    sender_no_zero = clean_sender.lstrip("0")
                    if len(w_no_zero) >= 7 and len(sender_no_zero) >= 7:
                        if sender_no_zero.endswith(w_no_zero) or w_no_zero.endswith(sender_no_zero):
                            matched = True
                            break

            # 2. Name / String comparison
            w_lower = w_item_str.lower()
            if sender_name and (sender_name.lower() == w_lower or w_lower in sender_name.lower()):
                matched = True
                break
            if contact:
                c_name = contact.get("name", "").lower()
                c_rel = contact.get("relationship", "").lower()
                if (c_name and (c_name == w_lower or w_lower in c_name)) or (c_rel and (c_rel == w_lower or w_lower in c_rel)):
                    matched = True
                    break

        if not matched:
            logging.debug(
                f"PulseEngine: WhatsApp message from sender_id='{sender_id}' (name='{sender_name}') not in whitelist.")
            return

        cooldown = sys_settings.get("ratelimit-minutes", 5)
        if (time.time() - self.last_pulse_time) < (cooldown * 60):
            logging.info(
                "PulseEngine: WhatsApp pulse suppressed due to rate limiting.")
            return

        buffer_seconds = sys_settings.get("buffer-seconds", 30)
        self.pending_whatsapp_sender = sender_name or f"+{clean_sender}"

        if self.whatsapp_timer:
            self.whatsapp_timer.cancel()

        self.whatsapp_timer = threading.Timer(
            buffer_seconds, self.execute_whatsapp_pulse, args=[sys_settings])
        self.whatsapp_timer.start()
        logging.debug(
            f"PulseEngine: WhatsApp message from {self.pending_whatsapp_sender} buffered for {buffer_seconds}s.")

    def execute_whatsapp_pulse(self, sys_settings):
        from core.logger_config import agent_id_var
        agent_id_var.set(self.agent_id)
        self.last_pulse_time = time.time()

        prompt = "[AGENT_PULSE] Check your unread WhatsApp messages now."
        self.trigger_pulse.emit(prompt)
