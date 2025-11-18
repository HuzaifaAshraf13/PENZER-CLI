from fastmcp import FastMCP
import asyncio

# 1) SINGLE shared MCP instance
mcp = FastMCP(name="PenzerMCP")

# 2) Ensure MCP has tools/resources attributes
if not hasattr(mcp, "tools"):
    mcp.tools = {}
if not hasattr(mcp, "resources"):
    mcp.resources = {}

# 3) Register example internal tools
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# 4) Function to load external tools
def load_tools():
    try:
        import tools.tools  # <-- all @mcp.tool() decorators run here
        print("TOOLS IMPORTED SUCCESSFULLY")
    except ImportError as e:
        print(f"ERROR: Failed to import tools.tools: {e}")

# 5) Server start function
def start_server():
    print("Starting in-process PenzerMCP server…")
    print(f"Server Name: {mcp.name}")

    # Load external tools now
    load_tools()

    tools_list = list(getattr(mcp, "tools", {}).keys())
    resources_list = list(getattr(mcp, "resources", {}).keys())

    print(f"Registered Tools: {tools_list}")

    print(f"Registered Tools: {len(tools_list)}")
    print(f"Registered Resources: {len(resources_list)}")

    return mcp

# 6) Run server if executed directly
if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    print("Press Ctrl+C to stop.")

    server.run()
