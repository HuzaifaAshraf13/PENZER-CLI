# agent/server.py

from fastmcp import FastMCP

# 1. Create the MCP server instance
# This 'mcp' object is what the tools in tools/tools.py reference and register to.
mcp = FastMCP(name="PenzerMCP", 
              description="A security analysis server providing Nmap, Metasploit, GitHub Policy, and Exploit DB search capabilities.")

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
# This single line is the mechanism that automatically registers all 
# tools and resources (nmap_scan, run_msfconsole_command, get_security_policy, 
# search_exploit_db) decorated with @mcp.tool() or @mcp.resource() from tools/tools.py 
# into the 'mcp' object above.
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
    print(f"Registered Tools: {len(mcp.get_tools())}")
    print(f"Registered Resources: {len(mcp.get_resources())}")
    
    # Return the configured MCP object
    return mcp

# Optional: Add a main block to run the server directly
if __name__ == "__main__":
    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    print("Press Ctrl+C to stop.")
    
    # The mcp.run() function starts the server listening on the default transport (usually stdio)
    # This is required for a client (like an LLM sandbox) to connect.
    server.run()