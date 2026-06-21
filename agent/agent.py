"""
PENZER — ReflAct Agent
Goal locked at start. Reflected every step. One loop. No drift.
"""
import json
import logging
import inspect
import asyncio
from typing import Any, Callable

from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_memory, get_memory_context, remember,
    load_history, save_history, clear_history,
)
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
MAX_ITER      = 15   # hard ceiling
TRIM_AT       = 35   # compress history past this length
KEEP_LAST     = 10   # messages kept verbatim during trim
STUCK_WINDOW  = 6    # history window for stuck detection
STUCK_MIN     = 2    # minimum tool results before stuck can fire
MAX_FAILURES  = 3    # recovery attempts before giving up

TOOL_LABELS: dict[str, str] = {
    "browser":        "🌐 Searching",
    "terminal":       "⚡ Running",
    "run_python":     "🐍 Python",
    "run_bash":       "📜 Script",
    "file_editor":    "📁 File",
    "memory":         "🧠 Memory",
    "planning":       "📋 Planning",
    "skill_generator":"🔧 Learning",
}

# Only fall back when the destination is a genuine equivalent.
# browser → terminal removed: completely different capability.
FALLBACKS: dict[str, str] = {
    "terminal":   "run_bash",
    "run_bash":   "run_python",
    "run_python": "terminal",
    "file_editor":"terminal",
}

# ── Agent ───────────────────────────────────────────────────────────────────────

class PenzerAgent:
    def __init__(self) -> None:
        self.llm              = LLM()
        self.tools: dict      = {}
        self.memory           = load_memory() or {}
        self.history          = load_history()
        self.on_status: Callable[[str], None] = lambda m: None

        # Per-run state — fully reset in run()
        self._cache: dict[str, str]   = {}   # "tool:args_json" → result (errors NOT cached)
        self._trace: list[dict]       = []   # lightweight step log
        self._failures: int           = 0
        self._goal: str               = ""
        self._skills_dirty: bool      = False
        self._last_matched_skills: list[str] = []

        data = load_all_skills()
        self.core_skills = data["core"]
        self.gen_skills  = data["generated"]

    async def async_init(self) -> "PenzerAgent":
        try:
            import tools.tools
        except Exception as e:
            logger.warning("Tools import: %s", e)
        try:
            self.tools = await mcp.get_tools() or {}
        except Exception as e:
            logger.warning("MCP init: %s", e)
        return self

    # ── Public ─────────────────────────────────────────────────────────────────

    async def run(self, user_input: str) -> str:
        self._goal         = user_input
        self._trace        = []
        self._failures     = 0
        self._cache        = {}
        self._skills_dirty = False
        self._last_matched_skills = []

        self.history.append({"role": "user", "content": user_input})

        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )
        self._last_matched_skills = (
            [s.name for s in self.core_skills
             if any(k.lower() in user_input.lower() for k in s.keywords)]
            + [s.name for s in matched_gen]
        )

        system = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            extra=get_memory_context(self.memory),
        )

        result = await self._loop(system)

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills = data["core"]
            self.gen_skills  = data["generated"]

        remember(self.memory, f"Completed: {result[:120]}")
        save_history(self.history)
        return result

    # ── Core loop ───────────────────────────────────────────────────────────────

    async def _loop(self, system: str) -> str:
        empty_streak = 0

        for i in range(MAX_ITER):
            await self._trim()
            self.on_status("Thinking…" if i == 0 else f"Step {i + 1}…")

            msgs = self._build_messages(i)
            r    = await self.llm.chat(system=system, messages=msgs)

            tool_calls = r.get("tool_calls") or []
            content    = r.get("content", "").strip()

            # ── No tool calls: answer or nudge ───────────────────────────────
            if not tool_calls:
                if content:
                    self.history.append({"role": "assistant", "content": content})
                    return content
                empty_streak += 1
                if empty_streak >= 2:
                    return "No response received. Try rephrasing your request."
                if self._last_role() == "tool":
                    self.history.append({
                        "role": "user",
                        "content": (
                            f"Goal: {self._goal}\n"
                            "You have a tool result above. "
                            "Does it fully answer the goal? "
                            "Give your final answer or call the next tool."
                        ),
                    })
                continue

            empty_streak = 0
            self.history.append({
                "role": "assistant",
                "content": json.dumps({"reflection": content, "tool_calls": tool_calls}),
            })

            # ── Stuck detection ──────────────────────────────────────────────
            if self._is_stuck():
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    return (
                        f"Stuck after {MAX_FAILURES} recovery attempts. "
                        "Try breaking the task into smaller steps."
                    )
                recovery = await self._reflect()
                self.history.append({"role": "user", "content": f"[Recovery] {recovery}"})
                continue

            # ── Execute — sequential for correct ReflAct ────────────────────
            valid   = [c for c in tool_calls if c["name"] in self.tools]
            invalid = [c for c in tool_calls if c["name"] not in self.tools]

            for c in invalid:
                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", c["name"]),
                    "content": (
                        f"Unknown tool '{c['name']}'. "
                        f"Available: {', '.join(sorted(self.tools))}."
                    ),
                })

            if not valid:
                continue

            # Sequential: each result feeds the next reflection (core ReflAct property)
            for c in valid:
                self.on_status(TOOL_LABELS.get(c["name"], c["name"]) + "…")
                raw    = await self._run(c["name"], c.get("arguments", {}))
                output = self._fmt(c["name"], raw)
                ok     = not self._is_error(raw)

                self._trace.append({
                    "step":    i,
                    "tool":    c["name"],
                    "args":    c.get("arguments", {}),
                    "result":  str(raw)[:300],
                    "success": ok,
                })

                self.history.append({
                    "role":        "tool",
                    "tool_call_id": c.get("id", c["name"]),
                    "content":     f"[{c['name']}]\n{output}",
                })

                # Skills reload trigger — file_editor OR skill_generator
                if c["name"] in ("file_editor", "skill_generator"):
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp:
                        self._skills_dirty = True

        return "Reached iteration limit. Break the task into smaller steps."

    # ── ReflAct message builder ─────────────────────────────────────────────────

    def _build_messages(self, step: int) -> list[dict]:
        """
        Inject a goal-state reflection prompt at every step after the first.
        Based on the ReflAct paper: the agent must reflect on its current state
        *in relation to* the task goal before selecting the next action.
        Not a question for the agent to answer — a directive to structure its thought.
        """
        if step == 0 or not self._trace:
            return self.history

        last    = self._trace[-1]
        status  = "succeeded" if last["success"] else "FAILED"
        prior   = [
            f"  step {s['step']}: {s['tool']} → {'ok' if s['success'] else 'FAILED'}"
            for s in self._trace
        ]

        # Paper instruction: reflect on state in relation to goal, then act.
        # We surface the raw facts; the agent generates the reflection itself.
        injection = (
            f"[ReflAct — step {step}]\n"
            f"GOAL       : {self._goal}\n"
            f"LAST ACTION: {last['tool']}({json.dumps(last['args'])[:80]}) → {status}\n"
            f"LAST OUTPUT: {last['result'][:250]}\n"
            f"PRIOR STEPS:\n" + "\n".join(prior) + "\n\n"
            "Before your next action, reflect in one sentence on your current state "
            "in relation to the task goal. Then either output your final answer or "
            "call the next tool. Do not repeat a failed action."
        )

        return self.history + [{"role": "user", "content": injection}]

    # ── Tool execution ──────────────────────────────────────────────────────────

    async def _run(self, name: str, args: dict) -> str:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"

        # Only return cached result for successes — never serve a cached failure
        if key in self._cache:
            return self._cache[key]

        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' not available."

        for attempt in range(2):
            try:
                fn  = getattr(tool, "fn", tool)
                sig = inspect.signature(fn)
                kw  = {k: v for k, v in args.items() if k in sig.parameters}
                if name == "memory":
                    kw.setdefault("workspace_id", "penzer_default")

                out = await fn(**kw) if inspect.iscoroutinefunction(fn) else fn(**kw)
                s   = str(out)
                self._cache[key] = s  # cache only on success
                return s

            except Exception as e:
                logger.error("%s attempt %d: %s", name, attempt + 1, e)
                if attempt == 1:
                    fb = FALLBACKS.get(name)
                    if fb and fb in self.tools:
                        self.on_status(f"Retrying with {fb}…")
                        return await self._run(fb, self._adapt_args(name, fb, args))
                    return f"{name} error: {e}"

        return f"{name}: unexpected failure"

    def _adapt_args(self, from_tool: str, to_tool: str, args: dict) -> dict:
        """Translate args as losslessly as possible when falling back."""
        if to_tool in ("terminal", "run_bash", "run_python"):
            cmd = (
                args.get("command")
                or args.get("query")
                or args.get("code")
                or args.get("script", "")
            )
            return {"command": cmd}
        return args

    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _fmt(self, name: str, raw: Any) -> str:
        s = str(raw).strip()
        if not s:
            return "(empty output)"
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                if d.get("status") == "error":
                    return f"ERROR: {d.get('message', s)}"
                for key in ("output", "content", "data", "result", "text"):
                    if key in d:
                        return str(d[key])
        except (json.JSONDecodeError, ValueError):
            pass
        if len(s) > 2000:
            return s[:1500] + f"\n…[{len(s) - 1500} chars truncated]"
        return s

    def _is_error(self, result: Any) -> bool:
        s = str(result).lower()
        return any(tok in s for tok in (
            "error:", "failed:", "exception", "traceback",
            "not found", "permission denied", "no such file",
        ))

    def _last_role(self) -> str:
        for m in reversed(self.history):
            if m.get("role") in ("user", "assistant", "tool"):
                return m["role"]
        return ""

    def _is_stuck(self) -> bool:
        window    = self.history[-STUCK_WINDOW:]
        tool_msgs = [m for m in window if m.get("role") == "tool"]

        # Need at least STUCK_MIN results before stuck can fire — prevents false positive
        # on first tool call (old bug: set of 1 element == 1, always triggered)
        if len(tool_msgs) < STUCK_MIN:
            return False

        # Identical outputs
        outputs = [str(m.get("content", ""))[:100] for m in tool_msgs]
        if len(set(outputs)) == 1:
            return True

        # Same tool called 3+ times in a row with no progress
        tool_names: list[str] = []
        for m in window:
            if m.get("role") == "assistant":
                try:
                    for tc in json.loads(m["content"]).get("tool_calls", []):
                        tool_names.append(tc["name"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
        if len(tool_names) >= 3 and len(set(tool_names)) == 1:
            return True

        # All recent steps failed
        recent = self._trace[-STUCK_MIN:]
        if len(recent) >= STUCK_MIN and all(not s["success"] for s in recent):
            return True

        return False

    async def _reflect(self) -> str:
        failed = [
            f"step {s['step']}: {s['tool']}({json.dumps(s['args'])[:60]}) → {s['result'][:100]}"
            for s in self._trace[-4:] if not s["success"]
        ]
        prompt = (
            f"GOAL: {self._goal}\n"
            f"FAILED STEPS:\n" + "\n".join(failed or ["(none recorded)"]) + "\n\n"
            "Respond in this exact format:\n"
            "DIAGNOSIS : [what is going wrong]\n"
            "HYPOTHESIS: [why it is failing]\n"
            "NEXT      : [one specific alternative to try]"
        )
        r = await self.llm.chat(
            system="You are a precise agent debugger. Be specific, not generic.",
            messages=[{"role": "user", "content": prompt}],
        )
        return r.get("content", "Try a completely different approach.")

    async def _trim(self) -> None:
        if len(self.history) <= TRIM_AT:
            return
        first, middle, recent = (
            self.history[:1],
            self.history[1:-KEEP_LAST],
            self.history[-KEEP_LAST:],
        )
        if not middle:
            return
        snippet = "\n".join(
            f"{m['role']}: {str(m.get('content', ''))[:150]}" for m in middle
        )
        try:
            r = await self.llm.chat(
                system="Summarize what was attempted and what was learned. Two sentences max.",
                messages=[{"role": "user", "content": snippet}],
            )
            self.history = (
                first
                + [{"role": "assistant", "content": f"[Context snoummary] {r.get('content', '')}"}]
                + recent
            )
        except Exception:
            self.history = first + recent
        logger.debug("History trimmed to %d messages.", len(self.history))

    # ── Session ─────────────────────────────────────────────────────────────────

    def clear_session(self) -> None:
        self.history.clear()
        self._cache.clear()
        self._trace.clear()
        self._goal = ""
        clear_history()

    def get_trace(self) -> list[dict]:
        return self._trace