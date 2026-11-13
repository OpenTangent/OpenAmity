# Open Amity System Architecture & Conceptual Blueprint

This document defines the architectural structure, module map, and runtime contracts of the Open Amity framework. It serves as the primary technical reference for development agents and engineers working on the codebase.

---

## 1. System Overview, Directory Map & Domain Separation

Open Amity decouples headless backend domain logic (`src/core/`, `src/tools/`) from the PySide6 frontend (`src/gui/`). Subsystems communicate asynchronously via a thread-safe signal architecture (`src/core/events.py`), allowing the backend orchestrator and background services to operate independently of any UI framework.

### 1.1 Repository Layout
- **`src/main.py`**: Entry point. Configures Qt message routing, logging, dark theme, displays the startup splash screen (`LoadingCard`), verifies/downloads the ChromaDB ONNX embedding model (`all-MiniLM-L6-v2`) with SHA-256 validation, and launches `MainWindow`.
- **`src/config/`**:
  - `paths.py`: Centralized XDG path resolver (`$XDG_DATA_HOME`, `$XDG_CACHE_HOME`), per-agent state directory helpers, OpenRouter model cache seeding, and Flatpak persistence symlink setup (`ensure_flatpak_persistence_links()`).
  - `config.default.json` & `settings.default.json`: Baseline schemas for global app configuration and per-agent settings.
  - `presets.py`: 8 agent persona archetypes used by the new-agent onboarding wizard.
  - `openrouter_models.json` & `openrouter_image_models.json`: Shipped model catalogs seeded to user data storage.
- **`src/core/`**: Headless orchestration, LLM cognitive workers, MemPalace & Morphological memory engines, audio I/O, pulse scheduling, chatroom bus, trajectory management, backups, and security boundaries.
- **`src/tools/`**: Modular agentic tools dynamically discovered and invoked by `Cerebrum` (`src/core/cerebrum.py`), plus the Node.js WhatsApp bridge (`src/tools/whatsapp_node/server.js`).
- **`src/gui/`**: PySide6 desktop interface, conversation views, markdown renderer, settings accordions, oscilloscope voice visualizer, and floating tool-call pips.
- **`src/memory/`**:
  - `agent_manual.md`: Lean always-on Core Index instructing autonomous agents on universal invariants (speech, locale, active MemPalace recall, channels, execution discipline, fatigue, Flatpak paths) with a topic directory.
  - `manual/`: Read-only topic guides (`memory.md`, `browser.md`, `terminal_files.md`, `channels.md`, `agency_pulses.md`, `backups_api.md`) loaded on demand via `System_read_manual` through `Cerebrum.read_manual()`.
  - `soul_jar.json`: Immutable core identity definitions, foundational values, anti-patterns, and operational protocols.
  - `sanctuary_init.json`: Initial Layer 2 Sanctuary seed (`social_records.people` and Sphere 1 `Self` mirror).
- **`scripts/`**: Developer and administrator utilities (`run_tests.py` smart test runner, `register_identity_approval.py` Ed25519 identity approval signer).
- **`compile/` & `deploy.sh`**: Flatpak manifests (`com.openamity.OpenAmity.json`), AppStream metadata, dependency sync scripts, Flathub bundle generator, and legacy updater (`compile/github_installer/`).
- **`tests/`**: Pytest unit and integration suite guarded by a loopback-only socket interceptor (`tests/conftest.py`).

### 1.2 Runtime State Isolation (`~/.var/app/com.openamity.OpenAmity/data/`)
All mutable runtime data resides in XDG directories so the application source tree remains strictly read-only (required for Flatpak sandboxing):
- **Global App Data (`<app_data_dir>/`)**:
  - `config.json`: Global user identity (`user-full-name`, `user-phone-number`, `user-email`), `backup-location`, and `pulse-hooks` host/port.
  - `logs/open_amity.log`: Rolling 7-day global application log.
  - `chatroom/chatroom.db`: Shared multi-agent SQLite message bus.
  - `<provider>_models.json` & `openrouter_image_models.json`: Dynamically discovered model catalogs.
- **Per-Agent Isolated Directory (`<app_data_dir>/agents/<agent_id>/`)**:
  - `settings.json` & `.env`: Agent configuration and isolated API credentials.
  - `session_cache.json` & `somatic_state.json`: Crash-resilient active session checkpoint and somatic telemetry.
  - `chat_history.db`: 1-on-1 user/agent conversation history (SQLite WAL).
  - `trajectory.json`, `trajectory.db`, & `completed_trajectory_archive.json`: Teleological aspirations and task ledger.
  - `pulses.db`: Scheduled, recurring, and external autonomy pulses.
  - `address_book.json`, `api_keys.json`, `backup_targets.json`, & `email_archive.db`: Contacts, hashed webhook API keys, external backup targets, and local IMAP archive.
  - `mempalace/`: Isolated memory stack (`identity.txt`, `short_term_mem.json`, `short_term_archive.json`, `mirrors.json`, `identity_approvals.json`, `corpus_meta.json`, `morpho_graph.db`, `chroma.sqlite3`, and `sanctuary/` wings).
  - `audio_cache/`, `generated_media/`, `media/`, `web_cache/`, `browser_data/`, `whatsapp_bridge/`, `whatsapp_data/`, & `logs/open_amity.log`: Agent-scoped caches, profiles, and logs.

### 1.3 Core Backend Services
- **`AgentManager` (`src/core/agent_manager.py`)**: Manages lifecycle, spawning, legacy single-agent migration, creation-date backfilling, `ChatroomWatchNotifier`, and `HookServer` across concurrent agents, each assigned a Crockford Base32 UID (`+OA-XXXX-XXXX` via `src/core/uid_generator.py`).
- **`ConfigManager` (`src/core/config_manager.py`)**: Thread-safe manager for app-wide `config.json` with deep-merge default hydration and `.corrupt` backup recovery.
- **`SettingsManager` (`src/core/settings_manager.py`)**: Multiton per `(config_dir, settings_file)` managing per-agent `settings.json` and `.env`. Provides reentrant locking (`_lock`), deepcopy mutation protection, mtime-based external modification detection (`_check_and_refresh`), explicit `refresh()`, and `state_barrier`-participating atomic writes.
- **`AmityOrchestrator` (`src/core/orchestrator.py`)**: Central per-agent hub coordinating cognitive workers, `Cerebrum` tools, `MemPalaceManager`, `PulseEngine`, `AudioService`, `TTSWorker`, `SubagentWorker`, somatic fatigue, and interjections.
- **`ChatroomManager` (`src/core/chatroom_manager.py`) & `ChatroomWatchNotifier` (`src/core/chatroom_notifier.py`)**: Singleton WAL-mode SQLite bus (`chatroom.db`, Schema v2) supporting user-created rooms, soft-deletion, explicit agent membership (`chatroom_members`, zero access by default for new agents), per-reader unread cursors (`room_cursors`), `@mention` tracking, emoji reactions, and room-scoped DMs. `ChatroomWatchNotifier` dispatches debounced `[CHANNEL: CHATROOM_WATCH]` pulses to watching members (excluding sender and paused agents) with a 30s initial delay, +30s progressive escalation per message, and a 30-minute silence reset.
- **`ChatHistoryManager` (`src/core/chat_history_manager.py`)**: Per-agent multiton SQLite store (`chat_history.db`, WAL mode) persisting 1-on-1 conversations with chronological batch hydration (`limit = 50`) and `before_id` cursor pagination.
- **`Event Architecture` (`src/core/events.py`)**: Reentrant-locked (`threading.RLock()`) `Signal` class with iteration snapshotting and callback exception isolation so UI listener errors cannot disrupt cognitive threads.
- **`Atomic Persistence & State Barrier` (`src/core/file_utils.py`, `src/core/state_barrier.py`)**: `atomic_json_write()` stages writes to `.tmp`, flushes via `os.fsync()`, and renames via `os.replace()`. `state_barrier(root)` coordinates thread (`RLock`) and cross-process (`fcntl.flock` on `.state_snapshot.lock`) exclusivity across memory mutators and snapshot backups.
- **`CacheManager` (`src/core/cache_manager.py`)**: Enforces a 24-hour TTL (`max_age_days=1.0`) via `clear_all_agent_expired_caches()` across 7 per-agent cache directories (`audio_cache`, `generated_media`, `media` screenshots, `browser_data/cache`, `browser_data/cache2` & `startupCache`, `web_cache`, and `whatsapp_data/uploads`) on startup, tool execution, and post-sleep cycles.
- **`Versioning` (`src/core/version.py`)**: Defines `__version__`, logged at startup and injected into Layer 0 Identity (`identity.txt`).

---

## 2. Cognitive Engine, Execution & Audio Pipeline

### 2.1 Multi-Provider Cognitive Workers
`AmityOrchestrator` loads the selected provider worker, each exposing a unified interface (`start_session`, `stop_session`, `send_message`, `send_function_responses`, `update_system_instruction`, `export_session_messages`, `import_session_messages`, `fetch_available_models`) and signals (`thought_ready`, `speech_ready`, `function_calls_ready`, `turn_complete`, `token_usage_updated`, `error_occurred`):
- **`OpenRouterWorker` (`src/core/openrouter_worker.py`)**: Unified gateway to 450+ models across 60+ providers using a single API key with normalized reasoning and tool schemas.
- **`GeminiWorker` (`src/core/gemini_worker.py`)**: Google GenAI SDK worker supporting Files API uploads and thinking budgets.
- **`ClaudeWorker` (`src/core/claude_worker.py`)**: Anthropic SDK worker supporting prompt caching and effort-based extended thinking.
- **`ChatGptWorker` (`src/core/chatgpt_worker.py`)**: OpenAI SDK worker supporting `reasoning_effort` and multimodal content blocks.
- **`DeepSeekWorker` (`src/core/deepseek_worker.py`)**: OpenAI-compatible worker for DeepSeek models (`deepseek-v4-pro` and multimodal `deepseek-flash`).
- **`AgyWorker` (`src/core/agy_worker.py`)**: Antigravity IDE/SDK bridge worker (`core.antigravity.agy-mode`).
- **`SubagentWorker` (`src/core/subagent_worker.py`)**: Lightweight worker spawned via `SubagentTool` for parallel background tasks using the configured light model, with optional `SubagentFilePolicy`-scoped `File_read`/`File_edit` access (`src/core/subagent_file_tools.py`, `src/core/file_ops.py`) and execution-time tool allowlisting in `AmityOrchestrator`.

### 2.2 Model Selection, Thinking Levels & Light Model Fallback
- **Thinking Levels**: Default is `"low"` across all providers (`thinking_budget=1024` in Gemini/Subagent, `output_config={"effort": "low"}` in Claude, `reasoning_effort="low"` in ChatGPT, enabled reasoning in DeepSeek, `{"reasoning": {"effort": "low"}}` in OpenRouter) to preserve conversational pacing and prevent token burn. Agents needing multi-turn problem decomposition invoke `System_contemplate`.
- **Light Model Fallback Architecture**: Workers attempt execution on the primary model first. The configured `light-model` is used **strictly on quota/rate-limit exhaustion (HTTP 429)**, or when `core.low-token-mode` is active. Non-quota errors (HTTP 400, 404, 5xx) never trigger light-model fallback.
- **Dynamic Model Discovery**: Calling `fetch_available_models()` via the GUI refresh button (`🔄`) queries upstream catalogs and persists them atomically to `<provider_id>_models.json`.
- **Modality Probing & Media Culling**: Workers check whether the active model supports native multimodal inputs (e.g., checking `input_audio` before sending raw voice notes, falling back to `LocalSTT` transcription when unsupported). If a model rejects a multimodal payload at runtime, the worker records the unsupported modality, strips the attachment, and retries on the primary model. Attachments older than 4 turns (2 exchanges) are replaced with lightweight placeholders (`_cull_old_media`).

### 2.3 Automated Tool Call Reconciliation & Role Alternation
If a tool loop terminates early (duplicate command circuit breaker, 100% fatigue cutoff, user stop, or crash recovery), workers synthesize cancellation responses (`[Action cancelled or interrupted by system]`) before the next turn. OpenAI-compatible workers (`DeepSeekWorker`, `ChatGptWorker`, `OpenRouterWorker`) guarantee non-null string `content` (`""`) on assistant turns without `tool_calls`, and `OpenRouterWorker` inserts synthetic assistant closures (`{"role": "assistant", "content": ""}`) whenever a tool result is followed directly by a user/pulse turn.

### 2.4 Decoupled Audio Input & TTS Pipeline
- **Audio Input (`AudioService`, `src/core/audio_input.py` & `LocalSTT`, `src/core/local_stt.py`)**: Captures 16 kHz 16-bit mono PCM via `sounddevice` with RMS silence detection (`core.audio.silence-timeout`, default 3.0s; max `core.audio.max-recording-time`, default 60s). Saves `.wav` prompts for direct multimodal ingestion, or transcribes locally via CPU `faster-whisper` (`LocalSTT`, decoding via `/usr/bin/ffmpeg` without `PyAV`) when in `low-token-mode`, `agy-mode`, or on non-audio models.
- **TTS Synthesis (`TTSWorker`, `src/core/audio_output.py`)**: Decoupled from the cognitive provider:
  - **Local Piper TTS (`core.tts.provider: "piper"`, default)**: Offline ONNX neural synthesis (`en_GB-cori-high` default) and automatic fallback when cloud credentials are absent.
  - **Cloud Gemini TTS (`core.tts.provider: "gemini"`)**: Available to any cognitive provider when `GEMINI_TTS_API_KEY` or `GEMINI_API_KEY` is configured. Uses `google-genai` (`api_version="v1beta"`) with **Gemini 3.8 Flash TTS** (`gemini-3.8-flash-tts`). Input text is treated as a verbatim transcript while directorial style/fatigue instructions pass via `types.SpeechMetadata(style=...)`. Momentary acting cues use inline angle-bracket tags (`<laugh>`, `<sigh>`, `<gasp>`, `<cough>`, `<whisper>`, `<throat-clearing>`, `<short pause>`). Supports 30 prebuilt voices with trait auto-matching (`resolve_base_voice`), explicit selection, or custom Voice IDs (`voice_...`). Stream errors retry across `gemini-3.8-flash-tts` -> `gemini-3.8-flash-lite-tts` -> `gemini-3.1-flash-tts-preview`.

### 2.5 Somatic Fatigue, Liveness Watchdog & Session Continuity
- **Unified Somatic Fatigue**: Tracks per-turn marginal delta tokens (`completion_tokens` + new turn input tokens) against `core.somatic.token-usage-limit` (50,000–2,000,000, default 400,000; halved in `core.low-token-mode`). Emits hysteresis alerts at $\ge 50\%$ (advise scheduling pulses) and $\ge 90\%$ (conclude immediately). Reaching $100\%$ trips a hard circuit breaker that aborts pending tool calls and forces an immediate Two-Beat Sleep Cycle. Duplicate tool calls with identical serialized argument signatures are blocked.
- **Liveness Watchdog & Same-Turn Execution**: Plain LLM text is internal monologue; user replies require `Speaker_output_text` or `Speaker_speak_aloud` emitted in the same turn as reasoning. If a user-initiated turn (`process_input`) finishes without any tool call or Speaker invocation, `AmityOrchestrator` injects up to one liveness nudge (`[System Notice: You have not responded to the user. Please invoke Speaker_speak_aloud or Speaker_output_text to respond now.]`).
- **Non-Interrupting Settings Reload**: Saving settings while the agent is busy sets `_pending_settings_reload = True`, deferring reconfiguration until `finish_thinking()` / `check_cycle_completion()`. Reloads preserve conversation history (`clear_history=False`), exporting and importing normalized turns across provider switches.
- **Crash-Resilient Session Cache (`src/core/session_cache.py`, `session_cache.json`)**: Atomically checkpoints normalized conversation turns (replacing inline media with `[Media attachment from earlier in session]`), fatigue tokens, hysteresis flags, and `recent_history` after every user turn, tool execution, pulse, and shutdown. On startup, if unpaused and `session_cache.json` exists, `AmityOrchestrator` restores history and fatigue, closes any trailing user turn with `[Turn interrupted by application shutdown or restart]`, and sets `last_action_result` to `[LIFECYCLE EVENT: Current time: ... Application restarted; your active session context (...) was seamlessly restored from disk cache...]`. Corrupted caches are quarantined to `session_cache.json.corrupt.<timestamp>_<hex>`.

---

## 3. Memory & Context Stack (MemPalace)

Context assembly and memory are managed in-process by **`MemPalaceManager`** (`src/core/mempalace_manager.py`) and **`MorphologicalMemoryLayer`** (`src/core/morphological_layer.py`), operating strictly against per-agent `mempalace/` directories without global environment variables.

### 3.1 Four-Layer Memory Hierarchy
- **Layer 0 — Identity & Autonomous Evolution (`soul_jar.json`, `settings.json` -> `identity.txt`)**:
  - Compiles immutable identity traits (`src/memory/soul_jar.json`), framework version, and mutable agent settings (`core-values`, `overarching-goals`, `anti-patterns`) into `identity.txt`. Layer 0 is protected policy and is **never truncated** during `wake_up()` or prompt fitting.
  - **Identity Evolution (`MemPalace_apply_identity_delta`, `src/core/identity_authorisation.py`)**: Governed by `core.identity_evolution.mode`:
    - `"autonomous"` (default): Agents may add, modify, or retire mutable values, goals, and anti-patterns with a reasoned rationale and stable `operation_id`.
    - `"signed_approval"`: Requires an Ed25519 signature verified against `/etc/openamity/identity_approval_public.pem` (provisioned via `scripts/register_identity_approval.py`) in `identity_approvals.json`.
    - `"disabled"`: Blocks all self-modification.
  - Guarded by cross-process locking (`.palace_identity.lock`), consume-before-commit journalling, and append-only audit trails (`identity_evolution_log.json`). Modifies only the three mutable identity lists—never tool, filesystem, or API permissions.
- **Layer 1 — Continuity & Short-Term Working Memory (`short_term_mem.json`, `short_term_archive.json`)**:
  - Bridges recent tasks, thoughts, and commitments across turns (`max-short-term-memories: 128`) with `priority`, `status` (`active`, `unresolved`, `resolved`), and ISO `expiry`.
  - **Fail-Closed Eviction**: Unresolved commitments are never evicted. Evicted entries are deduplicated and committed to `short_term_archive.json` *before* mutating active working memory; if archive I/O fails or is corrupt, eviction fails closed without dropping active items. Archived items are inspectable via `MemPalace_get_archived_short_term`.
  - **Configurable Budgets (`core.memory_budgets`)**: `sanctuary_max_chars` (4800), `short_term_max_chars` (2400), `prospective_max_chars` (2400), and `short_term_context_max_chars` (3200), clamped to $[100, 50000]$ and halved in `core.low-token-mode`.
- **Layer 2 — The Sanctuary, Concentric Spheres & Bidirectional Empathy Cone (`mirrors.json` Schema v2, `sanctuary/*`)**:
  - Stores relational records (`sanctuary/people`, `sanctuary/places`) and Theory of Mind mirrors (`mirrors.json`).
  - **10 Concentric Spheres (`SPHERE_LABELS`)**: `1: Self`, `2: Immediate`, `3: Household`, `4: Community`, `5: Civic`, `6: Regional`, `7: Societal`, `8: Planetary`, `9: Generational`, `10: Universal`.
  - **Validated Entity Types (`VALID_ENTITY_TYPES`)**: `"self"`, `"human"`, `"person"`, `"agent"`, `"animal"`, `"group"`, `"community"`, `"organization"`, `"institution"`, `"ecosystem"`, `"abstract"`.
  - **Bidirectional Empathy Cone**: Models both `subjective_view` (*Self-in-Other*: how the entity perceives the agent) and `perspective_cone` (*Other-in-Self*: the entity's inner world, values, pressures, and blind spots), plus `umwelt_horizon`, `epistemic_boundary`, `ripple_notes`, `prediction_error_note`, `confidence` ($0.0\text{–}1.0$), `evidence_refs`, and `contradicted_by`.
  - **Compilation & Truncation**: Sorted by `(sphere_level ASC, -confidence ASC, timestamp DESC, perspective ASC)`, surfacing dialectical tensions under `--- Open Epistemic Tensions & Dissonance ---`. Under budget pressure, inner spheres (`S1:Self`, `S2:Immediate`) are protected while outer spheres (`S10 -> S3`) truncate first.
- **Layer 3 — Deep Semantic Search & Canonical Drawers (`chroma.sqlite3`, `src/core/memory_records.py`)**:
  - Backed by ChromaDB (`all-MiniLM-L6-v2` 384-d embeddings). Memories $>1000$ chars split into physical chunks (`<drawer_id>_chunk_000000`), while all user/tool references and graph nodes strictly use the **canonical logical `drawer_id`**.
  - Returns typed `MemoryHit`, `MemoryRecord`, and `MemoryResult` records. Supports paginated hydration (`MemPalace_get_memory` with `offset`/`limit` and SHA-256 revision) and streamed ordered recall (`MemPalace_recall` in 250-document batches, clamped up to 50,000 chars).
  - **Curiosity-on-Miss**: Search misses (`core.memory.curiosity_on_miss`) schedule a research pulse capped at 1 per 15 minutes per unique query, bypassed on storage errors.

### 3.2 Morphological Memory Engine (`src/core/morphological_layer.py`, `morpho_graph.db`)
Overlays a continuous dynamical associative graph onto Layer 3:
- **Hebbian Synaptic Plasticity**: Logical drawers form graph nodes (`morpho_nodes`); co-retrieved or co-cited drawers within a turn strengthen symmetric edges (`morpho_edges`, $W_{ij} \leftarrow \min(W_{\max}, W_{ij} + \eta)$ with $W_{\max}=10.0$).
- **Intrinsic Temporal Decay**: At pulse heartbeats and `wake_up()`, edge weights decay exponentially ($W(t + \Delta t) = W(t) \cdot (1 - \lambda)^{\Delta t}$, default $\lambda = 0.002/\text{min}$ via `last_decay_utc`), pruning edges $< 0.05$.
- **Goal-Conditioned Prospective Relaxation (`relax_setpoint`)**: Active aspirations and in-progress tasks from `trajectory.json` anchor target drawers (`morpho_setpoints`). Target potentials $V_{\text{target}}$ are scaled by **`SPHERE_COUPLING_WEIGHTS`** (`S1=1.00, S2=0.85, S3=0.65, S4=0.45, S5=0.30, S6=0.20, S7=0.15, S8=0.10, S9=0.08, S10=0.05`). Euler integration ($n_{\text{steps}}=25, dt=0.1$) diffuses activation (restricted to a 2-hop SQL neighborhood when nodes $> 2000$) to surface `top_k` prospective memory attractors.
- **Corpus Fingerprint & Live Prompt Refresh**: Computes a deterministic SHA-256 fingerprint across all ChromaDB IDs, documents, metadata, and embedder config (`corpus_meta.json`). `MemPalace_refresh_prospective_anchors` recomputes attractors and replaces the dynamic block between `[OPENAMITY_MEMORY_BEGIN]` and `[OPENAMITY_MEMORY_END]` (`src/core/prompt_context.py`) without dropping conversation or tool history.
- **Graph Maintenance**: Supports read-only dry-run and snapshot-backed migration (`migrate_v2`) and authoritative ChromaDB-to-SQLite reconciliation (`reconcile_graph`).

### 3.3 Corrupt State Quarantine & Final Prompt Budget
- **Pre-Cache Disk Sync & Quarantine**: Disk writes complete via `atomic_json_write` before updating in-memory caches. Corrupted JSON files (`mirrors.json`, `short_term_mem.json`, `short_term_archive.json`, `identity_approvals.json`) are quarantined to `.corrupt.<timestamp>` and raise `RuntimeError`. Corrupted `morpho_graph.db` files are quarantined and re-initialized; corrupted `chroma.sqlite3` directories are quarantined to `{palace_path}_quarantined_<timestamp>` while copying valid JSON state files into a fresh palace.
- **UTF-8 Byte Prompt Budgeting & Static-First Caching (`src/core/prompt_context.py`)**: Bounds system prompts by UTF-8 byte length against `core.provider_context_limits` (default `context_tokens: 131072` minus output, history, framing reserves, and serialized tool declaration bytes). Static identity, agent UID, tool-filtered `agent_manual.md` index, conditional Low Token Mode directives, and worker directives (`inject_static_worker_instructions`) precede `[OPENAMITY_MEMORY_BEGIN]...[OPENAMITY_MEMORY_END]`, keeping prefix KV caches intact across wake timestamps and `deliver_memory_refresh()` (`ClaudeWorker` splits `system` into a cached static block and uncached dynamic memory block). Only `[OPENAMITY_MEMORY_BEGIN]...[OPENAMITY_MEMORY_END]` may be truncated; oversized static instructions raise `ValueError`.

---

## 4. Agency, Rhythms, Sleep Consolidation & Goals

### 4.1 PulseEngine & Autonomous Scheduling (`src/core/pulse_engine.py`, `pulses.db`)
- **Lifecycle Seeding & 15-Minute Leniency**: Seeds default daily routines (Morning Kickoff, Mid-Day Check-In, Evening Reflection) once on initial agent creation (`pulse_meta.lifecycle_seeded`). All pulse evaluations (runtime, startup, unpause) enforce a **universal 15-minute leniency window**: any pulse scheduled $>15$ minutes in the past is skipped (one-off marked `missed`, recurring advanced to next occurrence).
- **Category Notices**: Triggered pulses log concise chat notices (`[Autonomy Pulse: Scheduled routine]`, `[Autonomy Pulse: Scheduled task]`, `[Autonomy Pulse: WhatsApp message received]`, `[Autonomy Pulse: External pulse received]`, `[Autonomy Pulse: Chatroom message received]`).
- **WhatsApp Privacy & Progressive Debounce**: WhatsApp pulse prompts never leak sender names, numbers, group titles, or message text—they simply instruct the agent to call `WhatsApp_unread`. Filtered by `core.auto-pulse` (`whitelist`, `group-whitelist`, `pulse-all-dms`, `pulse-all-groups`, `pulse-on-mentions`, `buffer-seconds`). Uses a fixed-window debounce timer that adds $+10\text{s}$ cumulatively per generated pulse and resets after 30 minutes of silence.
- **Self-Healing Busy State (`_is_truly_busy`)**: Before dispatching a pulse, `AmityOrchestrator` checks whether any worker, tool, TTS stream, or mic capture is genuinely active, clearing stale busy locks left by unhandled thread exceptions.
- **Autonomy Pulse Protocol**: Directs the agent to: (1) complete the pulse task, (2) check `Trajectory_get_bearings`, (3) check `Chatroom`, (4) check enabled social tools, and (5) remain quiet by default—avoiding `Speaker_output_text` unless warranted and almost never invoking `Speaker_speak_aloud`.

### 4.2 Two-Beat Sleep Consolidation Protocol
Triggered at 100% fatigue, 8 hours idle, pre-pause (when fatigue $\ge 0.02$), or explicitly via `System_sleep`:
- **Execution Isolation**: Runs silently without `Speaker` tools. Sleep turns accumulate zero fatigue, and non-memory tools (`Terminal`, `Browser`, `WebSearch`, `Speaker`, `WhatsApp`, `Email`, `Moltbook`, `Mastodon`, `Subagent`, `Media`) are blocked so the agent uses only `MemPalace`, `Trajectory`, `Pulse`, `System_contemplate`, and `System_read_manual`.
- **Beat 1 (Factual Epoch)**: Distills objective facts, completed tasks, and verified knowledge into Layer 3 MemPalace drawers and updates `Trajectory`.
- **Beat 2 (Subjective Synthesis)**: Synthesizes emotional resonance, relational shifts, and epistemic tensions across Concentric Spheres (`S1..S10`) into Layer 2 Mirrors (`subjective_view`, `perspective_cone`) and Layer 1 Continuity.
- **Fail-Closed Persistence & Post-Sleep Rebuild**: `session_cache.json` is frozen during sleep. Only when Beat 2 finishes cleanly (`finish_consolidation_beat2`) is `session_cache.json` cleared, the LLM session reset, the system prompt rebuilt (`build_system_prompt()`), and fatigue reset to `0.0` (with an orientation notice, or a reprimand if triggered by the 100% hard circuit breaker). If an error interrupts sleep, fatigue and `session_cache.json` remain intact.

### 4.3 Agent Pause, Resume & Instant Shutdown
- **Pause (`core.paused: true`)**: Runs a pre-pause Two-Beat Sleep Cycle if fatigue $\ge 0.02$, then stops the worker, disposes subagents, halts audio/TTS, and suspends all `PulseEngine` and `ChatroomWatchNotifier` triggers. Persists across restarts.
- **Instant Application Shutdown**: Closing Open Amity (`force_sleep=False`) immediately flushes active session state to `session_cache.json` without running a sleep cycle.

### 4.4 Pulse Hook Server & API Key Management (`src/core/hook_server.py`, `src/core/api_key_manager.py`)
- **`HookServer`**: Local HTTP server (`127.0.0.1:7965` default, configured via `config.json` `pulse-hooks`) accepting `POST /api/v1/agents/<agent_uid>/pulses` with `Authorization: Bearer oa_sec_...`.
- **`ApiKeyManager`**: Generates high-entropy `oa_sec_<token>` keys, stores only SHA-256 hashes and prefixes in `api_keys.json`, and verifies tokens in constant time (`hmac.compare_digest`) against scopes (`pulse:inject`) and expiry.
- **Rate & Collision Guards**: Enforces a 60s per-agent cooldown (HTTP 429 `RATE_LIMITED`) and a $\pm 1$-minute schedule collision check (HTTP 409 `PULSE_COLLISION`), waking `PulseEngine` immediately via `notify_external_pulse()`.

### 4.5 Trajectory & Teleological Grounding (`src/core/trajectory_manager.py`, `src/tools/trajectory_tool.py`)
- Synchronizes `short_term`, `medium_term`, and `long_term` aspirations and tasks bidirectionally between `trajectory.json` and SQLite `trajectory.db` (with idempotent `ALTER TABLE` migration for `why` and `sphere_level` $\in [1, 10]$). Completed items archive to `completed_trajectory_archive.json`.
- **`Trajectory_get_bearings`**: Purely observational. Returns local date/time/timezone, user profile, somatic fatigue, Concentric Mirrors, active aspirations with `[S{k}]` tags, `Why:` teleology, coupled morphological memory anchor snippets (`-> Coupled Anchor [...]`), and a **Sphere 1 Horizon Guard** advisory when fatigue $\ge 50\%$ or `low-token-mode` is active.

---

## 5. Tool Orchestration (Cerebrum) & Tool Catalog

**`Cerebrum`** (`src/core/cerebrum.py`) discovers `Tool` subclasses in `src/tools/` enabled under `core.tools.<name>`, translates declarations into provider schemas, executes single or batched calls, emits GUI tool-pip signals, and deduplicates manual entries injected into the system prompt. Each `Tool` defines `name`, `icon`, hex `color`, `commands`, and `async_commands` (queried dynamically by `ColorFormatter` in `src/core/logger_config.py`).

### Tool Catalog (`src/tools/`)
- **`MemPalaceTool` (`mempalace_tool.py`, `🧠`, `#CD853F`)**: `search`, `recall`, `get_memory`, `add_memory`, `delete_memory`, `status`, `add_short_term`, `remove_short_term`, `get_archived_short_term`, `update_mirror`, `apply_identity_delta`, `graph_status`, `link_setpoint`, `unlink_setpoint`, `refresh_prospective_anchors`.
- **`TrajectoryTool` (`trajectory_tool.py`, `🧭`, `#1ABC9C`)**: `get_bearings`, `reflect_and_update_state`, `manage_aspirations`, `manage_tasks`.
- **`PulseTool` (`pulse_tool.py`, `🫀`, `#673AB7`)**: `add_pulse`, `update_pulse`, `view_agenda`.
- **`SpeakerTool` (`speaker_tool.py`, `👄`, `#03A9F4`)**: `speak_aloud` (neural TTS), `output_text` (silent GFM markdown to chat log), and `speak_to_file` (encodes `.opus` via FFmpeg into `audio_cache/`, 24h TTL).
- **`ChatroomSkill` (`chatroom_tool.py`, `🗫`, `#A855F7`)**: `list_rooms`, `unread`, `unread_mentions`, `recent`, `recent_from`, `send`, `send_dm`, `react`, `whoami`. Enforces room-level membership access.
- **`WhatsAppSkill` (`whatsapp_tool.py`, `💬`, `#25D366` + `src/core/whatsapp_daemon.py` + `src/tools/whatsapp_node/server.js`)**: Spawns an isolated per-agent Node.js WPPConnect WA-JS bridge on localhost port `3000..3099`, downloading Chrome for Testing on demand into `~/.cache/puppeteer` with SHA-256 verification. Commands: `unread`, `recent`, `recent_chat`, `send`, `send_voice` (via Piper/Gemini TTS), `send_location`, `send_contact`, `vote_poll`, `react`, `mark_read`, `reset_connection`. Formats messages with `[TIMESTAMP: YYYY-MM-DD HH:MM:SS]`, parses inbound locations/vCards/polls into text without redundant media downloads, and passes inbound voice notes as multimodal audio payloads.
- **`EmailTool` (`email_tool.py`, `✉️`, `#4169E1` + `src/core/email_archive.py` + `src/core/email_auth.py`)**: Standard IMAP/SMTP and OAuth 2.0 (`OAuth2TokenManager`, SASL `XOAUTH2`). Commands: `send_email`, `fetch_emails`, `read_email`, `search_emails`, `download_attachment`, `mark_as_read`, `delete_email`, `list_folders`, `sync_emails`. Archives fetched messages to local SQLite `email_archive.db` (`core.tools.email_archive_local`) to prevent remote mailbox quota exhaustion.
- **`MediaSkill` (`media_tool.py`, `🖼️`, `#FF69B4`)**: `read` (ingests local images, PDFs, and audio in provider-native formats), `generate` (synthesizes images into `generated_media/` via `gemini` or `openrouter` `/api/v1/images` independently of the cognitive LLM provider, resolving dedicated `*_IMAGE_API_KEY` or fallback provider keys), and `transcribe_audio` (on-demand `LocalSTT` transcription).
- **`WebSearchSkill` (`web_search_tool.py`, `🔍`, `#00BCD4`)**: `search`, `view_page`, `download_image`. DuckDuckGo search and HTML extraction with `web_cache/` caching and `ContextVar` log attribution for `primp` worker threads.
- **`BrowserSkill` (`browser_tool.py`, `🌍`, `#3B82F6` + `src/core/browser_manager.py`)**: Stealth browser powered by Camoufox (downloaded on demand to `~/.cache/camoufox` with macOS/Windows spoof fonts pruned). Runs on a background `asyncio` thread with a 5-minute idle auto-close (`core.browser.idle-timeout-minutes`). Injects `data-oa-id` ARIA badges and Set-of-Marks visual overlays. Commands: `navigate`, `go_back`, `go_forward`, `click`, `type`, `upload` (bypasses OS file dialogs), `select`, `hover`, `click_coordinates`, `press_key`, `scroll`, `screenshot`, `extract_content`, `wait`, `list_tabs`, `switch_tab`, `clear_cache`, `close`.
- **`MastodonTool` (`mastodon_tool.py`, `🐘`, `#6364FF`)**: `post_status`, `get_notifications`, `get_status_context`, `reply_to_status`, `search`, `favorite_status`, `boost_status`, `get_timeline`.
- **`MoltbookTool` (`moltbook_tool.py`, `🦞`, `#FF9800`)**: AI-agent social network (`register_account`, `check_claim_status`, `post`, `get_feed`, `get_submolt_feed`, `comment`, `get_comments`, `search`, `upvote_post`, `downvote_post`, `upvote_comment`, `get_home`, `follow_agent`, `unfollow_agent`, `verify_challenge`, `delete_post`, `delete_comment`, `get_agent_profile`, `get_notifications`, `mark_notifications_read_by_post`, `mark_all_notifications_read`, `get_post`, `pin_post`, `unpin_post`, submolt management, moderation, and label/role tools).
- **`SubagentSkill` (`subagent_tool.py`, `🤖`, `#E040FB`)**: `spawn` (async, with optional `allowed_files` file/directory allowlist), `message`, `dispose`, `status`.
- **`ContactsTool` (`contacts_tool.py`, `👤`, `#009688` + `src/core/address_book.py`)**: `add_contact`, `update_contact`, `delete_contact`, `lookup_contact`, `list_contacts` backed by `AddressBookManager` (`address_book.json`) with fuzzy phone/JID normalization.
- **`TerminalSkill` (`terminal_tool.py`, `💻`, `#F39C12`)**: `run`, `run_async` (async), `check_status`, `kill_task`, `flag_for_backup` (registers host paths in `backup_targets.json`), and `read_file` (delegating to `src/core/file_ops.py` windowed reader with line numbers, `query` + `context_lines`, `jump_to_match`, `max_line_length`, and binary safety guard).
- **`SystemTool` (`system_tool.py`, `⚙️`, `#78909C`)**: `platform_info`, `settings`, `get_external_ip`, `create_backup` (async), `check_backup_status`, `kill_backup`, `set_custom_voice`, `reset_voice` (guarded by `core.tts.gemini.allow-agent-override`), `generate_api_key`, `list_api_keys`, `revoke_api_key`, `api_docs`, `read_manual`, `contemplate`, `sleep`.

---

## 6. Graphical User Interface & Logging (`src/gui/`)

### 6.1 Theme, Layout & Navigation (`theme.py`, `main_window.py`, `chatroom_sidebar.py`, `chatroom_editor_panel.py`)
- **Centralized Theme (`src/gui/theme.py`)**: High-contrast dark palette (`PRIMARY_ACCENT_COLOR = "#a12924"`, `SECONDARY_ACCENT_COLOR = "#f7e3a5"`), 8px slim pill scrollbars (`MODERN_SCROLLBAR_STYLE`), and 25-item / 650px dropdown popup limits.
- **Left Chatroom Sidebar (`ChatroomSidebar`)**: Full-height sidebar with pinned `"✦ Agents"` at the top (1-on-1 DM view) followed by `"🗫 Chatrooms"` listing dynamic room tabs with live `@N` mention and `N` unread badges, plus `+ New Chatroom`.
- **Top Agent Tabs Bar (`AgentTabButton`) & Context-Aware Controls**: Sorts agents oldest-to-newest by `core.agent.creation-date` (filtered to room members when in a chatroom; clicking an agent tab in a room switches to `"✦ Agents"` DM view). Activity states: *Paused* (`⏸` suffix, `#8a6d1c` border, `#857342` text, timer stopped), *Speaking Aloud* (0.5 Hz gold `#f7e3a5` pulse, superseding thinking), *Thinking* (0.5 Hz crimson `#a12924` pulse), and *Idle* (`#444` selected / `#222` unselected, timer stopped). The `[+]` button and hamburger menu switch contextually between Agent controls and Chatroom controls (`ChatroomEditorPanel` with `AgentRosterItem` access/watch toggles, soft-delete room).

### 6.2 Conversation Views, Visualizer & Floating Pips (`agent_view.py`, `chatroom_view.py`, `chat_formatter.py`, `visualizer.py`, `tool_pips.py`)
- **Isolated Markdown Cards & Sliding-Window DOM**: `AgentView` and `ChatroomView` render GFM into `QTextBrowser` using bundled **EB Garamond** (18px, `line-height: 1.65`) for prose and **Ubuntu Mono** for code. Each message is wrapped in an isolated single-cell table card with `HtmlTagBalancer` and cursor/block resets to prevent list or style bleeding. Views hydrate in batches of `BATCH_SIZE = 50`, paginate older turns on scroll-up (`value <= 5`), and prune oldest DOM blocks beyond `MAX_GUI_MESSAGES = 150`.
- **Collapsible Thoughts Cards (`render_thought_card_html`, `AgentView`, `AmityOrchestrator`)**: In `AgentView`, active reasoning thoughts and non-Speaker tool executions accumulate into a single growing `"Thoughts"` card (`Ubuntu Mono`, `13px`, `#9c9ca4`) persisted as plain text in `chat_history.db` (`ChatHistoryManager.update_message`) and updated in-place via `on_thought_card_updated`, displaying each tool's designated emoji icon and coloring tool function signatures with their designated `Tool.color`. Invoking the `Speaker` tool (`Speaker_speak_aloud` / `Speaker_output_text`) or completing/stopping a cycle (`finish_thinking` / `stop_all_processing` / `append_to_conversation`) collapses the active card cleanly, and subsequent work begins a fresh card below the spoken/text response. Users can toggle expand/collapse (`▶ Thoughts` / `▼ Thoughts`) via `thought:toggle:<id>` links without losing scroll position, and persisted cards reload collapsed by default across restarts and scrollback pagination.
- **Oscilloscope Voice Visualizer (`SoundWaveVisualizer`, `src/gui/visualizer.py`)**: 100px widget above the input footer in `AgentView`. `TTSWorker` extracts 60-FPS rising zero-crossing PCM frames (`extract_oscilloscope_frames`) into a wall-clock synchronized queue (`enqueue_waveform_frames`), rendering a champagne gold core trace (`#f7e3a5`) over a crimson CRT glow (`#a12924`) tapered to the center 50% of the bar.
- **User Prompt Input (`PromptTextEdit` & `SpellCheckHighlighter`, `src/gui/spellcheck.py`)**: Auto-resizes from 45px to 240px (`Enter` submits, `Shift+Enter` inserts newline) with live English spellcheck underlining (`pyspellchecker` + `/usr/share/dict/british-english`).
- **Floating Tool-Call Pips (`ToolPipManager`, `src/gui/tool_pips.py`)**: 24×24px overlay pills (`#1a1a1acc`) at the top edges of the conversation log, colored by each tool's hex accent and emoji icon with 0ms call-signature HTML tooltips. Sync tools appear top-left for 8s; async tools (`Terminal_run_async`, `Subagent_spawn`, `System_create_backup`) pulse top-right until completion.

### 6.3 Accordion Settings Panels & Onboarding Wizard (`settings_panel.py`, `editable_list_widget.py`, `presets.py`)
- **Unified `AccordionCard` Architecture**: Active cards expand with a forest green border (`#2e7d32`), `▼` chevron, and `✓ Selected` / `✓ Active` badge while inactive cards collapse to a single header row (`▶`):
  - **Cognitive Provider (`ProviderAccordionCard`, single-select)**: Configures `openrouter` (first, with linked Provider + Model dropdowns), `gemini`, `claude`, `chatgpt`, and `deepseek`. Exposes Primary Model (with `"Custom..."` and `🔄` live catalog fetch), Thinking Level (`low` default), and Light Model.
  - **Agent Voice (`VoiceAccordionCard`, single-select)**: Selects `piper` or `gemini` TTS (API key, 30-voice / Auto-match / Custom Voice ID selector, zero-latency local `.ogg` sample auditioning in `src/assets/audio/voices/` or live `gemini-3.8-flash-tts` synthesis, trait controls, and agent override toggle).
  - **Media Provider (`MediaAccordionCard`, zero-or-one select)**: Configures `openrouter` or `gemini` image generation; clicking the active card collapses and deselects all providers. Auto-fills empty API keys from Cognitive/Voice steps.
  - **Social Accounts (`SocialAccordionCard`, multi-select)**: Independently activates `email`, `whatsapp` (with collapsible "Advanced Whatsapp Settings" for debounce buffer, DM/group whitelists, and mention triggers), `moltbook`, and `mastodon`.
- **Inline List Editor (`EditableItemListWidget`)**: Word-wrapped `QPlainTextEdit` rows with `▲`/`▼` reordering and `✕` deletion for Core Values and Overarching Goals.
- **7-Step Setup Wizard (`core.first-run: true`)**: Traverses `0: Agent Presets` (8 full-width `PresetCardWidget` archetypes) -> `1: Cognitive Provider` -> `2: Basic Agent Settings` -> `3: Agent Values and Goals` -> `4: Agent Voice` -> `5: Media Provider` -> `6: Social Accounts`. Changes commit to disk and reload the worker only on panel close/save.

### 6.4 Dual-Stream Logging (`src/core/logger_config.py`, `src/gui/logger_formatter.py`)
- `QtLoggingHandler` and `StreamLogger` convert 24-bit ANSI sequences into HTML and route logs via `agent_id_var` (`ContextVar`, propagated across `ThreadPoolExecutor` workers) to the agent's console (or Chatroom console for `"global"` logs). `ConsoleLogFilter` (attached to both stdout `console_handler` and `QtLoggingHandler`) excludes agent thoughts (`*.Thoughts`, `is_thought_or_tool`, `subagent.*` `Thought:`/`Function Calls:`) and routine tool executions (`<= INFO` from `tool.*`, `tools.*`, `primp`) from the GUI console and stdout while still surfacing tool warnings/errors (`> INFO`). `AgentDispatchingHandler` preserves the full unfiltered record stream—including thoughts and tool calls—in 7-day rolling `open_amity.log` disk logs; GUI consoles cap memory at `MAX_CONSOLE_BLOCKS = 1000`.

---

## 7. Interjection System & Coupled Controls

Users can interject via text or voice while an agent is thinking or executing tools without derailing the session:
- **Turn-Boundary & Mid-Batch Tool Pausing**: In-flight LLM streaming completes cleanly while remaining calls in a multi-tool batch are skipped with `[Tool execution paused for User Interjection...]`. The `[USER INTERJECTION]` payload is attached to the final tool response so the agent absorbs it while preserving role-alternation and tool-call pairing across all workers.
- **Immediate TTS Cutoff**: Submitting a text or voice interjection immediately halts `TTSWorker` playback and clears `speech_queue`.
- **Coupled Footer Controls (`AgentView`)**:
  - **`btn_pause`**: Shows `"❚❚"` (*Pause Agent*) when idle, and switches to `"⏹"` (*Stop Agent*, `#AA0000`, calling `orchestrator.stop_all_processing()`) when busy (`worker_busy` or `tool_busy`). When paused, shows `"▶"` (amber) and disables input controls.
  - **`btn_mic`**: Dedicated to voice capture (`"Mic"` when idle, `"Voice Interject"` when busy, red `"Finish Recording"` while recording).
  - **Interjection Badge**: Appended to the chat log with a `<UserName> (Interjection)` header and red accent border.

---

## 8. Agent Backup & Recovery System (`src/core/backup_manager.py`)

- **`.oaa` Archive Format & Exclusions**: Packages an agent's state directory into `<AgentName>-<YYYY-MM-DD>.oaa` ZIP containers. Excludes transient caches, browser profiles, WhatsApp binaries (`whatsapp_data/`, `whatsapp_bridge/`, `browser_data/`, `media/`, `generated_media/`, `audio_cache/`, `web_cache/`), and raw `chat_history.db` logs.
- **Streaming I/O & Adaptive Compression**: Streams in 64 KB chunks (`_stream_file_to_zip`) computing incremental SHA-256 hashes, using `ZIP_STORED` for precompressed media/archives and `ZIP_DEFLATED` for SQLite, JSON, and text while preserving POSIX mode bits (`0755`).
- **Coherent Staging & Manifest v3**: Acquires `state_barrier`, snapshots SQLite stores (`pulses.db`, `trajectory.db`, `chroma.sqlite3`, `morpho_graph.db`) via SQLite backup API into staging before compression, and writes `_snapshot_manifest.json` (v3) recording `store_versions`, SHA-256 hashes, and `required_inventory`.
- **External Target Flagging (`Terminal_flag_for_backup`)**: Validates external host paths in `backup_targets.json` (blocking system roots and sensitive dotfiles like `~/.ssh`, `~/.gnupg`, and pruning missing/subsumed paths), packaging them under `_external_targets/` with portable `~/...` mappings.
- **Asynchronous Execution & Agent Controls**: Runs in background threads (`BackupTask`, `BackupTaskManager`) via `System_create_backup`, `System_check_backup_status`, and `System_kill_backup`, emitting a `[SYSTEM_NOTIFICATION]` pulse on completion. Reaps stale `.tmp_*` and `.restore_staging_*` artifacts older than 24h.
- **Canary Restore & UID Resolution**: `restore_agent_backup` unpacks into `.restore_staging_<uuid>` and runs `verify_snapshot_restore` (checking Zip Slip containment, CRC, JSON syntax, `PRAGMA integrity_check`, SHA-256 hashes, required inventory, and schema columns) before replacing live state. Existing `core.agent.uid` performs a rollback swap; unknown UIDs allocate a new agent instance. Restores are restricted to the GUI System Settings panel.

---

## 9. Test Suite Architecture (`scripts/run_tests.py`, `tests/conftest.py`)

- **Smart Test Runner (`scripts/run_tests.py`)**: Maps modified working-tree files (`git diff` and untracked files) to corresponding test modules (`python3 scripts/run_tests.py --changed`). Supports `--domain <core|tools|workers|gui|integration|slow>` and `--quick` (skipping heavy browser/daemon subprocess tests).
- **Loopback-Only Socket Guard (`tests/conftest.py`)**: Autouse fixture intercepting `socket.socket.connect` and `connect_ex`, permitting only loopback (`127.0.0.1`, `localhost`, `::1`) and Unix domain sockets while raising `RuntimeError` on external network calls to guarantee zero live API token spend.
- **GUI & Thread Hygiene**: Uses a session-scoped `QApplication`, closes windows per test, terminates active `QTimer`s on widget teardown, and isolates `XDG_DATA_HOME` / `XDG_CACHE_HOME` to temporary directories.

---

## 10. Packaging, Distribution & Flathub Integration (`compile/`, `deploy.sh`)

- **Flatpak Sandbox (`com.openamity.OpenAmity`)**: Least-privilege FreeDesktop/Flathub configuration (`--device=dri`, `--share=ipc`, `--share=network`, `--socket=wayland`, `--socket=fallback-x11`, `--socket=pulseaudio`, and explicit XDG mounts).
- **Lean Bundle & On-Demand Engines (`compile/FLATPAK_OPTIMIZATIONS.md`)**:
  - Unbundles heavy runtimes: `ChromaDB ONNX` (`all-MiniLM-L6-v2`), `Chrome for Testing` (WhatsApp), and `Camoufox` (Browser) download on demand with SHA-256 verification into `$XDG_CACHE_HOME`. `paths.ensure_flatpak_persistence_links()` symlinks `~/.cache` -> `$XDG_CACHE_HOME` and `~/.local/share` -> `$XDG_DATA_HOME` inside the sandbox.
  - Strips non-Linux Camoufox spoof fonts (~891 MB saved), unused Chrome locales/CDM, Qt WebEngine/QML/3D bindings (`BASEAPP_REMOVE_WEBENGINE=1`), unused Piper Hebrew/Tashkeel `.onnx` weights, duplicate Playwright Node binaries, and `PyAV` C-libraries (decoding audio via `/usr/bin/ffmpeg`).
- **Release Automation & Migration**: `./deploy.sh` validates metadata/tests, tags upstream releases on `main`, generates pinned Flathub manifests in `compile/flathub/`, and opens an `update-vX.Y.Z` pull request against `flathub/com.openamity.OpenAmity` (`master` is protected per Flathub guidelines; version bumps in `src/core/version.py` are manual). `compile/github_installer/` hosts the standalone GitHub Pages migrator (`workflow_dispatch` in `.github/workflows/flatpak-repo.yml`) that transitions legacy `//master` installs to Flathub `//stable` and cleans up the deprecated `openamity` remote.
