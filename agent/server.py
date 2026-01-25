# agent/server.py
import asyncio
from agent.core import mcp

# ---------------- INTERNAL TOOLS ----------------
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# ---------------- LOAD EXTERNAL TOOLS ----------------
import tools.tools      # registers tool decorators
import session.session  # registers memory resources

# ---------------- HELPER TO LIST TOOLS ----------------
async def _get_mcp_tool_keys():
    if hasattr(mcp, 'get_tools'):
        tools_dict = await mcp.get_tools()
        return list(tools_dict.keys())
    elif hasattr(mcp, 'tools'):
        return list(mcp.tools.keys())
    return []

# ---------------- START SERVER ----------------
def start_server():
    print("Starting in-process PenzerMCP server…")
    print(f"Server Name: {mcp.name}")

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        tools_list = loop.run_until_complete(_get_mcp_tool_keys())
    except Exception as e:
        print(f"FATAL MCP SETUP ERROR: {e}")
        tools_list = []

    resources_list = list(getattr(mcp, "resources", {}).keys())
    print(f"Registered Tools: {tools_list}")
    print(f"Registered Tools Count: {len(tools_list)}")
    print(f"Registered Resources: {len(resources_list)}")

    return mcp

# ---------------- RUN SERVER DIRECTLY ----------------
if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    server.run()
