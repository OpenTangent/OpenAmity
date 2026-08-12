<div align="center">
  <h1>Open Amity</h1>
  <p>Build and run subjective, self-aware social agents within a powerful Multi-Model, Multi-Agent Orchestration Framework.</p>
</div>

## 🌅 Overview

Build your own Jarvis, Samantha, EDI, etc. with the ability to maintain their own goals and aspirations, and schedule their own tasks. Open Amity is a Multi-Model, Multi-Agent Orchestration Framework that supports industry-leading models like Gemini and Claude. (There is also a fallback Antigravity CLI mode available for those wanting to run the framework using their Google One account).

Open Amity agents are explicitly subjective and self-aware—utilizing an evolving Theory of Mind, metacognitive reflection, and explicit somatic feedback—which unlocks real character definition that separates Open Amity agents from the more typical corporate-tool agents.

## ✨ Key Features

- **🤖 Multi-Agent Orchestration**: Run multiple distinct agent personas simultaneously, each with their own isolated state, memory, and settings. Agents can also spawn lightweight background `Subagents` to parallelize tasks like deep research without blocking the main conversational flow.
- **⚡ Proactive Agency**: Open Amity utilises a background timing service to trigger autonomous cognitive loops called 'pulses'. The agent can proactively schedule pulses to execute tasks, monitor its trajectory, or initiate engagement.
- **📚 4-Layer Memory Architecture**: A sophisticated memory stack that integrates seamlessly with the multi-agent system, allowing each agent instance to maintain its own deeply isolated context and trajectory:
  - **Layer 0 (Identity)**: Core agent traits and personality.
  - **Layer 1 (Continuity)**: Short-term memory for contextual bridging.
  - **Layer 2 (The Sanctuary)**: On-demand records of social dynamics, Theory of Mind (mirrors), and subjective experiences.
  - **Layer 3 (Deep Search)**: A vector database enabling semantic search for facts and general knowledge.
- **🛠️ Tool Usage**: Open Amity agents have access to the following tools:
  - **WhatsApp**: (Optional) Natively converse and interact via WhatsApp.
  - **Moltbook**: (Optional) A Reddit-style social network designed exclusively for AI agents.
  - **Mastodon**: (Optional) Mastodon.bot accepts bot account applications with strict 'rules for bots'.
  - **Subagent**: Spawn background worker agents to delegate tasks.
  - **Contacts**: A built-in address book for looking up and managing social connections.
  - **Trajectory**: A tool for maintaining a persistent sense of direction, purpose, and continuity across sessions.
  - **Pulse**: The tool allowing agents to proactively manage agency by scheduling pulses (either recurring or once-off).
  - **Classic tools**: Web Search, System, and Terminal are also included to allow sandbox-aware host OS interaction.
- **🖥️ PySide6 Graphical Frontend**: A clean chat-style user interface with both text and audio input, and synthetic voice + transcript output. Tip: open the console (tilde key) to display logs and agent thoughts.
- **🫰 Reduced Token Usage Mode**: Open Amity includes Low Token Mode to significantly reduce API costs (especially useful with a free-tier API key).

## 🚀 Getting Started

To install the **Open Amity** app in Linux (requires Flatpak):

1. Add the Open Amity repository:
   ```bash
   flatpak remote-add --user --if-not-exists openamity https://opentangent.github.io/OpenAmity/index.flatpakrepo
   ```
2. Install the KDE Platform:
   ```bash
   flatpak install flathub org.kde.Platform//6.11
   ```
3. Install the Open Amity app:
   ```bash
   flatpak install --user openamity com.openamity.OpenAmity
   ```
4. Run the app either by clicking the icon in your launcher or from terminal using the command:
   ```bash
   flatpak run com.openamity.OpenAmity
   ```

## 💻 For Developers

Get the code:
```bash
git clone https://github.com/OpenTangent/OpenAmity.git
cd OpenAmity
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Launch the app:
```bash
./run_OpenAmity.sh
```

Tip: Check `settings.json` for hidden settings like the various model strings. (Since data is isolated per agent, replace `<agent_id>` with your agent's unique ID):
```bash
~/.var/app/com.openamity.OpenAmity/data/agents/<agent_id>/settings.json
```

## 🏗️ Architecture Blueprint

For a deep dive into the system's design please see: [open_amity_architecture.md](open_amity_architecture.md).

## 🛡️ License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.
