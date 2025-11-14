# agent/agent.py

# --- ADD THIS LINE ---
from typing import List, Dict, Any, Optional
# ---------------------

from agent.llm import LLM
from agent.server import mcp  # in-process MCP instance

from agent.llm import LLM
# Import the configured MCP instance from the server module
from agent.server import mcp 
import json
from typing import Dict, Any, Optional

from agent.prompts import SYSTEM_PROMPT


class Agent:
    def __init__(self):
        # Initialize Gemini LLM (Assuming LLM class is implemented)
        self.llm = LLM()
        # In-process MCP client is the configured server instance
        self.mcp_client = mcp
        
        # Prepare the MCP context for the LLM
        self.tool_schema = self._get_tool_schema()
        self.resource_uris = self._get_resource_uris()
        self.formatted_system_prompt = SYSTEM_PROMPT.format(
            tool_schema=json.dumps(self.tool_schema, indent=2),
            resource_uris="\n".join(self.resource_uris)
        )

    def _get_tool_schema(self) -> Dict[str, Any]:
        """Collects the schema for all registered MCP tools."""
        schema = {}
        for tool in self.mcp_client.get_tools():
            # Get the tool signature/description, which the LLM uses to understand arguments
            schema[tool.name] = {
                "description": tool.description,
                "parameters": tool.parameters
            }
        return schema
    
    def _get_resource_uris(self) -> List[str]:
        """Collects the URIs for all registered MCP resources."""
        return [resource.uri for resource in self.mcp_client.get_resources()]

    def run_tool(self, tool_name: str, args: dict):
        """Call an MCP tool by name with arguments."""
        # Note: Asynchronous tools require a slight change here to be awaited,
        # but for this simple sync structure, we rely on the MCP client handling execution.
        print(f"-> Calling MCP Tool: {tool_name} with args: {args}")
        try:
            return self.mcp_client.call_tool(tool_name, args)
        except Exception as e:
            return f"ERROR executing tool {tool_name}: {e}"
        
    def get_resource_content(self, uri: str) -> Optional[str]:
        """Accesses an MCP resource by URI."""
        print(f"-> Accessing MCP Resource: {uri}")
        try:
            return self.mcp_client.get_resource(uri)
        except Exception as e:
            return f"ERROR accessing resource {uri}: {e}"

    def process_input(self, user_input: str):
        """
        Lets the LLM decide whether to call a tool, access a resource, or respond.
        """
        # The prompt is simplified here since the main logic is encoded in the SYSTEM_PROMPT.
        full_prompt = f"User input: {user_input}\n\nBased on the available tools and resources, what action should be taken? Provide ONLY the JSON dictionary."
        
        # The LLM receives the full context via the system prompt
        decision = self.llm.generate_content(
            system_instruction=self.formatted_system_prompt,
            prompt=full_prompt
        )

        # Try to parse LLM output
        try:
            # We assume the LLM returns valid JSON based on the system prompt instruction
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
            # Check for resource URI first (e.g., 'git://security-policy')
            if tool_name.startswith("resource://") or tool_name in self.resource_uris:
                # The LLM chose to access a resource
                result = self.get_resource_content(tool_name)
                print(f"Agent (Resource {tool_name}):\n{result}")
                
                # OPTIONAL: Pass the resource content back to the LLM for summarization
                # If you need the LLM to interpret the resource, you'd call generate_content again here.

            else:
                # The LLM chose to call a functional tool (Nmap, Metasploit, Exploit DB Search)
                result = self.run_tool(tool_name, args)
                print(f"Agent (Tool {tool_name}): {result}")
                
                # OPTIONAL: Pass the tool result back to the LLM for summarization
                # (Same as above, a second LLM call would be used for interpretation)
                
        elif response:
            # The LLM chose to respond directly
            print(f"Agent: {response}")
        else:
            # Fallback if the JSON structure was unexpected
            print("Agent: Invalid decision structure from LLM.")


if __name__ == "__main__":
    # NOTE: You need to implement the LLM class (e.g., wrap Gemini API calls)
    # The setup below is the correct way to run your agent locally.
    
    print("Initializing Penzer Security Agent...")
    try:
        agent = Agent()
        print(f"Agent initialized with {len(agent.tool_schema)} tools and {len(agent.resource_uris)} resources.")
        
        # Start main interaction loop
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