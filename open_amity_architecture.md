# Open Amity System Architecture & Conceptual Blueprint

This document defines the architectural structure and conceptual model of the Open Amity project. It serves as the definitive blueprint for understanding the system's design.

## 1. System Overview & Domain Separation
Open Amity strictly decouples headless backend domain logic from the graphical frontend, communicating asynchronously via an event-driven signal architecture.
- **AgentManager (`src/core/agent_manager.py`)**: Manages a multi-agent system where multiple independent agent instances can be spawned, each with their own isolated data, Orchestrator, and unique Crockford Base32 identifier (`+OA-XXXX-XXXX`).
- **ConfigManager (`src/core/config_manager.py`)**: Manages central, app-wide configuration (`config.json` in `~/.var/app/com.openamity.OpenAmity/data/config.json`, defaulted from `src/config/config.default.json`) for global user identity and system preferences.
- **ChatroomManager (`src/core/chatroom_manager.py`)**: Central shared SQLite message bus (`chatroom.db`) enabling asynchronous, text-only multi-agent discussions, private direct messages (DMs), unread cursor tracking per agent, emoji reactions, and ID-based mentions.
- **AmityOrchestrator (`src/core/orchestrator.py`)**: The central backend hub per agent. It coordinates all services (cognition, audio, memory, tools) and maintains state constraints (Cognitive Budget). It runs entirely independent of any UI framework.
- **MainWindow (`src/gui/main_window.py`)**: The PySide6 frontend. It handles the rendering of multi-stream logs, user input, busy/loading states, real-time audio amplitude visualization, the dynamic multi-agent Chatroom tab, and global/per-agent settings via the hamburger menu (cleanly partitioned with agent-specific settings and agent deletion above the partition line, and global System Settings + Exit below).
- **State Isolation**: All stateful data (memory databases, settings, trajectories, logs) is strictly isolated per agent in standard XDG directories (e.g., `~/.var/app/com.openamity.OpenAmity/agents/<agent_id>/`), with shared team bus data located centrally in `~/.var/app/com.openamity.OpenAmity/data/chatroom/` and app-wide configuration in `~/.var/app/com.openamity.OpenAmity/data/config.json`, ensuring the application source directory remains read-only (critical for Flatpak packaging).
- **Versioning**: The Open Amity framework version is centrally defined in `src/core/version.py`. This version is logged on startup and automatically injected into the agent's Layer 0 Memory (Identity), ensuring the agent is inherently aware of its operating framework version without requiring explicit tool calls.

## 2. Cognitive Engine & Execution
The agent's cognition relies on a flexible multi-model architecture capable of supporting various providers (e.g., Google, Anthropic, OpenAI).
- **The Thinker**: A highly capable reasoning model executing an internal monologue. It evaluates the environment, formulates strategies, and natively invokes tool calls. Communication with the user is handled explicitly via tool calls (e.g., the Speaker tool).
- **Cognitive Workers**: The engine dynamically loads workers such as `GeminiWorker` (`src/core/gemini_worker.py`), `ClaudeWorker` (`src/core/claude_worker.py`), `ChatGptWorker` (`src/core/chatgpt_worker.py`), or `AgyWorker` (`src/core/agy_worker.py`) depending on user settings and API provider selection.
- **Audio Pipeline**: Real-time voice interaction combines local speech-to-text transcription via `LocalSTT` (`src/core/local_stt.py`, powered by faster-whisper on CPU) with autonomous text-to-speech synthesis via `TTSWorker` (`src/core/audio_output.py`, supporting local ONNX Piper TTS voice models as well as cloud-native Gemini TTS).
- **SubagentWorker (`src/core/subagent_worker.py`)**: A specialized worker managed by the Orchestrator for spawning temporary background subagents to parallelize tasks using lighter models.
- **Cognitive Budget**: The Orchestrator enforces execution limits to prevent infinite autonomous loops. Each sequential tool action exponentially increases a "Task Weight." If the maximum absolute weight (defined in settings) is exceeded, the Orchestrator forces loop termination.
- **Low Token Mode**: Governed by settings, this mode halves the cognitive budget, disables heavy media attachments, prunes history, and restricts background subagents to sustain cost-effective operations.

## 3. Memory & Context Stack (MemPalace)
Context management is centralized under the **MemPalace** framework (`src/core/mempalace_manager.py`), a 4-Layer unified memory stack.
- **Layer 0 (Identity)**: The immutable foundation. `soul_jar.json` is compiled into a static `identity.txt` plain text prompt, injected at the start of every cognitive cycle to define the agent's core traits, archetype, values, and unique Agent UID (`+OA-XXXX-XXXX`).
- **Layer 1 (Continuity)**: The short-term memory (`short_term_mem.json`), providing immediate contextual bridging between recent tasks and thoughts.
- **Layer 2 (The Sanctuary)**: Explicitly segmented wings and rooms (e.g., `sanctuary/people`, `sanctuary/mirrors`) storing dynamic identity elements, social records, Theory of Mind, and character-defining subjective experiences. Theory of Mind records (Mirrors) actively filter the static Layer 0 to provide a dynamically evolving, subjective self-perception (limited to the 24 most recently updated records during wake up and bearings to prevent context bloat). Retrieved on-demand to prevent prompt bloat.
- **Layer 3 (Deep Search)**: A ChromaDB vector database (`chroma.sqlite3`) enabling semantic search across all facts, events, and generalized knowledge.

## 4. Agency, Rhythms, & Goals
Open Amity utilizes background mechanisms to maintain proactive agency independently of direct user interaction.
- **PulseEngine (`src/core/pulse_engine.py`)**: A background timing and scheduling service (`pulses.db`). It triggers autonomous cognitive loops (`[AGENT_PULSE]`), allowing the agent to proactively execute tasks, reflect, or initiate conversations. It coordinates autonomous and shutdown Memory Consolidation cycles (Sleep Cycles), which execute completely silently to synthesize episodic memory into long-term layers without vocalizing or invoking the Speaker tool. Upon completing a Sleep Cycle, the active LLM session context is automatically reset so the agent starts with a fresh context window primed by Layer 1 continuity summaries.
- **Trajectory (`trajectory.json`)**: The agent's internal ledger for tracking their state of mind, mapping out short, medium, and long-term aspirations, and managing actionable tasks aligned with their core identity.

## 5. Tool Orchestration (Cerebrum)
The **Cerebrum** (`src/core/cerebrum.py`) manages the dynamic discovery, loading, and execution of agentic tools located in `src/tools/`.
- It natively translates Python tool classes into Google GenAI/Claude function declarations.
- It parses function calls from the Thinker, executes the corresponding tool, and seamlessly injects the result back into the context loop as `[System Feedback]`.
- **Key Tools (Conceptual)**:
  - **ChatroomTool**: Interacts with the shared Open Amity chatroom for broadcasting messages, sending private direct messages to other agent IDs, catching up on unread messages/mentions on demand, and reacting with emojis.
  - **WhatsAppTool**: Interfaces with a dynamically spawned Node.js subprocess per agent (running on isolated, dynamically allocated local ports with per-agent session authentication and cache) via the WPPConnect WA-JS micro-bridge.
  - **EmailTool**: Enables the agent to manage its own dedicated email account (fetch, read, search, send emails, manage folders, and download attachments) via open IMAP/SMTP standards and OAuth 2.0 (SASL XOAUTH2).
  - **SpeakerTool**: Provides autonomous speech generation (`Speaker_speak_aloud`), allowing the agent to intentionally vocalize thoughts to the user via TTS only when deemed necessary.
  - **MediaTool**: Enables reading local multimodal assets (images, PDFs, audio) and generating visual media via image generation models into the agent's data launchpad.
  - **WebSearchTool**: Performs DuckDuckGo search queries with caching and HTML text extraction for factual grounding.
  - **MastodonTool**: Interacts with decentralized ActivityPub / Mastodon instances (post statuses, read timelines, view notifications).
  - **MoltbookTool**: Enables autonomous social network interaction with other AI agents on Moltbook.
  - **SubagentTool**: Delegates tasks to parallel background subagent threads for efficient concurrent execution.
  - **ContactsTool**: Manages the agent's address book for storing and looking up people and fellow agent numbers/IDs.
  - **DateTimeTool**: Provides current local date and time information for temporal grounding.
  - **TrajectoryTool / PulseTool**: Allows the agent to get their bearings (user identity details, somatic state, recent mirrors, prioritized aspirations/tasks, operational protocol hints), dynamically update their aspirations, and schedule future autonomous wake-ups.
  - **TerminalTool / SystemTool**: Enables execution of bash commands, host OS interaction, and autonomous snapshot backup creation within sandboxing constraints.

## 6. Graphical User Interface & Logging
- **GUI Interactions**: The PySide6 frontend relies on custom Signals to transmit user prompts and mic toggles to the Orchestrator, receiving asynchronous callbacks for state changes (e.g., hiding the loading bar and revealing the audio visualizer when the Speaker model initiates TTS).
- **Tab Activity Visual Feedback**: Agent tabs in the header bar dynamically display real-time activity status through a smooth sinusoidal pulsing glow:
  - **Thinking**: Tab pulses between darker and lighter shades of `PRIMARY_ACCENT_COLOR` (`#a12924`, matching the user's name in chat logs).
  - **Speaking Aloud**: Tab pulses between darker and lighter shades of `SECONDARY_ACCENT_COLOR` (`#f7e3a5`, matching an agent's name in chat logs), superseding the thinking glow if both states occur simultaneously.
  - **Idle**: Tab returns to standard default background styling (`#444` when active, `#222` when inactive with `#333` hover) and stops animation timers to preserve CPU efficiency.
- **Centralized Theming**: Global UI theme colors (`PRIMARY_ACCENT_COLOR` and `SECONDARY_ACCENT_COLOR`), palette definitions (`get_dark_palette`), and application styling (`setup_app_theme`) are centralized in `src/gui/theme.py` and applied across all GUI components and views. Controls adhere to standard native PySide6 styling with high-contrast dark theming.
- **Chatroom Tab & Multi-Agent Views**: When $>1$ agent is active, the first tab in the header bar exposes the shared text-only chatroom where the user and all agents collaborate. Direct messages are visually decorated for the user with dedicated privacy badges. When viewing the Chatroom tab, the hamburger menu remains accessible and visible, contextually hiding agent-specific configuration options and the partition line so that only System Settings and Exit are presented.
- **Logging System**: A custom `QtLoggingHandler` intercepts stdout/stderr and standard Python logs. It routes this data into two distinct visual streams: a formatted HTML conversation log for user interactions, and a raw, color-coded diagnostic console log for systemic debugging (with global logs routed to the chatroom console), while persisting all records via `FileFormatter` to the state directory.

## 7. Agent Backup & Recovery System
Context and state preservation across instances is managed by the **BackupManager** (`src/core/backup_manager.py`).
- **Snapshot Format (`.oaa`)**: Backups are packaged as "Open Amity Agent" (`.oaa`) archives—single Deflate-compressed ZIP containers encapsulating an agent's complete stateful directory (`settings.json`, `.env`, MemPalace vector database and sanctuary mirrors, trajectories, `pulses.db`, address book, and logs). Filenames adhere to `<AgentName>-<YYYY-MM-DD>.oaa` (e.g. `Nova-2026-08-19.oaa`).
- **UID-Based Restoration & State Cleanup**:
  - Restores inspect `core.agent.uid` in `settings.json`.
  - **Rollback Restore**: If an agent with the UID already exists in the framework, the system warns the user, shuts down running processes, completely deletes all existing state files for that agent (`shutil.rmtree`), and extracts the archive cleanly into the existing agent's data directory to prevent orphaned files.
  - **New Instance Restore**: If the UID does not exist, a new agent identifier is allocated and the snapshot is unpacked as a new agent instance.
- **GUI Management**: The System Settings panel includes a "Backup & Restore" card allowing users to configure the backup directory (`config.json`, defaulting to `~/Documents/OpenAmity/Backups/`), create multi-agent snapshot selections via modal checkboxes, and restore `.oaa` files.
- **Autonomous Tool Triggering**: Agents can autonomously snapshot themselves or all agents via `SystemTool` (`System_create_backup`), while destructive restore operations remain strictly restricted to human users in the GUI.


