# tools/tools.py
"""
Core security tools and utilities for autonomous pentesting
Standardized tool interface with consistent error handling and logging
"""

import os
import json
import datetime
import subprocess
import requests
import shlex
import logging
from typing import Dict, Any, Optional

from bs4 import BeautifulSoup
import urllib.parse

from agent.core import mcp
from tools.standards import success, error, warning
from config import SECURITY_TOOLS, DEFAULT_TOOL_TIMEOUT

# Setup logging
logger = logging.getLogger(__name__)


@mcp.tool()
def check_available_tools(tool_category: str = "all") -> dict:
    """
    Check which security/penetration testing tools are available on the system.
    Use this to understand what capabilities you have before planning attacks.
    
    Args:
        tool_category: 'network', 'vuln', 'enum', 'system', etc., or 'all' (default: all)
    
    Returns:
        Standardized result with list of available tools and their paths
        Format: {"status": "success", "data": {"available_tools": {...}}}
    """
    try:
        logger.info(f"Checking available tools for category: {tool_category}")
        
        # Build tool list from config
        if tool_category == "all":
            tools = []
            for cat_data in SECURITY_TOOLS.values():
                tools.extend(cat_data.get("tools", []))
        else:
            cat_data = SECURITY_TOOLS.get(tool_category, {})
            tools = cat_data.get("tools", [])
        
        if not tools:
            return warning(
                data={"category": tool_category, "available_tools": {}, "count": 0},
                message=f"Unknown category: {tool_category}",
                metadata={"operation": "check_available_tools"}
            )
        
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
            except subprocess.TimeoutExpired:
                logger.debug(f"Timeout checking tool: {tool}")
            except Exception as e:
                logger.debug(f"Error checking tool {tool}: {e}")
        
        logger.info(f"Found {len(available)} available tools in category '{tool_category}'")
        
        return success(
            data={
                "category": tool_category,
                "available_tools": available,
                "count": len(available),
                "total_tools": len(tools)
            },
            metadata={"operation": "check_available_tools", "timestamp": datetime.datetime.now().isoformat()}
        )
    
    except Exception as e:
        logger.error(f"Error checking available tools: {e}")
        return error(f"Failed to check available tools: {str(e)}")


@mcp.tool()
def execute_system_command(command: str, timeout: int = 300) -> dict:
    """
    Execute any system command or penetration testing tool directly on the system.
    This gives you full control to run ANY command for reconnaissance, exploitation, etc.
    
    Examples:
    - Network scanning: "nmap -sV 192.168.1.0/24"
    - Port checking: "nc -zv target.com 22-80"
    - Enumeration: "enum4linux target.com"
    - Command execution: "whoami", "id", "pwd", "ls -la"
    - Data extraction: "find / -name '*.txt' -type f"
    
    Args:
        command: Shell command to execute (string)
        timeout: Max execution time in seconds (default 300)
    
    Returns:
        Standardized result with stdout, stderr, and exit code
        Format: {"status": "success"/"error"/"warning", "data": {"stdout": "...", "stderr": "...", "exit_code": 0}}
    """
    try:
        # Validate timeout
        if timeout <= 0:
            return error("Timeout must be positive")
        
        if timeout > 3600:  # Cap at 1 hour
            timeout = 3600
            logger.warning(f"Timeout capped at 3600 seconds")
        
        logger.info(f"Executing command: {command[:100]}")
        
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        # Truncate large outputs
        stdout = result.stdout[:10000] if result.stdout else ""
        stderr = result.stderr[:10000] if result.stderr else ""
        
        if result.returncode == 0:
            logger.info(f"Command succeeded: {command[:100]}")
            return success(
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": result.returncode
                },
                metadata={"command": command, "timeout": timeout}
            )
        else:
            # Non-zero exit code = warning, but include output
            logger.warning(f"Command exited with code {result.returncode}: {command[:100]}")
            return warning(
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": result.returncode
                },
                message=f"Command exited with code {result.returncode}",
                metadata={"command": command, "timeout": timeout}
            )
    
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout} seconds: {command[:100]}")
        return error(f"Command timed out after {timeout} seconds")
    
    except ValueError as e:
        logger.error(f"Invalid command format: {command}")
        return error(f"Invalid command format: {str(e)}")
    
    except Exception as e:
        logger.error(f"Command execution failed: {str(e)}")
        return error(f"Command execution failed: {str(e)}")

# ======================================================================
#  GITHUB SEARCH — GitHub API (online)
# ======================================================================

@mcp.tool()
def search_github_repository(owner: str, repo: str, query: str) -> dict:
    """
    Search code in a GitHub repository using the GitHub API.
    Agent decides search intent, tool queries GitHub API and returns results.
    
    Args:
        owner: Repository owner
        repo: Repository name
        query: Search query
    
    Returns:
        Standardized ToolResult with search results
    """
    try:
        if not owner or not repo or not query:
            return error("owner, repo, and query parameters are required")
        
        logger.info(f"Searching GitHub: {owner}/{repo} for '{query[:50]}'")
        
        q = f"{query} repo:{owner}/{repo}"
        encoded_q = urllib.parse.quote(q)
        
        api_url = f"https://api.github.com/search/code?q={encoded_q}&per_page=20"
        
        headers = {
            "Accept": "application/vnd.github+json"
        }
        
        if os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"
        
        resp = requests.get(api_url, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            error_msg = f"GitHub API error (HTTP {resp.status_code})"
            logger.error(f"{error_msg}: {resp.text[:200]}")
            return error(f"{error_msg}: {resp.text[:200]}")
        
        data = resp.json()
        
        results = []
        for item in data.get("items", []):
            results.append({
                "file": item.get("path"),
                "repo": item.get("repository", {}).get("full_name"),
                "url": item.get("html_url"),
                "score": item.get("score")
            })
        
        logger.info(f"GitHub search found {len(results)} results")
        
        return success(
            data={
                "query": query,
                "repository": f"{owner}/{repo}",
                "total_matches": data.get("total_count", 0),
                "results": results
            },
            metadata={"total_results": len(results)}
        )
    
    except requests.Timeout:
        logger.error("GitHub API request timed out")
        return error("GitHub API request timed out")
    
    except Exception as e:
        logger.error(f"GitHub search failed: {str(e)}")
        return error(f"GitHub search failed: {str(e)}")



# ======================================================================
#  EXPLOIT DB SEARCH — Scrape website live
# ======================================================================

@mcp.tool()
def search_exploit_db(query: str) -> dict:
    """
    Search exploits on Exploit-DB by scraping the website.
    Agent decides query intent, tool scrapes Exploit-DB and returns results.
    
    Args:
        query: Search query (CVE, keyword, platform, etc.)
    
    Returns:
        Standardized ToolResult with exploit data
    """
    try:
        if not query:
            return error("Query parameter is required")
        
        logger.info(f"Searching Exploit-DB for: {query[:50]}")
        
        encoded_query = urllib.parse.quote(query)
        url = f"https://www.exploit-db.com/search?text={encoded_query}"
        
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        
        if r.status_code != 200:
            error_msg = f"Exploit-DB unreachable (HTTP {r.status_code})"
            logger.error(error_msg)
            return error(error_msg)
        
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
                "cve": cols[3] if cols[3] else None,
                "platform": cols[4],
                "type": cols[5],
                "author": cols[6]
            })
        
        logger.info(f"Exploit-DB search found {len(exploits)} exploits")
        
        return success(
            data={
                "query": query,
                "total_results": len(exploits),
                "exploits": exploits
            },
            metadata={"parsed_results": len(exploits)}
        )
    
    except requests.Timeout:
        logger.error("Exploit-DB request timed out")
        return error("Exploit-DB request timed out")
    
    except BeautifulSoup as e:
        logger.error(f"Exploit-DB parsing failed: {str(e)}")
        return error(f"Exploit-DB parsing failed: {str(e)}")
    
    except Exception as e:
        logger.error(f"Exploit-DB search failed: {str(e)}")
        return error(f"Exploit-DB search failed: {str(e)}")


# ======================================================================
#  DEBUG — Show registered tools
# ======================================================================

@mcp.tool()
def list_registered_tools() -> dict:
    """
    Lists all registered MCP tools available to the agent.
    Useful for debugging and understanding available capabilities.
    
    Returns:
        Standardized ToolResult with tool list
    """
    try:
        logger.info("Listing registered MCP tools")
        
        # Access tools from the FastMCP tool manager
        if hasattr(mcp, '_tool_manager') and hasattr(mcp._tool_manager, '_tools'):
            tools_dict = mcp._tool_manager._tools
            tools_list = sorted(list(tools_dict.keys()))
            
            logger.info(f"Found {len(tools_list)} registered tools")
            
            return success(
                data={
                    "total_tools": len(tools_list),
                    "tools": tools_list
                },
                metadata={"operation": "list_registered_tools", "timestamp": datetime.datetime.now().isoformat()}
            )
        else:
            error_msg = "Tool manager not accessible"
            logger.error(error_msg)
            return error(error_msg)
    
    except Exception as e:
        logger.error(f"Failed to list tools: {str(e)}")
        return error(f"Failed to list tools: {str(e)}")

