# agent/server.py
from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP(name="PenzerMCP")

# Example tool: echoes back message
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

# Example tool: adds two numbers
@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

# Function to start the server (blocking)
def start_server():
    print("Starting MCP server on 127.0.0.1:8000 …")
    mcp.run(transport="http", host="127.0.0.1", port=8000)

