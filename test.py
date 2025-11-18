from agent.server import mcp
import tools.tools  # forces @mcp.tool() decorators to run

print(mcp.tools.keys())
