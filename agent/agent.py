# agent/agent.py
import json
import asyncio
import inspect
from typing import Dict, Any, List, Optional

from agent.core import mcp
from agent.llm import LLM
from agent.prompts import SYSTEM_PROMPT

# Import and register prompts FIRST (before anything uses mcp)
import session.sessionprompts  # registers session prompts
import tools.ToolsPrompts      # registers tool prompts

# Import session tools and resources
import session.session  # registers memory resources and tools


class Agent:
    def __init__(self):
        self.llm = LLM()
        self.mcp_client = mcp

        # Keep empty for now; async_init will fill them
        self.tool_schema: Dict[str, Any] = {}
        self.resource_uris: List[str] = []
        self.formatted_system_prompt: str = ""

    async def async_init(self):
        # Async-safe initialization
        self.tool_schema = await self._load_tool_schema()
        self.resource_uris = self._load_resource_uris()
        self.formatted_system_prompt = self._build_system_prompt()
        return self

    # -----------------------------------------------------------
    # LOADERS
    # -----------------------------------------------------------
    async def _load_tool_schema(self) -> Dict[str, Any]:
        """FastMCP exposes get_tools(), not .tools on some versions."""
        try:
            if hasattr(self.mcp_client, "get_tools"):
                return await self.mcp_client.get_tools()
            return getattr(self.mcp_client, "tools", {})
        except Exception as e:
            print("Error loading tool schema:", e)
            return {}

    def _load_resource_uris(self) -> List[str]:
        return list(getattr(self.mcp_client, "resources", {}).keys())

    def _serialize_tools_for_prompt(self, tools_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert FastMCP tool objects into a JSON-serializable dict that contains
        only tool names and parameter lists (no function objects).
        """
        serial = {}
        for name, tool_obj in tools_dict.items():
            # try to find the underlying callable
            fn = getattr(tool_obj, "fn", tool_obj)
            # try common wrappers
            if hasattr(fn, "__wrapped__"):
                fn = fn.__wrapped__
            params = []
            try:
                sig = inspect.signature(fn)
                params = [p for p in sig.parameters.keys()]
            except Exception:
                # fallback: look for annotations if available
                ann = getattr(fn, "__annotations__", {}) or {}
                params = list(ann.keys())
            serial[name] = {"args": params}
        return serial

    def _build_system_prompt(self) -> str:
        """Build system prompt with registered tools and resources."""
        # Retrieve registered prompts from MCP server
        prompts_dict = getattr(self.mcp_client, "prompts", {})
        
        # Extract prompt names that are registered
        prompt_names = list(prompts_dict.keys())
        
        # create a safe, JSON-serializable summary of registered tools
        tools_info = self._serialize_tools_for_prompt(self.tool_schema or {})

        merged = f"""
{SYSTEM_PROMPT}

# === REGISTERED PROMPTS ===
Available prompts: {', '.join(prompt_names)}

# === REGISTERED TOOLS (names + args) ===
{json.dumps(tools_info, indent=2)}

# === RESOURCES ===
{chr(10).join(self.resource_uris)}
"""
        return merged.strip()

    # -----------------------------------------------------------
    # TOOL EXECUTION (supports FastMCP.get_tools())
    # -----------------------------------------------------------
  

    async def run_tool(self, tool_name: str, args: Dict) -> Dict:
        workspace_id = "pentest_1"
        if "workspace_id" not in args:
            args["workspace_id"] = workspace_id

        try:
            tools_dict = (
                await self.mcp_client.get_tools()
                if hasattr(self.mcp_client, "get_tools")
                else getattr(self.mcp_client, "tools", {})
            )
        except Exception as e:
            return {"error": f"Cannot fetch MCP tools: {e}"}

        tool = tools_dict.get(tool_name)
        if not tool:
            return {"error": f"Unknown tool: {tool_name}"}

        callable_obj = getattr(tool, "fn", tool)

        try:
            # Signature-aware argument filtering
            sig = inspect.signature(callable_obj)
            filtered_args = {k: v for k, v in args.items() if k in sig.parameters}

            # Ensure workspace_id is included
            if "workspace_id" in sig.parameters and "workspace_id" not in filtered_args:
                filtered_args["workspace_id"] = workspace_id

            # Async or sync execution
            if asyncio.iscoroutinefunction(callable_obj):
                return await callable_obj(**filtered_args)
            return callable_obj(**filtered_args)

        except TypeError as e:
            return {
                "error": f"Tool invocation failed (TypeError): {e}",
                "provided_args": args,
                "filtered_args": filtered_args
            }
        except Exception as e:
            return {
                "error": f"Tool execution failed: {type(e).__name__}: {e}"
            }
    def _parse_llm_decision(self, raw: str) -> dict | None:
        """
        Convert LLM raw output (JSON) into a Python dict.
        Handles code block formatting.
        """
        try:
            txt = raw.strip()
            # Remove markdown-style code blocks
            if txt.startswith("```") and txt.endswith("```"):
                lines = txt.split("\n")
                txt = "\n".join(lines[1:-1]).strip()
            return json.loads(txt)
        except Exception:
            print("\nLLM decision parse failed. Raw output:\n", raw)
            return None
            
    # -----------------------------------------------------------
    # LLM DECISION PARSER — MULTI-STEP WORKFLOW
    # -----------------------------------------------------------
    async def process_input(self, user_input: str):
        workspace_id = "pentest_1"
        max_iterations = 5  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1

            # 1️⃣ Load memory
            short_mem = await self.run_tool("mem_get_short", {"workspace_id": workspace_id})
            long_mem = await self.run_tool("mem_get_long", {"workspace_id": workspace_id})

            # 2️⃣ Build context
            chain_context = (
                f"[SHORT MEMORY]: {json.dumps(short_mem)}\n"
                f"[LONG MEMORY]: {json.dumps(long_mem)}\n"
                f"User Request: {user_input}\n\n"
                f"Next Action (JSON):"
            )

            # 3️⃣ Single LLM call
            decision_raw = await asyncio.to_thread(
                self.llm.generate_content,
                system_instruction=self.formatted_system_prompt,
                prompt=chain_context
            )

            decision = self._parse_llm_decision(decision_raw)
            if not decision:
                print("Agent: Invalid LLM output.")
                return

            tool_name = decision.get("tool")
            args = decision.get("args", {}) or {}
            response = decision.get("response")

            # 4️⃣ Execute tool
            if tool_name:
                result = await self.run_tool(tool_name, args)
                print(f"\n[ACTION] {tool_name} executed. Output:\n{json.dumps(result, indent=2)}")

                # Store result in memory for context in next iteration
                await self.run_tool("mem_set_short", {
                    "workspace_id": workspace_id,
                    "data": {
                        "last_tool": tool_name,
                        "last_result": json.dumps(result)
                    }
                })

                # DISCOVERY TOOLS: Continue loop for follow-up action
                # These tools gather information but don't provide final results
                discovery_tools = ["check_available_tools", "search_github_repository", "search_exploit_db"]
                
                if tool_name in discovery_tools:
                    # Store discovery result and continue loop to act on it
                    await self.run_tool("mem_set_short", {
                        "workspace_id": workspace_id,
                        "data": {f"{tool_name}_result": json.dumps(result)}
                    })
                    continue  # Loop again to execute follow-up action

                # ACTION TOOLS: Analyze results and return
                # These tools produce final actionable results
                action_tools = ["execute_system_command", "mem_get_short", "mem_get_long", "mem_set_short", "mem_set_long"]
                
                if tool_name in action_tools or result.get("status") in ["success", "warning"]:
                    # Determine analysis type based on tool and result
                    if tool_name == "execute_system_command":
                        analysis_prompt = (
                            f"System command executed: {args.get('command', 'N/A')}\n"
                            f"Result:\n{json.dumps(result, indent=2)}\n\n"
                            f"Provide a concise, human-readable summary of findings. "
                            f"Focus on: discovered hosts, services, open ports, vulnerabilities, exploits, or other key security insights. "
                            f"Use tables for structured data. Be direct and technical."
                        )
                    else:
                        analysis_prompt = (
                            f"Tool '{tool_name}' executed and returned:\n"
                            f"{json.dumps(result, indent=2)}\n\n"
                            f"Provide a concise summary of findings and next steps if applicable. "
                            f"Be direct and technical."
                        )
                    
                    summary = await asyncio.to_thread(
                        self.llm.generate_content,
                        system_instruction=self.formatted_system_prompt,
                        prompt=analysis_prompt
                    )
                    
                    if summary and summary.strip():
                        print(f"\n[FINDINGS]\n{summary}")
                    return

                # Default: analyze any other tool result
                analysis_prompt = (
                    f"Tool '{tool_name}' was executed with result:\n"
                    f"{json.dumps(result, indent=2)}\n\n"
                    f"Provide a concise, human-readable summary of findings."
                )
                
                summary = await asyncio.to_thread(
                    self.llm.generate_content,
                    system_instruction=self.formatted_system_prompt,
                    prompt=analysis_prompt
                )
                
                if summary and summary.strip():
                    print(f"\n[FINDINGS]\n{summary}")
                return

            # 5️⃣ Or plain response
            if response:
                print(f"\nAgent: {response}")
                return
            
            # If we get here, something went wrong
            print("Agent: Unable to determine next action.")
            return

# -----------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------
if __name__ == "__main__":
    print("Starting Penzer Security Agent...")

    async def main():
        # Async-safe agent creation
        agent = await Agent().async_init()
        print(
            f"Agent ready with {len(agent.tool_schema)} tools "
            f"and {len(agent.resource_uris)} resources."
        )

        while True:
            q = input("\nUser: ")
            if q.lower() in ("quit", "exit"):
                print("Shutting down.")
                break
            await agent.process_input(q)  # ✅ async call

    try:
        asyncio.run(main())
    except Exception as e:
        print("Fatal startup error:", e)
