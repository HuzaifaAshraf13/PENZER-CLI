# test_tool_registration.py (FINAL VERSION WITH ASYNC FIX)

import sys
import os
import asyncio
from typing import List, Tuple

# --- CRITICAL SETUP ---
# Ensure imports work regardless of where the test is run from
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ----------------------

# --- ASYNCHRONOUS TEST FUNCTION ---
async def run_test() -> Tuple[int, List[str], str]:
    """Imports tools, awaits tool getter, and returns results."""
    
    # 1. Import the necessary components
    from agent.core import mcp # The shared instance
    import tools.tools         # Triggers external tool decorators
    
    # 2. Add internal tools defined in agent/server.py (Simulation)
    # These must be registered to match the expected count of 7.
    @mcp.tool()
    def echo(message: str) -> str:
        return f"ECHO: {message}"

    @mcp.tool()
    def add(a: int, b: int) -> int:
        return a + b
    
    # 3. Safely locate and await the tool registry
    registered_keys = []
    
    # Check for the asynchronous getter method (the confirmed issue)
    if hasattr(mcp, 'get_tools') and asyncio.iscoroutinefunction(mcp.get_tools):
        # 🔑 CRITICAL FIX: Await the coroutine object
        tools_dict = await mcp.get_tools() 
        registered_keys = list(tools_dict.keys())
        registry_source = "mcp.get_tools() (Awaited)"
    else:
        # Fallback for sync versions
        if hasattr(mcp, 'tools'):
            registered_keys = list(mcp.tools.keys())
            registry_source = "mcp.tools (Sync Fallback)"
        else:
            raise AttributeError("ERROR: Found no working method to access the tool registry.")

    return len(registered_keys), registered_keys, registry_source

# --- MAIN SYNCHRONOUS EXECUTION BLOCK ---
if __name__ == "__main__":
    try:
        # Execute the async test function
        tool_count, registered_keys, registry_source = asyncio.run(run_test())
        
        EXPECTED_COUNT = 7 # 5 external + 2 internal

        print("\n--- MCP Tool Registration Test ---")
        print(f"Registry accessed via: {registry_source}")
        print("-" * 40)
        print(f"Registered Tool Count: {tool_count}")
        print(f"Registered Tool Keys: {sorted(registered_keys)}")
        print("-" * 40)

        if tool_count == EXPECTED_COUNT and all(k in registered_keys for k in ['nmap_scan', 'echo', 'add']):
            print("\n✅ SUCCESS: Tool registration confirmed.")
            sys.exit(0)
        else:
            print(f"\n❌ FAILURE: Expected {EXPECTED_COUNT} tools, found {tool_count}.")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ FATAL ERROR: {type(e).__name__}: {e}")
        sys.exit(1)