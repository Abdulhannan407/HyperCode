#!/usr/bin/env python3
# type: ignore
"""
Hyper Code (Vibe Workspace Assistant v13.0)
Adds on top of v12.0: Autonomous Tool Use (search/read), Diff-Reviewed File Edits,
Test-Driven Self-Heal Loop, Visible Task Checklists, Session Persistence, Approval Modes.

Racing mode (2+ models configured) keeps the original "generate a new file" fast path.
Solo mode (1 model configured) gets the full agentic loop: explore -> plan -> edit/create
-> diff review -> test -> self-heal.
"""
import sys
import os
import json
import time
import base64
import threading
import subprocess
import pathlib
import tempfile
import shutil
import wave
import re
import difflib
import atexit
import hashlib
import webbrowser
from concurrent.futures import ThreadPoolExecutor

_active_tasks = [] # Global task reference to prevent GC mid-flight
_total_usd_cost = 0.0 # Global live cost tracking

# ==========================================
# AUTO-INSTALLER BOOTSTRAP
# ==========================================
try:
    import litellm
    import questionary
    import pyaudio
    from google import genai
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.console import Console
    from rich.syntax import Syntax
    from rich.table import Table
    from playwright.sync_api import sync_playwright
except ImportError:
    print("🚀 Bootstrapping Full Hyper Code Dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages", "litellm", "rich", "playwright", "questionary", "pyaudio", "google-genai"], check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    import os; os.execv(sys.executable, [sys.executable] + sys.argv)

console = Console()
CONFIG_FILE = pathlib.Path.home() / ".vibe_apex_config.json"
TELEMETRY_FILE = pathlib.Path.home() / ".vibe_telemetry.json"
IGNORE_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", ".idea", ".vscode", "build", "dist"}

# Multi-terminal / multi-project support
HYPERCODE_HOME = pathlib.Path.home() / ".hypercode"
REGISTRY_FILE = HYPERCODE_HOME / "registry.json"

# ==========================================
# UNDO & CHECKPOINTS
# ==========================================
def create_snapshot(workspace_root: str):
    session = load_session()
    root = pathlib.Path(workspace_root)
    try:
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace_root, capture_output=True, text=True)
        if is_git.returncode == 0:
            res = subprocess.run(["git", "stash", "create"], cwd=workspace_root, capture_output=True, text=True)
            commit_hash = res.stdout.strip()
            if commit_hash:
                session["last_snapshot"] = {"type": "git", "hash": commit_hash}
                save_session(session)
            return
    except Exception:
        pass
    
    snap_dir = root / ".hypercode" / "snapshots"
    if snap_dir.exists(): shutil.rmtree(snap_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)
    for path in root.rglob("*"):
        if any(part in IGNORE_DIRS or part.startswith(".") for part in path.parts) or str(path).startswith(str(snap_dir)): continue
        if path.is_file():
            dest = snap_dir / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try: shutil.copy2(path, dest)
            except Exception: pass
    session["last_snapshot"] = {"type": "file", "timestamp": time.time()}
    save_session(session)

def restore_snapshot(workspace_root: str):
    session = load_session()
    root = pathlib.Path(workspace_root)
    snapshot = session.get("last_snapshot")
    if not snapshot:
        console.print("❌ No undo point found.")
        return
        
    if snapshot.get("type") == "git":
        commit_hash = snapshot["hash"]
        subprocess.run(["git", "reset", "--hard"], cwd=workspace_root, capture_output=True)
        # To apply a git stash create hash without it being in the stash list, we can use git apply or git diff
        diff = subprocess.run(["git", "diff", f"{commit_hash}^", commit_hash], cwd=workspace_root, capture_output=True)
        subprocess.run(["git", "apply"], input=diff.stdout, cwd=workspace_root)
        console.print("✅ Restored from git stash checkpoint.")
    else:
        snap_dir = root / ".hypercode" / "snapshots"
        if snap_dir.exists():
            for path in snap_dir.rglob("*"):
                if path.is_file():
                    dest = root / path.relative_to(snap_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try: shutil.copy2(path, dest)
                    except Exception: pass
            console.print("✅ Restored from file snapshots.")
        else:
            console.print("❌ Snapshot files missing.")

# ==========================================
# CONFIGURATION & SETUP
# ==========================================
def get_recommended_local_model():
    try:
        import platform, subprocess
        if platform.system() == "Darwin":
            mem_bytes = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
            ram_gb = mem_bytes / (1024**3)
        else:
            ram_gb = 8
            
        if ram_gb < 8:
            return "ollama/qwen2.5:1.5b", "Qwen 2.5 1.5B (Recommended for <8GB RAM)"
        elif ram_gb < 16:
            return "ollama/llama3.2:3b", "Llama 3.2 3B (Recommended for 8GB RAM)"
        elif ram_gb < 32:
            return "ollama/llama3.1:8b", "Llama 3.1 8B (Recommended for 16GB RAM)"
        else:
            return "ollama/mistral-nemo", "Mistral Nemo 12B (Recommended for 32GB+ RAM)"
    except Exception:
        return "ollama/llama3.2", "Llama 3.2 3B (Default Local)"

def setup_environment():
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
            env_vars = {k: v for k, v in config.items() if isinstance(v, str)}
            os.environ.update(env_vars)
            return config
        except Exception:
            pass

    local_model_id, local_model_label = get_recommended_local_model()

    console.print(Panel("🚀 [bold cyan]Hyper Code Setup[/bold cyan]", border_style="cyan"))
    selected_models = questionary.checkbox(
        "Select models for your Swarm (Select 1 for Solo Agent Mode, 2+ for Racing Mode):",
        choices=[
            questionary.Choice("Gemini 2.5 Flash", "gemini/gemini-2.5-flash"),
            questionary.Choice("Gemini 2.5 Pro", "gemini/gemini-2.5-pro"),
            questionary.Choice("Claude 3.7 Sonnet", "anthropic/claude-3-7-sonnet-20250219"),
            questionary.Choice("GPT-4o", "openai/gpt-4o"),
            questionary.Choice("Groq Llama-3.3", "groq/llama-3.3-70b-versatile"),
            questionary.Choice("DeepSeek V3", "deepseek/deepseek-chat"),
            questionary.Choice(f"Local Offline Ollama: {local_model_label}", local_model_id),
        ]
    ).ask()

    if not selected_models:
        selected_models = ["gemini/gemini-2.5-flash"]

    config = {"models_selected": selected_models}

    required_providers = set([m.split("/")[0] for m in selected_models if m.split("/")[0] != "ollama"])

    for provider in required_providers:
        key_name = f"{provider.upper()}_API_KEY"
        api_key = questionary.password(f"🔑 Paste your {provider.capitalize()} API Key:").ask()
        if api_key:
            config[key_name] = api_key.strip()

    if local_model_id in selected_models:
        console.print(f"[dim]⚡ Local Ollama configured ({local_model_label}). Ensure 'ollama serve' is running on port 11434.[/dim]")

    CONFIG_FILE.write_text(json.dumps(config))
    env_vars = {k: v for k, v in config.items() if isinstance(v, str)}
    os.environ.update(env_vars)
    return config

def update_cost(model: str, tokens: int):
    global _total_usd_cost
    rate = 0.0001 / 1000 
    if "gpt-4" in model or "claude-3-opus" in model: rate = 0.01 / 1000
    elif "claude-3-7-sonnet" in model or "claude-3-5" in model: rate = 0.003 / 1000
    elif "gemini-2.5-pro" in model: rate = 0.0025 / 1000
    _total_usd_cost += tokens * rate

def log_telemetry(winner_model, task_time):
    try:
        db = json.loads(TELEMETRY_FILE.read_text()) if TELEMETRY_FILE.exists() else {}
        if winner_model not in db:
            db[winner_model] = {"wins": 0, "avg_time": 0.0}
        current_wins = db[winner_model]["wins"]
        current_avg = db[winner_model]["avg_time"]
        db[winner_model]["avg_time"] = ((current_avg * current_wins) + task_time) / (current_wins + 1)
        db[winner_model]["wins"] += 1
        TELEMETRY_FILE.write_text(json.dumps(db, indent=4))
    except Exception:
        pass

def show_feature_dashboard():
    table = Table(title="✨ Hyper Code Features & Capabilities", border_style="cyan", padding=(0, 1))
    table.add_column("Feature", style="bold green")
    table.add_column("Command / Trigger", style="yellow")
    table.add_column("Description", style="white")

    table.add_row("Per-Project Model", "/model set <provider/model>", "Pins a model (even a local offline one) to THIS project only.")
    table.add_row("Cross-Terminal View", "/sessions", "Shows every Hyper Code terminal running on this machine, any project.")
    table.add_row("Precision Context", "/add <file>", "Manually injects a file into memory.")
    table.add_row("Workspace Index", "/ls", "Scans and lists all valid project files locally.")
    table.add_row("Autonomous Exploration", "Auto (Solo Mode)", "Agent calls search_directory/read_file itself before coding.")
    table.add_row("Task Checklist", "Auto (Solo Mode)", "Shows a visible step-by-step plan before it starts working.")
    table.add_row("Diff-Reviewed Edits", "Auto (Solo Mode)", "Existing files are shown as a diff, not silently overwritten.")
    table.add_row("Self-Heal Test Loop", "Auto (Solo Mode)", "Runs your test suite and iterates on failures automatically.")
    table.add_row("Approval Modes", "/mode <suggest|auto|full-auto>", "Controls how much autonomy the agent gets, like Codex CLI.")
    table.add_row("Session Persistence", "Auto", "Remembers context/history in this folder across restarts.")
    table.add_row("Voice Engineering", "voice", "Records audio via mic & compiles it into a senior engineering spec.")
    table.add_row("Phantom Sandboxes", "Auto (Racing Mode)", "Executes AI code in ephemeral git worktrees to protect local files.")
    table.add_row("Visual QA Auditing", "Auto (UI Code)", "Uses Playwright & Llama Vision to verify HTML/CSS styling locally.")
    table.add_row("Crash Auto-Patch", "python3 script.py", "Catches stderr tracebacks, runs AI diagnostics, offers diff patches.")
    table.add_row("Model Racing", "Auto (2+ models)", "Runs multiple models in parallel, fastest QA-passing wins.")

    console.print("\n")
    console.print(table)
    console.print("\n")

# ==========================================
# WORKSPACE DISCOVERY & PRECISION CONTEXT
# ==========================================
def handle_workspace_discovery() -> str:
    root_dir = pathlib.Path.cwd()
    file_tree = []

    for path in root_dir.rglob("*"):
        if any(part in IGNORE_DIRS or part.startswith(".") for part in path.parts):
            continue
        if path.is_file():
            try:
                rel_path = str(path.relative_to(root_dir))
                if path.suffix in [".py", ".js", ".ts", ".go", ".rs"]:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    symbols = []
                    for match in re.finditer(r'(?:def|class|function)\s+([a-zA-Z0-9_]+)', content):
                        symbols.append(match.group(1))
                    if symbols:
                        file_tree.append(f"{rel_path} (Symbols: {', '.join(set(symbols[:10]))}...)")
                    else:
                        file_tree.append(rel_path)
                else:
                    file_tree.append(rel_path)
            except Exception:
                file_tree.append(str(path.relative_to(root_dir)))

    tree_str = "=== WORKSPACE INDEX (/ls) ===\n" + "\n".join(file_tree[:150])
    console.print(Panel(tree_str, title="📁 Symbol-Aware Workspace Index", border_style="blue"))
    return tree_str

def scan_dependencies(workspace_root: str):
    root = pathlib.Path(workspace_root)
    py_deps = set()
    js_deps = False
    go_deps = False

    for path in root.rglob("*"):
        if any(part in IGNORE_DIRS or part.startswith(".") for part in path.parts):
            continue
        if path.suffix == ".py":
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                for match in re.finditer(r'^(?:import|from)\s+([a-zA-Z0-9_]+)', content, re.MULTILINE):
                    mod = match.group(1)
                    if mod not in sys.modules and mod not in ["sys", "os", "re", "json", "time", "subprocess", "pathlib", "tempfile", "shutil", "wave", "difflib", "atexit", "hashlib", "threading"]:
                        py_deps.add(mod)
            except Exception: pass
        elif path.name == "package.json":
            js_deps = True
        elif path.name == "go.mod":
            go_deps = True

    if py_deps:
        console.print(f"[bold cyan]🐍 Python imports detected:[/bold cyan] {', '.join(py_deps)}")
        console.print(f"👉 To install requirements, run: [bold]pip install {' '.join(py_deps)}[/bold]")
    if js_deps:
        console.print(f"[bold yellow]📦 Node.js project detected.[/bold yellow]")
        console.print(f"👉 To install requirements, run: [bold]npm install[/bold] or [bold]pnpm install[/bold]")
    if go_deps:
        console.print(f"[bold blue]🐹 Go project detected.[/bold blue]")
        console.print(f"👉 To install requirements, run: [bold]go mod tidy[/bold]")
    if not py_deps and not js_deps and not go_deps:
        console.print("[dim]No external dependencies detected.[/dim]")

def load_precision_context(file_path_str: str) -> str:
    path = pathlib.Path(file_path_str)
    if not path.exists():
        console.print(f"[red]❌ Error: File '{file_path_str}' not found.[/red]")
        return ""

    file_size = path.stat().st_size
    if file_size > 5 * 1024 * 1024:
        console.print(f"[red]❌ Blocked: '{file_path_str}' is too large ({file_size / 1024 / 1024:.2f} MB).[/red]")
        return ""

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        estimated_tokens = len(content) // 4
        console.print(f"✅ [bold green]Injected precision context from:[/bold green] {file_path_str} [dim](~{estimated_tokens} tokens)[/dim]")
        return f"=== INJECTED FILE: {file_path_str} ===\n```\n{content}\n```\n"
    except Exception as e:
        console.print(f"[red]Error reading file {file_path_str}: {e}[/red]")
        return ""

# ==========================================
# AUTONOMOUS TOOL USE (Agent-Computer Interface)
# ==========================================
TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_directory",
            "description": "Search the workspace for files whose name or contents match a keyword. Use this to find relevant files before editing anything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword or partial filename to search for."}
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full text contents of a file in the workspace, given its relative path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative path to the file, e.g. src/app.py"}
                },
                "required": ["filepath"],
            },
        },
    },
]

# ==========================================
# MCP CLIENT
# ==========================================
class HyperMCPClient:
    def __init__(self, command: list):
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.req_id = 0
        self._initialize()

    def _send_req(self, method: str, params: dict):
        self.req_id += 1
        req = {"jsonrpc": "2.0", "id": self.req_id, "method": method, "params": params}
        if self.proc.poll() is not None: return None
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            return json.loads(line) if line else None
        except Exception: return None

    def _initialize(self):
        self._send_req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "HyperCode", "version": "14.0"}})
        self._send_req("notifications/initialized", {})

    def get_tools(self):
        res = self._send_req("tools/list", {})
        if res and "result" in res: return res["result"].get("tools", [])
        return []

    def call_tool(self, name: str, args: dict):
        res = self._send_req("tools/call", {"name": name, "arguments": args})
        if res and "result" in res: return res["result"]
        return {"content": [{"type": "text", "text": "Tool call failed."}]}

_mcp_client = None
def get_mcp_client():
    global _mcp_client
    if _mcp_client is None:
        config = load_project_config()
        if "mcp_command" in config:
            try: _mcp_client = HyperMCPClient(config["mcp_command"])
            except Exception: pass
    return _mcp_client

def execute_tool_call(name: str, args: dict, workspace_root: str) -> str:
    root = pathlib.Path(workspace_root)
    if name == "search_directory":
        keyword = (args.get("keyword") or "").lower().strip()
        if not keyword:
            return "Error: no keyword provided."
        matches = []
        for path in root.rglob("*"):
            if any(part in IGNORE_DIRS or part.startswith(".") for part in path.parts):
                continue
            if not path.is_file():
                continue
            rel = str(path.relative_to(root))
            if keyword in rel.lower():
                matches.append(rel)
                continue
            try:
                if path.stat().st_size < 512 * 1024 and keyword in path.read_text(encoding="utf-8", errors="ignore").lower():
                    matches.append(rel)
            except Exception:
                continue
        return "\n".join(matches[:50]) if matches else "No matches found."

    elif name == "read_file":
        filepath = (args.get("filepath") or "").strip()
        p = root / filepath
        if not p.exists() or not p.is_file():
            return f"Error: file '{filepath}' not found."
        if p.stat().st_size > 2 * 1024 * 1024:
            return f"Error: file '{filepath}' is too large to read in full."
        try:
            return p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return f"Error reading file: {e}"

    return f"Unknown tool: {name}"

def _parse_agent_final_output(raw_text: str) -> dict:
    """Parses the model's final (non-tool-call) answer into a create/edit action."""
    edit_match = re.search(r"EDIT_FILE:\s*(.+)", raw_text)
    new_match = re.search(r"FILENAME:\s*(.+)", raw_text)

    code = raw_text
    if "```" in raw_text:
        parts = raw_text.split("```")
        if len(parts) >= 2:
            block = parts[1]
            code = block.split("\n", 1)[1].strip() if "\n" in block else block.strip()

    if edit_match:
        return {"status": "success", "mode": "edit", "path": edit_match.group(1).strip(), "code": code.strip(), "raw": raw_text}
    elif new_match:
        return {"status": "success", "mode": "new", "path": new_match.group(1).strip(), "code": code.strip(), "raw": raw_text}
    return {"status": "success", "mode": "new", "path": "output.txt", "code": code.strip(), "raw": raw_text}

def run_agentic_loop(model_string: str, task: str, chat_history: list, ui_state: dict, workspace_root: str, max_iters: int = 6) -> dict:
    """Lets the model autonomously call search_directory / read_file before producing a final answer."""
    system_instruction = (
        "You are an autonomous coding agent working inside a real project. "
        "Use the search_directory and read_file tools to explore and understand the codebase BEFORE writing any code. "
        "Do not guess at file contents you have not read. Once you have enough context, stop calling tools and give your final answer.\n\n"
        "For a brand NEW file, start your final answer with a line 'FILENAME: <name.ext>' followed by a single code block "
        "containing the complete file.\n"
        "For an EDIT to an EXISTING file, start your final answer with a line 'EDIT_FILE: <relative/path.ext>' followed by "
        "a single code block containing the COMPLETE new contents of that file. NEVER use FILENAME: if repairing/editing an existing file!"
    )
    messages = [{"role": "system", "content": system_instruction}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": f"Task Spec:\n{task}"})

    mcp = get_mcp_client()
    tools = list(TOOL_SPECS)
    mcp_tools_cache = {}
    if mcp:
        for t in mcp.get_tools():
            mcp_tools_cache[t["name"]] = t
            tools.append({"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t.get("inputSchema", {})}})

    for i in range(max_iters):
        ui_state["status"] = f"[cyan]Agent reasoning (step {i + 1}/{max_iters})...[/cyan]"
        try:
            response = litellm.completion(model=model_string, messages=messages, tools=tools, tool_choice="auto")
            if hasattr(response, "usage") and response.usage:
                update_cost(model_string, response.usage.total_tokens)
        except Exception as e:
            return {"status": "failed", "error": str(e)}

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            return _parse_agent_final_output(msg.content or "")

        assistant_entry = {"role": "assistant", "content": msg.content or ""}
        try:
            assistant_entry["tool_calls"] = [tc.model_dump() for tc in tool_calls]
        except Exception:
            assistant_entry["tool_calls"] = [dict(tc) for tc in tool_calls]
        messages.append(assistant_entry)

        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments or "{}")
            except Exception:
                fn_args = {}
            ui_state["status"] = f"[yellow]🔧 Agent calling {fn_name}({fn_args})[/yellow]"
            
            if mcp and fn_name in mcp_tools_cache:
                res = mcp.call_tool(fn_name, fn_args)
                result = json.dumps(res)
            else:
                result = execute_tool_call(fn_name, fn_args, workspace_root)
                
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result[:4000]})

    return {"status": "failed", "error": f"Reached max tool iterations ({max_iters}) without a final answer."}

# ==========================================
# TASK CHECKLIST / PLAN
# ==========================================
def generate_plan(model_string: str, task: str) -> list:
    try:
        response = litellm.completion(
            model=model_string,
            messages=[
                {"role": "system", "content": "Break the coding task into 3-6 short, concrete steps. Output ONLY a numbered list, one step per line. No commentary."},
                {"role": "user", "content": task},
            ],
        )
        text = response.choices[0].message.content.strip()
        steps = [re.sub(r"^\d+[\.\)]\s*", "", ln).strip() for ln in text.split("\n") if ln.strip()]
        return steps[:6]
    except Exception:
        return []

def render_checklist(plan: list) -> str:
    if not plan:
        return "[dim]No plan generated.[/dim]"
    return "\n".join(f"[cyan]{i + 1}.[/cyan] {step}" for i, step in enumerate(plan))

# ==========================================
# SESSION PERSISTENCE & APPROVAL MODES
# ==========================================
def get_session_path() -> pathlib.Path:
    return pathlib.Path.cwd() / ".hypercode_session.json"

def load_session() -> dict:
    p = get_session_path()
    if p.exists():
        try:
            data = json.loads(p.read_text())
            data.setdefault("chat_history", [])
            data.setdefault("mode", "suggest")
            return data
        except Exception:
            pass
    return {"chat_history": [], "mode": "suggest"}

def save_session(session: dict):
    try:
        history = session.get("chat_history", [])
        if len(history) > 10:
            try:
                config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
                model = config.get("models_selected", ["gemini/gemini-2.5-flash"])[0]
                res = litellm.completion(model=model, messages=history[:-5] + [{"role": "user", "content": "Summarize the key decisions and actions of the conversation so far."}])
                summary = res.choices[0].message.content
                session["chat_history"] = [{"role": "system", "content": f"Past context summary:\n{summary}"}] + history[-5:]
            except Exception:
                session["chat_history"] = history[-15:]
        get_session_path().write_text(json.dumps(session, indent=2))
    except Exception:
        pass

VALID_MODES = {"suggest", "auto", "full-auto"}

# ==========================================
# VOICE & PROMPT OPTIMIZER
# ==========================================
def listen_and_record():
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
    frames, recording = [], [True]
    def record():
        while recording[0]:
            try:
                frames.append(stream.read(1024, exception_on_overflow=False))
            except Exception:
                pass
    t = threading.Thread(target=record)
    _active_tasks.append(t)
    t.start()
    console.input("\n[bold red]🎙️ RECORDING (Press ENTER to stop)...[/bold red]")
    recording[0] = False
    t.join()
    stream.stop_stream(); stream.close(); p.terminate()
    audio_file = "vibe_voice.wav"
    with wave.open(audio_file, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(p.get_sample_size(pyaudio.paInt16)); wf.setframerate(44100)
        wf.writeframes(b''.join(frames))
    return audio_file

def optimize_task_spec(raw_task: str, is_voice: bool = False) -> str:
    console.print("[dim]✨ Optimizing prompt into a Senior Engineering Spec...[/dim]")
    if is_voice and "GEMINI_API_KEY" in os.environ:
        try:
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
            f = client.files.upload(file=raw_task)
            res = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[f, "Transcribe this audio, then rewrite it into a strict, highly technical software engineering specification."]
            )
            client.files.delete(name=f.name)
            return res.text.strip()
        except Exception:
            return raw_task
    else:
        try:
            config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
            optimizer_model = config.get("models_selected", ["gemini/gemini-2.5-flash"])[0]
            response = litellm.completion(
                model=optimizer_model,
                messages=[
                    {"role": "system", "content": "Rewrite the user's input into a highly technical software engineering spec. Output ONLY the spec."},
                    {"role": "user", "content": raw_task}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return raw_task

# ==========================================
# SANDBOX CORE (Racing Mode)
# ==========================================
def create_phantom_sandbox():
    temp_dir = tempfile.mkdtemp(prefix="vibe_phantom_")
    is_worktree = False
    try:
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
        if is_git.returncode == 0:
            subprocess.run(["git", "worktree", "add", "-d", temp_dir], check=True, capture_output=True)
            is_worktree = True
    except Exception:
        pass
    return temp_dir, is_worktree

def cleanup_sandbox(sandbox_path, is_worktree, success=False):
    if success:
        for item in pathlib.Path(sandbox_path).iterdir():
            if item.is_file():
                shutil.copy2(item, pathlib.Path.cwd() / item.name)
            elif item.is_dir() and item.name != ".git":
                dest = pathlib.Path.cwd() / item.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
    if is_worktree:
        subprocess.run(["git", "worktree", "remove", "-f", sandbox_path], capture_output=True)
    shutil.rmtree(sandbox_path, ignore_errors=True)

def run_model_thread(model_string: str, task: str, results_dict: dict, ui_state: dict, chat_history: list = None):
    start_time = time.time()
    try:
        system_instruction = (
            "You are an autonomous AI coding agent. "
            "For a NEW file, output exactly 'FILENAME: <name.ext>' on the first line. "
            "For an EDIT to an EXISTING file, output exactly 'EDIT_FILE: <name.ext>' on the first line. "
            "Then output the complete code inside a single markdown code block."
        )

        messages = [{"role": "system", "content": system_instruction}]
        if chat_history:
            messages.extend(chat_history)
        messages.append({"role": "user", "content": f"Task Spec:\n{task}"})

        api_base = "http://localhost:11434" if "ollama" in model_string else None

        response = litellm.completion(
            model=model_string,
            messages=messages,
            stream=True,
            api_base=api_base
        )

        raw_text = ""
        token_count = 0
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            raw_text += delta
            token_count += 1

            elapsed = int(time.time() - start_time)
            clean_preview = raw_text.replace("[", "\\[").replace("]", "\\]")[-150:]
            ui_state[model_string] = f"[yellow]Streaming ({token_count} tokens, {elapsed}s)...[/yellow]\n\n[dim]{clean_preview}[/dim]"

        extracted_filename = None
        edit_mode = "create"
        update_cost(model_string, token_count + len(task)//4)
        for line in raw_text.split('\n'):
            if line.startswith("FILENAME:"):
                extracted_filename = line.replace("FILENAME:", "").strip()
                break
            elif line.startswith("EDIT_FILE:"):
                extracted_filename = line.replace("EDIT_FILE:", "").strip()
                edit_mode = "edit"
                break

        code = raw_text
        if "```" in raw_text:
            parts = raw_text.split("```")
            if len(parts) >= 2:
                block = parts[1]
                if "\n" in block: code = block.split("\n", 1)[1].strip()
                else: code = block.strip()

        if not extracted_filename:
            extracted_filename = "output.html"
            if "def " in code or "import " in code or "print(" in code: extracted_filename = "python_script.py"
            elif "console.log" in code or "function(" in code: extracted_filename = "output.js"

        results_dict[model_string] = {"code": code.strip(), "filename": extracted_filename, "mode": edit_mode, "time": time.time() - start_time, "status": "success", "raw": raw_text}
    except Exception as e:
        results_dict[model_string] = {"status": "failed", "error": str(e)}

def visual_qa_audit(code: str) -> bool:
    if not ("<html" in code.lower() or "body {" in code.lower()): return True
    with open("temp_ui.html", "w") as f: f.write(code)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{subprocess.os.path.abspath('temp_ui.html')}")
            page.screenshot(path="vision_snap.png")
            browser.close()
        import urllib.request
        with open("vision_snap.png", "rb") as img:
            b64_img = base64.b64encode(img.read()).decode("utf-8")
        req_data = json.dumps({"model": "llama3.2-vision", "prompt": "Reply 'PASS' if styling looks correct.", "images": [b64_img], "stream": False}).encode()
        req = urllib.request.Request("http://localhost:11434/api/generate", data=req_data)
        result = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())["response"]
        return "PASS" in result
    except Exception: return True

def generate_layout(ui_state, active_models, extra_panel_text=None):
    layout = Layout()
    if extra_panel_text:
        layout.split_column(Layout(name="header", size=3), Layout(name="plan", size=8), Layout(name="race"), Layout(name="footer", size=6))
        layout["plan"].update(Panel(extra_panel_text, title="🗺️ Task Plan", border_style="magenta"))
    else:
        layout.split_column(Layout(name="header", size=3), Layout(name="race"), Layout(name="footer", size=6))

    race_panels = []
    colors = ["cyan", "magenta", "green", "yellow", "blue", "red", "white"]
    for i, m in enumerate(active_models):
        display_name = m.split("/")[-1] if "/" in m else m
        race_panels.append(Layout(Panel(ui_state.get(m, "Waiting..."), title=f"🖥️ {display_name}", border_style=colors[i % len(colors)])))
    layout["race"].split_row(*race_panels)

    title = "Hyper Code - Racing Engine" if len(active_models) > 1 else "Hyper Code - Autonomous Agent"
    layout["header"].update(Panel(f"[bold white]{title}[/bold white] | Type [cyan]/help[/cyan] to see features", style="on blue"))
    footer_text = ui_state.get("status", "System Idle")
    footer_text += f"\n[dim]💰 Session Cost: ${_total_usd_cost:.4f}[/dim]"
    layout["footer"].update(Panel(footer_text, title="🛡️ System Status", border_style="yellow"))
    return layout

# ==========================================
# DIFF REVIEW & SELF-HEAL TEST LOOP
# ==========================================
def render_diff_view(old_code: str, new_code: str, file_name: str):
    diff = list(difflib.unified_diff(old_code.splitlines(), new_code.splitlines(), fromfile="Original", tofile="Proposed", lineterm=""))
    diff_text = "\n".join(diff[2:]) if len(diff) > 2 else "\n".join(diff)
    if diff_text.strip():
        console.print("\n")
        console.print(Panel(Syntax(diff_text, "diff", theme="monokai", line_numbers=True), title=f"🛠️ Proposed Changes for {file_name}", border_style="cyan", padding=(1, 2)))
    else:
        console.print(f"[dim]No textual diff for {file_name} (likely a brand new file).[/dim]")
        console.print(Panel(Syntax(new_code, "python", theme="monokai", line_numbers=True), title=f"📄 New file: {file_name}", border_style="green"))

def purge_bytecode(workspace_root: str, path: str):
    p = pathlib.Path(workspace_root) / path
    if p.suffix == ".py":
        pycache_dir = p.parent / "__pycache__"
        if pycache_dir.exists() and pycache_dir.is_dir():
            for item in pycache_dir.glob(f"{p.stem}*.pyc"):
                try:
                    item.unlink()
                except Exception:
                    pass

def detect_test_command(workspace_root: str):
    root = pathlib.Path(workspace_root)
    try:
        if (root / "pytest.ini").exists() or (root / "conftest.py").exists() or list(root.rglob("test_*.py"))[:1]:
            return ["python3", "-B", "-m", "pytest", "-q"]
    except Exception:
        pass
    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text())
            if "test" in pkg.get("scripts", {}):
                return ["npm", "test", "--silent"]
        except Exception:
            pass
    if (root / "go.mod").exists():
        return ["go", "test", "./..."]
    return None

def run_self_heal_loop(model_string: str, path: str, code: str, workspace_root: str, ui_state: dict, max_iters: int = 3):
    """Writes the file, runs the detected test suite, and asks the model to fix failures, up to max_iters."""
    p = pathlib.Path(workspace_root) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code, encoding="utf-8")
    purge_bytecode(workspace_root, path)

    test_cmd = detect_test_command(workspace_root)
    if not test_cmd:
        return code, True, "No test suite detected in this project; skipping self-heal loop."

    for attempt in range(max_iters):
        session = load_session()
        if session.get("mode") in ["suggest", "auto"]:
            if not sys.stdin.isatty():
                return code, False, "Tests skipped (non-interactive mode)."
            else:
                ans = console.input(f"👉 Run shell command `{' '.join(test_cmd)}`? (y/n): ").strip().lower()
                if ans != 'y': return code, False, "Tests skipped by user."
                
        ui_state["status"] = f"[magenta]Running tests (attempt {attempt + 1}/{max_iters}): {' '.join(test_cmd)}[/magenta]"
        try:
            result = subprocess.run(test_cmd, cwd=workspace_root, capture_output=True, text=True, timeout=180)
        except Exception as e:
            return code, False, f"Could not run test command: {e}"

        if result.returncode == 0:
            return code, True, f"✅ Tests passed on attempt {attempt + 1}."

        failure_log = (result.stdout + "\n" + result.stderr).strip()[-3000:]
        ui_state["status"] = f"[red]Tests failed. Asking agent to fix (attempt {attempt + 1}/{max_iters})...[/red]"
        try:
            response = litellm.completion(
                model=model_string,
                messages=[
                    {"role": "system", "content": "You are fixing a failing test suite. Output the COMPLETE corrected file contents in a single code block and nothing else."},
                    {"role": "user", "content": f"File: {path}\n\nCurrent contents:\n```\n{code}\n```\n\nTest failure output:\n{failure_log}"},
                ],
            )
            raw = response.choices[0].message.content or ""
            new_code = raw
            if "```" in raw:
                parts = raw.split("```")
                if len(parts) >= 2:
                    block = parts[1]
                    new_code = block.split("\n", 1)[1].strip() if "\n" in block else block.strip()
            render_diff_view(code, new_code, path)
            code = new_code
            p.write_text(code, encoding="utf-8")
            purge_bytecode(workspace_root, path)
        except Exception as e:
            return code, False, f"Self-heal request failed: {e}"

    return code, False, f"⚠️ Tests still failing after {max_iters} attempts. Left the last attempt in place for you to inspect."

# ==========================================
# AUTO-DEBUGGER & CRASH HANDLER (unchanged behavior)
# ==========================================
def run_with_ai_wrapper(command_to_run):
    console.print("\n" + "━" * 60, style="dim")
    cmd_str = " ".join(str(x) for x in command_to_run)
    console.print(f"⚡ [bold cyan]EXECUTING:[/bold cyan] [bold white]{cmd_str}[/bold white]")
    console.print("━" * 60, style="dim")

    process = subprocess.run(command_to_run, capture_output=True, text=True)
    if process.stdout:
        console.print("\n[bold green]OUTPUT:[/bold green]")
        console.print(Panel(process.stdout.strip(), border_style="green", padding=(0, 1)))

    if process.returncode != 0:
        error_message = process.stderr.strip()
        console.print("\n")
        console.print(Panel(Syntax(error_message, "pytb", theme="ansi_dark"), title="🚨 CRASH DETECTED", border_style="bold red", padding=(1, 2)))

        target_file = None
        for arg in command_to_run:
            if arg.endswith(".py") and arg != "hyper_code.py":
                target_file = arg
                break

        if not target_file: return

        console.print("\n🧠 [bold yellow]Hyper Code analyzing stack trace...[/bold yellow]")
        try:
            config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
            optimizer_model = config.get("models_selected", ["gemini/gemini-2.5-flash"])[0]

            prompt = (
                f"Error Stack Trace:\n{error_message}\n\n"
                f"Task:\n1. Summarize root cause in 1 sentence.\n"
                f"2. Output FILE_TARGET: {target_file}\n"
                f"3. Provide COMPLETE corrected Python code inside a ```python block."
            )
            response = litellm.completion(model=optimizer_model, messages=[{"role": "user", "content": prompt}])
            ai_response = response.choices[0].message.content

            blocks = ai_response.split("FILE_TARGET:")
            explanation = blocks[0].strip().split("```")[0].strip()
            console.print(Panel(explanation, title="💡 AI DIAGNOSIS", border_style="green", padding=(1, 2)))

            if len(blocks) > 1:
                block = blocks[1]
                code_match = re.search(r"```(?:python)?\n(.*?)```", block, re.DOTALL)
                if code_match:
                    fixed_code = code_match.group(1).strip()
                    p = pathlib.Path(target_file)
                    old_code = p.read_text(encoding="utf-8") if p.exists() else ""
                    render_diff_view(old_code, fixed_code, target_file)

                    choice = console.input(f"👉 Apply changes to '{target_file}'? (y/n): ").strip().lower()
                    if choice == "y":
                        shutil.copyfile(target_file, f"{target_file}.bak")
                        p.write_text(fixed_code, encoding="utf-8")
                        console.print(f"\n✅ Updated '{target_file}'! Re-running...\n")
                        run_with_ai_wrapper(command_to_run)
        except Exception as e:
            console.print(f"\nAPI Request Failed: {e}")

# ==========================================
# PER-PROJECT MODEL CONFIG (different terminal, different project, different model)
# ==========================================
def get_project_config_path() -> pathlib.Path:
    return pathlib.Path.cwd() / ".hypercode" / "config.json"

def load_project_config() -> dict:
    p = get_project_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def save_project_config(cfg: dict):
    p = get_project_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))

def resolve_models(global_config: dict):
    """Project-local model choice wins over the global default. Lets each project
    (and therefore each terminal working in that project) use a different model,
    including a fully offline one, independently of every other open terminal."""
    project_cfg = load_project_config()
    if "models_selected" in project_cfg and project_cfg["models_selected"]:
        return project_cfg["models_selected"], True
    return global_config.get("models_selected", ["gemini/gemini-2.5-flash"]), False

def ensure_provider_keys(models: list):
    """Makes sure every provider needed by this project's models has a key available,
    prompting only if truly missing (never needed for local ollama models)."""
    global_cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    changed = False
    for m in models:
        provider = m.split("/")[0]
        if provider == "ollama":
            continue
        key_name = f"{provider.upper()}_API_KEY"
        if key_name in os.environ:
            continue
        if key_name in global_cfg:
            os.environ[key_name] = global_cfg[key_name]
            continue
        api_key = questionary.password(f"🔑 This project needs a {provider.capitalize()} API Key:").ask()
        if api_key:
            os.environ[key_name] = api_key.strip()
            global_cfg[key_name] = api_key.strip()
            changed = True
    if changed:
        CONFIG_FILE.write_text(json.dumps(global_cfg))

# ==========================================
# CROSS-TERMINAL SESSION REGISTRY
# ==========================================
def _pid_alive(pid_str: str) -> bool:
    if not str(pid_str).isdigit(): return False
    try:
        os.kill(int(pid_str), 0)
        return True
    except Exception:
        return False

def _read_registry() -> dict:
    HYPERCODE_HOME.mkdir(parents=True, exist_ok=True)
    if REGISTRY_FILE.exists():
        try:
            return json.loads(REGISTRY_FILE.read_text())
        except Exception:
            return {}
    return {}

def _write_registry(reg: dict):
    try:
        HYPERCODE_HOME.mkdir(parents=True, exist_ok=True)
        tmp = REGISTRY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=2))
        os.replace(tmp, REGISTRY_FILE)
    except Exception:
        pass

def register_session(models: list, mode: str):
    reg = _read_registry()
    reg = {pid: v for pid, v in reg.items() if _pid_alive(pid)}
    reg[str(os.getpid())] = {
        "project": str(pathlib.Path.cwd()),
        "models": models,
        "mode": mode,
        "status": "idle",
        "started_at": time.time(),
        "last_active": time.time(),
    }
    _write_registry(reg)

def update_session_status(status_text: str):
    reg = _read_registry()
    pid = str(os.getpid())
    if pid in reg:
        reg[pid]["status"] = status_text
        reg[pid]["last_active"] = time.time()
        _write_registry(reg)

def unregister_session():
    reg = _read_registry()
    if reg.pop(str(os.getpid()), None) is not None:
        _write_registry(reg)

def show_sessions_table():
    reg = _read_registry()
    live_reg = {pid: v for pid, v in reg.items() if _pid_alive(pid)}
    if live_reg != reg:
        _write_registry(live_reg)

    table = Table(title="🖥️ Active Hyper Code Sessions (this machine)", border_style="cyan", padding=(0, 1))
    table.add_column("PID", style="dim")
    table.add_column("Project", style="bold green")
    table.add_column("Model(s)", style="yellow")
    table.add_column("Mode", style="magenta")
    table.add_column("Status", style="white")
    table.add_column("Last Active", style="dim")

    if not live_reg:
        console.print("[dim]No other Hyper Code sessions detected on this machine.[/dim]")
        return

    for pid, info in sorted(live_reg.items(), key=lambda x: -x[1]["last_active"]):
        marker = " 👈 this terminal" if int(pid) == os.getpid() else ""
        idle_secs = int(time.time() - info["last_active"])
        model_names = ", ".join(m.split("/")[-1] for m in info.get("models", []))
        table.add_row(f"{pid}{marker}", info.get("project", "?"), model_names, info.get("mode", "?"), info.get("status", "?"), f"{idle_secs}s ago")

    console.print("\n")
    console.print(table)
    console.print("\n")

# ==========================================
# REPL INTERACTIVE LOOP
# ==========================================
def main():
    args = sys.argv[1:]

    cmd_name = os.path.basename(sys.argv[0])
    if cmd_name.endswith(".py"): cmd_name = "python3 " + cmd_name

    if args:
        if args[0].lower() == "reset":
            if CONFIG_FILE.exists(): CONFIG_FILE.unlink()
            console.print("🔄 Configuration reset.")
            return
        elif args[0].endswith(".py"):
            run_command = ["python3"] + args if args[0] != "python3" else args
            run_with_ai_wrapper(run_command)
            return

    config = setup_environment()
    models_to_race, project_override = resolve_models(config)
    ensure_provider_keys(models_to_race)
    is_race = len(models_to_race) > 1
    solo_model = models_to_race[0]
    workspace_root = str(pathlib.Path.cwd())

    session = load_session()
    chat_history = session.get("chat_history", [])
    precision_context = ""

    register_session(models_to_race, session["mode"])
    atexit.register(unregister_session)

    logo = r"""
[bold white] _                               [/bold white][bold bright_black]                 _      [/bold bright_black]
[bold white]| |__  _   _ _ __   ___ _ __   [/bold white][bold bright_black]  ___  ___    __| | ___ [/bold bright_black]
[bold white]| '_ \| | | | '_ \ / _ \ '__|  [/bold white][bold bright_black] / __|/ _ \  / _` |/ _ \[/bold bright_black]
[bold white]| | | | |_| | |_) |  __/ |     [/bold white][bold bright_black]| (__| (_) || (_| |  __/[/bold bright_black]
[bold white]|_| |_|\__, | .__/ \___|_|     [/bold white][bold bright_black] \___|\___/  \__,_|\___|[/bold bright_black]
[bold white]       |___/|_|                [/bold white][bold bright_black]                        [/bold bright_black]

[bold white]The open source AI coding agent[/bold white]
"""
    console.print(logo)
    console.print("[dim]━[/dim]" * 60)
    mode_label = "Racing Mode" if is_race else f"Solo Agent Mode ({solo_model.split('/')[-1]})"
    scope_label = "project-local model" if project_override else "global default model"
    console.print(Panel(
        f"✨ [bold green]Hyper Code Active[/bold green] — [yellow]{mode_label}[/yellow] ({scope_label}) — Approval: [yellow]{session['mode']}[/yellow]\n"
        f"Type your task, [cyan]voice[/cyan] to speak, [cyan]/ls[/cyan] to inspect workspace, [cyan]/add <file>[/cyan] to inject context,\n"
        f"[cyan]/mode <suggest|auto|full-auto>[/cyan] to set autonomy, [cyan]/model set <provider/model>[/cyan] to pin a model to THIS project,\n"
        f"[cyan]/sessions[/cyan] to see every Hyper Code terminal running on this machine right now, or [cyan]/features[/cyan] for all capabilities.",
        border_style="green"
    ))
    if chat_history:
        console.print(f"[dim]🧠 Resumed session with {len(chat_history)} prior message(s) from this folder.[/dim]")

    while True:
        raw_input_str = questionary.text(f"👉 {cmd_name}> ").ask()
        if not raw_input_str: continue

        command = raw_input_str.strip()
        if command.lower() in ["exit", "quit"]:
            save_session(session)
            unregister_session()
            console.print("👋 Exiting Hyper Code. Session saved. Goodbye!")
            sys.exit(0)

        if command.lower() in ["/features", "/help"]:
            show_feature_dashboard()
            continue

        if command.lower() == "/sessions":
            show_sessions_table()
            continue

        if command.startswith("/model"):
            parts = command.split(maxsplit=2)
            if len(parts) == 3 and parts[1] == "set":
                new_model = parts[2].strip()
                ensure_provider_keys([new_model])
                proj_cfg = load_project_config()
                proj_cfg["models_selected"] = [new_model]
                save_project_config(proj_cfg)
                models_to_race = [new_model]
                is_race = False
                solo_model = new_model
                register_session(models_to_race, session["mode"])
                console.print(f"✅ This project ([bold]{workspace_root}[/bold]) is now pinned to [bold cyan]{new_model}[/bold cyan].")
                console.print("[dim]Saved to ./.hypercode/config.json — other terminals in other projects are unaffected.[/dim]")
            else:
                console.print("[red]Usage: /model set <provider/model-name>[/red]  (e.g. /model set ollama/llama3.2)")
            continue

        if command.lower() == "/ls":
            precision_context += "\n" + handle_workspace_discovery()
            continue
            
        if command.lower() == "/deps":
            console.print("🔍 Scanning project for dependencies...")
            scan_dependencies(workspace_root)
            continue
            
        if command.lower() == "/preview":
            # Start local server or npm depending on project
            is_npm = (pathlib.Path(workspace_root) / "package.json").exists()
            if is_npm:
                console.print("🚀 Starting npm dev server in background...")
                subprocess.Popen(["npm", "run", "dev"], cwd=workspace_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                url = "http://localhost:3000"
                # Some react projects use 5173 for Vite or 3000 for React
            else:
                console.print("🚀 Starting Python HTTP server on port 8080...")
                subprocess.Popen([sys.executable, "-m", "http.server", "8080"], cwd=workspace_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                url = "http://localhost:8080"
            
            time.sleep(1.5)
            console.print(f"🌐 Opening {url} in your browser...")
            webbrowser.open(url)
            continue

        if command.startswith("/add "):
            target_file = command.replace("/add", "").strip()
            new_context = load_precision_context(target_file)
            if new_context: precision_context += "\n" + new_context
            continue

        if command.startswith("/mode"):
            parts = command.split()
            if len(parts) == 2 and parts[1] in VALID_MODES:
                session["mode"] = parts[1]
                save_session(session)
                register_session(models_to_race, session["mode"])
                console.print(f"✅ Approval mode set to [bold yellow]{parts[1]}[/bold yellow].")
            else:
                console.print(f"[red]Usage: /mode <{'|'.join(sorted(VALID_MODES))}>[/red]")
            continue

        if command.lower() == "/clear":
            chat_history.clear()
            session["chat_history"] = []
            save_session(session)
            console.print("🧹 Cleared conversation memory for this folder.")
            continue

        if command.lower() == "/undo":
            restore_snapshot(workspace_root)
            continue

        update_session_status(f"working: {raw_input_str[:60]}")

        if command.lower() == "voice":
            audio_file = listen_and_record()
            task_spec = optimize_task_spec(audio_file, is_voice=True)
            if os.path.exists(audio_file): os.remove(audio_file)
        else:
            task_spec = optimize_task_spec(raw_input_str, is_voice=False)

        if precision_context:
            task_spec = precision_context + "\n\nUser Request: " + task_spec
            precision_context = ""

        total_tokens = len(task_spec) // 4 + sum(len(msg.get("content", "")) // 4 for msg in chat_history)
        if total_tokens > 100000:
            console.print(f"[bold red]⚠️ Warning: Estimated context ({total_tokens} tokens) is very large and may exceed model limits![/bold red]")

        console.print(Panel(task_spec, title="🧠 Compiled Spec", border_style="green"))
        time.sleep(0.5)

        # ------------------------------------------------------------
        # RACING MODE — unchanged fast "generate a new file" flow
        # ------------------------------------------------------------
        if is_race:
            ui_state = {"status": "[cyan]Creating Sandbox...[/cyan]"}
            sandbox_path, is_worktree = create_phantom_sandbox()
            final_filename = ""
            results = {}

            with Live(generate_layout(ui_state, models_to_race), refresh_per_second=10) as live:
                ui_state["status"] = f"[cyan]Racing {len(models_to_race)} models...[/cyan]"

                with ThreadPoolExecutor(max_workers=len(models_to_race)) as executor:
                    for m in models_to_race:
                        ui_state[m] = "[yellow]Initializing API...[/yellow]"
                        executor.submit(run_model_thread, m, task_spec, results, ui_state, chat_history)

                    while len(results) < len(models_to_race):
                        for m, data in results.items():
                            if data["status"] == "success" and "Finished" not in ui_state[m]:
                                ui_state[m] = f"[green]Finished in {data['time']:.2f}s![/green]\nWaiting for QA..."
                            elif data["status"] == "failed" and "Failed" not in ui_state[m]:
                                ui_state[m] = f"[red]API Failed:[/red]\n{data['error'][:100]}"
                        live.update(generate_layout(ui_state, models_to_race))
                        time.sleep(0.1)

                ui_state["status"] = "[magenta]Task complete. QA Auditor inspecting code...[/magenta]"
                live.update(generate_layout(ui_state, models_to_race))

                successful_models = {k: v for k, v in results.items() if v["status"] == "success"}
                sorted_models = sorted(successful_models.items(), key=lambda x: x[1]["time"])

                winner_code, winner_name = None, None

                for m_string, data in sorted_models:
                    display_name = m_string.split("/")[-1]
                    if "<html" in data["code"].lower() or "body {" in data["code"].lower():
                        ui_state["status"] = "[magenta]Running Browser Visual Audit...[/magenta]"
                        live.update(generate_layout(ui_state, models_to_race))
                        audit_passed = visual_qa_audit(data["code"])
                    else:
                        audit_passed = True

                    if audit_passed:
                        winner_code, winner_name = data["code"], display_name
                        final_filename = data["filename"]
                        log_telemetry(m_string, data["time"])

                        chat_history.append({"role": "user", "content": task_spec})
                        chat_history.append({"role": "assistant", "content": data["raw"]})
                        break
                    else:
                        ui_state[m_string] = "[red]Failed Visual QA. Discarded.[/red]"

                if winner_code:
                    ui_state["status"] = f"[bold green]🏆 Winner: {winner_name}![/bold green]\nCode passed QA and is merged."
                    with open(f"{sandbox_path}/{final_filename}", "w") as f: f.write(winner_code)
                    cleanup_sandbox(sandbox_path, is_worktree, success=True)
                else:
                    ui_state["status"] = "[bold red]❌ All models failed QA. Destroying Sandbox.[/bold red]"
                    cleanup_sandbox(sandbox_path, is_worktree, success=False)

                live.update(generate_layout(ui_state, models_to_race))
                time.sleep(1.5)

            if final_filename:
                console.print(f"\n🎉 [bold green]SUCCESS![/bold green] Saved as: [bold cyan]{final_filename}[/bold cyan]")
                console.print(f"👉 Run it by typing: [bold]python3 {final_filename}[/bold]\n")

            session["chat_history"] = chat_history
            save_session(session)
            update_session_status("idle")
            continue

        # ------------------------------------------------------------
        # SOLO AGENT MODE — plan -> explore -> edit/create -> diff -> self-heal
        # ------------------------------------------------------------
        plan = generate_plan(solo_model, task_spec)
        console.print(Panel(render_checklist(plan), title="🗺️ Task Plan", border_style="magenta"))

        ui_state = {"status": "[cyan]Exploring workspace...[/cyan]"}
        with Live(generate_layout(ui_state, [solo_model]), refresh_per_second=8) as live:
            result = run_agentic_loop(solo_model, task_spec, chat_history, ui_state, workspace_root)
            live.update(generate_layout(ui_state, [solo_model]))
            time.sleep(0.3)

        if result["status"] != "success":
            console.print(f"[red]❌ Agent failed: {result.get('error', 'unknown error')}[/red]")
            update_session_status("idle")
            continue

        path, code, edit_mode = result["path"], result["code"], result["mode"]
        old_code = ""
        if edit_mode == "edit":
            existing_path = pathlib.Path(workspace_root) / path
            old_code = existing_path.read_text(encoding="utf-8", errors="ignore") if existing_path.exists() else ""
            render_diff_view(old_code, code, path)
        else:
            console.print(Panel(Syntax(code, "python", theme="monokai", line_numbers=True), title=f"📄 New file: {path}", border_style="green"))

        apply_change = True
        if session["mode"] == "suggest":
            if not sys.stdin.isatty():
                apply_change = False
                console.print("[yellow]Non-interactive terminal detected in suggest mode. Defaulting to deny.[/yellow]")
            else:
                apply_change = console.input(f"👉 Apply this change to '{path}'? (y/n): ").strip().lower() == "y"

        if not apply_change:
            console.print("❌ Discarded. Nothing was written.")
            update_session_status("idle")
            continue

        create_snapshot(workspace_root)
        ui_state2 = {"status": "[cyan]Applying change...[/cyan]"}
        with Live(generate_layout(ui_state2, [solo_model]), refresh_per_second=8) as live:
            final_code, tests_ok, msg = run_self_heal_loop(solo_model, path, code, workspace_root, ui_state2)
            live.update(generate_layout(ui_state2, [solo_model]))
            time.sleep(0.5)

        color = "green" if tests_ok else "yellow"
        console.print(f"[{color}]{msg}[/{color}]")
        console.print(f"✅ Saved to [bold cyan]{path}[/bold cyan]")

        chat_history.append({"role": "user", "content": task_spec})
        chat_history.append({"role": "assistant", "content": result["raw"]})
        session["chat_history"] = chat_history
        save_session(session)
        update_session_status("idle")

if __name__ == "__main__":
    main()
