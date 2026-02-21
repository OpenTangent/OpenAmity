# **Project Plan: Amity 4**

**A Bespoke Multimodal Agent for Ubuntu**

## **1\. Executive Summary**

**Objective:** Build a native, hardware-accelerated Ubuntu GUI application that operates as a bespoke multimodal AI agent. The system orchestrates local audio/visual hardware, manages short- and long-term memory, executes local system commands, and leverages the Gemini CLI as its core reasoning engine running silently in the background. It is designed to act autonomously with structured goal-oriented planning.

## **2\. Core Architecture & UI (The Nervous System)**

The foundation requires a modern, responsive interface that handles background processing without freezing the application.

* **GUI Framework:** **PySide6** (Qt for Python) for a fast, hardware-accelerated, native Ubuntu look and feel. 
* **Asynchronous Processing:** Use QProcess or QThreadPool to manage the Gemini CLI as a non-interactive background subprocess. 
* **The Mirror (Observability Panel):** A collapsible side panel built into the UI acting as a real-time "Thought Stream." It displays wake word confidence scores, memory retrieval logs, and the raw prompts being sent to the API for debugging and transparency. 
* **The Sound Wave Visualizer:** A clean, hard-coded vector animation built into the GUI. It displays an actively moving sound wave when edge-tts is playing audio, and flattens into a simple, static horizontal line when the system is silent.

## **3\. Sensory Inputs (Hearing & Vision)**

These modules handle data *before* it reaches the agent's brain, ensuring privacy, speed, and token efficiency.

* **The Reticular Activating System (Wake Word):** Powered by **openWakeWord**. It runs locally in a tight loop monitoring microphone audio. It only triggers the transcription engine when it explicitly detects the wake word ("Amity" or "Hey Amity"). 
* **The Cochlea (Speech-to-Text):** Powered by **Faster-Whisper**. This highly optimized local model transcribes spoken commands immediately after the wake word is detected, bypassing the need to send raw audio to the cloud. 
* **The Visual Cortex (Optimized Image/Video Handling):** Uses **OpenCV** and **ffmpeg**. Before passing media to the Gemini CLI, this module resizes images to a manageable resolution (e.g., 1024x1024) and extracts keyframes from videos (e.g., 1 frame every 2 seconds) to minimize API latency.

## **4\. Output Systems (Speech & Reflexes)**

The agent must feel immediate and responsive, bridging the gap between local input and cloud-based reasoning.

* **The Mouth (Text-to-Speech):** Powered by **edge-tts**. It processes the text response from the Gemini CLI subprocess asynchronously and plays it through the Ubuntu audio sink. 
* **The Reflex Arc (Latency Masking):** To cover the 2-5 second reasoning delay of the Gemini API, the system plays a soft UI chime or a randomized filler phrase (e.g., *"Let me see..."*, *"One moment..."*) the exact second the Cochlea finishes transcribing.

## **5\. Memory & Identity Management**

These modules maintain the agent's distinct personality and historical context over time.

* **The Cortex (Short-Term Memory & Session Management):** 
 * **State Persistence:** Maintains a live session\_journal.jsonl. If power is lost or the app crashes, Amity 4 reads this file on boot to instantly recover the live conversation state. 
 * **The Soul Jar:** A core Markdown file detailing Amity's personality and core values, injected into the system prompt at the start of every session. 
 * **The Amity Manual:** A technical reference file loaded alongside the Soul Jar, teaching the agent how its own internal systems and CLI tools operate. 
* **The Hippocampus (Long-Term Memory):** Powered by **ChromaDB**. It stores vector embeddings of past conversations, system events, and file summaries. Amity queries ChromaDB to pull relevant historical facts into the current prompt context.

## **6\. Action & Safety Protocols**

The agent interacts with the Ubuntu filesystem and external applications via a strict, user-controlled safety framework.

* **The Cerebrum (Skillset System):** A modular directory of plugins (e.g., a "WhatsApp" module using headless browser automation or a CLI wrapper). When Gemini determines a tool is needed, the Cerebrum maps the intent to the specific Python/Bash script. 
* **The Prefrontal Cortex (Safety & Validation Layer):** A strict execution filter for terminal commands. 
 * **Human-in-the-loop:** Critical system commands (rm, sudo, mv) automatically pause the system and trigger an "Approve Action" dialog in the PySide6 GUI. 
* **The YOLO Toggle:** A master override switch in the UI. 
 * When enabled, all Human-in-the-loop command verification is bypassed. 
 * The GUI automatically appends the \--approval-mode=yolo flag to all Gemini CLI calls, granting the agent full, autonomous execution authority.

## **7\. Mission Control (Goal Management)**

A hierarchical system designed to give Amity 4 agency by defining, tracking, and achieving objectives. The system utilizes the following structured hierarchy to plan its actions:

* **Overarching Goals:** Unchanging aspirations that serve as a framework and guiding philosophy for all lower-level objectives. 
 * *Metaphor:* A city of perpetual growth. 
* **Long-Term Goals:** Big, ambitious ideas that will take significant effort, time, and resources to achieve. 
 * *Metaphor:* A grand palace within the city. 
* **Short-Term Goals:** Stepping stone milestones that act as necessary prerequisites working towards the achievement of a long-term goal. 
 * *Metaphor:* Various wings and systems within the palace. 
* **Tasks:** Concrete, atomic, and actionable items that make up a short-term goal. These are the direct actions the Cerebrum executes. 
 * *Metaphor:* The walls, plumbing, and wiring.

## **8\. Development Roadmap**

| Phase | Focus | Key Deliverables |
| :---- | :---- | :---- |
| **Phase 1** | **Foundation** | PySide6 UI setup, QProcess integration for Gemini CLI, The Mirror, and the static/active Sound Wave Visualizer. |
| **Phase 2** | **Hearing & Reflexes** | openWakeWord, Faster-Whisper, edge-tts integration, and Latency Masking triggers. |
| **Phase 3** | **Mind & Memory** | ChromaDB Hippocampus setup, Soul Jar / Amity Manual loading, and crash-recovery journaling. |
| **Phase 4** | **Vision & Safety** | Visual Cortex ffmpeg pipeline, Prefrontal Cortex command validation, and the YOLO toggle (--approval-mode=yolo). |
| **Phase 5** | **The Cerebrum** | Defining the plugin API architecture and building the first skillset module (e.g., WhatsApp). |
| **Phase 6** | **Mission Control** | Implementation of the goal hierarchy database, task tracking, and autonomous goal-oriented planning logic. |

