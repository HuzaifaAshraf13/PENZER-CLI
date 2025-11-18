# agent/agent.py

import json
import asyncio
from typing import Dict, Any
from agent.llm import LLM
from agent.server import mcp          # <-- USE THE MCP FROM SERVER ONLY
from agent.prompts import SYSTEM_PROMPT


class Agent:
    def __init__(self):
        # 1. Initialize LLM
        self.llm = LLM()

        # 2. Use shared MCP instance
        self.mcp_client = mcp

        # 3. Load tools/resources safely
        try:
            self.tool_schema = asyncio.run(self._load_tool_schema())
            self.resource_uris = asyncio.run(self._load_resource_uris())
        except Exception as e:
            print(f"FATAL SETUP ERROR during MCP configuration: {e}")
            self.tool_schema = {}
            self.resource_uris = []

        # 4. Build system prompt
        self.formatted_system_prompt = SYSTEM_PROMPT.format(
            tool_schema=json.dumps(self.tool_schema, indent=2),
            resource_uris="\n".join(self.resource_uris)
        )

    # -----------------------------------------------------------
    # SAFE LOADERS
    # -----------------------------------------------------------
    async def _load_tool_schema(self):
        return getattr(self.mcp_client, "tools", {})

    async def _load_resource_uris(self):
        return list(getattr(self.mcp_client, "resources", {}).keys())

    # -----------------------------------------------------------
    # TOOL EXECUTION
    # -----------------------------------------------------------
    def run_tool(self, tool_name: str, args: Dict):
        tool = self.mcp_client.tools.get(tool_name)
        if not tool:
            return {"error": f"Tool '{tool_name}' not found."}

        try:
            if asyncio.iscoroutinefunction(tool):
                return asyncio.run(tool(**args))
            else:
                return tool(**args)
        except Exception as e:
            return {"error": f"Tool execution failed: {e}"}

    # -----------------------------------------------------------
    # MAIN DECISION HANDLER
    # -----------------------------------------------------------
    def process_input(self, user_input: str):
        full_prompt = (
            f"User input: {user_input}\n\n"
            f"Based on the available tools and resources, "
            f"what action should be taken? Provide ONLY the JSON dictionary."
        )

        decision = self.llm.generate_content(
            system_instruction=self.formatted_system_prompt,
            prompt=full_prompt
        )

        # Parse LLM JSON output
        try:
            decision_str = decision.strip()

            if decision_str.startswith("```"):
                lines = decision_str.split("\n")
                decision_str = "\n".join(lines[1:-1]).strip()

            decision_dict = json.loads(decision_str)

        except Exception as e:
            print(f"\n--- LLM Decision Parse Error ({e}) ---")
            print("Raw Output:\n", decision)
            print("--------------------------------------")
            return

        tool_name = decision_dict.get("tool")
        args = decision_dict.get("args", {})
        response = decision_dict.get("response")

        # Tool execution
        if tool_name:
            if tool_name.startswith("resource://") or tool_name in self.resource_uris:
                print(f"Agent (Resource {tool_name}): NOT IMPLEMENTED")
                return

            result = self.run_tool(tool_name, args)

            if isinstance(result, (dict, list)):
                print(f"Agent (Tool {tool_name}):\n{json.dumps(result, indent=2)}")
            else:
                print(f"Agent (Tool {tool_name}): {result}")

        elif response:
            print(f"Agent: {response}")

        else:
            print("Agent: Invalid decision structure.")

# -----------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------
if __name__ == "__main__":
    print("Initializing Penzer Security Agent...")

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        agent = Agent()
        print(f"Agent initialized with {len(agent.tool_schema)} tools and {len(agent.resource_uris)} resources.")

        while True:
            query = input("\nUser: ")
            if query.lower() in ["quit", "exit"]:
                print("Agent shutting down.")
                break
            agent.process_input(query)

    except NameError:
        print("\nFATAL ERROR: The 'LLM' class is missing.")
    except Exception as e:
        print(f"\nFATAL ERROR during agent initialization: {e}")
