<img width="1383" height="293" alt="image" src="https://github.com/user-attachments/assets/f4eceee1-de9d-44f9-9256-6c047e870222" />
# ⚡ Hyper Code (Vibe Workspace Assistant v13.0)

Hyper Code is an open-source, autonomous AI coding agent that lives directly in your terminal[cite: 5]. It features autonomous workspace exploration, diff-reviewed file edits, test-driven self-healing, and a unique "Racing Mode" to run multiple LLMs in parallel[cite: 5].

## ✨ Key Features

*   **Dual Operating Modes:**
    *   **🏎️ Racing Mode (2+ models):** Runs multiple models in parallel inside ephemeral Git sandboxes. The fastest model to pass the Playwright Visual QA audit wins and merges its code[cite: 5].
    *   **🤖 Solo Agent Mode (1 model):** Engages a full agentic loop. The model autonomously explores the workspace using `search_directory` and `read_file` tools, creates a visible task checklist, edits files in place, and reviews diffs before applying[cite: 5].
*   **Test-Driven Self-Heal Loop:** Automatically detects test suites (`pytest`, `npm test`, `go test`) and iterates on failures automatically[cite: 5].
*   **Diff-Reviewed File Edits:** Existing files are shown as a unified diff for review, not silently overwritten[cite: 5].
*   **Approval Modes:** Control agent autonomy with `/mode <suggest|auto|full-auto>`[cite: 5].
*   **Undo & Session Persistence:** Remembers chat history across restarts and allows you to revert changes using Git stashes and file snapshots (`/undo`)[cite: 5].
*   **Phantom Sandboxes:** Executes AI code in temporary Git worktrees to protect your local files[cite: 5].
*   **Voice Engineering:** Record audio via your microphone and have the AI compile it into a senior engineering spec[cite: 5].

## 🛠️ Installation

We have provided a unified install script that handles system dependencies (like PortAudio for voice features) and Python requirements automatically.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Abdulhannan407/HyperCode.git
   cd HyperCode
   ```

2. **Make the installer executable and run it:**
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

## 🚀 Quick Start

Run the assistant by executing the main script:

```bash
python3 crack.py
```

On the first run, the interactive setup will ask you to:

1. Select the models you want to use (e.g., Gemini 2.5 Flash, Claude 3.7 Sonnet, GPT-4o, or Local offline Ollama models).
2. Provide the necessary API keys. These are securely saved to `~/.vibe_apex_config.json`.

## 💻 Commands Cheat Sheet

Inside the interactive Hyper Code REPL, you can use the following commands:

| Command | Description |
| --- | --- |
| `/ls` | Scans and lists all valid project files locally with symbol-aware indexing. |
| `/add <file>` | Manually injects a specific file into the AI's precision context. |
| `voice` | Records audio via mic & compiles it into a senior engineering spec. |
| `/mode <mode>` | Set autonomy level to `suggest`, `auto`, or `full-auto`. |
| `/model set <model>` | Pins a specific model (e.g., `ollama/llama3.2`) to THIS project only. |
| `/deps` | Scans the project for dependencies and suggests installation commands. |
| `/preview` | Starts a local dev server (npm or Python HTTP) and opens your browser. |
| `/undo` | Restores the workspace from the last file snapshot or Git stash. |
| `/clear` | Clears conversation memory for the current folder. |
| `/sessions` | Shows every Hyper Code terminal running on your machine. |
| `/features` | Shows the full feature dashboard. |

## 🚨 Crash Auto-Patching

If you have a broken Python script, you can use Hyper Code as an auto-debugger. Instead of entering the interactive loop, run your broken script through Hyper Code:

```bash
python3 crack.py my_broken_script.py
```

It will catch `stderr` tracebacks, run AI diagnostics, and offer an interactive diff patch to fix the crash.
