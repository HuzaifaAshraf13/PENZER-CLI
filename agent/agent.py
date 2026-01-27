# agent/agent.py
import json
import asyncio
import inspect
from typing import Dict, Any, List, Optional

from agent.server import mcp
from agent.llm import LLM
from agent.prompts import SYSTEM_PROMPT

from tools.ToolsPrompts import (
    NMAP_SCAN_PROMPT,
    RUN_MSFCONSOLE_COMMAND_PROMPT,
    SEARCH_GITHUB_TOOL_PROMPT,
    SEARCH_EXPLOIT_DB_TOOL_PROMPT,
    MEM_LOG_FINDING_PROMPT,  # <-- add this
)

# Import session tools and resources
import session.session  # registers memory resources and tools

# Import session prompts
from session.sessionprompts import (
    SCOPE_PROMPT,
    SESSION_SUMMARY_PROMPT,
    OPERATOR_PREF_PROMPT,
    MEMORY_QUERY_TEMPLATE,
    SHORT_TERM_MEMORY_PROMPT,
)


class Agent:
    def __init__(self):
        self.llm = LLM()
        self.mcp_client = mcp

        # Load schema & resources (FastMCP may expose async get_tools)
        self.tool_schema: Dict[str, Any] = asyncio.run(self._load_tool_schema())
        self.resource_uris: List[str] = self._load_resource_uris()

        # Build system prompt
        self.formatted_system_prompt: str = self._build_system_prompt()

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
        """SYSTEM_PROMPT + merged tool prompts + registered tools + resources."""
        combined_tool_guide = "\n\n".join(
            [
                NMAP_SCAN_PROMPT,
                RUN_MSFCONSOLE_COMMAND_PROMPT,
                SEARCH_GITHUB_TOOL_PROMPT,
                SEARCH_EXPLOIT_DB_TOOL_PROMPT,
                MEM_LOG_FINDING_PROMPT,  # <-- include this
            ]
        )

        # create a safe, JSON-serializable summary of registered tools
        tools_info = self._serialize_tools_for_prompt(self.tool_schema or {})

        merged = f"""
{SYSTEM_PROMPT}

# === SESSION CONTEXT ===
{SCOPE_PROMPT}
{SESSION_SUMMARY_PROMPT}
{OPERATOR_PREF_PROMPT}
{MEMORY_QUERY_TEMPLATE}
{SHORT_TERM_MEMORY_PROMPT}

# === TOOL INSTRUCTIONS ===
{combined_tool_guide}

# === REGISTERED TOOLS (names + args) ===
{json.dumps(tools_info, indent=2)}

# === RESOURCES ===
{chr(10).join(self.resource_uris)}
"""
        return merged.strip()

    # -----------------------------------------------------------
    # TOOL EXECUTION (supports FastMCP.get_tools())
    # -----------------------------------------------------------
    def run_tool(self, tool_name: str, args: Dict) -> Dict:
        # Default workspace (agent-level context)
        workspace_id = "pentest_1"

        # Inject workspace if agent has it
        if "workspace_id" not in args:
            args["workspace_id"] = workspace_id

        try:
            tools_dict = (
                asyncio.run(self.mcp_client.get_tools())
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
            # 🔥 Signature-aware filtering (TOOLS + SESSION)
            sig = inspect.signature(callable_obj)
            filtered_args = {
                k: v for k, v in args.items()
                if k in sig.parameters
            }

            # If tool explicitly wants workspace_id, ensure it's there
            if "workspace_id" in sig.parameters and "workspace_id" not in filtered_args:
                filtered_args["workspace_id"] = workspace_id

            if asyncio.iscoroutinefunction(callable_obj):
                return asyncio.run(callable_obj(**filtered_args))
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

    # -----------------------------------------------------------
    # LLM DECISION PARSER
    # -----------------------------------------------------------
    def _parse_llm_decision(self, raw: str) -> Optional[Dict]:
        try:
            txt = raw.strip()
            if txt.startswith("```"):
                lines = txt.split("\n")
                txt = "\n".join(lines[1:-1]).strip()
            return json.loads(txt)
        except Exception:
            print("\nLLM decision parse failed. Raw output:\n", raw)
            return None

    # -----------------------------------------------------------
    # MAIN INPUT PROCESSOR
    # -----------------------------------------------------------
    def process_input(self, user_input: str):
        workspace_id = "pentest_1"  # default workspace

        # 1️⃣ Fetch memory first
        short_mem = self.run_tool("mem_get_short", {"workspace_id": workspace_id})
        long_mem = self.run_tool("mem_get_long", {"workspace_id": workspace_id})

        # 2️⃣ Build initial chain context for LLM
        chain_context = (
            f"[SHORT MEMORY]: {json.dumps(short_mem)}\n"
            f"[LONG MEMORY]: {json.dumps(long_mem)}\n"
            f"User Request: {user_input}"
        )

        # 3️⃣ Run LLM-tool loop (max 4 iterations)
        for _ in range(4):
            # Ask LLM for next action
            decision_raw = self.llm.generate_content(
                system_instruction=self.formatted_system_prompt,
                prompt=f"{chain_context}\n\nNext Action (JSON):"
            )

            decision = self._parse_llm_decision(decision_raw)
            if not decision:
                print("Agent: Logic error or invalid LLM output.")
                break

            tool_name = decision.get("tool")
            args = decision.get("args", {}) or {}
            response = decision.get("response")

            # 4️⃣ Execute tool if present
            if tool_name:
                result = self.run_tool(tool_name, args)

                # Print output for user immediately
                print(f"\n[ACTION] {tool_name} executed. Output:\n{json.dumps(result, indent=2)}")

                # Auto-log execution tool output to short-term memory
                MEMORY_TOOLS = {"mem_get_short", "mem_set_short", "mem_get_long", "mem_set_long", "mem_log_finding"}
                if tool_name not in MEMORY_TOOLS:
                    self.run_tool("mem_set_short", {
                        "workspace_id": workspace_id,
                        "last_tool": tool_name,
                        "last_result": json.dumps(result)
                    })

                # Append result to chain context for LLM
                chain_context += f"\nObservation from {tool_name}: {json.dumps(result)}"
                continue

            # 5️⃣ Output LLM response if no tool
            if response:
                print(f"\nAgent: {response}")
                break

# -----------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------
if __name__ == "__main__":
    print("Starting Penzer Security Agent...")

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        agent = Agent()
        print(
            f"Agent ready with {len(agent.tool_schema)} tools "
            f"and {len(agent.resource_uris)} resources."
        )

        while True:
            q = input("\nUser: ")
            if q.lower() in ("quit", "exit"):
                print("Shutting down.")
                break
            agent.process_input(q)

    except Exception as e:
        print("Fatal startup error:", e)
