
# Penzer

Penzer is a local AI terminal assistant that can reason through tasks, use tools, remember what it learns, and grow its own skillset over time.

It is designed for users who want an interactive shell agent that can help with coding, automation, investigation, and repeatable terminal workflows.

> Local-first, agentic, and surprisingly capable.

---

## ✨ Highlights

- **Autonomous task execution** — plan, act, observe, and retry with resumable state
- **Persistent memory** — retain useful context across sessions with episodic, semantic, and KV memory
- **Runtime skill growth** — create and reuse helper tools and generated skills
- **Terminal control** — run shell commands with approval-aware safety checks
- **Plugin support** — turn repeated workflows into reusable helpers at runtime
- **Reliability safeguards** — protect against malformed resume state, inconsistent phase transitions, and untrusted tool output

---

## 🚀 Quick start

### 1. Install Penzer

Clone the repository and run the installer:

```bash
git clone https://github.com/HuzaifaAshraf13/PENZER-CLI
cd PENZER-CLI
chmod +x setup.sh
./setup.sh
```

The installer sets up the project environment and installs a `penzer` launcher so you can run it from your terminal.

If the command is not found, restart your terminal or run:

```bash
source ~/.bashrc
```

### 2. Start Penzer

```bash
penzer
```

You should see the Penzer banner and a prompt like `>>>`.

### 3. Configure your model

You can use either a local model server or a cloud API.

### Option A: Cloud API

Create a `.env` file in the project root:

```bash
touch .env
```

Then add:

```env
LLM_API_KEY="your-api-key-here"
LLM_API_URL="https://api.openai.com/v1"
LLM_MODEL="gpt-4o-mini"
```

### Option B: Local model server

If you run a local server such as Ollama, llama.cpp, vLLM, or LM Studio, add:

```env
LOCAL_MODEL_ENABLED="true"
LOCAL_MODEL_PATH="/path/to/model.gguf"
```

If both cloud and local settings are present, Penzer will ask which one to use.

### 4. Set credentials from inside Penzer

You can also manage credentials directly inside the CLI:

```text
apikey show
apikey set <API_KEY> <URL>
apikey local <LOCAL_SERVER_URL>
```

Example:

```bash
apikey set your-api-key https://api.openai.com/v1
```

---

## ⌨️ Main commands inside Penzer

```text
help          Show help
clear         Clear the screen
plugins       List available runtime plugin tools
doctor        Show startup health diagnostics
state         Show current execution state
memory        Show saved facts and memory state
checkpoints   Show saved checkpoints
resume        Resume the last interrupted task
profile       Show or switch the current CLI profile
benchmark     Show a lightweight quality summary
exit          Exit Penzer
```

---

## 🧩 Plugin tools

Penzer can create and reuse lightweight plugin helpers at runtime.

To see what plugins are available:

```bash
plugins
```

Generated plugins are stored under `tools/plugins/`, and their registry is kept in `.penzer/plugin_registry.json`.

---

## 🗂️ Project structure

```text
PENZER-CLI/
├── cli.py                   # Main CLI entry point
├── config.py                # Runtime settings, profile defaults, and validation
├── setup.py                 # Package setup
├── setup.sh                 # Installer script
├── requirements.txt         # Python dependencies
├── .env                     # API credentials
├── agent/                   # Agent orchestration, prompts, and managers
├── session/                 # Memory, persistence, checkpoints, and history
├── tools/                   # Built-in tools, executor, and plugin support
├── tests/                   # Regression tests for runtime and safety guarantees
└── logs/                    # Runtime logs
```

---

## 🧠 How it works

1. You enter a task or goal.
2. Penzer reasons about the best next step.
3. It selects tools, skills, or plugins to act.
4. It executes, observes the result, and adapts.
5. It records progress and can persist a resumable snapshot for later recovery.
6. It stores useful lessons for later tasks.

```text
User goal → Reasoning → Tool use → Observation → Memory → Resume-safe state
```

---

## 🛠️ Troubleshooting

If `penzer` does not start, try:

```bash
source ~/.bashrc
penzer
```

If you want to validate the current setup, run:

```bash
penzer
# then type: doctor
```

If you want to reconfigure model access, use:

```bash
apikey show
apikey set <API_KEY> <URL>
```

---

## 👤 Author

Huzaifa Ashraf

---

## ⚙️ Installation

Clone the repository:

```bash
git clone https://github.com/HuzaifaAshraf13/PENZER-CLI
cd PENZER-CLI
```

### Recommended install (system-wide user install)

Run the installer:

```bash
chmod +x setup.sh
./setup.sh
```

This script updates package sources, installs required system dependencies, and installs Penzer in editable mode so the `penzer` command becomes available in your user environment.

If the command is not found immediately, restart your terminal or run:

```bash
source ~/.bashrc
```

### Local virtual environment install

If you prefer to keep everything inside the repository, use:

```bash
python -m venv env
source env/bin/activate  
pip install -r requirements.txt
pip install -e .
```

---

## ⚙️ Configuration

### Cloud API

Create a `.env` file in the project root:

```bash
touch .env
```

Add your API credentials:

```env
API_KEY="your-api-key-here"
URL="your-api-url-here"
```

### Local Server

If you want to run Penzer with a local model server (llama.cpp, vLLM, ollama, LM Studio):

Start your local server first, then add to `.env`:

```env
LOCAL_SERVER_URL="http://localhost:8000"
```

Common local server URLs:
- llama.cpp / vLLM: `http://localhost:8000`
- ollama: `http://localhost:11434`
- LM Studio: `http://localhost:1234`

If both are configured, Penzer will ask you to choose on startup:

```
Multiple model sources detected. Choose which to use:
  1. Local Server
  2. Cloud API
```

---

## ▶️ Running Penzer

After installation, start the CLI with:

```bash
penzer
```

Inside the REPL, you can use the following built-in commands:

```text
help          Show available commands
clear         Clear the screen
plugins       List available runtime plugin tools
apikey show   Show current API settings from .env
apikey set    Set cloud API credentials
apikey local  Set a local model server URL
update        Check for updates
exit          Exit Penzer
```

Example:

```bash
apikey set your-api-key https://api.openai.com/v1
```

---

# Plugin tools

Penzer can create and reuse lightweight plugin helpers at runtime. Use the built-in CLI command to inspect available plugins:

```bash
plugins
```

Generated plugins live under `tools/plugins/`, and the registry is persisted in `.penzer/plugin_registry.json`.

---

## 🔐 API key management

Penzer supports in-CLI API credential management via the `apikey` command. This updates the local `.env` file in the project root.

```bash
apikey show
apikey set <API_KEY> <URL>
apikey local <LOCAL_SERVER_URL>
```

Example:

```bash
apikey set mykey https://api.openai.com/v1
```

The command writes values into `.env` like:

```env
API_KEY="your-api-key-here"
URL="your-api-url-here"
```

Use `apikey show` to verify current credentials.

---

## 🧠 How It Works

```
User enters a task
        ↓
Penzer checks if a skill exists for this task
        ↓
If yes → load skill and execute
If no  → reason through it from scratch
        ↓
Validate execution succeeded
        ↓
Synthesize and store a new skill from what worked
        ↓
Next similar task → skill already exists
```

---

## 🗂️ Project Structure

```
PENZER-CLI/
├── cli.py                   # Main CLI entry point
├── setup.py                 # Package setup
├── setup.sh                 # Installation script
├── requirements.txt         # Dependencies
├── .env                     # API credentials
├── agent/
│   ├── agent.py            # Core agentic loop
│   ├── llm.py              # LLM interface
│   ├── system_prompts.py   # Agent prompts
│   ├── core.py             # MCP server initialization
│   └── skills/             # Self-built runtime skills
├── session/                 # Session management
├── tools/                   # Tool definitions
└── logs/                    # Operation logs
```

---

# Author

Huzaifa Ashraf

