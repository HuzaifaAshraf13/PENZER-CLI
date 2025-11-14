# agent/agent.py

import json
import asyncio # REQUIRED to run async MCP methods in sync code
from typing import List, Dict, Any, Optional

from agent.llm import LLM
# Import the configured MCP instance from the server module
from agent.server import mcp 
# Assuming you have a file named agent/prompts.py
from agent.prompts import SYSTEM_PROMPT


class Agent:
    def __init__(self):
        # Initialize Gemini LLM (Assuming LLM class is implemented)
        self.llm = LLM()
        # In-process MCP client is the configured server instance
        self.mcp_client = mcp
        
        # Prepare the MCP context for the LLM
        # Use asyncio.run() to execute the async methods in the sync __init__
        self.tool_schema = asyncio.run(self._get_tool_schema_async())
        self.resource_uris = asyncio.run(self._get_resource_uris_async())
        
        self.formatted_system_prompt = SYSTEM_PROMPT.format(
            tool_schema=json.dumps(self.tool_schema, indent=2),
            resource_uris="\n".join(self.resource_uris)
        )

    # --- Async Helpers for Initialization (FIXED AWAIT SYNTAX) ---

    # agent/agent.py (Corrected _get_tool_schema_async method)

    # ... (Other methods above) ...

    # agent/agent.py (The definitive fix for 'str' object error)

    # agent/agent.py (Corrected _get_tool_schema_async method)

    async def _get_tool_schema_async(self) -> Dict[str, Any]:
        """Collects the schema for all registered MCP tools."""
        schema = {}
        
        # 1. Await the coroutine to get the list of tool NAMES (strings).
        tool_names = await self.mcp_client.get_tools()
        
        # 2. Iterate over the NAMES (strings).
        for tool_name in tool_names:
            # 3. FIX: AWAIT the get_tool method to get the actual Tool object.
            tool = await self.mcp_client.get_tool(tool_name) 
            
            if tool:
                schema[tool_name] = {
                    "description": tool.description,
                    "parameters": tool.parameters 
                }
            # Note: The "else" block (omitted here for brevity) is still good practice.
                 
        return schema
    
    # ... (Other methods below) ...
    
    async def _get_resource_uris_async(self) -> List[str]:
        """Collects the URIs for all registered MCP resources (must be awaited)."""
        
        # FIX: Await the coroutine to get the list of URI strings, then return it directly.
        resources_list = await self.mcp_client.get_resources()
        
        # Since the list contains strings (the URIs), we just return it.
        return resources_list

    # --- Tool Execution (Requires synchronous wrapping for process_input) ---

    def _run_mcp_operation_sync(self, coroutine):
        """Helper to run an async operation synchronously, handling the event loop."""
        try:
            return asyncio.run(coroutine)
        except Exception as e:
            return f"ERROR executing MCP operation: {e}"

    def run_tool(self, tool_name: str, args: dict):
        """Call an MCP tool by name with arguments (uses sync wrapper)."""
        print(f"-> Calling MCP Tool: {tool_name} with args: {args}")
        coroutine = self.mcp_client.call_tool(tool_name, args)
        return self._run_mcp_operation_sync(coroutine)
        
    def get_resource_content(self, uri: str) -> Optional[str]:
        """Accesses an MCP resource by URI (uses sync wrapper)."""
        print(f"-> Accessing MCP Resource: {uri}")
        coroutine = self.mcp_client.get_resource(uri)
        return self._run_mcp_operation_sync(coroutine)

    # --- Main Processing Loop ---

    def process_input(self, user_input: str):
        """
        Lets the LLM decide whether to call a tool, access a resource, or respond.
        """
        full_prompt = f"User input: {user_input}\n\nBased on the available tools and resources, what action should be taken? Provide ONLY the JSON dictionary."
        
        decision = self.llm.generate_content(
            system_instruction=self.formatted_system_prompt,
            prompt=full_prompt
        )

        try:
            decision_dict = json.loads(decision)
        except Exception:
            print(f"\n--- LLM Decision Parse Error ---\nRaw LLM Output:\n{decision}\n------------------------------")
            print("Agent is confused. Please try again.")
            return

        tool_name = decision_dict.get("tool")
        args = decision_dict.get("args", {})
        response = decision_dict.get("response")
        
        # --- Action Execution ---
        
        if tool_name:
            # Check for resource URI first 
            if tool_name.startswith("resource://") or tool_name in self.resource_uris:
                result = self.get_resource_content(tool_name)
                print(f"Agent (Resource {tool_name}):\n{result}")
            else:
                result = self.run_tool(tool_name, args)
                print(f"Agent (Tool {tool_name}): {result}")
                
        elif response:
            print(f"Agent: {response}")
        else:
            print("Agent: Invalid decision structure from LLM.")


if __name__ == "__main__":
    print("Initializing Penzer Security Agent...")
    try:
        # Check if an event loop is already running before trying to create a new one
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
        print("\nFATAL ERROR: The 'LLM' class is not defined.")
        print("Please ensure your 'agent/llm.py' file correctly implements the LLM class.")
    except Exception as e:
        print(f"\nFATAL ERROR during agent initialization: {e}")