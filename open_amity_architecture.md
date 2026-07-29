# Open Amity System Architecture & Conceptual Blueprint

This document defines the architectural structure and conceptual model of the Open Amity project. It serves as the definitive blueprint for understanding the system's design.

## 1. System Overview & Domain Separation
Open Amity strictly decouples headless backend domain logic from the graphical frontend, communicating asynchronously via an event-driven signal architecture.
- **AmityOrchestrator (`src/core/orchestrator.py`)**: The central backend hub. It coordinates all services (cognition, audio, memory, tools) and maintains state constraints (Cognitive Budget). It runs entirely independent of any UI framework.
- **MainWindow (`src/gui/main_window.py`)**: The PySide6 frontend. It handles the rendering of multi-stream logs, user input, busy/loading states, and real-time audio amplitude visualization.
- **State Isolation**: The goal is for all stateful data (memory databases, settings, trajectories, logs) to be strictly isolated in standard XDG directories (e.g., `~/.local/share/OpenAmity/`), ensuring the application source directory remains read-only (which is critical for Flatpak packaging). Note: During development, some tools like `whatsapp_node` may leak state (e.g., `.wwebjs_auth`) into the source tree if not carefully managed.
- **Versioning**: The Open Amity framework version is centrally defined in `src/core/version.py`. This version is logged on startup and automatically injected into the agent's Layer 0 Memory (Identity), ensuring the agent is inherently aware of its operating framework version without requiring explicit tool calls.

## 2. Cognitive Engine & Execution
The agent's cognition relies on a unified single-model architecture managed by `GeminiWorker` (`src/core/gemini_worker.py`).
- **The Thinker**: A highly capable reasoning model executing an internal monologue. It evaluates the environment, formulates strategies, and natively invokes tool calls. All communication with the user is handled explicitly via tool calls (e.g., the Speaker tool), enabling a fully autonomous feedback loop.
- **Cognitive Budget**: The Orchestrator enforces execution limits to prevent infinite autonomous loops. Each sequential tool action exponentially increases a "Task Weight." If the maximum absolute weight (defined in `settings.json`) is exceeded, the Orchestrator forces loop termination.
- **Low Token Mode**: Governed by `settings.json`, this mode halves the cognitive budget, disables heavy media attachments, and aggressively prunes history to sustain cost-effective operations.

## 3. Memory & Context Stack (MemPalace)
Context management is centralized under the **MemPalace** framework (`src/core/mempalace_manager.py`), a 4-Layer unified memory stack.
- **Layer 0 (Identity)**: The immutable foundation. `soul_jar.json` is compiled into a static `identity.txt` plain text prompt, injected at the start of every cognitive cycle to define the agent's core traits, archetype, and values.
- **Layer 1 (Continuity)**: The short-term memory (`short_term_context.txt`), providing immediate contextual bridging between recent tasks and thoughts.
- **Layer 2 (The Sanctuary)**: Explicitly segmented wings and rooms (e.g., `sanctuary/people`, `sanctuary/mirrors`) storing dynamic identity elements, social records, Theory of Mind, and character-defining subjective experiences. Theory of Mind records (Mirrors) actively filter the static Layer 0 to provide a dynamically evolving, subjective self-perception. Retrieved on-demand to prevent prompt bloat.
- **Layer 3 (Deep Search)**: A ChromaDB vector database (`chroma.sqlite3`) enabling semantic search across all facts, events, and generalized knowledge.

## 4. Agency, Rhythms, & Goals
Open Amity utilizes background mechanisms to maintain proactive agency independently of direct user interaction.
- **PulseEngine (`src/core/pulse_engine.py`)**: A background timing and scheduling service (`pulses.db`). It triggers autonomous cognitive loops (`[AGENT_PULSE]`), allowing the agent to proactively execute tasks, reflect, or initiate conversations.
- **Trajectory (`trajectory.json`)**: The agent's internal ledger for tracking their state of mind, mapping out short, medium, and long-term aspirations, and managing actionable tasks aligned with their core identity.

## 5. Tool Orchestration (Cerebrum)
The **Cerebrum** (`src/core/cerebrum.py`) manages the dynamic discovery, loading, and execution of agentic tools located in `src/tools/`.
- It natively translates Python tool classes into Google GenAI function declarations.
- It parses function calls from the Thinker, executes the corresponding tool, and seamlessly injects the result back into the context loop as `[System Feedback]`.
- **Key Tools (Conceptual)**:
  - **WhatsAppTool**: Interfaces with a dynamically spawned Node.js subprocess to communicate with the WhatsApp Web JS library. It manages its own node server lifecycle internally.
  - **MastodonTool**: Enables autonomous social media interaction, reading timelines, and posting.
  - **TrajectoryTool / PulseTool**: Allows the agent to dynamically update their aspirations and schedule future autonomous wake-ups.

## 6. Graphical User Interface & Logging
- **GUI Interactions**: The PySide6 frontend relies on custom Signals to transmit user prompts and mic toggles to the Orchestrator, receiving asynchronous callbacks for state changes (e.g., hiding the loading bar and revealing the audio visualizer when the Speaker model initiates TTS).
- **Logging System**: A custom `QtLoggingHandler` intercepts stdout/stderr and standard Python logs. It routes this data into two distinct visual streams: a formatted HTML conversation log for user interactions, and a raw, color-coded diagnostic console log for systemic debugging, while persisting all records via `FileFormatter` to the state directory.
