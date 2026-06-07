"""
Penzer Agent — LLM-driven State Machine
LLM decides every state transition. No loop, no iteration counter.
States: PLAN → EXECUTE → VERIFY → DONE (RECOVER on failure)
"""

import json
import logging
import inspect
import asyncio
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from agent.core import mcp
from agent.llm import LLM
from agent.memory import load_memory, save_memory
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills
from agent.skills.search import semantic_search_skills, build_context_from_history

logger = logging.getLogger(__name__)
SESSION_FILE = Path(".penzer_session.json")


class State(Enum):
    PLAN    = "plan"
    EXECUTE = "execute"
    VERIFY  = "verify"
    RECOVER = "recover"
    DONE    = "done"


class PenzerAgent:
    def __init__(self):
        self.llm = LLM()
        self.tools: dict[str, Any] = {}
        self.memory = load_memory()
        self.skills = load_all_skills()
        self.history = self._load_session()
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
        self._check_memory_health()
        logger.info(f"Agent ready — {len(self.tools)} tools loaded")
        if self.tools:
            logger.info(f"Available tools: {', '.join(self.tools.keys())}")
        return self

    async def run(self, user_input: str) -> str:
        """
        LLM-driven state machine.
        LLM decides what state to enter next — not a loop.
        Recursion depth = number of state transitions.
        """
        self.history.append({"role": "user", "content": user_input})
        self._trace = []
        self._failures = 0

        all_skills = []
        if self.skills:
            for phase, skills in self.skills.items():
                all_skills.extend(skills or [])

        return await self._transition(State.PLAN, user_input, all_skills)

    async def _transition(self, state: State, user_input: str, all_skills: list, depth: int = 0) -> str:
        """
        Single state execution. LLM response determines next state.
        No loop — each transition calls _transition recursively.
        Max depth = 20 to prevent infinite recursion.
        """
        if state == State.DONE or depth >= 20:
            self._save_session()
            save_memory(self.memory)
            return self.history[-1].get("content", "Done") if self.history else "Done"

        # Build context-aware system prompt
        context = build_context_from_history(self.history)
        relevant_skills = semantic_search_skills(
            user_request=user_input,
            available_skills=all_skills,
            memory=self.memory,
            top_k=3,
            context=context
        )
        system = build_system_prompt(skills=relevant_skills) + f"\n\nCurrent state: {state.value.upper()}"

        await self._trim_history()

        try:
            if state == State.PLAN:
                return await self._plan(user_input, system, all_skills, depth)
            elif state == State.EXECUTE:
                return await self._execute(user_input, system, all_skills, depth)
            elif state == State.VERIFY:
                return await self._verify(user_input, system, all_skills, depth)
            elif state == State.RECOVER:
                return await self._recover(user_input, system, all_skills, depth)
        except Exception as e:
            logger.error(f"State {state.value} error: {e}")
            self._trace.append({"state": state.value, "error": str(e)})
            self._failures += 1
            if self._failures >= 3:
                self._save_session()
                save_memory(self.memory)
                return "Task failed after multiple errors."
            return await self._transition(State.RECOVER, user_input, all_skills, depth + 1)

    # ─────────────────────────────────────────
    # STATES — LLM response decides next state
    # ─────────────────────────────────────────

    async def _plan(self, user_input: str, system: str, all_skills: list, depth: int) -> str:
        self.on_status("Planning...")
        self._trace.append({"state": "plan"})

        response = await self.llm.chat(system=system, messages=self.history)

        if response.get("tool_calls"):
            # LLM wants to act — go straight to EXECUTE
            self.history.append({"role": "assistant", "content": response})
            return await self._transition(State.EXECUTE, user_input, all_skills, depth + 1)

        content = response.get("content", "")
        self.history.append({"role": "assistant", "content": content})

        # LLM has a final answer already — done
        if not response.get("tool_calls"):
            return await self._transition(State.DONE, user_input, all_skills, depth + 1)

        return await self._transition(State.EXECUTE, user_input, all_skills, depth + 1)

    async def _execute(self, user_input: str, system: str, all_skills: list, depth: int) -> str:
        self.on_status("Executing...")

        # Find pending tool calls from last assistant message
        last = next(
            (m for m in reversed(self.history)
             if isinstance(m.get("content"), dict) and m["content"].get("tool_calls")),
            None
        )

        if not last:
            # No pending tool calls — ask LLM what to do
            response = await self.llm.chat(system=system, messages=self.history)
            if not response.get("tool_calls"):
                answer = response.get("content", "Done")
                self.history.append({"role": "assistant", "content": answer})
                return await self._transition(State.DONE, user_input, all_skills, depth + 1)
            self.history.append({"role": "assistant", "content": response})
            last = self.history[-1]

        calls = last["content"]["tool_calls"]

        TOOL_LABELS = {
            "browser":     "Searching the web",
            "terminal":    "Running command",
            "run_python":  "Executing Python",
            "run_bash":    "Running script",
            "file_editor": "Editing file",
            "memory":      "Accessing memory",
        }

        # Run tool calls — parallel if multiple
        if len(calls) > 1:
            results = await asyncio.gather(*[
                self._call_tool(c["name"], c.get("arguments", {})) for c in calls
            ])
        else:
            results = [await self._call_tool(calls[0]["name"], calls[0].get("arguments", {}))]

        for call, result in zip(calls, results):
            self.on_status(f"{TOOL_LABELS.get(call['name'], call['name'])}...")
            self._trace.append({"state": "execute", "tool": call["name"], "result": str(result)[:200]})
            self.history.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result
            })

        # After execution — LLM verifies
        return await self._transition(State.VERIFY, user_input, all_skills, depth + 1)

    async def _verify(self, user_input: str, system: str, all_skills: list, depth: int) -> str:
        self.on_status("Verifying...")

        response = await self.llm.chat(system=system, messages=self.history)
        content = response.get("content", "")
        self._trace.append({"state": "verify", "response": content[:200]})

        if response.get("tool_calls"):
            # More work needed
            self.history.append({"role": "assistant", "content": response})
            return await self._transition(State.EXECUTE, user_input, all_skills, depth + 1)

        if self._is_stuck():
            return await self._transition(State.RECOVER, user_input, all_skills, depth + 1)

        # LLM satisfied — done
        self.history.append({"role": "assistant", "content": content})
        return await self._transition(State.DONE, user_input, all_skills, depth + 1)

    async def _recover(self, user_input: str, system: str, all_skills: list, depth: int) -> str:
        self.on_status("Recovering...")
        self._failures += 1

        if self._failures >= 3:
            return await self._transition(State.DONE, user_input, all_skills, depth + 1)

        reflection = await self._reflect(user_input)
        self._trace.append({"state": "recover", "reflection": reflection})
        self.history.append({"role": "user", "content": f"[Recovery]: {reflection}"})

        return await self._transition(State.EXECUTE, user_input, all_skills, depth + 1)

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

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
                result = await fn(**filtered) if inspect.iscoroutinefunction(fn) else fn(**filtered)
                result_str = str(result)
                self._tool_cache[cache_key] = result_str
                return result_str
            except Exception as e:
                logger.error(f"Tool '{name}' attempt {attempt + 1} error: {e}")
                if attempt == 1:
                    return await self._fallback_tool(name, args, str(e))

    async def _fallback_tool(self, failed: str, args: dict, error: str) -> str:
        fallbacks = {"browser": "terminal", "terminal": "run_bash",
                     "run_python": "terminal", "file_editor": "terminal"}
        fallback = fallbacks.get(failed)
        if not fallback or fallback not in self.tools:
            return f"Tool '{failed}' failed: {error}"
        self.on_status(f"Retrying with {fallback}...")
        cmd = args.get("query") or args.get("command") or args.get("code", "echo fallback")
        return await self._call_tool(fallback, {"command": cmd})

    def _is_stuck(self) -> bool:
        if len(self.history) < 6:
            return False
        last = [m for m in self.history[-6:] if m.get("role") == "tool"]
        return len(last) >= 3 and len(set(str(m.get("content", ""))[:50] for m in last)) == 1

    async def _reflect(self, task: str) -> str:
        response = await self.llm.chat(
            system="You are a self-reflecting agent. Be critical and specific.",
            messages=[{"role": "user", "content": f"Stuck on: {task}\nWhat went wrong? What to try differently?"}]
        )
        return response.get("content", "Try a different approach.")

    def _check_memory_health(self) -> None:
        if self.memory is None:
            self.memory = {}
            logger.warning("Memory reset to empty")

    async def _trim_history(self) -> None:
        if len(self.history) <= 82:
            return
        first = self.history[:1]
        middle = self.history[1:-20]
        recent = self.history[-20:]
        if not middle:
            return
        middle_text = "\n".join([
            f"{m.get('role', '')}: {str(m.get('content', ''))[:200]}"
            for m in middle
        ])
        try:
            summary_response = await self.llm.chat(
                system="Summarize this conversation history in 3-5 sentences. What was done, found, and failed.",
                messages=[{"role": "user", "content": middle_text}]
            )
            summary = summary_response.get("content", "Previous actions summarized.")
            self.history = first + [{"role": "assistant", "content": f"[History Summary]: {summary}"}] + recent
            logger.info("History summarized")
        except Exception:
            self.history = first + recent

    def _save_session(self) -> None:
        try:
            SESSION_FILE.write_text(json.dumps(self.history, default=str))
        except Exception as e:
            logger.debug(f"Session save failed: {e}")

    def _load_session(self) -> list:
        try:
            if SESSION_FILE.exists():
                data = SESSION_FILE.read_text()
                if data.strip():
                    return json.loads(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Session corrupted — resetting: {e}")
            SESSION_FILE.unlink(missing_ok=True)
        return []

    def clear_session(self) -> None:
        self.history = []
        self._tool_cache = {}
        self._trace = []
        SESSION_FILE.unlink(missing_ok=True)
        logger.info("Session cleared")

    def get_trace(self) -> list:
        return self._trace