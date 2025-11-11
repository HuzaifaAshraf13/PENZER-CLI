# agent/agent.py
from agent.llm import LLM
from agent.server import mcp   # import the in-process MCP server instance


class Agent:
    def __init__(self):
        # Initialize Gemini LLM
        self.llm = LLM()

        # In-process MCP client (no host, no port)
        self.mcp_client = mcp


    def run_tool(self, tool_name: str, args: dict):
        """Call an MCP tool."""
        return self.mcp_client.call_tool(tool_name, args)


    def process_input(self, user_input: str):
        """Process input and decide whether to call tools or LLM."""
        if user_input.startswith("echo "):
            msg = user_input[5:]
            result = self.run_tool("echo", {"message": msg})
            print(f"Agent: {result}")

        else:
            # Fallback LLM response
            response = self.llm.generate_content(user_input)
            print(f"Agent: {response}")

