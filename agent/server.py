# agent/server.py
"""
MCP Server initialization and setup.
Registers all tools, resources, and prompts for the pentesting agent.
"""

import asyncio
import logging
from agent.core import mcp

# Configure logging
logger = logging.getLogger("penzer.server")
logger.setLevel(logging.INFO)

# ---------------- BUILT-IN TOOLS ----------------
@mcp.tool()
def echo(message: str) -> str:
    """Echo utility tool for testing MCP connectivity."""
    return f"ECHO: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    """Simple math tool for testing."""
    return a + b

# ---------------- LOAD EXTERNAL TOOLS AND PROMPTS ----------------
# Import order matters - these register decorators with mcp
try:
    import tools.tools          # Registers security/pentesting tools
    logger.info("✓ Loaded tools.tools")
except Exception as e:
    logger.warning(f"Failed to load tools.tools: {e}")

try:
    import session.session      # Registers memory tools (mem_get_short, mem_set_short, mem_get_long, mem_set_long)
    logger.info("✓ Loaded session.session")
except Exception as e:
    logger.warning(f"Failed to load session.session: {e}")

# ---------------- HELPER TO LIST TOOLS ----------------
async def _get_mcp_tool_keys():
    """Get list of all registered MCP tools."""
    tools_dict = {}
    
    # Try async get_tools() first
    if hasattr(mcp, 'get_tools') and callable(getattr(mcp, 'get_tools')):
        try:
            tools_dict = await mcp.get_tools()
            return list(tools_dict.keys())
        except Exception as e:
            logger.debug(f"get_tools() failed: {e}")
    
    # Fallback to .tools attribute
    if hasattr(mcp, 'tools'):
        try:
            tools_dict = getattr(mcp, 'tools', {})
            return list(tools_dict.keys())
        except Exception as e:
            logger.debug(f".tools access failed: {e}")
    
    # Try _tools for internal access
    if hasattr(mcp, '_tools'):
        try:
            tools_dict = getattr(mcp, '_tools', {})
            return list(tools_dict.keys())
        except Exception as e:
            logger.debug(f"._tools access failed: {e}")
    
    return []

async def _get_mcp_resources():
    """Get list of all registered MCP resources."""
    try:
        resources = getattr(mcp, 'resources', {})
        return list(resources.keys())
    except Exception as e:
        logger.debug(f"Failed to get resources: {e}")
        return []

# ---------------- TOOL REGISTRATION VALIDATION ----------------
REQUIRED_TOOLS = [
    "terminal",
    "memory"
]

def _validate_tool_registration():
    """
    Validate that all required tools are registered.
    Raises ValueError if critical tools are missing.
    
    Returns:
        (bool, list) - (all_valid, missing_tools)
    """
    try:
        loop = asyncio.get_event_loop()
        registered = loop.run_until_complete(_get_mcp_tool_keys())
        registered_set = set(registered)
        
        missing = []
        for tool in REQUIRED_TOOLS:
            if tool not in registered_set:
                missing.append(tool)
                logger.error(f"❌ CRITICAL: Required tool '{tool}' is NOT registered")
        
        if missing:
            logger.error(f"❌ Missing {len(missing)} required tools: {missing}")
            return False, missing
        
        logger.info(f"✓ All {len(REQUIRED_TOOLS)} required tools registered")
        return True, []
    
    except Exception as e:
        logger.error(f"Failed to validate tool registration: {e}")
        return False, ["validation_error"]

# ---------------- START SERVER ----------------
def start_server():
    """Initialize and display MCP server status."""
    print("\n" + "="*60)
    print("🚀 Starting Penzer MCP Server...")
    print("="*60)
    print(f"Server Name: {mcp.name}")
    
    try:
        # Get or create event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Fetch tools
        tools_list = loop.run_until_complete(_get_mcp_tool_keys())
        resources_list = loop.run_until_complete(_get_mcp_resources())
        
        # VALIDATE REQUIRED TOOLS
        all_valid, missing = _validate_tool_registration()
        if not all_valid:
            logger.warning(f"⚠️  Missing tools: {missing}")
            print(f"\n⚠️  WARNING: Missing critical tools: {missing}")
        else:
            print(f"\n✓ All critical tools verified")
    
    except Exception as e:
        logger.error(f"FATAL MCP SETUP ERROR: {e}", exc_info=True)
        tools_list = []
        resources_list = []
    
    # Display summary
    print(f"\n📋 Registered Components:")
    print(f"  Tools: {len(tools_list)}")
    for tool in sorted(tools_list)[:10]:
        print(f"    - {tool}")
    if len(tools_list) > 10:
        print(f"    ... and {len(tools_list) - 10} more")
    
    print(f"\n  Resources: {len(resources_list)}")
    for resource in sorted(resources_list)[:5]:
        print(f"    - {resource}")
    if len(resources_list) > 5:
        print(f"    ... and {len(resources_list) - 5} more")
    
    print(f"\n✓ MCP Server ready!")
    print("="*60 + "\n")
    
    logger.info(f"MCP Server started with {len(tools_list)} tools and {len(resources_list)} resources")
    
    return {
        "tools": tools_list,
        "resources": resources_list,
        "status": "ready"
    }


def get_server_status():
    """Get current MCP server status."""
    try:
        loop = asyncio.get_event_loop()
        tools_list = loop.run_until_complete(_get_mcp_tool_keys())
        resources_list = loop.run_until_complete(_get_mcp_resources())
    except Exception as e:
        logger.error(f"Failed to get server status: {e}")
        tools_list = []
        resources_list = []
    
    return {
        "status": "running",
        "tools_count": len(tools_list),
        "resources_count": len(resources_list),
        "server_name": mcp.name
    }

    return mcp

# ---------------- RUN SERVER DIRECTLY ----------------
if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    server = start_server()
    print("\nServer is running and listening for MCP clients...")
    server.run()
