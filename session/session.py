# session/session.py
from agent.core import mcp, reme_app

# ---------------- SHORT-TERM MEMORY ----------------
session_memory = {}

@mcp.resource("pentest://{workspace_id}/session_short")
async def get_short_memory(workspace_id: str):
    """Return the short-term session memory for workspace."""
    return session_memory.get(workspace_id, {})

@mcp.tool("mem_set_short")
async def set_short_memory(workspace_id: str, key: str, value):
    """Set/update a short-term session memory key."""
    if workspace_id not in session_memory:
        session_memory[workspace_id] = {}
    session_memory[workspace_id][key] = value
    return True

# ---------------- LONG-TERM MEMORY ----------------
@mcp.resource("pentest://{workspace_id}/session_long")
async def get_long_memory(workspace_id: str):
    """Retrieve persistent long-term memory from ReMe."""
    async with reme_app as app:
        res = await app.async_execute(
            "retrieve_task_memory",
            workspace_id=workspace_id,
            query="Retrieve all persistent session memory."
        )
        return res.get("answer", {})

@mcp.tool("mem_set_long")
async def set_long_memory(workspace_id: str, key: str, value):
    """Update persistent memory in ReMe."""
    async with reme_app as app:
        return await app.async_execute(
            "summary_task_memory",
            workspace_id=workspace_id,
            trajectories=[{"role": "assistant", "content": f"{key}: {value}"}]
        )
