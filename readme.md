
# Penzer

Penzer is a local cognitive shell built for AI-powered terminal workflows, autonomous reasoning, and self-evolving task execution.

---

# Features

- **Autonomous Agentic Loop** — Penzer reasons, acts, and observes in a continuous loop with iteration caps and retry logic
- **Self-Building Skills** — Penzer creates, stores, retrieves, and refines its own skills at runtime — no prebuilt skills
- **Memory Persistence** — context and learnings persist across sessions via the memory MCP tool
- **Terminal Control** — full system access via the terminal MCP tool with safety checks, validation, and rollback
- **Self-Evolution** — Penzer audits its own skill library, detects gaps, and autonomously builds new capabilities
- **Session Persistence** — sessions saved and resumed via `.penzer_session.json`
- **Streaming Output** — real-time output during execution

---

# Installation

Clone the repository:

```bash
git clone https://github.com/HuzaifaAshraf13/PENZER-CLI
cd PENZER-CLI
```

Run the installer:

```bash
chmod +x setup.sh
./setup.sh
```

Or manually set up a virtual environment:

```bash
python -m venv env
source env/bin/activate  # Windows: env\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

---

# Configuration

## Cloud API

Create a `.env` file in the project root:

```bash
touch .env
```

Add your API credentials:

```env
API_KEY="your-api-key-here"
URL="your-api-url-here"
```

## Local Server

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

# Running Penzer

```bash
penzer
```

If the command is not found:

```bash
source ~/.bashrc
penzer
```

---

# How It Works

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

# Project Structure

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

