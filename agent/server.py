# agent/server.py

from fastmcp import FastMCP
import asyncio # <-- REQUIRED for running async methods in synchronous start_server

# 1. Create the MCP server instance
# This 'mcp' object is what the tools in tools/tools.py reference and register to.
mcp = FastMCP(name="PenzerMCP")

# --- Example Internal Tools (These register directly here) ---
@mcp.tool()
def echo(message: str) -> str:
    """Echoes the input message."""
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    """Adds two integers."""
    return a + b

# 2. Import the tools module
# This registers all external tools and resources with the 'mcp' object.
try:
    import tools.tools
except ImportError as e:
    print(f"ERROR: Failed to import tools.tools. Make sure tools/tools.py exists and dependencies are installed. Error: {e}")
    # You might want to exit or handle this error depending on your application logic

# ----------------------------------------
# Server Execution Function
# ----------------------------------------

def start_server():
    """Initializes and returns the configured MCP server instance."""
    print("Starting in-process PenzerMCP server…")
    print(f"Server Name: {mcp.name}")
    
    # --- FIX: Use asyncio.run() to correctly execute the async methods ---
    try:
        # Get the actual list of tools by running the coroutine synchronously
        tools_list = asyncio.run(mcp.get_tools())
        resources_list = asyncio.run(mcp.get_resources())
        
        print(f"Registered Tools: {len(tools_list)}")
        print(f"Registered Resources: {len(resources_list)}")
    except Exception as e:
        # This catch handles threading issues or other async loop conflicts gracefully
        print(f"Warning: Could not get counts synchronously due to: {e}")
        print("Registered Tools: UNKNOWN (Error in counting)")
        print("Registered Resources: UNKNOWN (Error in counting)")
    
    # Return the configured MCP object
    return mcp

# Optional: Add a main block to run the server directly
if __name__ == "__main__":
    # Ensure the main thread has an event loop set up for synchronous coroutine execution
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Only set a new loop if one isn't running
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    print("Press Ctrl+C to stop.")
    
    # The mcp.run() function starts the server listening on the default transport
    server.run()