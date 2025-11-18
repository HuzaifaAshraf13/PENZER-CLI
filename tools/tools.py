import os
import shlex
import tempfile
import subprocess
from typing import List, Optional, Dict

import httpx
from agent.server import mcp

# Prompts
from tools.ToolsPrompts import (
    NMAP_SCAN_PROMPT,
    RUN_MSFCONSOLE_COMMAND_PROMPT,
    SEARCH_GITHUB_TOOL_PROMPT,
    SEARCH_EXPLOIT_DB_TOOL_PROMPT
)

# Global config
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
EXPLOIT_DB_API = "https://www.exploit-db.com/api/search"


# ======================================================
# 1) NMAP SCAN TOOL
# ======================================================
@mcp.tool()
def nmap_scan(target: str, args: Optional[str] = "-sV -Pn") -> Dict:
    """Execute an Nmap scan with safe, structured output."""
    _ = NMAP_SCAN_PROMPT

    try:
        cmd = ["nmap"] + shlex.split(args) + [target]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        return {
            "status": "success",
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr
        }

    except Exception as e:
        return {"status": "error", "message": f"Nmap execution failed: {e}"}


# ======================================================
# 2) MSFCONSOLE NON‑INTERACTIVE TOOL
# ======================================================
@mcp.tool()
def run_msfconsole_command(commands: List[str]) -> Dict:
    """Runs msfconsole in scripted mode using a temp file."""
    _ = RUN_MSFCONSOLE_COMMAND_PROMPT
    cmdfile = None

    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            for c in commands:
                tf.write(c.rstrip("\n") + "\n")
            cmdfile = tf.name

        proc = subprocess.run(
            ["msfconsole", "-q", "-r", cmdfile],
            capture_output=True, text=True, timeout=300
        )

        return {
            "status": "success",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "commands_run": commands,
        }

    except Exception as e:
        return {"status": "error", "message": f"MSFconsole failed: {e}"}

    finally:
        if cmdfile and os.path.exists(cmdfile):
            try:
                os.remove(cmdfile)
            except:
                pass


# ======================================================
# 3) GITHUB SEARCH TOOL
# ======================================================
@mcp.tool()
async def search_github_repository(owner: str, repo: str, query: str) -> List[Dict]:
    """Search for code within a specific GitHub repository."""
    _ = SEARCH_GITHUB_TOOL_PROMPT

    if not GITHUB_TOKEN:
        return [{"error": "GITHUB_TOKEN environment variable is not set."}]

    url = f"https://api.github.com/search/code?q={query}+in:file+repo:{owner}/{repo}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return [
            {"path": i.get("path"), "url": i.get("html_url"), "score": i.get("score")}
            for i in data.get("items", [])
        ]

    except httpx.HTTPStatusError as e:
        return [{"error": f"HTTP Error {e.response.status_code} fetching GitHub data."}]
    except Exception as e:
        return [{"error": f"Unexpected error: {e}"}]


# ======================================================
# 4) EXPLOIT-DB SEARCH TOOL
# ======================================================
@mcp.tool()
async def search_exploit_db(query: str, platform: str = "") -> List[Dict]:
    """Query the official Exploit-DB JSON search API."""
    _ = SEARCH_EXPLOIT_DB_TOOL_PROMPT
    params = {"query": query}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(EXPLOIT_DB_API, params=params)
            resp.raise_for_status()
            data = resp.json()

        exploits = data.get("data", [])
        if platform:
            pl = platform.lower()
            exploits = [e for e in exploits if pl in (e.get("platform") or "").lower()]

        return [
            {
                "id": e.get("id"),
                "description": e.get("description") or e.get("title"),
                "cve": e.get("code") or e.get("cve") or "N/A",
                "platform": e.get("platform", "unknown"),
                "author": e.get("author", "unknown"),
            }
            for e in exploits
        ]

    except Exception as e:
        return [{"error": f"Exploit-DB query failed: {e}"}]
