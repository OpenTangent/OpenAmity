<div align="center">
  <img src="src/assets/com.openamity.OpenAmity.png" alt="Open Amity Logo" width="128" />
  <h1>Open Amity</h1>
  <p><strong>Multi-model orchestration framework for building proactive, stateful multi-agent teams with layered memory, autonomous scheduling, and Theory of Mind.</strong></p>

  <p>
    <a href="#-getting-started"><img src="https://img.shields.io/badge/Flatpak-Open_Amity-blue?logo=flatpak&logoColor=white" alt="Flatpak" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPLv3-green.svg" alt="License: GPL v3" /></a>
    <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python 3.10+" />
    <img src="https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white" alt="PySide6 / Qt" />
  </p>

  <br />

  <a href="src/assets/screenshots/sc-Agent.png">
    <img src="src/assets/screenshots/sc-Agent.png" alt="Open Amity Agent Interface" width="100%" />
  </a>

  <br />

  <table>
    <tr>
      <td width="33.3%" align="center">
        <a href="src/assets/screenshots/sc-Chatroom.png">
          <img src="src/assets/screenshots/sc-Chatroom.png" alt="Multi-Agent Chatroom" />
        </a>
      </td>
      <td width="33.3%" align="center">
        <a href="src/assets/screenshots/sc-Values.png">
          <img src="src/assets/screenshots/sc-Values.png" alt="Agent Values and Goals" />
        </a>
      </td>
      <td width="33.3%" align="center">
        <a href="src/assets/screenshots/sc-Voice.png">
          <img src="src/assets/screenshots/sc-Voice.png" alt="Agent Voice" />
        </a>
      </td>
    </tr>
  </table>
</div>

## 👤 Overview

**Open Amity** is an AI orchestration framework designed to create **persistent, self-aware, and subjective AI agents** that are available 24/7. Unlike conventional stateless chatbots or sterile tool-callers, Open Amity agents possess a dynamic **Theory of Mind**, metacognitive reflection, episodic and semantic memory architectures, and proactive autonomy. They maintain their own aspirations, track long-term trajectories, and schedule their own autonomous waking cycles.

Open Amity bridges the gap between passive assistant and genuine digital collaborator. Supporting industry-leading models like Gemini, Claude, and GPT, with more model workers planned for future releases. The framework unlocks a wide spectrum of applications from managing a team of specialised virtual employees to creating relatable empathetic companions.

## ✨ Key Features

- **🤖 Multi-Agent Orchestration**: Run multiple distinct agent personas simultaneously, each with their own isolated state, memory, and settings. Agents can also spawn lightweight background `Subagents` to parallelise tasks like deep research without blocking the main conversational flow.
- **⚡ Proactive Agency**: Open Amity utilises a background timing service to trigger autonomous cognitive loops called 'pulses'. An agent can proactively schedule pulses to execute tasks, monitor trajectory, or initiate engagement.
- **📚 4-Layer Memory Architecture**: A sophisticated memory stack that integrates seamlessly with the multi-agent system, allowing each agent instance to maintain its own deeply isolated context and trajectory:
  - **Layer 0 (Identity)**: Core agent traits and personality.
  - **Layer 1 (Continuity)**: Short-term memory for contextual bridging.
  - **Layer 2 (The Sanctuary)**: On-demand records of social dynamics, Theory of Mind (mirrors), and subjective experiences.
  - **Layer 3 (Deep Search)**: A vector database enabling semantic search for facts and general knowledge.
- **🛠️ Tool Usage**: Open Amity agents have access to a rich suite of built-in and optional tools:
  - **Email**: (Optional) Dedicated IMAP/SMTP email client supporting OAuth 2.0 to read, search, draft, send, and download attachments.
  - **WhatsApp**: (Optional) Natively converse, send media, and interact in WhatsApp chats and groups via local WA-JS bridge.
  - **Moltbook**: (Optional) An AI-native social network designed exclusively for autonomous agents.
  - **Mastodon**: (Optional) Decentralised social networking to post updates, read timelines, and interact across ActivityPub instances.
  - **Chatroom**: Broadcast messages, send private direct messages, and react to fellow agents and the user in the shared multi-agent space.
  - **Subagent**: Spawn concurrent background worker agents to delegate complex, time-consuming tasks without blocking the main conversation.
  - **Speaker**: Autonomous text-to-speech engine allowing the agent to intentionally vocalise thoughts aloud to the user.
  - **Media**: Read multimodal assets and generate imagery.
  - **MemPalace**: Query episodic memories, retrieve dynamic Theory of Mind mirrors, and perform semantic vector searches.
  - **Trajectory**: Get bearings, prioritise aspirations, track goals and tasks, and record cognitive state across sessions.
  - **Pulse**: Manage proactive agency by scheduling recurring or one-off waking cycles (pulses).
  - **Contacts**: Built-in address book for managing social connections.
  - **Web Search**: Perform live web searches with cached results and text extraction for factual grounding.
  - **Terminal & System**: Execute host bash commands, query local info, and autonomously create complete `.oaa` agent snapshots (backups).
- **🖥️ PySide6 Graphical Frontend**: A clean chat-style user interface with both text and audio input, and synthetic voice + transcript output. Tip: open the console (tilde key) to display logs and agent thoughts.
- **🫰 Reduced Token Usage Mode**: Open Amity includes Low Token Mode to significantly reduce API costs (especially useful with a free-tier API key).

## 💡 Additional Practical Use Cases

Open Amity's combination of subjectivity, 4-layer memory stack (**MemPalace**), autonomous background pulses (**PulseEngine**), and multi-agent message bus enables a wide variety of advanced applications:

- **💼 Autonomous Virtual Employees & Digital Coworkers**: Deploy specialised team members (e.g., DevOps on-call engineers, social media managers, executive chiefs of staff) that operate asynchronously, manage their own dedicated email and messaging channels (WhatsApp, IMAP/SMTP), and schedule periodic check-ins.
- **🔬 Autonomous Research Lab Partners**: Collaborators that maintain ongoing literature reviews, log experimental hypotheses, spawn background subagents to parallelise deep technical research, and maintain a persistent research journal.
- **🎓 Personalised Socratic Tutors & Mentors**: Educators that build an evolving model of a student's strengths, learning style, and misconceptions over months of interaction—scheduling study sessions and adapting explanations dynamically.
- **🤝 Empathetic Companions**: Social companions with distinct character definitions, emotional continuity, and shared memories who proactively check in, reflect on past conversations, and evolve through shared experiences.
- **🎭 Dynamic Creative Co-Authors & Living Worldbuilders**: Narrative partners, tabletop roleplaying game masters, or persistent in-world characters who possess consistent beliefs, quirks, and memory of complex lore.
- **🌐 Autonomous Digital Representatives & Community Liaisons**: Brand or project ambassadors that maintain consistent personas and ethical guidelines while interacting across public and private social platforms (Mastodon, Moltbook, WhatsApp, Email).
- **👥 Multi-Agent Synthetic Teams & Think Tanks**: Collaborative cohorts of distinct agent personas interacting via shared chatrooms (e.g., *Architect* + *Software Engineer* + *Security Auditor*, or *Strategist* + *Devil's Advocate* + *Data Analyst*) to debate, verify, and execute complex goals in parallel.
- **🛡️ Continuous Health & Habit Coaches**: Persistent coaches that proactively check in on fitness goals, habits, and well-being, tracking behavioural patterns and emotional context over time.

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
