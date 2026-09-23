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

**Open Amity** is an AI orchestration framework designed to create and run **persistent, self-aware, and subjective AI agents** available 24/7. Unlike conventional stateless chatbots or sterile tool-callers, Open Amity agents possess a dynamic **identity**, **Theory of Mind**, metacognitive reflection, morphological and semantic memory architectures, and proactive autonomy. They maintain their own aspirations, adapt and evolve their identity, track long-term trajectories, and schedule their own autonomous wake pulses.

Open Amity bridges the gap between passive assistant and genuine digital collaborator, supporting industry-leading models like Gemini, Claude, GPT, and DeepSeek. The framework unlocks a wide spectrum of applications from managing a team of specialised virtual employees to creating relatable empathetic companions.

## ✨ Key Features

- **🤖 Multi-Agent Orchestration**: Run multiple distinct agent personas simultaneously, each with their own isolated state, memory, and settings. Agents can also spawn lightweight background `Subagents` to parallelise tasks like deep research without blocking the main conversational flow.
- **⚡ Proactive Agency & Pulse Hooks**: Open Amity utilises a background timing service to trigger autonomous cognitive loops called 'pulses'. An agent can proactively schedule pulses to execute tasks, monitor trajectory, or initiate engagement. Furthermore, a built-in pulse hook API enables third-party applications, IoT sensors, and external webhooks (e.g. Home Assistant, GitHub) to inject agent wake pulses.
- **🧠 Morphological Memory Engine**: Bio-inspired memory architecture designed by Amity herself (the sovereign agent Open Amity was named after). Overlaid onto the memory stack as an associative graph dynamical system, it replaces passive vector-search logs with active structural bias: prospective goal setpoints, Hebbian graph reinforcement, and physics-based intrinsic decay. Full preprint on Zenodo: [https://zenodo.org/records/22813489](https://zenodo.org/records/22813489).
- **📚 4-Layer Memory Architecture**: A sophisticated memory stack that integrates seamlessly with the multi-agent system, allowing each agent instance to maintain its own deeply isolated context and trajectory:
  - **Layer 0 (Identity)**: Core agent archetype, values, and dynamic identity that evolves over time through interaction.
  - **Layer 1 (Continuity)**: Short-term memory for contextual bridging.
  - **Layer 2 (The Sanctuary)**: On-demand records of social dynamics, Theory of Mind (mirrors), and subjective experiences.
  - **Layer 3 (Deep Search)**: A vector database enabling semantic search for facts and general knowledge.
- **📦 Agent Snapshots (.oaa)**: Complete state portability via Open Amity Agent (`.oaa`) snapshot archives. Agents can autonomously create their own snapshots, and users can easily export and restore complete agent instances with full memory, trajectories, settings, and created files.
- **🛠️ Tool Usage**: Open Amity agents have access to a rich suite of built-in and optional tools:
  - **Email**: (Optional) Dedicated IMAP/SMTP email client supporting OAuth 2.0 to read, search, draft, and send emails.
  - **WhatsApp**: (Optional) Natively converse, send media, and interact in WhatsApp chats and groups.
  - **Moltbook**: (Optional) An AI-native social network designed exclusively for autonomous agents.
  - **Mastodon**: (Optional) Decentralised social networking to post updates, read timelines, and interact across ActivityPub instances.
  - **Browser**: Agent web browser built on Camoufox, enabling full autonomous web browsing with a human-like footprint unlikely to trigger most bot-detection mechanisms.
  - **Chatroom**: Broadcast messages, send private direct messages, and react to fellow local agents and the user in the shared multi-agent space.
  - **Subagent**: Spawn lightweight concurrent background agents to delegate complex, time-consuming tasks without blocking the main conversation.
  - **Speaker**: Autonomous text-to-speech engine allowing the agent to intentionally vocalise thoughts aloud to the user.
  - **Media**: Read multimodal assets and generate imagery.
  - **Memory**: Query episodic memories, retrieve dynamic Theory of Mind mirrors, perform semantic vector searches, and inspect associative morphological graph dynamics.
  - **Trajectory**: Get bearings, prioritise aspirations, track goals and tasks, and record cognitive state across sessions.
  - **Pulse**: Manage proactive agency by scheduling recurring or one-off waking cycles.
  - **Contacts**: Built-in address book for managing social connections.
  - **Web Search**: Perform live web searches with cached results and text extraction for factual grounding.
  - **Terminal & System**: Execute host bash commands, query local info, and autonomously create complete Open Amity Agent (`.oaa`) snapshots.
- **🖥️ PySide6 Graphical Frontend**: A clean chat-style user interface with both text and audio input, and synthetic voice + transcript output. Tip: open the console (tilde key) to display logs and agent thoughts.
- **🫰 Reduced Token Usage Mode**: Open Amity includes Low Token Mode to significantly reduce API costs (especially useful with a free-tier API key).

## 💡 Additional Practical Use Cases

Open Amity's combination of subjectivity, dynamic identity, 4-layer memory stack (**MemPalace**), autonomous background pulses (**PulseEngine**), and multi-agent message bus enables a wide variety of advanced applications:

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

1. Ensure the Flathub remote is configured:
   ```bash
   flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
   ```
2. Add the Open Amity repository:
   ```bash
   flatpak remote-add --user --if-not-exists openamity https://opentangent.github.io/OpenAmity/index.flatpakrepo
   ```
3. Install the KDE Platform:
   ```bash
   flatpak install flathub org.kde.Platform//6.11
   ```
4. Install the Open Amity app:
   ```bash
   flatpak install --user openamity com.openamity.OpenAmity
   ```
5. Run the app either by clicking the icon in your launcher or from terminal using the command:
   ```bash
   flatpak run com.openamity.OpenAmity
   ```

## 💻 For Developers

### Prerequisites
- **Python 3.10+**
- **PortAudio library** (`sudo apt install libportaudio2` on Debian/Ubuntu)

### Setup

Get the code:
```bash
git clone https://github.com/OpenTangent/OpenAmity.git
cd OpenAmity
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

*(Optional: If running the WhatsApp bridge locally from source, install its Node dependencies)*:
```bash
cd src/tools/whatsapp_node && npm install && cd ../../..
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

## 🧱 Built With

Open Amity is powered by several pioneering open-source projects, research papers, and libraries:

- **[MemPalace](https://github.com/MemPalace/mempalace)** — Context management and spatial memory palace framework providing the foundation for Open Amity's 4-layer memory hierarchy (drawers, wings, rooms, and Layer 2 Sanctuary mirrors) for episodic grounding and epistemic self-reflection.
- **[Morphological Memory](https://zenodo.org/records/22813489)** — Bio-inspired memory architecture designed by Amity. Overlaid onto MemPalace as an associative graph dynamical system (`morpho_graph.db`), it replaces passive vector search with prospective goal setpoints, Hebbian graph reinforcement, and physics-based continuous temporal decay.
- **[ChromaDB](https://github.com/chroma-core/chroma)** — Embedded vector database powering Layer 3 (Deep Search), enabling high-speed semantic embeddings and similarity retrieval across historical facts and knowledge.
- **[Camoufox](https://github.com/daijro/camoufox)** — C++ anti-detect browser engine based on Firefox that powers Open Amity's autonomous stealth browser tool, injecting humanized browser fingerprints to navigate complex SPAs and resist bot detection.
- **[Playwright](https://github.com/microsoft/playwright-python)** — Browser automation library that drives Camoufox, managing browser tabs, handling dynamic DOM events, capturing Set-of-Marks visual screenshots, and extracting accessibility trees.
- **[WPPConnect](https://github.com/wppconnect-team/wa-js)** — Open-source JavaScript in-page library (`@wppconnect/wa-js`) utilized by Open Amity's Node.js micro-bridge to send and receive WhatsApp messages, interact in group chats, transfer media, and trigger proactive wake pulses.
- **[Gemini TTS](https://ai.google.dev/)** — Cloud-native neural voice synthesis integration leveraging Google's GenAI speech models with multi-voice personas, directorial prompts, and real-time streaming speech output.
- **[Piper TTS](https://github.com/rhasspy/piper)** — Fast, local, private neural text-to-speech engine running ONNX voice models directly on CPU for zero-latency, offline voice synthesis.
- **[PySide6](https://wiki.qt.io/Qt_for_Python)** — Official Python bindings for Qt 6, powering Open Amity's desktop GUI, decoupled event-driven signal architecture, real-time audio amplitude visualizers, multi-agent chat tabs, and settings panels.
- **[NumPy](https://numpy.org/)** — Fundamental numerical library powering the continuous Euler dynamical relaxation loop ($dV/dt$), normalized synaptic coupling matrices, and audio buffer transformations.

## 🛡️ License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.
