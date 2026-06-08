"""
Core MCP initialization for Penzer agent.
Handles FastMCP server setup.
"""

import logging
from fastmcp import FastMCP

logger = logging.getLogger("penzer.core")
logger.setLevel(logging.INFO)

# ================== SINGLE MCP INSTANCE ==================
mcp = FastMCP(name="PenzerMCP")
logger.info(f"✓ FastMCP instance created: {mcp.name}")


# ================== MCP UTILITIES ==================

async def get_mcp_tools():
    """Get list of all registered MCP tools."""
    try:
        if hasattr(mcp, "get_tools") and callable(getattr(mcp, "get_tools")):
            tools_dict = await mcp.get_tools()
            return list(tools_dict.keys())
        elif hasattr(mcp, "tools"):
            return list(mcp.tools.keys())
        elif hasattr(mcp, "_tools"):
            return list(mcp._tools.keys())
    except Exception as e:
        logger.debug(f"Error fetching MCP tools: {e}")
    return []


async def get_mcp_resources():
    """Get list of all registered MCP resources."""
    try:
        resources = getattr(mcp, "resources", {})
        return list(resources.keys())
    except Exception as e:
        logger.debug(f"Error fetching MCP resources: {e}")
    return []


def get_mcp_status():
    """Get current MCP server status."""
    return {
        "name": mcp.name,
        "status": "ready"
    }