# agent/agent.py
from fastmcp import Client
from agent.llm import LLM

class Agent:
    def __init__(self):
        # Initialize Gemini LLM client
        self.llm = LLM()

        # Initialize MCP client (connects to local MCP server)
        self.mcp_client = Client("http://127.0.0.1:8000/mcp")

    def run_tool(self, tool_name: str, args: dict):
        """Call MCP tool and return result."""
        return self.mcp_client.call_tool(tool_name, args)

    def process_input(self, user_input: str):
        """Process user input and call tools or LLM as needed."""
        if user_input.startswith("echo "):
            msg = user_input[5:]
            result = self.run_tool("echo", {"message": msg})
            print(f"Agent: {result}")
        elif user_input.startswith("add "):
            parts = user_input[4:].split()
            if len(parts) == 2:
                try:
                    a = int(parts[0])
                    b = int(parts[1])
                    result = self.run_tool("add", {"a": a, "b": b})
                    print(f"Agent: {result}")
                except ValueError:
                    print("Agent: Invalid numbers provided for add command.")
            else:
                print("Agent: Please provide exactly two numbers for add.")
        else:
            # Fallback to LLM for free-form input
            response = self.llm.generate_content(user_input)
            print(f"Agent: {response}")
