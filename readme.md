# Penzer CLI

Penzer CLI is a Linux-friendly terminal agent that helps you work from the shell. It can reason through tasks, use tools, remember what it learns, and keep working across sessions.

If you want an AI assistant for the terminal, this project is built for that. It works on Linux and gives you a simple `penzer` command to start it.

---

## What it does

- Runs as an interactive AI terminal assistant
- Uses local or cloud LLM settings
- Keeps memory across tasks
- Can use tools and plugins
- Prompts for credentials if nothing is configured yet
- Works well in Linux environments

---

## Install on Linux

### Install using the project setup script

```bash
git clone https://github.com/HuzaifaAshraf13/PENZER-CLI
cd PENZER-CLI
chmod +x setup.sh
./setup.sh
```

This creates the local virtual environment, installs the project, and adds the `penzer` launcher to your user bin directory.

Then run:

```bash
penzer
```

### If `penzer` is not found

```bash
source ~/.bashrc
penzer
```

If the command still does not appear, run:

```bash
export PATH="$HOME/.local/bin:$PATH"
penzer
```

---

## First run

When you start Penzer for the first time, it checks whether you already configured a model.

If not, it asks you to choose one of these:

1. Local server URL
2. API key + API URL
3. Exit

Example:

```text
LLM configuration required.
Choose how you want to configure Penzer:
  1 Use a local server URL
  2 Enter API key and API URL
  3 Exit
```

This is much easier for new users than failing with a crash.

---

## Configure with a cloud API

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

---

## Configure with a local model

If you have a local OpenAI-compatible server running, add:

```env
LOCAL_SERVER_URL="http://localhost:8000"
```

Common examples:

- llama.cpp / vLLM: `http://localhost:8000`
- Ollama: `http://localhost:11434`
- LM Studio: `http://localhost:1234`

---

## In-app credential commands

Inside Penzer, you can also configure credentials with:

```text
apikey show
apikey set <LLM_API_KEY> <LLM_API_URL>
apikey local <LOCAL_SERVER_URL>
```

Example:

```bash
apikey set my-key https://api.openai.com/v1
apikey local http://localhost:8000
```

---

## Main commands

```text
help          Show available commands
clear         Clear the terminal screen
plugins       List available plugin tools
doctor        Show startup health diagnostics
state         Show current execution state
memory        Show saved facts and memory state
checkpoints   Show saved checkpoints
resume        Resume the last interrupted task
update        Check for updates
exit          Exit Penzer
```

---

## Project structure

```text
PENZER-CLI/
├── cli.py                  # Main CLI entry point
├── config.py               # Settings and validation
├── version.py              # Version info and update helpers
├── setup.py                # Setup metadata
├── requirements.txt        # Python dependencies
├── .env                    # Local runtime config
├── agent/                  # Agent logic and LLM flow
├── session/                # Memory and state history
├── tools/                  # Tools and plugins
├── logs/                   # Log files
├── tests/                  # Regression tests
└── env/                    # Local virtual environment
```

---

## Troubleshooting

If `penzer` is not found:

```bash
export PATH="$HOME/.local/bin:$PATH"
penzer
```

If you are working from the repo itself:

```bash
source env/bin/activate
penzer
```

If you want to check startup health inside the app:

```bash
penzer
# then type: doctor
```

---

## Notes

- The project is built for Linux use.
- It supports both local and API-based model configuration.
- It now asks the user for credentials instead of crashing when no config exists.
- The supported Linux install method is the project `setup.sh` script.

---

## Author

Huzaifa Ashraf

