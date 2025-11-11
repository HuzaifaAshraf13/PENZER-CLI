# agent/server.py
from fastmcp import FastMCP

# Create MCP server (no HTTP, no localhost)
mcp = FastMCP(
    name="PenzerMCP"
)

# Example tool
@mcp.tool()
def echo(message: str) -> str:
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b


def start_server():
    print("Starting in-process MCP server…")
    # No host, no port, no threads needed

    print("MCP running internally.")
    return mcp              # return server instance for Agent
