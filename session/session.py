from agent.core import mcp, reme_app
from tools.standards import success, error, warning
import json

# ---------------- SHORT-TERM MEMORY ----------------
session_memory = {}

@mcp.tool("mem_get_short")
async def mem_get_short(workspace_id: str):
    """Retrieve all short-term memory for a workspace."""
    try:
        data = session_memory.get(workspace_id, {})
        return success(
            data=data,
            metadata={"workspace_id": workspace_id, "type": "short_term", "entries": len(data)}
        )
    except Exception as e:
        return error(f"Failed to get short-term memory: {str(e)}")

@mcp.tool("mem_set_short")
async def mem_set_short(workspace_id: str, data: dict):
    """Set short-term memory with a dictionary of key-value pairs."""
    try:
        session_memory.setdefault(workspace_id, {}).update(data)
        return success(
            data={"workspace_id": workspace_id, "updated_keys": list(data.keys())},
            metadata={"type": "short_term", "keys_added": len(data)}
        )
    except Exception as e:
        return error(f"Failed to set short-term memory: {str(e)}")

# ---------------- LONG-TERM MEMORY ----------------
@mcp.tool("mem_get_long")
async def mem_get_long(workspace_id: str):
    """Retrieve all long-term memory for a workspace."""
    try:
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

@mcp.tool("mem_set_long")
async def mem_set_long(workspace_id: str, data: dict):
    """Store data to long-term memory with a dictionary of key-value pairs."""
    try:
        # Format trajectories from data dict
        trajectories = [{
            "role": "assistant",
            "content": json.dumps(data)
        }]
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
