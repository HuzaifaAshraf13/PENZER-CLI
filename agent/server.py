"""
MCP Server initialization and setup.
"""

import asyncio
import logging
from agent.core import mcp

logger = logging.getLogger("penzer.server")

# ---------------- BUILT-IN TOOLS ----------------
@mcp.tool()
def echo(message: str) -> str:
    """Echo utility tool for testing MCP connectivity."""
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    """Simple math tool for testing."""
    return a + b

# ---------------- LOAD EXTERNAL TOOLS ----------------
try:
    import tools.tools

except Exception as e:
    logger.warning(f"Failed to load tools.tools: {e}")


# ---------------- HELPERS ----------------
async def _get_tool_keys() -> list:
    for attr in ["get_tools", "tools", "_tools"]:
        try:
            val = getattr(mcp, attr)
            result = await val() if callable(val) else val
            return list(result.keys())
        except Exception:
            continue
    return []

REQUIRED_TOOLS = ["terminal", "memory"]

def _validate_tools(tools: list) -> tuple[bool, list]:
    missing = [t for t in REQUIRED_TOOLS if t not in tools]
    return len(missing) == 0, missing

# ---------------- START SERVER ----------------
def start_server() -> dict:
    """Initialize MCP server silently."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        tools_list = loop.run_until_complete(_get_tool_keys())
        all_valid, missing = _validate_tools(tools_list)

        if not all_valid:
            logger.warning(f"Missing critical tools: {missing}")

        logger.info(f"MCP Server started with {len(tools_list)} tools")
        return {"tools": tools_list, "status": "ready"}

    except Exception as e:
        logger.error(f"MCP setup error: {e}")
        return {"tools": [], "status": "error"}


def get_server_status() -> dict:
    try:
        loop = asyncio.get_event_loop()
        tools_list = loop.run_until_complete(_get_tool_keys())
    except Exception:
        tools_list = []
    return {"status": "running", "tools_count": len(tools_list), "server_name": mcp.name}


if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    start_server()