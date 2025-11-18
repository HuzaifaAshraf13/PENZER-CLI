# tools/tools.py (FIXED: Importing mcp from agent.core)

import os
import json
import datetime

# ======================================================================
#  CRITICAL FIX: Import mcp from the dedicated core module
# ======================================================================
from agent.core import mcp
# ======================================================================

# ======================================================================
#  PROMPT IMPORT
# ======================================================================
# Importing the prompt constants from the separate file
from tools.ToolsPrompts import (
    NMAP_SCAN_PROMPT,
    RUN_MSFCONSOLE_COMMAND_PROMPT,
    SEARCH_GITHUB_TOOL_PROMPT,
    SEARCH_EXPLOIT_DB_TOOL_PROMPT
)

# ======================================================================
#  NMAP TOOL — placeholder (NO external execution)
# ======================================================================
@mcp.tool()
def nmap_scan(target: str, args: str = "-sV -Pn", authorization: str = "", requester_id: str = "", reason: str = "") -> dict:
    """Mock Nmap scan (no real nmap execution)."""
    return {
        "status": "mock",
        "reason": "Nmap execution disabled. Returning placeholder.",
        "target": target,
        "args": args,
        "started_at": datetime.datetime.utcnow().isoformat(),
        "finished_at": datetime.datetime.utcnow().isoformat(),
        "raw_output": "<mock output>",
        "summary": {
            "open_ports": [22, 80],
            "services": ["ssh", "http"]
        }
    }

# ======================================================================
#  MSFCONSOLE TOOL — placeholder
# ======================================================================
@mcp.tool()
def run_msfconsole(commands: list, authorization: str = "", target_list: list = None,
                   requester_id: str = "", reason: str = "") -> dict:
    """Mock MSF execution."""
    return {
        "status": "mock",
        "commands_run": commands,
        "per_command": [
            {"command": cmd, "status": "mock", "output_snippet": "<mock output>"}
            for cmd in commands
        ],
        "started_at": datetime.datetime.utcnow().isoformat(),
        "finished_at": datetime.datetime.utcnow().isoformat(),
    }

# ======================================================================
#  SIMPLE LOCAL SEARCH UTIL
# ======================================================================
def search_in_files(base_dir: str, query: str):
    """Search inside local text/code files."""
    results = []

    if not os.path.isdir(base_dir):
        return results

    for root, dirs, files in os.walk(base_dir):
        for file in files:
            filepath = os.path.join(root, file)

            # only check text-like files
            try:
                with open(filepath, "r", errors="ignore") as f:
                    text = f.read()

                if query.lower() in text.lower():
                    results.append({
                        "path": filepath.replace(base_dir + "/", ""),
                        "score": 1.0,
                        "snippet": text[:200]
                    })
            except:
                continue

    return results

# ======================================================================
#  GITHUB SEARCH TOOL (LOCAL DIRECTORY VERSION)
# ======================================================================
@mcp.tool()
def search_github_repository(owner: str, repo: str, query: str) -> list:
    """
    Searches a LOCAL repo folder:
        ./data/github/<owner>/<repo>/
    """
    base = f"data/github/{owner}/{repo}"

    results = search_in_files(base, query)

    return [
        {
            "path": r["path"],
            "url": f"https://github.com/{owner}/{repo}/blob/main/{r['path']}",
            "score": r["score"],
            "snippet": r["snippet"],
        }
        for r in results
    ]

# ======================================================================
#  EXPLOIT-DB SEARCH TOOL (LOCAL VERSION)
# ======================================================================
@mcp.tool()
def search_exploit_db(query: str, platform: str = "") -> list:
    """
    Searches a LOCAL copy of exploit-db folder:
        ./data/exploit-db/
    Each exploit stored as:
        <id>.txt   or   <id>.json
    """
    base = "data/exploit-db"

    raw = search_in_files(base, query)

    final = []
    for r in raw:
        exploit_id = os.path.splitext(os.path.basename(r["path"]))[0]
        final.append({
            "id": exploit_id,
            "description": f"Match found in {r['path']}",
            "cve": "Unknown",
            "platform": platform or "unknown",
            "author": "Local-DB",
            "snippet": r["snippet"],
        })

    return final

# ======================================================================
#  DEBUG TOOL — list what is registered
# ======================================================================
@mcp.tool()
def list_registered_tools() -> list:
    return list(mcp.tools.keys())