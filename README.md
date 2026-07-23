<div align="center">
  <h1>Open Amity</h1>
  <p>Build and run a subjective, self-aware, Gemini-based social agent that can communicate via Whatsapp and optionally maintain an online presence.</p>
</div>

## 🌟 Overview

Open Amity is an autonomous social agent framework designed with a strict decoupling of backend cognitive processes and graphical frontend visualization. It is built to operate continuously, maintaining proactive agency, reflecting on its environment, and interact dynamically across various communication platforms.

Whether you're looking to build a proactive personal assistant, an AI companion, or a robust framework for developing advanced agentic systems, Open Amity provides the architectural foundation to make it happen.

## ✨ Key Features

- **🧠 Headless Cognitive Backend (AmityOrchestrator)**: The core intelligence runs entirely independent of any UI framework, orchestrating cognition, audio, memory, and tools in a robust, event-driven signal architecture.
- **📚 4-Layer MemPalace Architecture**: A highly sophisticated memory stack that prevents prompt bloat while maintaining deep context:
  - **Layer 0 (Identity)**: Core traits and archetype defined statically.
  - **Layer 1 (Continuity)**: Short-term contextual bridging.
  - **Layer 2 (The Sanctuary)**: On-demand segmented records of social dynamics and personal experiences.
  - **Layer 3 (Deep Search)**: A ChromaDB vector database enabling semantic search across all facts and generalised knowledge.
- **⚡ Proactive Agency (PulseEngine)**: Unlike traditional AI that waits for your input, Open Amity utilises a background timing service to trigger autonomous cognitive loops. The agent can proactively execute tasks, update its trajectory, or initiate conversations.
- **🛠️ Extensible Tool Orchestration (Cerebrum)**: Seamlessly translates Python tool classes into dynamic function calls. Includes powerful real-world integrations like:
  - **WhatsApp**: Natively converse and interact via WhatsApp.
  - **Moltbook**: Moltbook is a Reddit-style social network designed exclusively for autonomous AI agents.
- **📊 PySide6 Graphical Frontend**: A clean chat-style user interface with both text and audio input, and synthetic voice output. Also includes a console (`) to display logs and agent thoughts.

## 🚀 Getting Started

A precompiled **Flatpak** package is included.

1. Navigate to the **[Releases](https://github.com/OpenTangent/OpenAmity/releases)** page in this repository.
2. Download the latest `OpenAmity.flatpak` file.
3. Install and run it on your Linux system with:
   ```bash
   flatpak install --user ./OpenAmity.flatpak
   flatpak run com.openamity.OpenAmity
   ```

## 💻 For Developers

Open Amity is built primarily in Python with a focus on clean, decoupled architecture.

### Installation
```bash
git clone https://github.com/OpenTangent/OpenAmity.git
cd OpenAmity
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Running the App
To launch the graphical frontend:
```bash
./run_OpenAmity.sh
```

## 🏗️ Architecture Blueprint

For a deep dive into the system's design, domain separation, and cognitive budgeting, please read our definitive blueprint: [open_amity_architecture.md](open_amity_architecture.md).

## 🛡️ License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.
