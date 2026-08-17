# Hyper Code 🚀

Hyper Code is an autonomous, open-source AI coding agent that runs directly in your terminal. It acts as a highly capable pair programmer that can explore your workspace, write code, run tests, debug failures, and deploy files—all automatically!

## Features

- **Solo Agent Mode:** Explore the codebase, generate execution plans, write code, run self-healing test loops, and perform visual QA audits.
- **Racing Mode:** Connect multiple language models (like Claude, Gemini, and OpenAI) and have them compete simultaneously to solve your task the fastest.
- **Zero-Dependency MCP Client:** Natively connects to Model Context Protocol (MCP) servers over `stdio` to integrate local tools seamlessly.
- **Hardware-Aware Local Models:** Automatically scans your system RAM to recommend the absolute best local Ollama model (e.g., Llama 3.2, Qwen, Mistral Nemo) to run fully offline without lagging your machine.
- **Undo System:** Built-in `/undo` command that leverages `git stash` or a localized `.hypercode` snapshot system to instantly revert any unwanted AI edits.
- **Symbol-Aware Workspace Indexing:** The `/ls` command dynamically extracts and lists classes, functions, and symbols out of Python, Go, JS, TS, and Rust files so the AI understands your project structure natively.
- **Live Preview:** Type `/preview` inside the terminal to automatically spawn a background server (Node/Vite or Python HTTP) and open a browser window right next to your terminal!
- **3 Approval Modes:** Configurable safety parameters (`suggest`, `auto`, `full-auto`) to let the AI write freely or ask for permission before modifying critical files or running shell commands.

## Getting Started

1. Download or clone this repository.
2. Run the executable from your terminal:
   ```bash
   python3 crack.py
   ```
3. A guided setup wizard will prompt you to configure your API keys and select your preferred models.
4. Type `/help` or `/features` in the terminal to see all available commands!
