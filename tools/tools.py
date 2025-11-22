# tools/tools.py

import os
import json
import datetime
import subprocess
import requests
from bs4 import BeautifulSoup

from agent.core import mcp

from tools.ToolsPrompts import (
    NMAP_SCAN_PROMPT,
    RUN_MSFCONSOLE_COMMAND_PROMPT,
    SEARCH_GITHUB_TOOL_PROMPT,
    SEARCH_EXPLOIT_DB_TOOL_PROMPT
)


# ======================================================================
#  NMAP — Real terminal execution
# ======================================================================
@mcp.tool(prompt=NMAP_SCAN_PROMPT)
def nmap_scan(target: str, args: str = "-sV -Pn") -> dict:
    cmd = ["nmap"] + args.split() + [target]

    start = datetime.datetime.utcnow().isoformat()

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "target": target,
            "args": args
        }

    end = datetime.datetime.utcnow().isoformat()

    return {
        "status": "ok",
        "target": target,
        "args": args,
        "started_at": start,
        "finished_at": end,
        "raw_output": output,
    }




# ======================================================================
#  METASPLOIT — Real msfconsole script execution
# ======================================================================
@mcp.tool(prompt=RUN_MSFCONSOLE_COMMAND_PROMPT)
def run_msfconsole(commands: list) -> dict:
    """
    Executes commands like:
      ['use auxiliary/scanner/ssh/ssh_version', 'set RHOSTS 10.0.0.0/24', 'run']
    """

    script = "\n".join(commands) + "\nexit\n"

    temp_path = "/tmp/pz_msf.rc"
    with open(temp_path, "w") as f:
        f.write(script)

    try:
        start = datetime.datetime.utcnow().isoformat()
        output = subprocess.check_output(
            ["msfconsole", "-r", temp_path],
            stderr=subprocess.STDOUT
        ).decode()
        end = datetime.datetime.utcnow().isoformat()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    return {
        "status": "ok",
        "commands_run": commands,
        "started_at": start,
        "finished_at": end,
        "raw_output": output[:15000]  # truncated
    }




# ======================================================================
#  GITHUB SEARCH — Real Online GitHub API Search
# ======================================================================
@mcp.tool(prompt=SEARCH_GITHUB_TOOL_PROMPT)
def search_github_repository(owner: str, repo: str, query: str) -> list:
    url = f"https://api.github.com/search/code?q={query}+repo:{owner}/{repo}"

    headers = {"Accept": "application/vnd.github.v3+json"}

    if "GITHUB_TOKEN" in os.environ:
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        return [{"error": "GitHub API error", "status_code": r.status_code}]

    data = r.json().get("items", [])

    final = []
    for item in data:
        final.append({
            "path": item["path"],
            "url": item["html_url"],
            "score": item.get("score", 1.0)
        })

    return final




# ======================================================================
#  EXPLOIT-DB SEARCH — Scrape Live Website (REAL)
# ======================================================================
@mcp.tool(prompt=SEARCH_EXPLOIT_DB_TOOL_PROMPT)
def search_exploit_db(query: str, platform: str = "") -> list:

    url = f"https://www.exploit-db.com/search?text={query}"

    r = requests.get(url, headers={"User-Agent": "Mozilla"})

    if r.status_code != 200:
        return [{"error": "Exploit-DB website unreachable"}]

    soup = BeautifulSoup(r.text, "html.parser")

    rows = soup.select("table tbody tr")

    output = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue

        exploit_id = cols[0].text.strip()
        description = cols[2].text.strip()
        cve = cols[3].text.strip() or "None"
        author = cols[6].text.strip()

        output.append({
            "id": exploit_id,
            "description": description,
            "cve": cve,
            "platform": platform or "unknown",
            "author": author
        })

    return output




# ======================================================================
#  DEBUG TOOL
# ======================================================================
@mcp.tool()
def list_registered_tools() -> list:
    return list(mcp.tools.keys())
