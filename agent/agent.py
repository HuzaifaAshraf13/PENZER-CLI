# agent/agent.py
from agent.llm import LLM
from agent.server import mcp  # in-process MCP instance

class Agent:
    def __init__(self):
        # Initialize Gemini LLM
        self.llm = LLM()
        # In-process MCP client
        self.mcp_client = mcp

    def run_tool(self, tool_name: str, args: dict):
        """Call an MCP tool by name with arguments."""
        return self.mcp_client.call_tool(tool_name, args)

    def process_input(self, user_input: str):
        """
        Let the LLM decide whether to call a tool or respond.
        The LLM should return a dict like:
        {"tool": "nmap_scan", "args": {"target": "127.0.0.1"}}
        or {"tool": None, "response": "..."}
        """
        decision = self.llm.generate_content(
            f"Decide whether to call an MCP tool or respond normally. User input: {user_input}"
        )

        # Try to parse LLM output
        try:
            decision_dict = eval(decision) if isinstance(decision, str) else decision
        except Exception:
            print(f"Agent: {decision}")
            return

        tool_name = decision_dict.get("tool")
        args = decision_dict.get("args", {})

        if tool_name:
            result = self.run_tool(tool_name, args)
            print(f"Agent (tool {tool_name}): {result}")
        else:
            response = decision_dict.get("response", "")
            print(f"Agent: {response}")

if __name__ == "__main__":
    agent = Agent()
    while True:
        query = input("User: ")
        agent.process_input(query)
