"""
Penzer Agent — Pure agentic loop.
LLM drives everything. One LLM call per iteration. No state machine.
"""
import json
import logging
import inspect
import asyncio
from typing import Any, Callable
from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_memory, save_memory, get_memory_context, remember,
    load_history, save_history, clear_history
)
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills
from agent.skills.search import semantic_search_skills, build_context_from_history

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 20
HISTORY_TRIM_THRESHOLD = 80
HISTORY_KEEP_RECENT = 20


class PenzerAgent:
    def __init__(self):
        self.llm = LLM()
        self.tools: dict[str, Any] = {}
        self.memory = load_memory() or {}
        self.skills = load_all_skills()          # list[Skill] now
        self.history = load_history()
        self.on_status: Callable[[str], None] = lambda msg: None
        self._tool_cache: dict = {}
        self._trace: list = []
        self._failures: int = 0

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
        self._trace = []
        self._failures = 0
        self._tool_cache = {}
        remember(self.memory, f"User asked: {user_input[:100]}")

        # Build system prompt ONCE per request
        context = build_context_from_history(self.history)
        relevant_skills = semantic_search_skills(
            user_request=user_input,
            available_skills=self.skills,
            memory=self.memory,
            top_k=3,
            context=context
        )
        system = build_system_prompt(
            skills=relevant_skills,
            extra=get_memory_context(self.memory)
        )

        result = await self._loop(system, user_input)
        self._save()
        return result

    async def _loop(self, system: str, user_input: str) -> str:
        TOOL_LABELS = {
            "browser":     "Searching the web",
            "terminal":    "Running command",
            "run_python":  "Executing Python",
            "run_bash":    "Running script",
            "file_editor": "Editing file",
            "memory":      "Accessing memory",
        }

        for iteration in range(MAX_ITERATIONS):
            await self._trim_history()

            self.on_status("Thinking..." if iteration == 0 else "Continuing...")
            response = await self.llm.chat(system=system, messages=self.history)

            tool_calls = response.get("tool_calls")
            content = response.get("content", "").strip()

            # No tool calls → LLM is done
            if not tool_calls:
                if content:
                    self.history.append({"role": "assistant", "content": content})
                    remember(self.memory, f"Completed: {content[:80]}")
                    return content
                # Empty response with no tools — shouldn't happen, bail out
                logger.warning("Empty response with no tool calls")
                return "Done."

            # Has tool calls — execute them
            self.history.append({"role": "assistant", "content": response})

            if self._is_stuck():
                reflection = await self._reflect(user_input)
                self._failures += 1
                self._trace.append({"iteration": iteration, "recovery": reflection})
                remember(self.memory, f"Recovery: {reflection[:80]}")
                self.history.append({"role": "user", "content": f"[Recovery]: {reflection}"})
                if self._failures >= 3:
                    return "Task failed after multiple recovery attempts."
                continue

            # Parallel tool execution
            if len(tool_calls) > 1:
                self.on_status("Running tools in parallel...")
                results = await asyncio.gather(*[
                    self._call_tool(c["name"], c.get("arguments", {}))
                    for c in tool_calls
                ])
            else:
                label = TOOL_LABELS.get(tool_calls[0]["name"], tool_calls[0]["name"])
                self.on_status(f"{label}...")
                results = [await self._call_tool(
                    tool_calls[0]["name"], tool_calls[0].get("arguments", {})
                )]

            for call, result in zip(tool_calls, results):
                self._trace.append({
                    "iteration": iteration,
                    "tool": call["name"],
                    "result": str(result)[:200]
                })
                remember(self.memory, f"Used {call['name']}: {str(result)[:80]}")
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", call["name"]),
                    "content": result
                })

        logger.warning("Max iterations reached")
        return "Task hit the iteration limit. Try breaking it into smaller steps."

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _call_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if not tool:
            similar = [t for t in self.tools if name[:4] in t]
            hint = f" Did you mean: {similar[0]}?" if similar else ""
            return f"Tool '{name}' not found.{hint}"

        cache_key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if cache_key in self._tool_cache:
            return self._tool_cache[cache_key]

        for attempt in range(2):
            try:
                fn = getattr(tool, "fn", tool)
                sig = inspect.signature(fn)
                filtered = {k: v for k, v in args.items() if k in sig.parameters}
                if name == "memory" and "workspace_id" not in filtered:
                    filtered["workspace_id"] = "penzer_default"
                result = (
                    await fn(**filtered)
                    if inspect.iscoroutinefunction(fn)
                    else fn(**filtered)
                )
                result_str = str(result)
                self._tool_cache[cache_key] = result_str
                return result_str
            except Exception as e:
                logger.error(f"Tool '{name}' attempt {attempt + 1} error: {e}")
                if attempt == 1:
                    return await self._fallback_tool(name, args, str(e))

    async def _fallback_tool(self, failed: str, args: dict, error: str) -> str:
        fallbacks = {
            "browser":     "terminal",
            "terminal":    "run_bash",
            "run_python":  "terminal",
            "file_editor": "terminal"
        }
        fallback = fallbacks.get(failed)
        if not fallback or fallback not in self.tools:
            return f"Tool '{failed}' failed: {error}"
        self.on_status(f"Retrying with {fallback}...")
        cmd = args.get("query") or args.get("command") or args.get("code", "echo fallback")
        return await self._call_tool(fallback, {"command": cmd})

    def _is_stuck(self) -> bool:
        if len(self.history) < 6:
            return False
        last_tools = [m for m in self.history[-6:] if m.get("role") == "tool"]
        if len(last_tools) < 3:
            return False
        same_result = len(set(str(m.get("content", ""))[:50] for m in last_tools)) == 1
        last_calls = [
            m for m in self.history[-6:]
            if isinstance(m.get("content"), dict) and m["content"].get("tool_calls")
        ]
        tool_names = [
            m["content"]["tool_calls"][0]["name"]
            for m in last_calls if m["content"].get("tool_calls")
        ]
        same_tool = len(tool_names) >= 3 and len(set(tool_names)) == 1
        return same_result or same_tool

    async def _reflect(self, task: str) -> str:
        response = await self.llm.chat(
            system="You are a self-reflecting agent. Be critical and specific.",
            messages=[{
                "role": "user",
                "content": f"Stuck on: {task}\nWhat went wrong? What to try differently?"
            }]
        )
        return response.get("content", "Try a different approach.")

    async def _trim_history(self) -> None:
        if len(self.history) <= HISTORY_TRIM_THRESHOLD:
            return
        first = self.history[:1]
        middle = self.history[1:-HISTORY_KEEP_RECENT]
        recent = self.history[-HISTORY_KEEP_RECENT:]
        if not middle:
            return
        middle_text = "\n".join([
            f"{m.get('role', '')}: {str(m.get('content', ''))[:200]}"
            for m in middle
        ])
        try:
            summary_response = await self.llm.chat(
                system="Summarize this conversation history in 3-5 sentences.",
                messages=[{"role": "user", "content": middle_text}]
            )
            summary = summary_response.get("content", "Previous actions summarized.")
            self.history = first + [
                {"role": "assistant", "content": f"[History Summary]: {summary}"}
            ] + recent
        except Exception:
            self.history = first + recent

    def _save(self) -> None:
        save_history(self.history)
        save_memory(self.memory)

    def clear_session(self) -> None:
        self.history = []
        self._tool_cache = {}
        self._trace = []
        clear_history()
        logger.info("Session cleared")

    def get_trace(self) -> list:
        return self._trace