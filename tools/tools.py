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
import xml.etree.ElementTree as ET

@mcp.tool()
def nmap_scan(target: str, args: str) -> dict:
    """
    Pure execution tool.
    LLM decides ALL flags.
    Tool only runs nmap and parses XML.
    """

    cmd = ["nmap"] + args.split() + ["-oX", "-", target]
    start_dt = datetime.datetime.utcnow()

    try:
        xml_output = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT
        ).decode()
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "command": " ".join(cmd),
            "target": target,
            "args": args
        }

    end_dt = datetime.datetime.utcnow()

    # =====================
    # XML parsing (safe)
    # =====================
    try:
        root = ET.fromstring(xml_output)
    except ET.ParseError as e:
        return {
            "status": "error",
            "error": f"XML parse failed: {e}",
            "command": " ".join(cmd),
            "raw_xml": xml_output
        }

    parsed = {
        "ports": [],
        "os_matches": [],
        "scripts": []
    }

    # =====================
    # Ports
    # =====================
    for port in root.findall(".//port"):
        state_el = port.find("state")
        state = state_el.attrib.get("state", "unknown") if state_el is not None else "unknown"

        service = port.find("service")

        parsed["ports"].append({
            "port": int(port.attrib.get("portid", 0)),
            "protocol": port.attrib.get("protocol"),
            "state": state,
            "service": service.attrib.get("name") if service is not None else None,
            "product": service.attrib.get("product") if service is not None else None,
            "version": service.attrib.get("version") if service is not None else None,
            "extrainfo": service.attrib.get("extrainfo") if service is not None else None,
        })

    # =====================
    # OS detection
    # =====================
    for osmatch in root.findall(".//osmatch"):
        parsed["os_matches"].append({
            "name": osmatch.attrib.get("name"),
            "accuracy": int(osmatch.attrib.get("accuracy", 0))
        })

    parsed["os_matches"].sort(
        key=lambda x: x["accuracy"],
        reverse=True
    )

    # =====================
    # NSE scripts
    # =====================
    for script in root.findall(".//script"):
        tables = []
        for table in script.findall("table"):
            row = {}
            for elem in table:
                key = elem.attrib.get("key")
                if key:
                    row[key] = elem.text
            if row:
                tables.append(row)

        parsed["scripts"].append({
            "id": script.attrib.get("id"),
            "output": script.attrib.get("output"),
            "tables": tables or None
        })

    # =====================
    # Final response
    # =====================
    return {
        "status": "ok",
        "target": target,
        "args": args,
        "command": " ".join(cmd),
        "started_at": start_dt.isoformat() + "Z",
        "finished_at": end_dt.isoformat() + "Z",
        "duration_sec": (end_dt - start_dt).total_seconds(),
        "parsed": parsed,
        "raw_xml": xml_output
    }

# ======================================================================
#  MSFCONSOLE — Real Metasploit execution via resource script
# ======================================================================
import subprocess
import datetime
import tempfile
import os
import re

@mcp.tool()
def run_msfconsole(commands: list) -> dict:
    """
    Pure execution tool.
    LLM decides ALL commands.
    Tool runs msfconsole and returns best-effort parsed results.
    """

    script_text = "\n".join(commands) + "\nexit\n"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".rc") as f:
        f.write(script_text.encode())
        rc_path = f.name

    start_dt = datetime.datetime.utcnow()

    try:
        output = subprocess.check_output(
            ["msfconsole", "-q", "-r", rc_path],
            stderr=subprocess.STDOUT
        ).decode(errors="ignore")
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "commands_run": commands
        }
    finally:
        os.unlink(rc_path)

    end_dt = datetime.datetime.utcnow()

    parsed = {
        "sessions": [],
        "credentials": [],
        "loot": [],
        "findings": []
    }

    # sessions (best-effort, version-agnostic)
    for m in re.findall(r"^\s*(\d+)\s+(\S+)\s+(\S+)", output, re.MULTILINE):
        parsed["sessions"].append({
            "id": m[0],
            "type": m[1],
            "target": m[2]
        })

    # credentials
    for m in re.findall(r"Username:\s*(\S+).*?Password:\s*(\S+)", output, re.DOTALL):
        parsed["credentials"].append({
            "username": m[0],
            "password": m[1]
        })

    # loot paths
    for m in re.findall(r"Stored in:\s*(/.*)", output):
        parsed["loot"].append({"path": m})

    # generic positive findings
    for m in re.findall(r"\[\+\]\s+(.*)", output):
        parsed["findings"].append({"info": m})

    return {
        "status": "ok",
        "commands_run": commands,
        "started_at": start_dt.isoformat() + "Z",
        "finished_at": end_dt.isoformat() + "Z",
        "duration_sec": (end_dt - start_dt).total_seconds(),
        "parsed": parsed,
        "raw_output": output
    }

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
