from agent.core import mcp, reme_app
from tools.standards import success, error, warning
import json
import contextlib
import os
import sys

# Suppress ReMeApp verbose logging using file descriptor manipulation
@contextlib.contextmanager
def suppress_reme_logs():
    """Suppress ReMeApp's verbose logging output by redirecting stderr"""
    try:
        # Save original stderr file descriptor
        saved_stderr = os.dup(2)  # 2 is stderr
        
        # Open /dev/null
        devnull = os.open(os.devnull, os.O_WRONLY)
        
        # Redirect stderr to /dev/null
        os.dup2(devnull, 2)
        os.close(devnull)
        
        # Also update sys.stderr to be consistent
        sys.stderr = open(os.devnull, 'w')
        
        yield
    finally:
        # Restore stderr
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)
        # Reopen sys.stderr to the actual stderr
        sys.stderr = sys.__stderr__

# ---------------- SHORT-TERM MEMORY ----------------
session_memory = {}

@mcp.tool()
async def mem_get_short(workspace_id: str):
    """
    Retrieve short-term memory for the current workspace.
    Use this to recall recent findings, discovered information, etc.
    Short-term memory persists within the current session.
    
    Args:
        workspace_id: Workspace identifier (e.g., "pentest_1")
    
    Returns:
        {"status": "success", "data": {...}} containing all stored short-term memory
    """
    try:
        data = session_memory.get(workspace_id, {})
        return success(
            data=data,
            metadata={"workspace_id": workspace_id, "type": "short_term", "entries": len(data)}
        )
    except Exception as e:
        return error(f"Failed to get short-term memory: {str(e)}")

@mcp.tool()
async def mem_set_short(workspace_id: str, data: dict):
    """
    Store information to short-term memory for later recall.
    Use this to save discoveries, results, findings, etc.
    
    Args:
        workspace_id: Workspace identifier (e.g., "pentest_1")
        data: Dictionary of key-value pairs to store
    
    Returns:
        {"status": "success", "data": {...}} confirming storage
    """
    try:
        session_memory.setdefault(workspace_id, {}).update(data)
        return success(
            data={"workspace_id": workspace_id, "updated_keys": list(data.keys())},
            metadata={"type": "short_term", "keys_added": len(data)}
        )
    except Exception as e:
        return error(f"Failed to set short-term memory: {str(e)}")

# ---------------- LONG-TERM MEMORY ----------------
@mcp.tool()
async def mem_get_long(workspace_id: str):
    """
    Retrieve long-term memory for the workspace.
    This persists across sessions and stores consolidated findings.
    Use this for persistent knowledge about targets, vulnerabilities, etc.
    
    Args:
        workspace_id: Workspace identifier (e.g., "pentest_1")
    
    Returns:
        {"status": "success", "data": {...}} containing consolidated long-term memory
    """
    try:
        with suppress_reme_logs():
            res = await reme_app.async_execute(
                "retrieve_task_memory",
                workspace_id=workspace_id,
                query="Return ALL stored key-value memory for this workspace. Do not summarize."
            )
        data = res.get("answer") or {}
        return success(
            data=data,
            metadata={"workspace_id": workspace_id, "type": "long_term"}
        )
    except Exception as e:
        return error(f"Failed to get long-term memory: {str(e)}")

@mcp.tool()
async def mem_set_long(workspace_id: str, data: dict):
    """
    Store consolidated findings to long-term memory for persistent knowledge.
    Use this to save phase summaries, critical findings, etc.
    
    Args:
        workspace_id: Workspace identifier (e.g., "pentest_1")
        data: Dictionary of key-value pairs to persistently store
    
    Returns:
        {"status": "success", "data": {...}} confirming persistent storage
    """
    try:
        # Format trajectories from data dict
        trajectories = [{
            "role": "assistant",
            "content": json.dumps(data)
        }]
        with suppress_reme_logs():
            res = await reme_app.async_execute(
                "summary_task_memory",
                workspace_id=workspace_id,
                trajectories=trajectories
            )
        return success(
            data=res,
            metadata={"workspace_id": workspace_id, "type": "long_term", "keys_stored": len(data)}
        )
    except Exception as e:
        return error(f"Failed to set long-term memory: {str(e)}")

# ---------------- UNIFIED MEMORY SEARCH ----------------
@mcp.tool()
async def mem_search(workspace_id: str, query: str):
    """
    Search both short-term and long-term memory for relevant information.
    Use this to find previous findings, discoveries, or stored data.
    
    Args:
        workspace_id: Workspace identifier (e.g., "pentest_1")
        query: Search query/keywords to find in memory
    
    Returns:
        {"status": "success", "data": {"short_term": {...}, "long_term": {...}}} with matched results
    """
    try:
        combined_results = {
            "short_term": {},
            "long_term": {},
            "query": query
        }
        
        # Search short-term memory (keyword matching)
        short_mem = session_memory.get(workspace_id, {})
        query_lower = query.lower()
        for key, value in short_mem.items():
            if query_lower in key.lower() or query_lower in str(value).lower():
                combined_results["short_term"][key] = value
        
        # Search long-term memory
        try:
            with suppress_reme_logs():
                long_res = await reme_app.async_execute(
                    "retrieve_task_memory",
                    workspace_id=workspace_id,
                    query=query
                )
            combined_results["long_term"] = long_res.get("answer") or {}
        except Exception as e:
            combined_results["long_term"] = {"error": str(e)}
        
        return success(
            data=combined_results,
            metadata={
                "workspace_id": workspace_id,
                "query": query,
                "short_term_matches": len(combined_results["short_term"]),
                "long_term_matches": len(combined_results["long_term"])
            }
        )
    except Exception as e:
        return error(f"Failed to search memory: {str(e)}")

@mcp.tool()
async def mem_clear_short(workspace_id: str):
    """
    Clear all short-term memory for a workspace (reset session memory).
    
    Args:
        workspace_id: Workspace identifier (e.g., "pentest_1")
    
    Returns:
        {"status": "success"} or {"status": "warning"} if no memory to clear
    """
    try:
        if workspace_id in session_memory:
            count = len(session_memory[workspace_id])
            del session_memory[workspace_id]
            return success(
                data={"workspace_id": workspace_id, "cleared_entries": count},
                metadata={"type": "short_term"}
            )
        return warning(
            data={"workspace_id": workspace_id},
            metadata={"message": "No short-term memory found"}
        )
    except Exception as e:
        return error(f"Failed to clear short-term memory: {str(e)}")
