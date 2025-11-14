# tools/tools.py

import os
import httpx
import subprocess
import shlex
import tempfile
from typing import List, Optional, Dict
from agent.server import mcp
# Ensure these prompt files are available in tools/ToolsPrompts.py
from tools.ToolsPrompts import (
    NMAP_SCAN_PROMPT, 
    RUN_MSFCONSOLE_COMMAND_PROMPT,
    GITHUB_SECURITY_POLICY_RESOURCE_PROMPT,
    SEARCH_EXPLOIT_DB_TOOL_PROMPT
)

# --- Configuration ---
# NOTE: Set this environment variable before running the server!
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# ----------------------------------------
# 1. Command-Line Execution Tools (Blocking)
# ----------------------------------------

@mcp.tool()
def nmap_scan(target: str, args: Optional[str] = "-sV -Pn", authorization: Optional[str] = None) -> str:
    """Executes an Nmap scan against a target with specified arguments."""
    _ = NMAP_SCAN_PROMPT  # reference prompt for LLM reasoning
    try:
        # Shlex.split safely handles command line arguments string
        cmd = ["nmap"] + shlex.split(args) + [target]
        
        # subprocess.run executes the command
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        # Return output, preferring stdout, then stderr, or an empty string
        return proc.stdout or proc.stderr or ""
    except Exception as e:
        return f"ERROR: Failed to run Nmap command: {e}"

@mcp.tool()
def run_msfconsole_command(commands: List[str], authorization: Optional[str] = None) -> str:
    """Executes a list of commands sequentially within the msfconsole shell."""
    _ = RUN_MSFCONSOLE_COMMAND_PROMPT
    cmdfile = None
    try:
        # 1. Write commands to a temporary file
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            for c in commands:
                tf.write(c.rstrip("\r\n") + "\n")
            cmdfile = tf.name # Store the path to the temporary file
        
        # 2. Run msfconsole using the temporary file as a resource script (-r)
        proc = subprocess.run(["msfconsole", "-q", "-r", cmdfile], capture_output=True, text=True, timeout=300)
        
        return proc.stdout or proc.stderr or ""
    except Exception as e:
        return f"ERROR: Failed to run msfconsole: {e}"
    finally:
        # 3. Clean up: Delete the temporary command file
        if cmdfile and os.path.exists(cmdfile):
            try: 
                os.remove(cmdfile)
            except Exception as cleanup_e: 
                print(f"Warning: Failed to clean up temp file {cmdfile}: {cleanup_e}")

# ----------------------------------------
# 2. Data Sources (Asynchronous)
# ----------------------------------------

@mcp.resource("git://security-policy")
async def get_security_policy() -> str:
    """
    Exposes the raw content of the SECURITY.md file from a specific GitHub repository 
    as a read-only context resource.
    """
    _ = GITHUB_SECURITY_POLICY_RESOURCE_PROMPT # reference prompt
    if not GITHUB_TOKEN:
        return "Error: GITHUB_TOKEN environment variable is not set."

    # >>> REPLACE with your actual GitHub repository details <<<
    url = "https://raw.githubusercontent.com/owner/repo/main/SECURITY.md"
    
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}", 
        "Accept": "application/vnd.github.v3.raw"
    }

    try:
        # Use httpx.AsyncClient for non-blocking HTTP requests
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status() # Raise exception for bad status codes
            return response.text
    except httpx.HTTPStatusError as e:
        return f"Error fetching GitHub resource: HTTP {e.response.status_code}. File/repo or token issue."
    except Exception as e:
        return f"An unexpected error occurred while fetching GitHub data: {e}"

@mcp.tool()
async def search_exploit_db(query: str, platform: str = "") -> List[Dict]:
    """
    Searches a conceptual Exploit Database API for exploits matching a query.
    This is an action (Tool) that the LLM can execute with parameters.
    """
    _ = SEARCH_EXPLOIT_DB_TOOL_PROMPT # reference prompt
    
    # --- MOCKUP LOGIC START ---
    # This section needs to be replaced with actual API calls to a public Exploit DB API 
    # or an internal service that wraps SearchSploit.
    if "wordpress" in query.lower():
        results = [
            {"id": 50000, "description": "WordPress Plugin X - SQL Injection", "cve": "CVE-2023-1234"},
        ]
    elif "windows" in platform.lower():
        results = [
            {"id": 49000, "description": "Windows LPE - Service Handle Abuse", "cve": "CVE-2022-9999"},
        ]
    else:
        results = [{"id": 0, "description": f"No exploits found matching '{query}'.", "cve": "N/A"}]
        
    return results
    # --- MOCKUP LOGIC END ---