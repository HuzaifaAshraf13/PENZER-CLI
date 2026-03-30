"""
Configuration and constants for PENZER-CLI
Centralized settings for the autonomous pentesting agent
"""

import os
from enum import Enum
from pathlib import Path

# ============================================================================
# PATHS
# ============================================================================
PROJECT_ROOT = Path(__file__).parent
LOGS_DIR = PROJECT_ROOT / "logs"
MODELS_DIR = PROJECT_ROOT / "model"
SESSION_DIR = PROJECT_ROOT / "session"
DATA_DIR = PROJECT_ROOT / "data"

# Ensure directories exist
LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ============================================================================
# LOGGING
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "penzer.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# ============================================================================
# AGENT SETTINGS
# ============================================================================
MAX_ITERATIONS = 10
REQUEST_TIMEOUT = 300  # seconds
LLM_TEMPERATURE = 0.7
LLM_MAX_TOKENS = 2048

# Memory
MEMORY_SHORT_TERM_MAX_ITEMS = 10
MEMORY_LONG_TERM_MAX_ITEMS = 50
AUTO_SAVE_MEMORY = True

# ============================================================================
# TERMINAL UI THEME
# ============================================================================
class Colors(str, Enum):
    """ANSI color codes for terminal output"""
    BRAND = "bold red"
    SUCCESS = "bold green"
    ERROR = "bold red"
    WARNING = "bold yellow"
    INFO = "bold cyan"
    HEADER = "bold magenta"
    DEBUG = "dim white"


class Spinners(str, Enum):
    """Available spinners for rich"""
    DEFAULT = "dots"
    DOTS_WAVES = "dots_waves"
    BOUNCING_BAR = "bouncing"
    DOTS_JUMPING = "dots_jumping"
    ARROW = "arrow"


# ============================================================================
# LLM / API SETTINGS
# ============================================================================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_URL = os.getenv("LLM_API_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "default")

# Local model settings
LOCAL_MODEL_PATH = str(MODELS_DIR / "qwen2.5-coder-3b-pentest-q4_k_m.gguf")
LOCAL_MODEL_ENABLED = os.getenv("LOCAL_MODEL_ENABLED", "true").lower() == "true"
LOCAL_MODEL_GPU_LAYERS = int(os.getenv("LOCAL_MODEL_GPU_LAYERS", "50"))
LOCAL_MODEL_THREADS = int(os.getenv("LOCAL_MODEL_THREADS", "4"))

# ============================================================================
# MCP SERVER SETTINGS
# ============================================================================
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "5000"))
MCP_TIMEOUT = 30

# ============================================================================
# SECURITY TOOLS CATEGORIES
# ============================================================================
SECURITY_TOOLS = {
    "network": {
        "tools": ["nmap", "netstat", "arp-scan", "ping", "fping", "masscan", "arping"],
        "description": "Network discovery and reconnaissance"
    },
    "vuln": {
        "tools": ["nessus", "openvas", "nikto", "metasploit", "burpsuite"],
        "description": "Vulnerability scanning and assessment"
    },
    "enum": {
        "tools": ["enum4linux", "ldapsearch", "rpcclient", "crackmapexec", "gobuster", "ffuf"],
        "description": "Service and user enumeration"
    },
    "system": {
        "tools": ["sudo", "grep", "awk", "sed", "find", "ls", "cat", "chmod"],
        "description": "System utilities"
    },
    "shells": {
        "tools": ["bash", "sh", "python", "python3", "perl"],
        "description": "Shell interpreters"
    },
    "access": {
        "tools": ["ssh", "telnet", "nc", "netcat"],
        "description": "Remote access tools"
    },
    "exploit": {
        "tools": ["msfvenom", "metasploit", "searchsploit"],
        "description": "Exploit generation and management"
    },
    "creds": {
        "tools": ["john", "hashcat", "hydra"],
        "description": "Credential cracking and bruteforce"
    },
    "misc": {
        "tools": ["curl", "wget", "zip", "tar", "base64"],
        "description": "Miscellaneous utilities"
    }
}

# ============================================================================
# PENTESTING PHASES
# ============================================================================
PENTESTING_PHASES = [
    "reconnaissance",
    "scanning",
    "enumeration",
    "vulnerability_analysis",
    "exploitation",
    "privilege_escalation",
    "post_exploitation",
    "reporting"
]

# ============================================================================
# API RATE LIMITS
# ============================================================================
GITHUB_API_RATE_LIMIT = 60  # per hour (unauthenticated)
EXPLOITDB_API_RATE_LIMIT = 100  # per hour

# ============================================================================
# DEFAULTS
# ============================================================================
DEFAULT_WORKSPACE = "pentest_1"
DEFAULT_TOOL_TIMEOUT = 300
DEFAULT_LLM_TIMEOUT = 60

# ============================================================================
# FEATURE FLAGS
# ============================================================================
ENABLE_MEMORY_PERSISTENCE = True
ENABLE_AUTO_SAVE = True
ENABLE_SKILL_FILTERING = False  # Full LLM autonomy by default
ENABLE_EXPERIMENTAL_FEATURES = False
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
