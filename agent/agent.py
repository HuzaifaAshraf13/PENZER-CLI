# agent/agent.py
import json
import asyncio
import inspect
from typing import Dict, Any, List, Optional

from agent.core import mcp
from agent.llm import LLM
from agent.prompts import SYSTEM_PROMPT
from agent.skill_selector import (
    PhaseSpecificSkillsRegistry,
    PentestPhase,
    PentestPhaseDetector,
    SkillSelector,
    ClaudeSkillsAPIBuilder,
    create_skill_aware_system_prompt
)

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
        
        # Phase-specific Claude Agent Skills
        self.skills_registry = PhaseSpecificSkillsRegistry()
        self.skill_selector: Optional[SkillSelector] = None
        self.current_phase: Optional[PentestPhase] = None
        self.current_skill: Optional[Dict[str, Any]] = None

    async def async_init(self):
        # Async-safe initialization
        self.tool_schema = await self._load_tool_schema()
        self.resource_uris = self._load_resource_uris()
        self.formatted_system_prompt = self._build_system_prompt()
        
        # Initialize phase-specific skill selector
        self.skill_selector = SkillSelector(self.skills_registry)
        
        print(f"[AGENT] Initialized with phase-specific pentest skills")
        print(f"[AGENT] Available phases: {', '.join([p.value for p in PentestPhase if p != PentestPhase.UNKNOWN])}")
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
        """Minimal system prompt - Claude Skills handle full instructions on-demand."""
        return SYSTEM_PROMPT.strip()

    def _build_skill_filtered_system_prompt(self, phase: PentestPhase) -> str:
        """
        Minimal phase context - Claude Skills load full instructions on-demand.
        """
        return f"Pentest Phase: {phase.value.upper()}"

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
    # LLM DECISION PARSER — AUTONOMOUS ReAct LOOP WITH PHASE-SPECIFIC SKILLS
    # -----------------------------------------------------------
    async def process_input(self, user_input: str):
        """
        Autonomous ReAct framework with phase-specific Claude Agent Skills.
        Workflow: Scan → Enumeration → Exploitation → Post-Exploitation → Reporting
        """
        workspace_id = "pentest_1"
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        
        # Initialize message history
        messages: List[Dict[str, Any]] = []
        
        # 1️⃣ DETECT PENTEST PHASE
        detected_phase = PentestPhaseDetector.detect_phase(user_input)
        self.current_phase = detected_phase
        
        # 2️⃣ SELECT SKILL FOR PHASE
        if not self.skill_selector:
            print("[AGENT] Skill selector not initialized")
            return
        
        selected_skill, phase = self.skill_selector.select_skill(user_request=user_input)
        self.current_phase = phase
        self.current_skill = selected_skill
        
        if selected_skill:
            print(f"[PHASE DETECTOR] Phase: {phase.value}")
            print(f"[SKILL SELECTOR] Selected '{selected_skill.get('name')}'")
        else:
            print("[SKILL SELECTOR] No matching skill found")
            return
        
        # 3️⃣ Auto-fetch memory context
        short_mem_result = await self.run_tool("mem_get_short", {"workspace_id": workspace_id})
        current_context = short_mem_result.get("data", {}) if isinstance(short_mem_result, dict) else {}
        
        # 4️⃣ Build skill context only (Claude loads full skill instructions on-demand)
        system_prompt_with_skills = create_skill_aware_system_prompt(
            selected_skill,
            base_context=""
        )
        
        # 5️⃣ Add current context
        system_prompt_with_context = f"""{system_prompt_with_skills}

# === CURRENT CONTEXT ===
{json.dumps(current_context, indent=2) if current_context else "No prior context"}
"""
        
        # Add initial user message to history
        messages.append({
            "role": "user",
            "content": user_input
        })
        
        while iteration < max_iterations:
            iteration += 1
            
            # 6️⃣ Build conversation prompt with message history
            conversation_text = self._build_conversation_prompt(messages)
            
            # 7️⃣ LLM Call with Claude Skills (full instructions loaded on-demand)
            decision_raw = await asyncio.to_thread(
                self.llm.generate_content,
                system_instruction=system_prompt_with_context,
                prompt=conversation_text
            )
            
            decision = self._parse_llm_decision(decision_raw)
            if not decision:
                print("Agent: Invalid LLM output. Expected JSON with 'thought' key.")
                return
            
            # Extract ReAct components
            thought = decision.get("thought", "")
            tool_name = decision.get("tool")
            tool_args = decision.get("args", {}) or {}
            final_answer = decision.get("final_answer")
            
            # Append thought to message history
            if thought:
                messages.append({
                    "role": "assistant",
                    "content": f"[THOUGHT] {thought}"
                })
            
            # 8️⃣ Check if task is complete
            if final_answer:
                messages.append({
                    "role": "assistant",
                    "content": f"[FINAL ANSWER] {final_answer}"
                })
                print(f"\nAgent: {final_answer}")
                return
            
            # 9️⃣ Execute tool if specified
            if tool_name:
                print(f"\n[ACTION] Executing: {tool_name}")
                result = await self.run_tool(tool_name, tool_args)
                
                # 🔟 Check for errors
                if isinstance(result, dict) and "error" in result:
                    error_msg = result.get("error", "Unknown error")
                    print(f"[ERROR] {error_msg}")
                    # Append error as observation so LLM can self-correct
                    messages.append({
                        "role": "user",
                        "content": f"[OBSERVATION - ERROR] Tool '{tool_name}' failed: {error_msg}. Please try a different approach."
                    })
                else:
                    # Success: append result as observation
                    print(f"[OBSERVATION] {json.dumps(result, indent=2)[:500]}...")
                    messages.append({
                        "role": "user",
                        "content": f"[OBSERVATION] Tool '{tool_name}' returned:\n{json.dumps(result, indent=2)}"
                    })
                    
                    # 1️⃣1️⃣ Auto-save tool result to short-term memory
                    await self._auto_save_to_memory(workspace_id, tool_name, result)
            else:
                # No tool specified and no final answer — agent is stuck
                print("Agent: No tool specified and no final answer. Ending loop.")
                return
        
        # Max iterations reached
        print(f"Agent: Reached maximum iterations ({max_iterations}). Ending session.")
        return
    
    def _build_conversation_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """
        Convert message history into a prompt string.
        Format: role: content
        """
        prompt_lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            prompt_lines.append(f"{role}: {content}")
        
        prompt_lines.append("\nNext decision (return JSON with 'thought', optionally 'tool'+'args', or 'final_answer'):")
        return "\n".join(prompt_lines)
    
    async def _auto_save_to_memory(self, workspace_id: str, tool_name: str, result: Any) -> None:
        """
        Automatically save tool execution result to short-term memory.
        Runs in background without blocking.
        """
        try:
            # Create a safe key from tool name
            safe_key = tool_name.replace(" ", "_")
            
            # Save the result
            await self.run_tool("mem_set_short", {
                "workspace_id": workspace_id,
                "data": {
                    f"last_execution": {
                        "tool": tool_name,
                        "timestamp": str(__import__('datetime').datetime.now()),
                        "result_summary": str(result)[:200]  # Store summary, not full result
                    }
                }
            })
        except Exception as e:
            # Fail silently for auto-save errors
            print(f"[DEBUG] Auto-save to memory failed: {e}")

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
