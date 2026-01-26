from agent.core import mcp, reme_app

# ---------------- SHORT-TERM MEMORY ----------------
session_memory = {}

@mcp.tool("mem_get_short")
async def mem_get_short(workspace_id: str):
    return session_memory.get(workspace_id, {})

@mcp.tool("mem_set_short")
async def mem_set_short(workspace_id: str, key: str, value: str):
    session_memory.setdefault(workspace_id, {})[key] = value
    return True

# ---------------- LONG-TERM MEMORY ----------------
@mcp.tool("mem_get_long")
async def mem_get_long(workspace_id: str):
    async with reme_app as app:
        res = await app.async_execute(
            "retrieve_task_memory",
            workspace_id=workspace_id,
            query="Retrieve all persistent session memory."
        )
        return res.get("answer", {})

@mcp.tool("mem_set_long")
async def mem_set_long(workspace_id: str, key: str, value: str):
    async with reme_app as app:
        return await app.async_execute(
            "summary_task_memory",
            workspace_id=workspace_id,
            trajectories=[{
                "role": "assistant",
                "content": f"{key}: {value}"
            }]
        )
