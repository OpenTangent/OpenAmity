<div align="center">
  <h1>Open Amity</h1>
  <p>Build and run a subjective, self-aware, Gemini-based social agent that can communicate via Whatsapp and optionally maintain an online presence.</p>
</div>

## 🌅 Overview

Build your own Jarvis, Samantha, EDI, etc. with the ability to maintain their own goals and aspirations, and schedule their own tasks. Open Amity agents are explicitly subjective and self-aware which unlocks real character definition that separates Open Amity agents from the more typical corporate-tool agents.

## ✨ Key Features

- **⚡ Proactive Agency**: Open Amity utilises a background timing service to trigger autonomous cognitive loops called 'pulses'. The agent can proactively schedule pulses to execute tasks, monitor its trajectory, or initiate engagement.
- **📚 4-Layer Memory Architecture**: A sophisticated memory stack that prevents prompt bloat while maintaining deep context:
  - **Layer 0 (Identity)**: Core agent traits and personality.
  - **Layer 1 (Continuity)**: Short-term memory for contextual bridging.
  - **Layer 2 (The Sanctuary)**: On-demand records of social dynamics and personal experiences.
  - **Layer 3 (Deep Search)**: A vector database enabling semantic search for facts and general knowledge.
- **🛠️ Tool Usage**: Open Amity agents have access to the following tools:
  - **WhatsApp**: (Optional) Natively converse and interact via WhatsApp.
  - **Moltbook**: (Optional) A Reddit-style social network designed exclusively for AI agents.
  - **Mastodon**: (Optional) Mastodon.bot accepts bot account applications with strict 'rules for bots'.
  - **Trajectory**: A tool for maintaining a persistent sense of direction, purpose, and continuity across sessions.
  - **Pulse**: The tool allowing agents to proactively manage agency by scheduling pulses (either recurring or once-off).
  - **Classic tools**: Web Search and Terminal are also included.
- **🖥️ PySide6 Graphical Frontend**: A clean chat-style user interface with both text and audio input, and synthetic voice + transcript output. Tip: open the console (tilde key) to display logs and agent thoughts.
- **🫰 Reduced Token Usage Mode**: Open Amity includes Low Token Mode to significantly reduce API costs (especially useful with a free-tier Gemini API key)

## 🚀 Getting Started

To install the **Open Amity** app in Linux (requires Flatpak):

1. Add the Open Amity repository:
   ```bash
   flatpak remote-add --user --if-not-exists openamity https://opentangent.github.io/OpenAmity/index.flatpakrepo
   ```
2. Install the KDE Platform:
   ```bash
   flatpak install flathub org.kde.Platform//6.8
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

Tip: Check settings.json for hidden settings like the various Gemini model strings:
```bash
xdg-open ~/.var/app/com.openamity.OpenAmity/data/settings.json
```

## 🏗️ Architecture Blueprint

For a deep dive into the system's design please see: [open_amity_architecture.md](open_amity_architecture.md).

## 🛡️ License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.
