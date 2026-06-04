"""
Penzer Agent — General-purpose agentic loop (Claude Code style)
"""

import json
import logging
import inspect
from pathlib import Path
from typing import Any

from agent.core import mcp
from agent.llm import LLM
from agent.memory import load_memory, save_memory
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills
from agent.skills.search import semantic_search_skills, format_relevant_skills_for_prompt

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 20
MAX_HISTORY_MESSAGES = 40   # trim beyond this to avoid context overflow
SESSION_FILE = Path(".penzer_session.json")


class PenzerAgent:
    def __init__(self):
        self.llm = LLM()
        self.tools: dict[str, Any] = {}
        self.memory = load_memory()
        self.skills = load_all_skills()
        self.history = self._load_session()

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    async def async_init(self) -> "PenzerAgent":
        """Initialize agent with tools from MCP server."""
        # Ensure tool modules are imported (registers tools via decorators)
        try:
            import tools.tools
            logger.info("✓ Loaded tools.tools")
        except Exception as e:
            logger.warning(f"Failed to load tools.tools: {e}")
        
        try:
            import session.session
            logger.info("✓ Loaded session.session")
        except Exception as e:
            logger.warning(f"Failed to load session.session: {e}")
        
        # Try to get tools from MCP
        self.tools = {}
        
        # First try get_tools() which is the standard FastMCP method
        if hasattr(mcp, "get_tools") and callable(getattr(mcp, "get_tools")):
            try:
                self.tools = await mcp.get_tools() or {}
            except Exception as e:
                logger.warning(f"Failed to fetch tools via get_tools(): {e}")
        
        # Fallback to _tools or tools attribute
        if not self.tools:
            self.tools = getattr(mcp, "_tools", None) or getattr(mcp, "tools", {})
        
        logger.info(f"Agent ready — {len(self.tools)} tools loaded")
        if self.tools:
            logger.info(f"Available tools: {', '.join(self.tools.keys())}")
        return self

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        """Agentic loop: think → call tools → respond."""
        self.history.append({"role": "user", "content": user_input})
        self._trim_history()

        # Flatten skills from dict (keyed by phase) to a flat list
        all_skills = []
        if self.skills:
            for phase, skills in self.skills.items():
                if skills:
                    all_skills.extend(skills)

        # SEMANTIC SEARCH: Find the most relevant skills for this request
        # This ensures the agent always consults the relevant skill docs before reasoning/acting
        relevant_skills = semantic_search_skills(
            user_request=user_input,
            available_skills=all_skills,
            memory=self.memory,
            top_k=3  # Top 3 most relevant skills
        )
        
        # Format relevant skills for injection into the system prompt
        skills_context = format_relevant_skills_for_prompt(relevant_skills)
        
        # Build system prompt with semantic-searched skills
        system = build_system_prompt(
            skills=relevant_skills,  # Pass only the relevant skills
            tools=self.tools,
            memory=self.memory,
            extra=f"\n{skills_context}"
        )

        iterations = 0

        while iterations < MAX_ITERATIONS:
            iterations += 1
            response = await self.llm.chat(system=system, messages=self.history)

            # No tool calls — final answer
            if not response.get("tool_calls"):
                self.history.append({"role": "assistant", "content": response["content"]})
                self._save_session()
                save_memory(self.memory)
                return response["content"]

            # Append assistant turn with tool calls
            self.history.append({"role": "assistant", "content": response})

            # Execute tools, feed results back
            for call in response["tool_calls"]:
                result = await self._call_tool(call["name"], call.get("arguments", {}))
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })

        logger.warning("Max iterations reached")
        return "Reached maximum iterations without a final answer. Try a more specific request."

    # ------------------------------------------------------------------
    # Tool execution (with 1 retry)
    # ------------------------------------------------------------------

    async def _call_tool(self, name: str, args: dict, _retry: bool = True) -> str:
        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' not found."
        
        try:
            fn = getattr(tool, "fn", tool)
            # Filter args to only what the function accepts
            sig = inspect.signature(fn)
            filtered = {k: v for k, v in args.items() if k in sig.parameters}
            
            # For memory tool, add workspace_id if missing
            if name == "memory" and "workspace_id" not in filtered:
                filtered["workspace_id"] = "penzer_default"
            
            result = await fn(**filtered) if inspect.iscoroutinefunction(fn) else fn(**filtered)
            return str(result)
        except Exception as e:
            if _retry:
                logger.warning(f"Tool '{name}' failed, retrying once: {e}")
                return await self._call_tool(name, args, _retry=False)
            logger.error(f"Tool '{name}' failed after retry: {e}")
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # History trimming
    # ------------------------------------------------------------------

    def _trim_history(self) -> None:
        """Keep history within token-safe limits, always preserving the first user message."""
        if len(self.history) <= MAX_HISTORY_MESSAGES:
            return
        # Keep first message (original context) + most recent messages
        self.history = self.history[:1] + self.history[-(MAX_HISTORY_MESSAGES - 1):]
        logger.debug("History trimmed to stay within context limits")

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def _save_session(self) -> None:
        try:
            SESSION_FILE.write_text(json.dumps(self.history, default=str))
        except Exception as e:
            logger.debug(f"Session save failed: {e}")

    def _load_session(self) -> list:
        try:
            if SESSION_FILE.exists():
                return json.loads(SESSION_FILE.read_text())
        except Exception:
            pass
        return []

    def clear_session(self) -> None:
        """Reset conversation history."""
        self.history = []
        SESSION_FILE.unlink(missing_ok=True)
        logger.info("Session cleared")