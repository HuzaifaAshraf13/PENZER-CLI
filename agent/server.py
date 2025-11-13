# agent/server.py
from fastmcp import FastMCP

# Create MCP server
mcp = FastMCP(name="PenzerMCP")

# Example internal tools
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# Import Penzer tools so MCP can access them
import tools.tools  # registers nmap_scan and run_msfconsole_command

def start_server():
    print("Starting in-process MCP server…")
    print("MCP running internally.")
    return mcp
