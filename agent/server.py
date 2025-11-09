# agent/server.py
from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP(name="PenzerMCP")

# Example tool: echoes input
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

# Example tool: adds two numbers
@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

def start_server():
    print("Starting MCP server on 127.0.0.1:8000 …")
    # http transport exposes /mcp for clients
    mcp.run(transport="http", host="127.0.0.1", port=8000)
    