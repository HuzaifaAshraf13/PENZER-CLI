# agent/server.py

from fastmcp import FastMCP
import asyncio

# -------------------------------
# 1. Create the MCP server instance
# -------------------------------
mcp = FastMCP(name="PenzerMCP")

# --- PATCH: Ensure 'tools' and 'resources' attributes exist ---
if not hasattr(mcp, "tools"):
    mcp.tools = {}
if not hasattr(mcp, "resources"):
    mcp.resources = {}

# -------------------------------
# 2. Internal Example Tools
# -------------------------------
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# -------------------------------
# 3. Import external tools
# -------------------------------
try:
    import tools.tools
except ImportError as e:
    print(f"ERROR: Failed to import tools.tools. Error: {e}")

# -------------------------------
# 4. Server Execution Function
# -------------------------------
def start_server():
    print("Starting in-process PenzerMCP server…")
    print(f"Server Name: {mcp.name}")

    try:
        # Safely count tools and resources
        tools_list = list(getattr(mcp, "tools", {}).keys())
        resources_list = list(getattr(mcp, "resources", {}).keys())

        print(f"Registered Tools: {len(tools_list)}")
        print(f"Registered Resources: {len(resources_list)}")
    except Exception as e:
        print(f"Warning: Could not get counts: {e}")
        print("Registered Tools: UNKNOWN")
        print("Registered Resources: UNKNOWN")

    return mcp

# -------------------------------
# 5. Run server if executed directly
# -------------------------------
if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    print("Press Ctrl+C to stop.")

    server.run()
