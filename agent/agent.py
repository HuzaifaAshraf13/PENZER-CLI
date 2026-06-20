"""
PENZER — Minimal ReflAct Agent
Goal locked at start. Injected every step. One loop. No states.
"""
import json
import logging
import inspect
import asyncio
from typing import Any, Callable
from agent.core import mcp
from agent.llm import LLM
from session.memory import load_memory, get_memory_context, remember, load_history, save_history, clear_history
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history

logger = logging.getLogger(__name__)

MAX_ITER  = 12
TRIM_AT   = 40
KEEP_LAST = 10

TOOL_LABELS = {
    "browser": "🌐 Searching", "terminal": "⚡ Command",
    "run_python": "🐍 Python", "run_bash": "📜 Script",
    "file_editor": "📁 File", "memory": "🧠 Memory",
}
FALLBACKS = {
    "browser": "terminal", "terminal": "run_bash",
    "run_python": "terminal", "file_editor": "terminal",
}


class PenzerAgent:
    def __init__(self):
        self.llm              = LLM()
        self.tools: dict      = {}
        self.memory           = load_memory() or {}
        self.history          = load_history()
        self.on_status: Callable = lambda m: None
        self._cache: dict     = {}
        self._trace: list     = []
        self._failures        = 0
        self._goal            = ""
        self._skills_dirty    = False
        self._last_matched_skills: list = []

        data = load_all_skills()
        self.core_skills = data["core"]
        self.gen_skills  = data["generated"]

    async def async_init(self) -> "PenzerAgent":
        try:
            import tools.tools
        except Exception as e:
            logger.warning(f"Tools: {e}")
        try:
            self.tools = await mcp.get_tools() or {}
        except Exception as e:
            logger.warning(f"MCP: {e}")
        return self

    async def run(self, user_input: str) -> str:
        self._goal            = user_input
        self._trace           = []
        self._failures        = 0
        self._cache           = {}
        self._skills_dirty    = False
        self._last_matched_skills = []

        self.history.append({"role": "user", "content": user_input})
        remember(self.memory, f"User asked: {user_input[:100]}")

        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history)
        )
        self._last_matched_skills = (
            [s.name for s in self.core_skills if any(k.lower() in user_input.lower() for k in s.keywords)]
            + [s.name for s in matched_gen]
        )

        system = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            extra=get_memory_context(self.memory)
        )

        result = await self._loop(system)

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills = data["core"]
            self.gen_skills  = data["generated"]

        save_history(self.history)
        return result

    async def _loop(self, system: str) -> str:
        empty_count = 0

        for i in range(MAX_ITER):
            await self._trim()
            self.on_status("Thinking..." if i == 0 else "Continuing...")

            # ReflAct: inject goal-state every step after first
            msgs = self.history if i == 0 or not self._trace else self.history + [{
                "role": "user",
                "content": (
                    f"GOAL: {self._goal}\n"
                    f"STEPS TAKEN: {len(self._trace)}\n"
                    f"LAST RESULT: {self._trace[-1]['result'][:150]}\n"
                    f"Are you aligned? Answer or continue."
                )
            }]

            r          = await self.llm.chat(system=system, messages=msgs)
            tool_calls = r.get("tool_calls")
            content    = r.get("content", "").strip()

            # No tool calls → done or nudge
            if not tool_calls:
                if content:
                    self.history.append({"role": "assistant", "content": content})
                    remember(self.memory, f"Done: {content[:80]}")
                    return content
                empty_count += 1
                if empty_count >= 2:
                    return "No response. Try rephrasing."
                if self._last_role() == "tool":
                    self.history.append({
                        "role": "user",
                        "content": f"Goal: {self._goal}\nAnalyze the result above and give your final answer."
                    })
                continue

            empty_count = 0
            self.history.append({
                "role": "assistant",
                "content": json.dumps({"thought": content, "tool_calls": tool_calls})
            })

            # Stuck detection
            if self._stuck():
                self._failures += 1
                if self._failures >= 3:
                    return "Stuck after 3 attempts. Try rephrasing."
                ref = await self._reflect()
                self.history.append({"role": "user", "content": f"[Recovery] {ref}"})
                continue

            # Execute
            calls   = tool_calls
            valid   = [c for c in calls if c["name"] in self.tools]
            invalid = [c for c in calls if c["name"] not in self.tools]

            for c in invalid:
                self.history.append({
                    "role": "tool", "tool_call_id": c.get("id", c["name"]),
                    "content": f"Tool '{c['name']}' not found."
                })

            if not valid:
                continue

            if len(valid) > 1:
                self.on_status("⚡ Parallel...")
                results = await asyncio.gather(*[self._run(c["name"], c.get("arguments", {})) for c in valid])
            else:
                self.on_status(TOOL_LABELS.get(valid[0]["name"], valid[0]["name"]) + "...")
                results = [await self._run(valid[0]["name"], valid[0].get("arguments", {}))]

            for c, res in zip(valid, results):
                self._trace.append({"i": i, "tool": c["name"], "result": str(res)[:200]})
                remember(self.memory, f"{c['name']}: {str(res)[:60]}")
                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", c["name"]),
                    "content": f"[{c['name']} result]\n{self._fmt(c['name'], res)}"
                })
                if c["name"] == "file_editor":
                    p = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in p and p.endswith(".skill.md"):
                        self._skills_dirty = True

        return "Hit iteration limit. Break the task into smaller steps."

    # ── Tool ──────────────────────────────────────────────────────────────────

    async def _run(self, name: str, args: dict) -> str:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if key in self._cache:
            return self._cache[key]

        tool = self.tools.get(name)
        for attempt in range(2):
            try:
                fn  = getattr(tool, "fn", tool)
                sig = inspect.signature(fn)
                kw  = {k: v for k, v in args.items() if k in sig.parameters}
                if name == "memory":
                    kw.setdefault("workspace_id", "penzer_default")
                out = await fn(**kw) if inspect.iscoroutinefunction(fn) else fn(**kw)
                s   = str(out)
                self._cache[key] = s
                return s
            except Exception as e:
                logger.error(f"{name} attempt {attempt+1}: {e}")
                if attempt == 1:
                    fb = FALLBACKS.get(name)
                    if fb and fb in self.tools:
                        self.on_status(f"Retrying with {fb}...")
                        cmd = args.get("query") or args.get("command") or args.get("code", "")
                        return await self._run(fb, {"command": cmd})
                    return f"{name} failed: {e}"
        return ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fmt(self, name: str, raw: Any) -> str:
        s = str(raw).strip()
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                if d.get("status") == "error":
                    return f"ERROR: {d.get('message', s)}"
                return str(d.get("output") or d.get("content") or d.get("data") or s)
        except (json.JSONDecodeError, ValueError):
            pass
        return s or "(empty)"

    def _last_role(self) -> str:
        for m in reversed(self.history):
            if m.get("role") in ("user", "assistant", "tool"):
                return m["role"]
        return ""

    def _stuck(self) -> bool:
        tools = [m for m in self.history[-6:] if m.get("role") == "tool"]
        if len(tools) < 3:
            return False
        same_out  = len(set(str(m["content"])[:80] for m in tools)) == 1
        names     = []
        for m in self.history[-6:]:
            if m.get("role") == "assistant":
                try:
                    names.append(json.loads(m["content"])["tool_calls"][0]["name"])
                except Exception:
                    pass
        same_tool = len(names) >= 3 and len(set(names)) == 1
        return same_out or same_tool

    async def _reflect(self) -> str:
        r = await self.llm.chat(
            system="Critical agent auditor. One sentence. What failed and what to try instead.",
            messages=[{"role": "user", "content": f"Goal: {self._goal}\nFailed steps: {self._trace[-3:]}"}]
        )
        return r.get("content", "Try a different approach.")

    async def _trim(self) -> None:
        if len(self.history) <= TRIM_AT:
            return
        first, middle, recent = self.history[:1], self.history[1:-KEEP_LAST], self.history[-KEEP_LAST:]
        if not middle:
            return
        try:
            r = await self.llm.chat(
                system="Summarize in 2 sentences: what was done, what worked.",
                messages=[{"role": "user", "content": "\n".join(
                    f"{m['role']}: {str(m.get('content',''))[:120]}" for m in middle
                )}]
            )
            self.history = first + [{"role": "assistant", "content": f"[Summary] {r.get('content','')}"}] + recent
        except Exception:
            self.history = first + recent

    def clear_session(self) -> None:
        self.history, self._cache, self._trace, self._goal = [], {}, [], ""
        clear_history()

    def get_trace(self) -> list:
        return self._trace