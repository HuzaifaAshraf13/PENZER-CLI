import sys
import os
import asyncio

# --- PATH SETUP ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

async def inspect_mcp():
    print("\n[!] Initializing Penzer Registry Inspection...")
    
    # 1. Import modules to trigger decorators on the shared MCP instance
    try:
        from agent.core import mcp
        import tools.tools        # Should register nmap, msf, etc.
        import session.session    # Should register mem_ tools and resources
        
        # Manually register the server-level tools to see if they attach
        @mcp.tool()
        def echo(message: str) -> str: return message

        @mcp.tool()
        def add(a: int, b: int) -> int: return a + b
        
    except ImportError as e:
        print(f"❌ Import Error: {e}")
        return

    # 2. Extract Tools
    if hasattr(mcp, 'get_tools'):
        tools_dict = await mcp.get_tools()
        tool_names = list(tools_dict.keys())
    else:
        # Fallback for older/sync versions
        tool_names = list(getattr(mcp, "tools", {}).keys())

    # 3. Extract Resources
    if hasattr(mcp, 'get_resources'):
        res_dict = await mcp.get_resources()
        resource_uris = list(res_dict.keys())
    else:
        # Fallback for older/sync versions
        resource_uris = list(getattr(mcp, "resources", {}).keys())

    # --- FINAL REPORT ---
    print("\n" + "="*50)
    print(f"MCP SERVER NAME: {mcp.name}")
    print("="*50)
    
    print(f"\n✅ TOOLS FOUND ({len(tool_names)}):")
    for tool in sorted(tool_names):
        print(f"  - {tool}")
        
    print(f"\n✅ RESOURCES FOUND ({len(resource_uris)}):")
    for res in sorted(resource_uris):
        print(f"  - {res}")
    
    print("\n" + "="*50)
    if len(resource_uris) > 0 and len(tool_names) > 0:
        print("RESULT: Registry is healthy and synchronized.")
    else:
        print("RESULT: Warning! One or more registries are empty.")
    print("="*50 + "\n")

if __name__ == "__main__":
    asyncio.run(inspect_mcp())