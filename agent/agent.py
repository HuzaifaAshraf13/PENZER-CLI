"""PENZER — ReflAct Agent"""
import json, logging, inspect, asyncio
from typing import Any, Callable
from agent.core import mcp
from agent.llm import LLM
from session.memory import load_memory, get_memory_context, remember, load_history, save_history, clear_history
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history

logger = logging.getLogger(__name__)

MAX_ITER, TRIM_AT, KEEP_LAST, STUCK_MIN, MAX_FAILURES = 15, 35, 10, 2, 3

TOOL_LABELS = {
    "browser": "🌐", "terminal": "⚡", "run_python": "🐍",
    "run_bash": "📜", "file_editor": "📁", "memory": "🧠", "planning": "📋",
}
FALLBACKS = {"terminal": "run_bash", "run_bash": "run_python", "run_python": "terminal", "file_editor": "terminal"}


class PenzerAgent:
    def __init__(self) -> None:
        self.llm      = LLM()
        self.tools    = {}
        self.memory   = load_memory() or {}
        self.history  = load_history()
        self.on_status: Callable[[str], None] = lambda m: None
        self._fn_cache: dict = {}   # fn → (signature, is_async)
        self._reset()
        data = load_all_skills()
        self.core_skills, self.gen_skills = data["core"], data["generated"]

    def _reset(self):
        self._cache: dict               = {}
        self._trace: list               = []
        self._failures: int             = 0
        self._goal: str                 = ""
        self._skills_dirty: bool        = False
        self._last_matched_skills: list = []   # fixed: was missing, caused AttributeError

    async def async_init(self) -> "PenzerAgent":
        try: import tools.tools
        except Exception as e: logger.warning("Tools: %s", e)
        try: self.tools = await mcp.get_tools() or {}
        except Exception as e: logger.warning("MCP: %s", e)
        return self

    # ── Public ──────────────────────────────────────────────────────────────────

    async def run(self, user_input: str) -> str:
        self._reset()
        self._goal = user_input
        self.history.append({"role": "user", "content": user_input})

        # Compute matched skills FIRST — needed before building system prompt
        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )
        self._last_matched_skills = (
            [s.name for s in self.core_skills
             if any(k.lower() in user_input.lower() for k in s.keywords)]
            + [s.name for s in matched_gen]
        )

        # Skills hint goes into extra= so agent knows which skills apply on step 0
        skills_hint = (
            f"SKILLS MATCHED FOR THIS TASK: {', '.join(self._last_matched_skills)}\n"
            "You MUST follow the matched skill's agent_behavior steps before using any tool.\n"
            "Before generating a new skill, check agent/skills/generated — it may already exist.\n"
        ) if self._last_matched_skills else (
            "No specific skills matched. Proceed carefully and generate a skill after solving.\n"
        )

        system = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            extra=get_memory_context(self.memory) + "\n\n" + skills_hint,
        )

        result = await self._loop(system)

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]

        remember(self.memory, f"Completed: {result[:120]}")
        save_history(self.history)
        return result

    # ── Loop ────────────────────────────────────────────────────────────────────

    async def _loop(self, system: str) -> str:
        empty = 0
        for i in range(MAX_ITER):
            asyncio.ensure_future(self._trim())
            self.on_status("Thinking…" if i == 0 else f"Step {i+1}…")

            r = await self.llm.chat(system=system, messages=self._msgs(i))
            calls, text = r.get("tool_calls") or [], r.get("content", "").strip()

            if not calls:
                if text:
                    self.history.append({"role": "assistant", "content": text})
                    return text
                empty += 1
                if empty >= 2:
                    return "No response. Try rephrasing."
                if self._last_role() == "tool":
                    self.history.append({"role": "user", "content":
                        f"Goal: {self._goal}\nAnalyze the result and give your final answer or call the next tool."})
                continue

            empty = 0
            self.history.append({"role": "assistant",
                                  "content": json.dumps({"reflection": text, "tool_calls": calls})})

            if len(self._trace) >= STUCK_MIN and self._stuck():
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    return f"Stuck after {MAX_FAILURES} attempts. Break the task into smaller steps."
                self.history.append({"role": "user", "content": f"[Recovery] {await self._reflect()}"})
                continue

            for c in calls:
                name = c["name"]
                if name not in self.tools:
                    self.history.append({"role": "tool", "tool_call_id": c.get("id", name),
                                         "content": f"Unknown tool '{name}'. Available: {', '.join(sorted(self.tools))}."})
                    continue

                self.on_status(TOOL_LABELS.get(name, name) + "…")
                raw = await self._run(name, c.get("arguments", {}))
                ok  = not self._is_error(raw)

                self._trace.append({"step": i, "tool": name, "args": c.get("arguments", {}),
                                     "result": str(raw)[:300], "success": ok})
                self.history.append({"role": "tool", "tool_call_id": c.get("id", name),
                                     "content": f"[{name}]\n{self._fmt(raw)}"})

                if name == "file_editor":
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp and fp.endswith(".skill.md"):
                        self._skills_dirty = True

        return "Reached iteration limit. Break the task into smaller steps."

    # ── ReflAct injection ───────────────────────────────────────────────────────

    def _msgs(self, step: int) -> list[dict]:
        if step == 0 or not self._trace:
            return self.history
        t      = self._trace[-1]
        steps  = " → ".join(f"{s['tool']}({'ok' if s['success'] else 'x'})" for s in self._trace)
        skills = f"SKILLS FOR THIS TASK: {', '.join(self._last_matched_skills)}\n" \
                 if self._last_matched_skills else ""
        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{skills}"
            f"LAST: {t['tool']} → {'ok' if t['success'] else 'FAILED'} | {t['result'][:200]}\n"
            f"STEPS: {steps}\n\n"
            "Check your matched skills above before acting. "
            "Reflect in one sentence on your current state in relation to the goal, "
            "then answer or call next tool."
        )
        return self.history + [{"role": "user", "content": inj}]

    # ── Tool execution ──────────────────────────────────────────────────────────

    async def _run(self, name: str, args: dict) -> str:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if key in self._cache:
            return self._cache[key]

        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' not available."

        for attempt in range(2):
            try:
                fn = getattr(tool, "fn", tool)
                if fn not in self._fn_cache:
                    self._fn_cache[fn] = (inspect.signature(fn), inspect.iscoroutinefunction(fn))
                sig, is_async = self._fn_cache[fn]
                kw = {k: v for k, v in args.items() if k in sig.parameters}
                if name == "memory":
                    kw.setdefault("workspace_id", "penzer_default")
                out = await fn(**kw) if is_async else fn(**kw)
                self._cache[key] = s = str(out)
                return s
            except Exception as e:
                logger.error("%s attempt %d: %s", name, attempt + 1, e)
                if attempt == 1:
                    fb = FALLBACKS.get(name)
                    if fb and fb in self.tools:
                        self.on_status(f"Retrying with {fb}…")
                        cmd = args.get("command") or args.get("query") or args.get("code") or args.get("script", "")
                        return await self._run(fb, {"command": cmd})
                    return f"{name} error: {e}"
        return ""

    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _fmt(self, raw: Any) -> str:
        s = str(raw).strip() or "(empty)"
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                if d.get("status") == "error":
                    return f"ERROR: {d.get('message', s)}"
                return str(next((d[k] for k in ("output","content","data","result","text") if k in d), s))
        except (json.JSONDecodeError, ValueError):
            pass
        return s[:1500] + f"\n…[{len(s)-1500} truncated]" if len(s) > 2000 else s

    def _is_error(self, r: Any) -> bool:
        return any(t in str(r).lower() for t in (
            "error:", "failed:", "exception", "traceback",
            "not found", "permission denied", "no such file",
        ))

    def _last_role(self) -> str:
        for m in reversed(self.history):
            if m.get("role") in ("user", "assistant", "tool"):
                return m["role"]
        return ""

    def _stuck(self) -> bool:
        w    = self.history[-6:]
        msgs = [m for m in w if m.get("role") == "tool"]
        if len(msgs) < STUCK_MIN: return False
        if len({str(m.get("content",""))[:100] for m in msgs}) == 1: return True
        names = []
        for m in w:
            if m.get("role") == "assistant":
                try: names.extend(tc["name"] for tc in json.loads(m["content"]).get("tool_calls", []))
                except (json.JSONDecodeError, KeyError, TypeError): pass
        if len(names) >= 3 and len(set(names)) == 1: return True
        recent = self._trace[-STUCK_MIN:]
        return len(recent) >= STUCK_MIN and all(not s["success"] for s in recent)

    async def _reflect(self) -> str:
        failed = "\n".join(
            f"step {s['step']}: {s['tool']} → {s['result'][:100]}"
            for s in self._trace[-4:] if not s["success"]
        ) or "(none)"
        r = await self.llm.chat(
            system="Precise agent debugger. Be specific.",
            messages=[{"role": "user", "content": f"GOAL: {self._goal}\nFAILED:\n{failed}\n\nDIAGNOSIS:\nHYPOTHESIS:\nNEXT:"}],
        )
        return r.get("content", "Try a different approach.")

    async def _trim(self) -> None:
        if len(self.history) <= TRIM_AT: return
        first, mid, tail = self.history[:1], self.history[1:-KEEP_LAST], self.history[-KEEP_LAST:]
        if not mid: return
        try:
            r = await self.llm.chat(
                system="Two sentences: what was done, what worked.",
                messages=[{"role": "user", "content": "\n".join(
                    f"{m['role']}: {str(m.get('content',''))[:150]}" for m in mid)}],
            )
            self.history = first + [{"role": "assistant", "content": f"[Summary] {r.get('content','')}"}] + tail
        except Exception:
            self.history = first + tail

    # ── Session ─────────────────────────────────────────────────────────────────

    def clear_session(self) -> None:
        self.history.clear()
        self._reset()
        clear_history()

    def get_trace(self) -> list[dict]:
        return self._trace