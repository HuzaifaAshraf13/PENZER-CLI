# agent/server.py (Final Version with Registration Test)

from fastmcp import FastMCP
import asyncio
from agent.core import mcp # <--- Uses the fixed, shared mcp instance

# 1) Register example internal tools
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# 2) Load external tools (Triggers registration decorators)
try:
    import tools.tools
    print("TOOLS IMPORTED SUCCESSFULLY at module level.")
except ImportError as e:
    print(f"ERROR: Failed to import tools.tools: {e}")

# 3) Server start function
def start_server():
    print("Starting in-process PenzerMCP server…")
    print(f"Server Name: {mcp.name}")

    # --- IMMEDIATE TOOL REGISTRATION CHECK ---
    tools_list = list(mcp.tools.keys())
    print("-" * 40)
    print("REGISTRATION TEST RESULT:")
    print(f"  Keys found: {tools_list}")
    print(f"  Expected count: 7 (2 internal + 5 external)")
    print("-" * 40)
    # ---------------------------------------

    resources_list = list(getattr(mcp, "resources", {}).keys())

    # Final summary output
    print(f"Registered Tools: {tools_list}")
    print(f"Registered Tools Count: {len(tools_list)}")
    print(f"Registered Resources: {len(resources_list)}")

    return mcp

# 4) Run server if executed directly
if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    server.run()