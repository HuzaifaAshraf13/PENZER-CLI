# tools/tools.py (SIMPLIFIED)
"""
Core pentesting tools - SIMPLIFIED to use raw terminal access
All complex operations reduced to execute_system_command wrapper
This keeps the code clean and maintainable
"""

import os
import datetime
import subprocess
import shlex
import logging
from typing import Dict, Any

from agent.core import mcp
from tools.standards import success, error, warning

logger = logging.getLogger(__name__)


@mcp.tool()
def execute_system_command(command: str, timeout: int = 300) -> dict:
    """
    Execute ANY system command on the host machine.
    Full terminal access for all pentesting operations.
    
    This is the PRIMARY tool - use this for everything:
    - Network scanning: "nmap -sV 192.168.1.0/24"
    - Enumeration: "enum4linux target.com"
    - Port scanning: "nc -zv target 22-80"
    - Commands: "whoami", "id", "pwd", "ls -la"
    - Data extraction: "find / -name '*.txt' -type f"
    - System info: "uname -a", "ifconfig", "route -n"
    - File operations: "cat /etc/passwd", "grep -r password ."
    - Anything else: Any valid shell command
    
    Args:
        command: Shell command to execute (string) - single command, NOT piped
        timeout: Max execution time in seconds (default 300, max 3600)
    
    Returns:
        dict with structure:
        {
            "status": "success" | "warning" | "error",
            "data": {
                "stdout": "command output (up to 10KB)",
                "stderr": "error output (up to 10KB)",
                "exit_code": integer (0 = success),
                "command": "the command that was run"
            },
            "metadata": {
                "execution_time_ms": execution time,
                "output_truncated": boolean if output was cut off
            }
        }
        
    RESPONSE STRUCTURE:
    - SUCCESS (exit_code 0): {"status": "success", "data": {...}}
    - PARTIAL SUCCESS (exit_code 1-255): {"status": "warning", "data": {...}}
    - EXECUTION FAILURE: {"status": "error", "data": {"error": "message"}}
    """
    try:
        # Validate timeout
        if timeout <= 0:
            return error("Timeout must be positive")
        
        if timeout > 3600:
            timeout = 3600
            logger.warning(f"Timeout capped at 3600 seconds")
        
        logger.info(f"Executing: {command[:100]}")
        
        # Execute command
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        # Capture output (limit to 10KB each)
        stdout = result.stdout[:10000] if result.stdout else ""
        stderr = result.stderr[:10000] if result.stderr else ""
        
        if result.returncode == 0:
            logger.info(f"Command succeeded")
            return success(
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": result.returncode,
                    "command": command
                }
            )
        else:
            logger.warning(f"Command returned exit code {result.returncode}")
            return warning(
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": result.returncode,
                    "command": command
                },
                message=f"Command returned exit code {result.returncode}"
            )
    
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout}s")
        return error(f"Command execution timed out after {timeout} seconds")
    
    except Exception as e:
        logger.error(f"Command failed: {str(e)}")
        return error(f"Command execution failed: {str(e)}")


@mcp.tool()
def check_available_tools(tool_category: str = "all") -> dict:
    """
    Check which tools are available on the system.
    
    Useful for determining what pentesting tools are installed
    before executing commands that depend on them.
    
    Args:
        tool_category: 'network', 'enum', 'exploit', 'system', 'crypto', or 'all'
    
    Returns:
        dict with structure:
        {
            "status": "success",
            "data": {
                "available_tools": ["tool1", "tool2", ...],
                "available_paths": {"tool1": "/usr/bin/tool1", ...},
                "category": "network|enum|exploit|system|crypto",
                "total_available": count
            }
        }
    """
    try:
        logger.info(f"Checking available tools: {tool_category}")
        
        # Common pentesting tools
        all_tools = {
            "network": ["nmap", "netstat", "ifconfig", "ip", "ping", "traceroute", "nc", "telnet"],
            "enum": ["enum4linux", "ldapsearch", "crackmapexec", "smbclient", "nikto", "gobuster"],
            "exploit": ["metasploit", "searchsploit", "exploit-db", "sqlmap", "wfuzz"],
            "system": ["whoami", "id", "pwd", "ls", "cat", "grep", "find", "which", "sudo"],
            "crypto": ["hashcat", "john", "openssl"],
        }
        
        if tool_category == "all":
            tools_to_check = []
            for cat_tools in all_tools.values():
                tools_to_check.extend(cat_tools)
        else:
            tools_to_check = all_tools.get(tool_category, [])
        
        available = {}
        for tool in tools_to_check:
            try:
                result = subprocess.run(
                    ["which", tool],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    available[tool] = {
                        "status": "available",
                        "path": result.stdout.strip()
                    }
            except:
                pass
        
        logger.info(f"Found {len(available)} available tools")
        
        return success(
            data={
                "category": tool_category,
                "available_tools": available,
                "count": len(available)
            }
        )
    
    except Exception as e:
        logger.error(f"Error checking tools: {e}")
        return error(f"Failed to check tools: {str(e)}")


@mcp.tool()
def list_registered_tools() -> dict:
    """
    List all registered MCP tools available to the agent.
    
    Use this to see what tools the agent can call.
    
    Returns:
        dict with structure:
        {
            "status": "success",
            "data": {
                "tools": ["tool1", "tool2", ...],
                "count": total,
                "categories": {
                    "system": [...],
                    "memory": [...],
                    ...
                }
            }
        }
    """
    try:
        logger.info("Listing registered tools")
        
        if hasattr(mcp, '_tool_manager') and hasattr(mcp._tool_manager, '_tools'):
            tools_dict = mcp._tool_manager._tools
            tools_list = sorted(list(tools_dict.keys()))
            
            logger.info(f"Found {len(tools_list)} tools")
            
            return success(
                data={
                    "total_tools": len(tools_list),
                    "tools": tools_list
                }
            )
        else:
            return error("Tool manager not accessible")
    
    except Exception as e:
        logger.error(f"Failed to list tools: {e}")
        return error(f"Failed to list tools: {str(e)}")
