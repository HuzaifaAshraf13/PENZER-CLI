# Penzer-CLI

Penzer is a local cognitive shell built for AI-powered terminal workflows, reasoning tasks, and autonomous pentesting operations.

**Version:** 1.0 - User-Driven ReAct Loop with Skill-Guided Intelligence

---

# Key Features

* **User-Driven Loop**: Ask user to continue after each ReAct iteration (no forced iterations)
* **Skill-Guided Intelligence**: 10 pentesting skills across 5 phases (scan, enumerate, exploit, post-exploit, reporting)
* **Local Server Support**: Compatible with llama.cpp, vLLM, ollama, LM Studio, text-generation-webui
* **API Mode Support**: Google Generative AI, OpenAI, or custom API backends
* **Error Recovery**: Fallback commands when LLM fails - agent continues operating
* **ReAct Framework**: Reason → Act → Observe loop with tool orchestration
* **Async-Safe Prompts**: Non-blocking user input using event loop executors
* **Global CLI**: Editable installation with `penzer` command

---

# Installation

Clone repository:

```bash
git clone https://github.com/HuzaifaAshraf13/PENZER-CLI
cd PENZER-CLI
```

Run installer:

```bash
chmod +x setup.sh
./setup.sh
```

Or install manually:

```bash
pip install -r requirements.txt
pip install -e .
```

---

# Running Penzer

## Start Penzer Interactive Shell

```bash
penzer
```

If command does not work immediately:

```bash
source ~/.bashrc
# or
source env/bin/activate  # if using venv
```

Then run again:

```bash
penzer
```

## Interactive Usage

Once started, Penzer will:

1. Initialize the MCP server and load tools
2. Detect available models (Local Server or API)
3. Prompt you to select model if both available
4. Start interactive pentesting shell

**Usage Pattern:**
```
user> scan the network for active devices
[AGENT] → REASON → ACT → OBSERVE
[AGENT] Progress: Hosts: 1 | Services: 2 | Vulns: 0
[AGENT] Continue analyzing? (yes/no): yes
[AGENT] → REASON → ACT → OBSERVE
[AGENT] Continue analyzing? (yes/no): no
[RESULT] Final synthesis of findings
```

---

# Model Configuration

Penzer supports two ways to run LLM models: **Local Servers** and **Cloud APIs**.

## Cloud AI API Mode

Cloud AI APIs provide fastest performance and easiest setup.

## Create `.env`

Inside project root:

```bash
touch .env
```

## Add credentials

```env
API_KEY="YOUR_API_KEY"
URL="YOUR_API_URL"
```

## Example

```env
API_KEY="sk-xxxxxxxxxxxxxxxx"
URL="https://api.example.com/v1/chat/completions"
```

---

# Local Server Mode (Advanced)

Run Penzer with a local AI server like **llama.cpp**, **vLLM**, **ollama**, or **text-generation-webui**.

This allows you to use larger models that don't fit in memory as GGUF files, with full local control.

## Setup Instructions

### 1. Start Your Local Server

**Using llama.cpp server:**
```bash
./llama-server -m model.gguf -p 8000
```

**Using vLLM:**
```bash
python -m vllm.entrypoints.openai.api_server --model model_name --port 8000
```

**Using ollama:**
```bash
ollama serve
```

**Using LM Studio:**
- Download and install LM Studio
- Load a model and start the local server (default: http://localhost:1234)

**Using text-generation-webui:**
```bash
python server.py --listen 0.0.0.0 --port 5000
```

### 2. Configure `.env`

Add your local server URL:

```env
LOCAL_SERVER_URL="http://localhost:8000"
```

**Common Server URLs:**
- llama.cpp: `http://localhost:8000`
- vLLM: `http://localhost:8000`
- ollama: `http://localhost:11434`
- LM Studio: `http://localhost:1234`
- text-generation-webui: `http://localhost:5000`

### 3. Start Penzer

```bash
penzer
```

Penzer will automatically detect `LOCAL_SERVER_URL` and use it for inference.

## Benefits of Local Server Mode

- ✓ Use very large models (70B, 120B+) with offloading
- ✓ Full local control and privacy
- ✓ Compatible with OpenAI-compatible API servers
- ✓ Flexible model switching without restart
- ✓ Faster inference than traditional GGUF loading

---

# Model Selection Priority

When starting Penzer, if multiple sources are available, you'll be prompted:

```
Multiple model sources detected. Choose which to use:
  1. Local Server (llama.cpp, vLLM, ollama, LM Studio, etc)
  2. Cloud AI API
```

Select based on your needs:
- **Option 1**: Full local control, no external services
- **Option 2**: Quickest setup, no local resources needed

---

# Recommended Models

For best reasoning quality and tool accuracy:

**Recommended Models:**
* Qwen 2.5 Coder (7B-32B) - Excellent coding/tools
* DeepSeek R1 (14B-671B) - Superior reasoning
* Llama 3.1/3.2 (70B+) - Strong performance
* Mistral Large (34B) - Good balance

**Recommended Sizes:**
* 7B (minimum for tools)
* 14B (good balance)
* 27B (recommended)
* 32B+ (best reasoning)

**Avoid:**
* 3B models (too small for reasoning)
* 8B models (limited tool accuracy)
* 9B models (inconsistent)

**Note:** Larger models work better with local servers (vLLM, ollama) for faster inference.

---

# Pentesting Skills System

Penzer includes 10 built-in pentesting skills organized by phase:

## Available Skills

**SCAN Phase (1 skill)**
- Host Discovery & Network Scanning: nmap, ping sweeps, port scanning

**ENUMERATION Phase (3 skills)**
- Service Enumeration: Banner grabbing, version detection
- Active Directory Enumeration: LDAP, Kerberos, SMB enumeration
- Web Application Enumeration: Directory discovery, technology identification

**EXPLOITATION Phase (2 skills)**
- Exploit Research: CVE lookup, PoC discovery via searchsploit
- Exploit Execution: Payload generation and delivery

**POST-EXPLOITATION Phase (3 skills)**
- Privilege Escalation: LinPEAS, WinPEAS, kernel exploits
- Lateral Movement: Pivoting, tunneling, persistence
- Data Extraction: Credential dumping, sensitive data exfiltration

**REPORTING Phase (1 skill)**
- Report Generation: Markdown/PDF report creation with findings

## How Skills Guide the Agent

1. User enters a pentesting request
2. Agent matches request keywords to relevant skills
3. Skills provide tactical guidance to the LLM
4. LLM generates reasoning and commands guided by skills
5. Agent executes commands and reports findings

Skills are matched automatically - no manual configuration needed.

---

# Project Structure

```
PENZER-CLI/
├── cli.py                    # Main CLI entry point
├── setup.py                  # Python package setup
├── setup.sh                  # Installation script
├── requirements.txt          # Python dependencies
├── README.md                 # This file
├── .env                      # API credentials (optional)
├── model/                    # GGUF models folder
├── agent/
│   ├── agent.py             # ReAct loop, user-driven control
│   ├── llm.py               # LLM interface (local server + API)
│   ├── system_prompts.py    # REASON, ACT, OBSERVE, SYNTHESIZE prompts
│   ├── core.py              # MCP server initialization
│   └── skills/              # 10 pentesting skills (5 phases)
├── session/                  # Session management
├── tools/                    # Tool definitions and execution
└── logs/                     # Agent operation logs
```

---

# Troubleshooting

## Local Server Not Connecting

Verify your local server is running and `.env` has correct `LOCAL_SERVER_URL`:

```env
LOCAL_SERVER_URL="http://localhost:8000"
```

Test the connection:

```bash
curl http://localhost:8000/v1/models
```

## API Not Working

Verify `.env` has correct API credentials:

```env
API_KEY="your-key-here"
URL="your-api-url-here"
```

---

# Developer Install

Editable install:

```bash
pip install -e .
```

---

# Author

Huzaifa Ashraf
