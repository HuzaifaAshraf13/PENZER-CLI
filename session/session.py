from agent.core import mcp, reme_app
import json

# ---------------- SHORT-TERM MEMORY ----------------
session_memory = {}

@mcp.tool("mem_get_short")
async def mem_get_short(workspace_id: str):
    """Retrieve all short-term memory for a workspace."""
    return session_memory.get(workspace_id, {})

@mcp.tool("mem_set_short")
async def mem_set_short(workspace_id: str, data: dict):
    """Set short-term memory with a dictionary of key-value pairs."""
    session_memory.setdefault(workspace_id, {}).update(data)
    return {"status": "success", "workspace_id": workspace_id, "updated_keys": list(data.keys())}

# ---------------- LONG-TERM MEMORY ----------------
@mcp.tool("mem_get_long")
async def mem_get_long(workspace_id: str):
    """Retrieve all long-term memory for a workspace."""
    res = await reme_app.async_execute(
        "retrieve_task_memory",
        workspace_id=workspace_id,
        query="Return ALL stored key-value memory for this workspace. Do not summarize."
    )
    return res.get("answer") or {}

@mcp.tool("mem_set_long")
async def mem_set_long(workspace_id: str, data: dict):
    """Store data to long-term memory with a dictionary of key-value pairs."""
    # Format trajectories from data dict
    trajectories = [{
        "role": "assistant",
        "content": json.dumps(data)
    }]
    return await reme_app.async_execute(
        "summary_task_memory",
        workspace_id=workspace_id,
        trajectories=trajectories
    )
