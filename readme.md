# Penzer-CLI

Penzer is a local cognitive shell built for AI-powered terminal workflows, reasoning tasks, and autonomous pentesting operations.

**Version:** 0.2.0 - User-Driven ReAct Loop with Skill-Guided Intelligence

---

# Key Features

* **User-Driven Loop**: Ask user to continue after each ReAct iteration (no forced iterations)
* **Skill-Guided Intelligence**: 10 pentesting skills across 5 phases (scan, enumerate, exploit, post-exploit, reporting)
* **Adaptive Token Configuration**: Auto-detects device RAM and optimizes token settings (512-2048 tokens)
* **Local GGUF Model Support**: Full offline operation with llama.cpp optimization
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
2. Detect available models (Local GGUF or API)
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

# API Mode 

API mode provides fastest performance and easiest setup.

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

# Local Model Mode (Offline)

Run Penzer fully offline using GGUF models with device-optimized settings.

## Create model folder

```bash
mkdir model
```

## Download model

```bash
wget -O model/model-name.gguf <MODEL_URL>
```

## Device-Optimized Token Configuration

Penzer automatically detects your device and configures tokens:

| Device RAM | Tokens | Context | Batch Size |
|-----------|--------|---------|-----------|
| < 8 GB    | 512    | 1024    | 128       |
| 8-16 GB   | 1024   | 2048    | 256       |
| 16-32 GB  | 1536   | 4096    | 512       |
| 32+ GB    | 2048   | 8192    | 1024      |

**No configuration needed** - Penzer detects and optimizes automatically!

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

**Note:** Smaller models work but may struggle with complex reasoning and tool usage. For pentesting tasks, 14B+ is strongly recommended.

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
│   ├── llm.py               # LLM interface (local + API)
│   ├── system_prompts.py    # REASON, ACT, OBSERVE, SYNTHESIZE prompts
│   ├── core.py              # MCP server initialization
│   └── skills/              # 10 pentesting skills (5 phases)
├── session/                  # Session management
├── tools/                    # Tool definitions and execution
└── logs/                     # Agent operation logs
```

---

# Local Model Placement

```bash
model/
│── your-model.gguf
```

Only `.gguf` files should be inside `model/`.

---

# Troubleshooting

## Model not loading

Verify:

```bash
model/
```

contains a valid `.gguf` model.

## API not working

Verify:

```bash
.env
```

exists and credentials are correct.

---

# Developer Install

Editable install:

```bash
pip install -e .
```

---

# Updating Penzer

For future updates, run:

penzer update

This pulls the latest version and refreshes Penzer.

# Author

Huzaifa Ashraf 
