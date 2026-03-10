# agent/agent.py
import json
import asyncio
import inspect
from typing import Dict, Any, List, Optional

from agent.core import mcp
from agent.llm import LLM
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

    def _build_memory_context(self, short_term: Dict[str, Any], long_term: Dict[str, Any]) -> str:
        """
        Build compact memory context from both short-term and long-term memory.
        Optimized for minimal token usage.
        """
        memory_lines = ["# === MEMORY ==="]
        
        # SHORT-TERM (current discoveries - max 5 entries)
        if short_term:
            memory_lines.append("## Short-term (Current)")
            for i, (key, value) in enumerate(short_term.items()):
                if i >= 5:  # Limit to 5 entries
                    break
                if isinstance(value, dict):
                    memory_lines.append(f"- {key}: {json.dumps(value)[:100]}")
                else:
                    memory_lines.append(f"- {key}: {str(value)[:100]}")
        
        # LONG-TERM (learned knowledge - max 3 entries)
        if long_term:
            memory_lines.append("## Long-term (Learned)")
            for i, (key, value) in enumerate(long_term.items()):
                if i >= 3:  # Limit to 3 entries
                    break
                if key not in ["timestamp", "metadata"]:
                    if isinstance(value, dict):
                        memory_lines.append(f"- {key}: {json.dumps(value)[:100]}")
                    else:
                        memory_lines.append(f"- {key}: {str(value)[:100]}")
        
        return "\n".join(memory_lines)

    def _build_system_prompt(self) -> str:
        """Minimal system prompt - skills handle all instructions on-demand."""
        return "You are Penzer, an autonomous pentesting agent. Return ONLY valid JSON."

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
        Optimized for speed with minimal debug output.
        """
        try:
            txt = raw.strip()
            
            # If it's already a dict, return it
            if isinstance(txt, dict):
                return txt
            
            # Remove markdown-style code blocks
            if isinstance(txt, str) and txt.startswith("```") and txt.endswith("```"):
                lines = txt.split("\n")
                txt = "\n".join(lines[1:-1]).strip()
            
            # Parse JSON
            result = json.loads(txt)
            
            # Ensure it has 'thought' key
            if isinstance(result, dict):
                if "thought" in result:
                    return result
                elif "tool" in result or "action" in result:
                    # Already has action structure, return as-is
                    return result
                else:
                    # Wrap other structures
                    return {"thought": json.dumps(result)}
            
            return {"thought": str(result)}
            
        except json.JSONDecodeError:
            # Try to extract JSON-like content
            if "{" in raw and "}" in raw:
                try:
                    start = raw.find("{")
                    end = raw.rfind("}") + 1
                    result = json.loads(raw[start:end])
                    return result if isinstance(result, dict) and "thought" in result else {"thought": json.dumps(result)}
                except:
                    pass
            return None
        except Exception as e:
            print(f"[ERROR] Parse failed: {e}")
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
        
        # 3️⃣ Fetch SHORT-TERM memory (current session discoveries) - QUICK
        short_mem_result = await self.run_tool("mem_get_short", {"workspace_id": workspace_id})
        short_term_context = short_mem_result.get("data", {}) if isinstance(short_mem_result, dict) else {}
        
        # 4️⃣ Fetch LONG-TERM memory ASYNC (don't block on this, try in background)
        long_term_context = {}
        try:
            # Use a timeout to avoid waiting too long for ReMeApp
            long_mem_task = asyncio.create_task(
                asyncio.to_thread(
                    self.run_tool,
                    "mem_get_long",
                    {"workspace_id": workspace_id}
                )
            )
            # Don't wait if it takes more than 2 seconds
            try:
                long_mem_result = await asyncio.wait_for(long_mem_task, timeout=2.0)
                long_term_context = long_mem_result if isinstance(long_mem_result, dict) else {}
            except asyncio.TimeoutError:
                print("[WARN] Long-term memory fetch timeout, proceeding with short-term only")
                long_term_context = {}
        except Exception as e:
            print(f"[WARN] Long-term memory error: {e}, using short-term only")
        
        # 5️⃣ Build skill context with available memory
        system_prompt_with_skills = create_skill_aware_system_prompt(
            selected_skill,
            base_context=""
        )
        
        # 6️⃣ Combine memory contexts (lightweight)
        memory_section = self._build_memory_context(short_term_context, long_term_context)
        
        system_prompt_with_context = f"""{system_prompt_with_skills}

{memory_section}
"""
        
        # Add initial user message to history
        messages.append({
            "role": "user",
            "content": user_input
        })
        
        while iteration < max_iterations:
            iteration += 1
            print(f"\n[ITERATION {iteration}/{max_iterations}]")
            
            # 6️⃣ Build conversation prompt with message history
            conversation_text = self._build_conversation_prompt(messages)
            
            # 7️⃣ LLM Call with Claude Skills (full instructions loaded on-demand)
            decision_raw = await asyncio.to_thread(
                self.llm.generate_content,
                system_instruction=system_prompt_with_context,
                prompt=conversation_text
            )
            
            print(f"[DEBUG] LLM Raw Response: {decision_raw[:100]}...")
            
            decision = self._parse_llm_decision(decision_raw)
            if not decision:
                print("\n[ERROR] Agent: Invalid LLM output. Expected JSON with 'thought' key.")
                print(f"[DEBUG] Full response was:\n{decision_raw}\n")
                print("[DEBUG] Attempting fallback: wrapping as thought...")
                # Fallback: wrap the raw response as thought
                decision = {"thought": str(decision_raw)[:200]}
                if not decision:
                    print("[ERROR] Fallback failed. Exiting.")
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
                
                # Auto-consolidate findings (async, non-blocking)
                asyncio.create_task(self._consolidate_phase_findings(workspace_id, final_answer))
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
                    
                    # Save tool result to memory (async, non-blocking)
                    asyncio.create_task(self._auto_save_to_memory(workspace_id, tool_name, result))
            else:
                # No tool specified and no final answer — provide a default completion
                print("[AGENT] No action specified. Generating final response...")
                final_answer = f"Completed {self.current_phase.value} phase analysis. Ready for next phase."
                messages.append({
                    "role": "assistant",
                    "content": f"[FINAL ANSWER] {final_answer}"
                })
                print(f"\nAgent: {final_answer}")
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
        
        # Explicit instruction for next decision
        prompt_lines.append("\n" + "="*60)
        prompt_lines.append("NEXT DECISION (respond ONLY with JSON):")
        prompt_lines.append("="*60)
        prompt_lines.append("Respond with JSON:")
        prompt_lines.append('{"thought": "...", "tool": "...", "args": {...}, "final_answer": "..."}')
        return "\n".join(prompt_lines)
    
    async def _consolidate_phase_findings(self, workspace_id: str, phase_summary: str) -> None:
        """
        Consolidate phase findings into long-term memory at phase completion.
        Ensures discoveries are persisted for future sessions.
        """
        try:
            consolidation_data = {
                "phase": self.current_phase.value if self.current_phase else "unknown",
                "skill": self.current_skill.get("name") if self.current_skill else "unknown",
                "summary": phase_summary,
                "timestamp": str(__import__('datetime').datetime.now()),
                "status": "completed"
            }
            
            # Save to long-term memory for persistent knowledge
            await self.run_tool("mem_set_long", {
                "workspace_id": workspace_id,
                f"phase_{self.current_phase.value}_findings": consolidation_data
            })
            
            print(f"[MEMORY] Phase {self.current_phase.value} findings saved to long-term memory")
            
        except Exception as e:
            print(f"[DEBUG] Phase consolidation failed: {e}")

    async def _auto_save_to_memory(self, workspace_id: str, tool_name: str, result: Any) -> None:
        """
        Automatically save tool execution result to BOTH short-term and long-term memory.
        
        Short-term: Quick access during current session
        Long-term: Persistent knowledge for future sessions via ReMeApp
        """
        try:
            # Prepare memory data
            memory_entry = {
                "tool": tool_name,
                "timestamp": str(__import__('datetime').datetime.now()),
                "phase": self.current_phase.value if self.current_phase else "unknown",
                "skill": self.current_skill.get("name") if self.current_skill else "unknown"
            }
            
            # Add result summary
            if isinstance(result, dict):
                memory_entry["result"] = {k: v for k, v in result.items() if k != "error"}[:500]
            else:
                memory_entry["result"] = str(result)[:200]
            
            # 1️⃣ SAVE TO SHORT-TERM MEMORY (in-memory, fast access)
            try:
                await self.run_tool("mem_set_short", {
                    "workspace_id": workspace_id,
                    f"{tool_name}_execution": memory_entry
                })
            except Exception as e:
                print(f"[DEBUG] Short-term memory save failed: {e}")
            
            # 2️⃣ SAVE TO LONG-TERM MEMORY (ReMeApp, persistent)
            try:
                await self.run_tool("mem_set_long", {
                    "workspace_id": workspace_id,
                    f"{tool_name}_{self.current_phase.value}": memory_entry
                })
            except Exception as e:
                print(f"[DEBUG] Long-term memory save failed: {e}")
                
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
