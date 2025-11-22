# agent/server.py (FINAL FIXED VERSION)

from fastmcp import FastMCP
import asyncio
from agent.core import mcp # <--- Use the single mcp instance

# 1) Register example internal tools (assumed to be here)
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# 2) Load external tools (Triggers registration decorators)
try:
    import tools.tools  # <-- This import runs the tool decorators
    print("TOOLS IMPORTED SUCCESSFULLY at module level.")
except ImportError as e:
    print(f"ERROR: Failed to import tools.tools: {e}")

# --- HELPER FUNCTION TO GET TOOL KEYS ASYNCHRONOUSLY ---
async def _get_mcp_tool_keys():
    """Awaits the async tool getter method and returns the keys."""
    if hasattr(mcp, 'get_tools'):
        tools_dict = await mcp.get_tools()
        return list(tools_dict.keys())
    # If this is not async, try direct access (safeguard)
    elif hasattr(mcp, 'tools'):
        return list(mcp.tools.keys())
    return []
# ---------------------------------------------------------


def start_server():
    print("Starting in-process PenzerMCP server…")
    print(f"Server Name: {mcp.name}")

    # 🔑 CRITICAL FIX: Use asyncio.run() to execute the async getter
    # This runs the helper function in a temporary event loop.
    try:
        # Check if an event loop is running (to avoid RuntimeError)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Run the async key retrieval function
        tools_list = loop.run_until_complete(_get_mcp_tool_keys())
    
    except Exception as e:
        print(f"FATAL MCP SETUP ERROR: Could not retrieve tool list: {e}")
        tools_list = []
        
    resources_list = list(getattr(mcp, "resources", {}).keys())

    # Output the required server info
    print(f"Registered Tools: {tools_list}")
    print(f"Registered Tools Count: {len(tools_list)}")
    print(f"Registered Resources: {len(resources_list)}")

    return mcp

# 4) Run server if executed directly
if __name__ == "__main__":
    # Ensure an event loop is ready for any other async calls
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    # NOTE: Assuming mcp.run() handles its own thread/loop or is synchronous
    server.run()