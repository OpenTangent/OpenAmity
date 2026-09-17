import time
import threading
import logging
import sqlite3
import os
import re
import calendar
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
        self._last_unpause_time = 0.0

        if hasattr(self.orchestrator, 'on_paused_state_changed'):
            try:
                self.orchestrator.on_paused_state_changed.connect(
                    lambda is_paused: self.notify_unpaused() if not is_paused else None)
            except Exception:
                pass

        self.init_db()

        # WhatsApp state
        self.last_pulse_time = 0
        self.whatsapp_timer = None
        self.pending_whatsapp_senders = set()

        # Wakeup event for instant trigger
        self._wakeup_event = threading.Event()

        # Timer for time-based checking
        self.is_running = True
        self.schedule_thread = threading.Thread(
            target=self._schedule_loop, daemon=True)
        self.schedule_thread.start()

    def notify_unpaused(self):
        self._last_unpause_time = time.time()
        self.notify_external_pulse()

    def notify_external_pulse(self):
        """Notifies the background schedule loop of an injected pulse for immediate evaluation."""
        self._wakeup_event.set()

    @property
    def pending_whatsapp_sender(self):
        if not self.pending_whatsapp_senders:
            return None
        return ", ".join(sorted(self.pending_whatsapp_senders))

    @pending_whatsapp_sender.setter
    def pending_whatsapp_sender(self, val):
        if val is None:
            self.pending_whatsapp_senders.clear()
        else:
            self.pending_whatsapp_senders = {val}

    def stop(self):
        self.is_running = False
        self._wakeup_event.set()
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
        except Exception as e:
            logging.error(f"PulseEngine: Error in check_pulses: {e}", exc_info=True)

        while self.is_running:
            self._wakeup_event.wait(timeout=60)
            self._wakeup_event.clear()
            if not self.is_running:
                return
            try:
                self.check_pulses()
            except Exception as e:
                logging.error(f"PulseEngine: Error in check_pulses: {e}", exc_info=True)

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
                    INSERT INTO pulses (title, context, scheduled_time, recurrence, status, has_run, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (title, context, sched.isoformat(), "daily", "pending", 0, now.isoformat()))

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

    def calculate_next_recurrence(self, current_sched, recurrence, now, target_day=None):
        target_day = target_day if target_day is not None else current_sched.day
        next_time = current_sched
        while next_time <= now:
            if recurrence == 'daily':
                next_time += timedelta(days=1)
            elif recurrence == 'weekly':
                next_time += timedelta(days=7)
            elif recurrence == 'monthly':
                month = next_time.month % 12 + 1
                year = next_time.year + (next_time.month // 12)
                max_days = calendar.monthrange(year, month)[1]
                d = min(target_day, max_days)
                next_time = next_time.replace(
                    year=year, month=month, day=d)
            else:
                next_time += timedelta(days=1)
        return next_time

    def check_pulses(self):
        if getattr(self.orchestrator, 'is_paused', False):
            return

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
                    context = (
                        f"You have reached context fatigue ({int(fatigue*100)}%) or long session duration. It is time for Memory Consolidation (Sleep Cycle). "
                        "Follow the Two-Beat Protocol:\n"
                        "1. Factual Epoch: Review the lived events of this session, noting milestones achieved, people engaged with, and tools used.\n"
                        "2. Subjective Synthesis: Reflect on what this day meant—how your character developed, lessons learned from errors or friction, and how your priorities or mirrors should adjust.\n"
                        "Synthesize these into your Sanctuary or Deep Search (Chroma) and update your Layer 1 short-term memory (MemPalace_add_short_term) so you wake up oriented. Keep your summary under 500 words. "
                        "Do NOT use this opportunity to perform other tasks or create new plans, as the active task context will be wiped immediately after this cycle. "
                        "You MUST perform this cycle completely silently: do NOT speak, talk, output spoken text, or invoke the Speaker tool."
                    )
                    self.fire_pulse(title, context, "sleep_cycle")
                    return  # Give sleep cycle priority

        now = datetime.now()
        conn = self.get_db_connection()
        try:
            c = conn.cursor()

            # Fetch pending pulses scheduled in the past
            c.execute('SELECT id, title, context, scheduled_time, recurrence, has_run, pulse_type FROM pulses WHERE status="pending" AND scheduled_time <= ?', (now.isoformat(),))
            pending = c.fetchall()

            for p in pending:
                p_id, title, context, sched_str, recurrence, has_run, p_type = p
                sched = datetime.fromisoformat(sched_str)

                if recurrence == 'none':
                    # Fire once-off pulse. It fires even if it was missed while offline.
                    active_type = p_type if p_type in ["external_hook", "sleep_cycle", "whatsapp"] else "scheduled_task"
                    self.fire_pulse(title, context, pulse_type=active_type)
                    c.execute(
                        'UPDATE pulses SET has_run=1, status="completed" WHERE id=?', (p_id,))
                    conn.commit()
                    break  # Process one pulse at a time to prevent cognitive overload
                else:
                    # It's a recurring pulse
                    next_sched = self.calculate_next_recurrence(
                        sched, recurrence, now, target_day=sched.day)

                    # Check if it was missed (offline or busy for more than 15 mins)
                    delta = (now - sched).total_seconds()
                    recently_unpaused = (time.time() - self._last_unpause_time) < 900
                    if delta > 900 and not recently_unpaused:  # 15 minutes grace period
                        logging.info(
                            f"PulseEngine: Skipped missed recurring pulse '{title}' (was scheduled for {sched_str})")
                        c.execute('UPDATE pulses SET scheduled_time=? WHERE id=?',
                                  (next_sched.isoformat(), p_id))
                        conn.commit()
                    else:
                        # Within grace period, fire it
                        active_type = p_type if p_type == "external_hook" else "scheduled_routine"
                        self.fire_pulse(title, context, pulse_type=active_type)
                        c.execute('UPDATE pulses SET scheduled_time=? WHERE id=?',
                                  (next_sched.isoformat(), p_id))
                        conn.commit()
                        break
        finally:
            conn.close()

    def fire_pulse(self, title, context, pulse_type=None):
        if pulse_type == "sleep_cycle" or "Sleep Cycle" in (title or ""):
            purpose = "Memory consolidation cycle"
        elif pulse_type == "whatsapp" or "WhatsApp Message" in (title or ""):
            purpose = "WhatsApp message received"
        elif pulse_type == "external_hook" or "External Pulse" in (title or "") or "External pulse" in (title or ""):
            purpose = "External pulse received"
        elif pulse_type == "scheduled_task":
            purpose = "Scheduled task"
        elif pulse_type == "scheduled_routine":
            purpose = "Scheduled routine"
        else:
            purpose = "Scheduled routine"

        if pulse_type == "sleep_cycle" or "Sleep Cycle" in title:
            prompt = (
                f"[CHANNEL: SYSTEM_CONTEMPLATION]\n"
                f"[AGENT_PULSE] Event: {title}\n"
                f"Context:\n{context}\n"
                f"[Directive]: This is a Memory Consolidation (Sleep Cycle). You MUST NOT speak aloud or invoke the Speaker tool. "
                f"Perform all memory synthesis, updates, and reflections completely silently."
            )
            self.trigger_pulse.emit(prompt, purpose=purpose)
            return

        available_social = []
        if self.orchestrator and hasattr(self.orchestrator, 'cerebrum') and hasattr(self.orchestrator.cerebrum, 'tools'):
            for tool_name in ["WhatsApp", "Moltbook", "Mastodon", "Email"]:
                if tool_name in self.orchestrator.cerebrum.tools:
                    available_social.append(tool_name)

        if available_social:
            social_instruction = f"Check your active social tools ({', '.join(available_social)}) for new messages or updates."
        else:
            social_instruction = "Check any other social tools (WhatsApp, Moltbook, etc.) if available in your tool declarations."

        prompt = (
            f"[CHANNEL: SYSTEM_SCHEDULE]\n"
            f"[AGENT_PULSE] Event: {title}\n"
            f"Context:\n{context}\n\n"
            f"[Operational Protocol]:\n"
            f"1. Perform the assigned pulse task.\n"
            f"2. Check your Trajectory (e.g. Trajectory_get_bearings) to maintain situational awareness and momentum.\n"
            f"3. Check the local chatroom for new messages or mentions (Chatroom_unread / Chatroom_unread_mentions).\n"
            f"4. {social_instruction}\n"
            f"5. Output & Voice Discretion:\n"
            f"   - Avoid unnecessary text output: You may use Speaker_output_text when necessary, but avoid unnecessary text output. Autonomous pulses run quietly by default; routine bearings checks, internal updates, and empty sweeps must complete silently without posting commentary to the user.\n"
            f"   - Avoid speaking aloud: Speaker_speak_aloud should only be used when responding to user prompts. Pulses should almost never use Speaker_speak_aloud. You are strongly dissuaded from speaking aloud during pulses as there will likely be no one in the room to hear it, though you retain autonomy to speak if you have an urgent, compelling reason."
        )
        self.trigger_pulse.emit(prompt, purpose=purpose)

    # --- WhatsApp Handling Ported from WakeUpService ---
    def handle_whatsapp_message(self, sender_id, sender_name):
        if getattr(self.orchestrator, 'is_paused', False):
            return
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
        sender_label = sender_name or f"+{clean_sender}"
        self.pending_whatsapp_senders.add(sender_label)

        if self.whatsapp_timer:
            self.whatsapp_timer.cancel()

        self.whatsapp_timer = threading.Timer(
            buffer_seconds, self.execute_whatsapp_pulse, args=[sys_settings])
        self.whatsapp_timer.start()
        logging.debug(
            f"PulseEngine: WhatsApp message from {sender_label} buffered for {buffer_seconds}s.")

    def execute_whatsapp_pulse(self, sys_settings):
        from core.logger_config import agent_id_var
        agent_id_var.set(self.agent_id)
        self.last_pulse_time = time.time()

        sender_label = ", ".join(sorted(self.pending_whatsapp_senders)) if self.pending_whatsapp_senders else "Contact"
        self.pending_whatsapp_senders.clear()
        title = f"WhatsApp Message from {sender_label}"
        context = "Check your unread WhatsApp messages now and reply if appropriate."
        self.fire_pulse(title, context, pulse_type="whatsapp")
