# tools/tools.py
# source env/bin/activate
import os
import json
import datetime
import subprocess
import requests
from bs4 import BeautifulSoup

from agent.core import mcp


import subprocess
import shlex

@mcp.tool()
def check_available_tools(tool_category: str = "network") -> dict:
    """
    Checks which security/network tools are available on the system.
    
    Args:
        tool_category: 'network', 'vuln', 'enum', or 'all'
    
    Returns:
        Dictionary with available tools and their versions
    """
    tools_to_check = {
        "network": ["nmap", "netstat", "arp-scan", "ping", "fping", "masscan"],
        "vuln": ["nessus", "openvas", "nikto", "metasploit"],
        "enum": ["enum4linux", "ldapsearch", "rpcclient"],
        "system": ["sudo", "grep", "awk", "sed"]
    }
    
    if tool_category == "all":
        tools = []
        for cat_tools in tools_to_check.values():
            tools.extend(cat_tools)
    else:
        tools = tools_to_check.get(tool_category, [])
    
    available = {}
    for tool in tools:
        try:
            result = subprocess.run(
                ["which", tool],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                available[tool] = {
                    "status": "available",
                    "path": path
                }
        except Exception:
            pass
    
    return {
        "status": "success",
        "category": tool_category,
        "available_tools": available,
        "count": len(available)
    }

@mcp.tool()
def execute_system_command(command: str, timeout: int = 300) -> dict:
    """
    Executes any pentesting or system command.
    The LLM should check for tool availability before running complex chains.
    
    Args:
        command: The command to execute (with sudo prefix if needed)
        timeout: Maximum execution time in seconds (default 300)
    """
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return {
            "status": "success" if result.returncode == 0 else "warning",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
# ======================================================================
#  GITHUB SEARCH — GitHub API (online)
# ======================================================================

import requests
import urllib.parse

@mcp.tool()
def search_github_repository(owner: str, repo: str, query: str) -> dict:
    """
    Pure execution tool.
    Agent decides search intent.
    Tool queries GitHub API and returns parsed results.
    """

    q = f"{query} repo:{owner}/{repo}"
    encoded_q = urllib.parse.quote(q)

    api_url = f"https://api.github.com/search/code?q={encoded_q}&per_page=20"

    headers = {
        "Accept": "application/vnd.github+json"
    }

    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"

    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if resp.status_code != 200:
        return {
            "status": "error",
            "error": "GitHub API error",
            "status_code": resp.status_code
        }

    data = resp.json()

    results = []
    for item in data.get("items", []):
        results.append({
            "file": item.get("path"),
            "repo": item.get("repository", {}).get("full_name"),
            "url": item.get("html_url"),
            "score": item.get("score")
        })

    return {
        "status": "ok",
        "query": query,
        "repository": f"{owner}/{repo}",
        "total_matches": data.get("total_count", 0),
        "results": results
    }


# ======================================================================
#  EXPLOIT DB SEARCH — Scrape website live
# ======================================================================
import requests
import urllib.parse
from bs4 import BeautifulSoup

@mcp.tool()
def search_exploit_db(query: str) -> dict:
    """
    Pure execution tool.
    Agent decides query intent.
    Tool scrapes Exploit-DB and returns parsed results.
    """

    encoded_query = urllib.parse.quote(query)
    url = f"https://www.exploit-db.com/search?text={encoded_query}"

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if r.status_code != 200:
        return {
            "status": "error",
            "error": "Exploit-DB unreachable",
            "status_code": r.status_code
        }

    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")

    exploits = []

    for row in rows:
        cols = [c.text.strip() for c in row.find_all("td")]
        if len(cols) < 7:
            continue

        exploits.append({
            "exploit_id": cols[0],
            "date": cols[1],
            "description": cols[2],
            "cve": cols[3] or None,
            "platform": cols[4],
            "type": cols[5],
            "author": cols[6]
        })

    return {
        "status": "ok",
        "query": query,
        "total_results": len(exploits),
        "exploits": exploits
    }


# ======================================================================
#  DEBUG — Show registered tools
# ======================================================================
@mcp.tool()
def list_registered_tools() -> list:
    return list(mcp.tools.keys())
