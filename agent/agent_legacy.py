# agent/agent.py
"""
Autonomous Pentesting Agent with ReAct Framework
Full LLM autonomy with tool execution and memory management
"""

import json
import asyncio
import inspect
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from agent.core import mcp, init_reme, cleanup_reme
from agent.llm import LLM
from agent.skill_selector import (
    SkillSelector,
    create_skill_aware_system_prompt
)
from agent.skills import PentestPhase, load_all_skills

# Import session tools and resources
import session.session  # registers memory resources and tools

# Setup logging
logger = logging.getLogger(__name__)


class Agent:
    def __init__(self):
        self.llm = LLM()
        self.mcp_client = mcp

        # Keep empty for now; async_init will fill them
        self.tool_schema: Dict[str, Any] = {}
        self.resource_uris: List[str] = []
        self.formatted_system_prompt: str = ""
        
        # Load all skills from modular system
        self.all_skills = load_all_skills()
        self.skill_selector: Optional[SkillSelector] = None
        self.current_phase: Optional[PentestPhase] = None
        self.current_skill: Optional[Dict[str, Any]] = None

    async def async_init(self):
        # Initialize ReMeApp for long-term memory
        reme_success = await init_reme()
        if not reme_success:
            print("[WARN] Long-term memory unavailable, continuing with short-term only")
        
        # Async-safe initialization
        self.tool_schema = await self._load_tool_schema()
        self.resource_uris = self._load_resource_uris()
        self.formatted_system_prompt = self._build_system_prompt()
        
        # Initialize skill selector with loaded skills
        self.skill_selector = SkillSelector(self.all_skills)
        
        # Skills are loaded in __init__, just report status
        print(f"[AGENT] Loaded {sum(len(s) for s in self.all_skills.values())} skills across {len(self.all_skills)} phases")
        return self

    # -----------------------------------------------------------
    # LOADERS
    # -----------------------------------------------------------
    async def _load_tool_schema(self) -> Dict[str, Any]:
        """FastMCP exposes get_tools(), not .tools on some versions."""
        try:
            if hasattr(self.mcp_client, "get_tools"):
                tools = await self.mcp_client.get_tools()
            else:
                tools = getattr(self.mcp_client, "tools", {})

            # Convert tool objects into a JSON-serializable schema suitable for
            # inclusion in system prompts (name -> {"args": [...]}) so the LLM
            # can inspect available tools without touching FunctionTool objects.
            try:
                return self._serialize_tools_for_prompt(tools)
            except Exception:
                # Fallback: return raw tools if serialization fails
                return tools
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

    def _build_llm_autonomous_system_prompt(self, short_term: Dict[str, Any], long_term: Dict[str, Any]) -> str:
        """
        Build system prompt that gives LLM complete autonomy.
        LLM sees: all available tools, all skills (as reference), and memory context.
        LLM decides: what to do, which tools to call, whether to follow skills or create own approach.
        """
        # Build available tools reference
        tools_ref = "## Available Tools (you can call any of these):\n"
        if self.tool_schema:
            for tool_name, tool_info in self.tool_schema.items():
                args = ", ".join(tool_info.get("args", []))
                tools_ref += f"  - {tool_name}({args})\n"
        else:
            tools_ref += "  (No tools loaded yet)\n"
        
        # Build available skills reference (for context, but LLM is not bound by them)
        skills_ref = "## Available Pentesting Skills (reference only, you can use any approach):\n"
        if self.all_skills:
            for phase, skills in self.all_skills.items():
                skills_ref += f"  {phase.value.upper()}:\n"
                for skill in skills:
                    skill_name = skill.name if hasattr(skill, 'name') else skill.get('name', 'Unknown')
                    skills_ref += f"    - {skill_name}\n"
        
        # Build memory context
        memory_section = self._build_memory_context(short_term, long_term)
        
        system_prompt = f"""You are Penzer, an autonomous pentesting agent with FULL AUTONOMY.

You have complete access to all available tools and skills.
YOU decide what to do based on the user's request.
YOU choose which tools to call.
YOU choose whether to follow the available skills or create your own approach.

{tools_ref}

{skills_ref}

{memory_section}

DECISION FRAMEWORK (ReAct):
1. THINK about what the user is asking
2. DECIDE which tool(s) to use or skill(s) to follow
3. EXECUTE the tool(s) with appropriate arguments
4. OBSERVE the results
5. REPEAT until you have a final answer or all attempts are exhausted
6. When done, provide FINAL_ANSWER with clear findings

RESPONSE FORMAT (ONLY valid JSON):
{{
  "thought": "Your reasoning and analysis",
  "tool": "tool_name_to_call",  [OPTIONAL - omit if providing final_answer]
  "args": {{"arg1": "value1", ...}},  [OPTIONAL - required if tool is specified]
  "final_answer": "Clear findings and results"  [OPTIONAL - provide when task is complete]
}}

Rules:
- Always include "thought" explaining your reasoning
- You can chain multiple tool calls by using observations from previous results
- You are NOT limited to the listed skills - use your judgment
- You can use any tool for any purpose based on the request
- When you have enough information, provide a final_answer
"""
        return system_prompt

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
        """
        Execute a tool via MCP.
        
        Returns:
            Standardized ToolResult dict with status, data, error, metadata
        """
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
            return {"status": "error", "error": f"Cannot fetch MCP tools: {e}"}

        tool = tools_dict.get(tool_name)
        if not tool:
            return {"status": "error", "error": f"Unknown tool: {tool_name}"}

        callable_obj = getattr(tool, "fn", tool)

        try:
            # Signature-aware argument filtering
            sig = inspect.signature(callable_obj)
            filtered_args = {k: v for k, v in args.items() if k in sig.parameters}

            # Ensure workspace_id is included if needed
            if "workspace_id" in sig.parameters and "workspace_id" not in filtered_args:
                filtered_args["workspace_id"] = workspace_id

            # Async or sync execution
            if asyncio.iscoroutinefunction(callable_obj):
                result = await callable_obj(**filtered_args)
            else:
                result = callable_obj(**filtered_args)
            
            # Ensure result is a dict with status field
            if not isinstance(result, dict):
                return {"status": "error", "error": f"Tool returned non-dict: {type(result)}"}
            
            # Wrap result if it doesn't have status field (backwards compatibility)
            if "status" not in result:
                return {"status": "success", "data": result}
            
            return result

        except TypeError as e:
            return {
                "status": "error",
                "error": f"Tool invocation failed (TypeError): {e}",
                "metadata": {
                    "tool_name": tool_name,
                    "provided_args": list(args.keys()),
                    "filtered_args": list(filtered_args.keys()) if 'filtered_args' in locals() else []
                }
            }
        except Exception as e:
            return {
                "status": "error",
                "error": f"Tool execution failed: {type(e).__name__}: {str(e)[:200]}"
            }
    
    def _parse_llm_decision(self, raw: str) -> dict | None:
        """
        Parse LLM response into structured decision dict.
        
        **ROBUSTNESS**: Handles multiple input formats:
        1. Valid JSON
        2. JSON wrapped in markdown backticks (```json ... ```)
        3. JSON with nested JSON string (json.dumps'd content)
        4. Partial JSON (extracts first {...} block)
        5. Non-JSON (wraps as thought field fallback)
        
        **RETURNS**: Dict with required "thought" field, or None if completely unparseable
        """
        if not raw or not isinstance(raw, str):
            return None
        
        txt = raw.strip()
        
        # Already a dict
        if isinstance(txt, dict):
            return txt if "thought" in txt else {"thought": json.dumps(txt)}
        
        # Try 1: Remove markdown code blocks (```...```)
        if txt.startswith("```") and txt.endswith("```"):
            lines = txt.split("\n")
            # Handle ```json ... ``` or just ```...```
            if len(lines) > 2:
                txt = "\n".join(lines[1:-1]).strip()
        
        # Try 2: Direct JSON parse
        try:
            result = json.loads(txt)
            if isinstance(result, dict):
                # Handle nested JSON in thought field
                if "thought" in result and isinstance(result["thought"], str):
                    try:
                        nested = json.loads(result["thought"])
                        if isinstance(nested, dict):
                            # Merge nested fields, preserving outer keys
                            for key in ["tool", "args", "final_answer"]:
                                if key not in result and key in nested:
                                    result[key] = nested[key]
                            if "thought" in nested:
                                result["thought"] = nested["thought"]
                    except json.JSONDecodeError:
                        pass  # thought is plain text, keep as is
                
                # Ensure 'thought' key exists
                if "thought" not in result:
                    result["thought"] = json.dumps({k: v for k, v in result.items() if k not in ["tool", "args", "final_answer"]})
                return result
            else:
                # Non-dict JSON (array, string, number, etc)
                return {"thought": json.dumps(result)}
        except json.JSONDecodeError:
            pass
        
        # Try 3: Extract JSON from partial/malformed input
        start_idx = txt.find("{")
        end_idx = txt.rfind("}")
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            try:
                extracted = txt[start_idx:end_idx + 1]
                result = json.loads(extracted)
                if isinstance(result, dict):
                    # Nested JSON handling again
                    if "thought" in result and isinstance(result["thought"], str):
                        try:
                            nested = json.loads(result["thought"])
                            if isinstance(nested, dict):
                                for key in ["tool", "args", "final_answer"]:
                                    if key not in result and key in nested:
                                        result[key] = nested[key]
                                if "thought" in nested:
                                    result["thought"] = nested["thought"]
                        except json.JSONDecodeError:
                            pass
                    
                    if "thought" not in result:
                        result["thought"] = json.dumps({k: v for k, v in result.items() if k not in ["tool", "args", "final_answer"]})
                    return result
            except json.JSONDecodeError:
                pass
        
        # Try 4: Last resort - wrap as thought if it looks like a response
        if any(keyword in txt.lower() for keyword in ["thought", "tool", "final_answer", "action", "observation"]):
            return {"thought": txt[:500]}
        
        # Completely unparseable
        return None
            
    # -----------------------------------------------------------
    # USER REQUEST HANDLER
    # -----------------------------------------------------------
    async def execute_user_request(self, user_input: str) -> Dict[str, str]:
        """
        Execute user request and return standardized response.
        
        Returns:
            {"status": "success|error", "response": "..."}
        """
        try:
            print(f"[DEBUG] Processing user input: {user_input}")
            # Add timeout to prevent hanging
            await asyncio.wait_for(self.process_input(user_input), timeout=60.0)
            return {"status": "success", "response": "Request processed successfully"}
        except asyncio.TimeoutError:
            print("[ERROR] Request timed out after 60 seconds")
            return {"status": "error", "response": "Request timed out"}
        except Exception as e:
            print(f"[ERROR] Exception occurred: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "response": str(e)}
    
    # -----------------------------------------------------------
    # UNIFIED SKILL-DRIVEN WORKFLOW (ReAct Framework)
    # -----------------------------------------------------------
    async def process_input(self, user_input: str):
        """
        Pure LLM-driven ReAct workflow with full autonomy.
        
        Flow:
        1. Fetch memory context and available tools/skills
        2. Give LLM complete control with:
           - All available MCP tools
           - All pentesting skills (as reference information)
           - User request
        3. LLM decides what to do (which tools, which skills to follow, etc)
        4. Autonomous loop (max 10 iterations):
           a. LLM analyzes situation and decides action
           b. Execute LLM-decided tool
           c. Feed result back to LLM
           d. Repeat until LLM provides final_answer
        5. Auto-save results to memory
        """
        workspace_id = "pentest_1"
        max_iterations = 10
        iteration = 0
        messages: List[Dict[str, Any]] = []
        
        # ========== INITIALIZATION ==========
        # 1️⃣ Fetch memory for context (but LLM decides what to do with it)
        short_term_context = {}
        short_mem_result = await self.run_tool("mem_get_short", {"workspace_id": workspace_id})
        if short_mem_result.get("status") == "success":
            short_term_context = short_mem_result.get("data", {})
            print(f"[MEMORY] Short-term context loaded: {len(short_term_context)} entries")
        
        long_term_context = {}
        try:
            long_mem_task = asyncio.create_task(
                self.run_tool("mem_get_long", {"workspace_id": workspace_id})
            )
            try:
                long_mem_result = await asyncio.wait_for(long_mem_task, timeout=2.0)
                if long_mem_result.get("status") == "success":
                    long_term_context = long_mem_result.get("data", {})
                    print(f"[MEMORY] Long-term context loaded: {len(long_term_context)} entries")
            except asyncio.TimeoutError:
                print("[WARN] Long-term memory fetch timed out")
        except Exception as e:
            print(f"[WARN] Long-term memory error: {e}")
        
        # 2️⃣ Build system prompt with ALL tools and skills available (no restrictions)
        system_prompt = self._build_llm_autonomous_system_prompt(short_term_context, long_term_context)
        
        # 3️⃣ INITIALIZE messages list with user input
        messages.append({
            "role": "user",
            "content": user_input
        })
        
        # ========== AUTONOMOUS LOOP ==========
        while iteration < max_iterations:
            iteration += 1
            print(f"\n[ITERATION {iteration}/{max_iterations}]")
            
            # 6️⃣ BUILD CONVERSATION from message history
            conversation_text = self._build_conversation_prompt(messages)
            
            # 7️⃣ LLM CALL with system context
            decision_raw = await asyncio.to_thread(
                self.llm.generate_content,
                system_instruction=system_prompt,
                prompt=conversation_text
            )
            
            print(f"[DEBUG] LLM Response: {decision_raw[:80]}...")
            
            # 8️⃣ PARSE DECISION
            decision = self._parse_llm_decision(decision_raw)
            if not decision or "thought" not in decision:
                print(f"[ERROR] Invalid LLM output. Raw response:\n{decision_raw[:200]}")
                # Fallback: use raw output as thought
                decision = {"thought": str(decision_raw)[:500]}
            
            # Extract ReAct components
            thought = decision.get("thought", "")
            tool_name = decision.get("tool")
            tool_args = decision.get("args", {}) or {}
            final_answer = decision.get("final_answer")
            
            # 9️⃣ Append THOUGHT to message history
            if thought:
                messages.append({
                    "role": "assistant",
                    "content": f"[THOUGHT] {thought}"
                })
            
            # ========== DECISION BRANCHES ==========
            
            # 🔟 Branch 1: FINAL_ANSWER - Task complete
            if final_answer:
                messages.append({
                    "role": "assistant",
                    "content": f"[FINAL ANSWER] {final_answer}"
                })
                print(f"\n[FINAL] Agent: {final_answer}")
                
                # Auto-consolidate findings (async, non-blocking)
                asyncio.create_task(self._consolidate_phase_findings(workspace_id, final_answer))
                return
            
            # 1️⃣1️⃣ Branch 2: HAS TOOL - Execute tool
            if tool_name:
                print(f"[ACTION] Executing: {tool_name}")
                result = await self.run_tool(tool_name, tool_args)
                
                # Handle result status
                result_status = result.get("status", "unknown")
                
                if result_status == "error":
                    # ERROR OBSERVATION: Tool failed, LLM should try different approach
                    error_msg = result.get("error", "Unknown error")
                    print(f"[ERROR] {error_msg}")
                    messages.append({
                        "role": "user",
                        "content": f"[OBSERVATION - ERROR] Tool '{tool_name}' failed: {error_msg}. Try a different approach."
                    })
                    # Continue to next iteration for self-correction
                    
                else:
                    # SUCCESS OBSERVATION: Tool succeeded, save result
                    data = result.get("data", result)
                    print(f"[SUCCESS] Tool executed successfully")
                    messages.append({
                        "role": "user",
                        "content": f"[OBSERVATION] Tool '{tool_name}' returned:\n{json.dumps(data, indent=2)[:1000]}"
                    })
                    
                    # AUTO-SAVE to memory (non-blocking background task)
                    asyncio.create_task(self._auto_save_to_memory(workspace_id, tool_name, result))
            else:
                # Branch 3: NO TOOL and NO FINAL_ANSWER - Generate default completion
                print("[AGENT] No action specified. Generating completion...")
                final_answer = f"Completed {self.current_phase.value} phase. Ready for next phase."
                messages.append({
                    "role": "assistant",
                    "content": f"[FINAL ANSWER] {final_answer}"
                })
                print(f"\n[FINAL] Agent: {final_answer}")
                return
        
        # ========== SAFEGUARD: Max iterations reached ==========
        print(f"\n[SAFEGUARD] Reached maximum iterations ({max_iterations}). Ending session.")
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
        Automatically save tool execution result to memory.
        
        **DESIGN**: Non-blocking, runs in background after tool execution
        - Timing: After successful tool execution (status != error)
        - Behavior: Fails silently, doesn't interrupt main loop
        - Destination: Short-term memory for quick access
        - Benefit: Next iteration reads updated memory for context
        """
        try:
            # Extract result summary (first 200 chars)
            result_summary = ""
            if isinstance(result, dict):
                result_data = result.get("data", result)
                if isinstance(result_data, dict):
                    result_summary = json.dumps(result_data)[:200]
                else:
                    result_summary = str(result_data)[:200]
            else:
                result_summary = str(result)[:200]
            
            # Prepare memory entry
            memory_entry = {
                "tool": tool_name,
                "timestamp": str(__import__('datetime').datetime.now()),
                "phase": self.current_phase.value if self.current_phase else "unknown",
                "skill": self.current_skill.get("name") if self.current_skill else "unknown",
                "result_summary": result_summary
            }
            
            # Save to short-term memory (fast, in-memory)
            await self.run_tool("mem_set_short", {
                "workspace_id": workspace_id,
                "last_execution": memory_entry
            })
            
            print(f"[MEMORY] Auto-saved {tool_name} to short-term memory")
            
        except Exception as e:
            # Fail silently - don't interrupt main loop
            print(f"[DEBUG] Auto-save failed (non-blocking): {e}")

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