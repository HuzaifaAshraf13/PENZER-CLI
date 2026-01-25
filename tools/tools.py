# tools/tools.py
# source env/bin/activate
import os
import json
import datetime
import subprocess
import requests
from bs4 import BeautifulSoup

from agent.core import mcp

# session tools
from agent.core import mcp, reme_app

@mcp.tool("mem_log_finding")
async def log_finding(workspace_id: str, finding: str, severity: str = "info"):
    """Log a finding to long-term memory."""
    async with reme_app as app:
        return await app.async_execute(
            "summary_task_memory",
            workspace_id=workspace_id,
            trajectories=[{"role": "assistant", "content": f"[{severity.upper()}] {finding}"}]
        )

# ======================================================================
#  NMAP — Real terminal execution
# ======================================================================
@mcp.tool()
def nmap_scan(target: str, args: str = "-sV -Pn") -> dict:
    """
    Run a real Nmap scan from terminal.
    """
    cmd = ["nmap"] + args.split() + [target]

    start = datetime.datetime.utcnow().isoformat()

    try:
        output = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT
        ).decode()
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
#  MSFCONSOLE — Real Metasploit execution via resource script
# ======================================================================
@mcp.tool()
def run_msfconsole(commands: list) -> dict:
    """
    Executes commands inside msfconsole using a temporary .rc script.
    """

    script_text = "\n".join(commands) + "\nexit\n"
    rc_path = "/tmp/pz_msf.rc"

    with open(rc_path, "w") as f:
        f.write(script_text)

    try:
        start = datetime.datetime.utcnow().isoformat()

        output = subprocess.check_output(
            ["msfconsole", "-r", rc_path],
            stderr=subprocess.STDOUT
        ).decode()

        end = datetime.datetime.utcnow().isoformat()

    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

    return {
        "status": "ok",
        "commands_run": commands,
        "started_at": start,
        "finished_at": end,
        "raw_output": output[:15000]   # prevent overflow
    }



# ======================================================================
#  GITHUB SEARCH — GitHub API (online)
# ======================================================================
@mcp.tool()
def search_github_repository(owner: str, repo: str, query: str) -> list:
    """
    Search code inside a GitHub repo using GitHub's code search API.
    """

    api_url = f"https://api.github.com/search/code?q={query}+repo:{owner}/{repo}"

    headers = {
        "Accept": "application/vnd.github.v3+json"
    }

    if "GITHUB_TOKEN" in os.environ:
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    response = requests.get(api_url, headers=headers)

    if response.status_code != 200:
        return [{
            "error": "GitHub API error",
            "status_code": response.status_code
        }]

    items = response.json().get("items", [])

    results = []
    for item in items:
        results.append({
            "path": item["path"],
            "url": item["html_url"],
            "score": item.get("score", 1.0)
        })

    return results



# ======================================================================
#  EXPLOIT DB SEARCH — Scrape website live
# ======================================================================
@mcp.tool()
def search_exploit_db(query: str, platform: str = "") -> list:
    """
    Scrape exploit-db.com search page live.
    """

    url = f"https://www.exploit-db.com/search?text={query}"

    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})

    if r.status_code != 200:
        return [{"error": "Exploit-DB unreachable"}]

    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")

    exploits = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue

        exploit_id = cols[0].text.strip()
        description = cols[2].text.strip()
        cve = cols[3].text.strip() or "None"
        author = cols[6].text.strip()

        exploits.append({
            "id": exploit_id,
            "description": description,
            "cve": cve,
            "platform": platform or "unknown",
            "author": author
        })

    return exploits



# ======================================================================
#  DEBUG — Show registered tools
# ======================================================================
@mcp.tool()
def list_registered_tools() -> list:
    return list(mcp.tools.keys())
