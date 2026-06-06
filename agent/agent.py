"""
Penzer Agent — Agentic loop with Reflexion
"""

import json
import logging
import inspect
from pathlib import Path
from typing import Any, Callable

from agent.core import mcp
from agent.llm import LLM
from agent.memory import load_memory, save_memory
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills
from agent.skills.search import semantic_search_skills

logger = logging.getLogger(__name__)
SESSION_FILE = Path(".penzer_session.json")
MAX_ITERATIONS = 20


class PenzerAgent:
    def __init__(self):
        self.llm = LLM()
        self.tools: dict[str, Any] = {}
        self.memory = load_memory()
        self.skills = load_all_skills()
        self.history = self._load_session()
        self.on_status: Callable[[str], None] = lambda msg: None  # CLI hook

    async def async_init(self) -> "PenzerAgent":
        try:
            import tools.tools
        except Exception as e:
            logger.warning(f"Failed to load tools: {e}")
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
        self.history.append({"role": "user", "content": user_input})

        all_skills = []
        if self.skills:
            for phase, skills in self.skills.items():
                all_skills.extend(skills or [])

        relevant_skills = semantic_search_skills(
            user_request=user_input,
            available_skills=all_skills,
            memory=self.memory,
            top_k=3
        )

        system = build_system_prompt(skills=relevant_skills)
        answer = "No response"

        for iteration in range(MAX_ITERATIONS):
            self._trim_history()
            self.on_status(f"Thinking... (step {iteration + 1})")
            logger.info(f"Iteration {iteration + 1}/{MAX_ITERATIONS}")

            response = await self.llm.chat(system=system, messages=self.history)

            if not response.get("tool_calls"):
                answer = response.get("content", "Done")
                self.history.append({"role": "assistant", "content": answer})
                break

            self.history.append({"role": "assistant", "content": response})

            for call in response["tool_calls"]:
                tool_name = call["name"]
                self.on_status(f"Using {tool_name}...")
                logger.info(f"Calling tool: {tool_name}")
                result = await self._call_tool(tool_name, call.get("arguments", {}))
                logger.info(f"Tool result: {str(result)[:200]}")
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result
                })

            if self._is_stuck():
                logger.warning("Agent stuck — reflecting")
                self.on_status("Reflecting on approach...")
                reflection = await self._reflect(user_input)
                self.history.append({"role": "user", "content": f"[Reflection]: {reflection}"})

        else:
            answer = "Task incomplete — max iterations reached."
            logger.warning("Max iterations reached")
            self.history.append({"role": "assistant", "content": answer})

        self._save_session()
        save_memory(self.memory)
        return answer

    async def _call_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' not found."
        for attempt in range(2):
            try:
                fn = getattr(tool, "fn", tool)
                sig = inspect.signature(fn)
                filtered = {k: v for k, v in args.items() if k in sig.parameters}
                if name == "memory" and "workspace_id" not in filtered:
                    filtered["workspace_id"] = "penzer_default"
                result = await fn(**filtered) if inspect.iscoroutinefunction(fn) else fn(**filtered)
                return str(result)
            except Exception as e:
                logger.error(f"Tool '{name}' attempt {attempt + 1} error: {e}")
                if attempt == 1:
                    return f"Error: {e}"

    def _is_stuck(self) -> bool:
        if len(self.history) < 6:
            return False
        last = [m for m in self.history[-6:] if m.get("role") == "tool"]
        return len(last) >= 3 and len(set(str(m.get("content", ""))[:50] for m in last)) == 1

    async def _reflect(self, original_task: str) -> str:
        response = await self.llm.chat(
            system="You are a self-reflecting agent. Be critical and specific.",
            messages=[{"role": "user", "content": f"You are stuck on: {original_task}\nWhat went wrong? What should you try differently?"}]
        )
        return response.get("content", "Try a different approach.")

    def _trim_history(self) -> None:
        if len(self.history) > 82:
            self.history = self.history[:1] + self.history[-80:]

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
        self.history = []
        SESSION_FILE.unlink(missing_ok=True)
        logger.info("Session cleared")