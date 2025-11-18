# agent/agent.py (Cleaned and Refactored)

import json
import asyncio
from typing import Dict, Any, List, Optional

# Import mcp from the core module (assuming fix applied in agent/server.py)
from agent.server import mcp
from agent.llm import LLM
from agent.prompts import SYSTEM_PROMPT


class Agent:
    def __init__(self):
        # 1. Initialize LLM instance
        self.llm = LLM()

        # 2. Use shared MCP instance
        self.mcp_client = mcp

        # 3. Load tool schema and resources synchronously (FIXED from previous versions)
        try:
            self.tool_schema: Dict[str, Any] = self._load_tool_schema()
            self.resource_uris: List[str] = self._load_resource_uris()
        except Exception as e:
            print(f"FATAL SETUP ERROR during MCP configuration: {e}")
            self.tool_schema = {}
            self.resource_uris = []

        # 4. Build system prompt using loaded info
        self.formatted_system_prompt: str = self._build_system_prompt()

    # -----------------------------------------------------------
    # SETUP & LOADERS
    # -----------------------------------------------------------

    def _load_tool_schema(self) -> Dict[str, Any]:
        """Loads registered tools from the shared MCP instance."""
        return getattr(self.mcp_client, "tools", {})

    def _load_resource_uris(self) -> List[str]:
        """Loads registered resource URIs from the shared MCP instance."""
        return list(getattr(self.mcp_client, "resources", {}).keys())
    
    def _build_system_prompt(self) -> str:
        """Formats the system prompt with the current tool schema and resources."""
        return SYSTEM_PROMPT.format(
            tool_schema=json.dumps(self.tool_schema, indent=2),
            resource_uris="\n".join(self.resource_uris)
        )

    # -----------------------------------------------------------
    # TOOL EXECUTION
    # -----------------------------------------------------------

    def run_tool(self, tool_name: str, args: Dict) -> Dict:
        """Executes a tool, handling both sync and async functions."""
        tool = self.mcp_client.tools.get(tool_name)
        if not tool:
            return {"error": f"Tool '{tool_name}' not found."}

        try:
            if asyncio.iscoroutinefunction(tool):
                # Execute async tool synchronously (requires an event loop running)
                return asyncio.run(tool(**args)) 
            else:
                return tool(**args)
        except Exception as e:
            return {"error": f"Tool execution failed: {type(e).__name__}: {e}"}

    # -----------------------------------------------------------
    # DECISION LOGIC
    # -----------------------------------------------------------

    def _parse_llm_decision(self, decision_raw: str) -> Optional[Dict]:
        """Parses the raw JSON output from the LLM, cleaning up surrounding markdown."""
        try:
            decision_str = decision_raw.strip()

            # Clean markdown code block wraps (```json ... ```)
            if decision_str.startswith("```"):
                lines = decision_str.split("\n")
                # Remove first line (```[json]) and last line (```)
                decision_str = "\n".join(lines[1:-1]).strip()

            return json.loads(decision_str)

        except Exception as e:
            print(f"\n--- LLM Decision Parse Error ({e}) ---")
            print("Raw Output:\n", decision_raw)
            print("--------------------------------------")
            return None

    def process_input(self, user_input: str):
        """Generates a decision from the LLM and executes the resulting tool/action."""
        
        # 1. Construct the prompt
        full_prompt = (
            f"User input: {user_input}\n\n"
            f"Based on the available tools and resources, "
            f"what action should be taken? Provide ONLY the JSON dictionary."
        )

        # 2. Get LLM decision
        decision_raw = self.llm.generate_content(
            system_instruction=self.formatted_system_prompt,
            prompt=full_prompt
        )

        # 3. Parse and validate decision
        decision_dict = self._parse_llm_decision(decision_raw)
        if not decision_dict:
            return

        tool_name = decision_dict.get("tool")
        args = decision_dict.get("args", {})
        response = decision_dict.get("response")

        # 4. Handle tool/resource execution or direct response
        if tool_name:
            if tool_name.startswith("resource://") or tool_name in self.resource_uris:
                # Placeholder for resource interaction logic
                print(f"Agent (Resource {tool_name}): NOT IMPLEMENTED")
                return

            # Execute tool
            result = self.run_tool(tool_name, args)

            # Output result
            output = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
            print(f"Agent (Tool {tool_name}):\n{output}")

        elif response:
            print(f"Agent: {response}")

        else:
            print("Agent: Invalid decision structure or missing 'tool'/'response' field.")


# -----------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------
if __name__ == "__main__":
    print("Initializing Penzer Security Agent...")

    # Set up the event loop needed for asyncio.run() calls inside Agent.run_tool
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        agent = Agent()
        
        # This print statement assumes all fixes (including agent/core.py) are applied
        print(f"Agent initialized with {len(agent.tool_schema)} tools and {len(agent.resource_uris)} resources.") 

        while True:
            query = input("\nUser: ")
            if query.lower() in ["quit", "exit"]:
                print("Agent shutting down.")
                break
            agent.process_input(query)

    except NameError:
        print("\nFATAL ERROR: The 'LLM' class is missing. Ensure it is defined.")
    except Exception as e:
        print(f"\nFATAL ERROR during agent initialization: {e}")