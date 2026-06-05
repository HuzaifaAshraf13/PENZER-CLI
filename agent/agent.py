"""
Penzer Agent — Single-pass execution like Claude
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
from tools.terminal_tool import terminal
from tools.browser_tool import browser
from tools.ui_tool import ui
from tools.file_editor_tool import file_editor

logger = logging.getLogger(__name__)
SESSION_FILE = Path(".penzer_session.json")


class PenzerAgent:
    def __init__(self):
        self.llm = LLM()
        self.tools: dict[str, Any] = {}
        self.memory = load_memory()
        self.skills = load_all_skills()
        self.history = self._load_session()

    async def async_init(self) -> "PenzerAgent":
        """Initialize agent with tools from MCP server."""
        # Import tools to register them with MCP
        try:
            import tools.tools
        except Exception as e:
            logger.warning(f"Failed to load tools: {e}")
        
        # Get tools from MCP using get_tools()
        try:
            self.tools = await mcp.get_tools() or {}
        except Exception as e:
            logger.warning(f"Failed to get tools from MCP: {e}")
            self.tools = {}
        
        logger.info(f"Agent ready — {len(self.tools)} tools loaded")
        if self.tools:
            logger.info(f"Available tools: {', '.join(self.tools.keys())}")
        return self

    async def run(self, user_input: str) -> str:
        """Single execution pass: think → act once → respond"""
        self.history.append({"role": "user", "content": user_input})
        
        # Get relevant skills only
        all_skills = []
        if self.skills:
            for phase, skills in self.skills.items():
                if skills:
                    all_skills.extend(skills)
        
        relevant_skills = semantic_search_skills(
            user_request=user_input,
            available_skills=all_skills,
            memory=self.memory,
            top_k=3
        )
        
        # Build minimal system prompt with only relevant skills
        system = build_system_prompt(skills=relevant_skills)
        
        # Single pass: get response
        response = await self.llm.chat(system=system, messages=self.history)
        
        # Execute tools if needed, then return final answer
        if response.get("tool_calls"):
            self.history.append({"role": "assistant", "content": response})
            
            for call in response["tool_calls"]:
                result = await self._call_tool(call["name"], call.get("arguments", {}))
                self.history.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            
            # Get final response after tool execution
            final_response = await self.llm.chat(system=system, messages=self.history)
            answer = final_response.get("content", "Done")
        else:
            answer = response.get("content", "No response")
        
        self.history.append({"role": "assistant", "content": answer})
        self._save_session()
        save_memory(self.memory)
        return answer

    async def _call_tool(self, name: str, args: dict) -> str:
        """Execute a tool"""
        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' not found."
        
        try:
            fn = getattr(tool, "fn", tool)
            sig = inspect.signature(fn)
            filtered = {k: v for k, v in args.items() if k in sig.parameters}
            
            if name == "memory" and "workspace_id" not in filtered:
                filtered["workspace_id"] = "penzer_default"
            
            result = await fn(**filtered) if inspect.iscoroutinefunction(fn) else fn(**filtered)
            return str(result)
        except Exception as e:
            logger.error(f"Tool '{name}' error: {e}")
            return f"Error: {e}"

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