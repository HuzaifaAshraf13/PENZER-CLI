"""
PENZER — Skill-First Long-Running Agent

Rules:
  1. Check core skills BEFORE any tool
  2. Generate skills ONLY for complex novel tasks (not trivial ones)
  3. Output: show actions not dumps
  4. Skills evolve over time via core.meta
"""
import json, logging, inspect, asyncio, signal, time, psutil
from typing import Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_memory, save_memory, remember, get_memory_context,
    load_history, save_history, clear_history,
    update_skill_metric, add_checkpoint,
)
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history

logger = logging.getLogger(__name__)

MAX_ITER         = 15
TRIM_AT          = 35
KEEP_LAST        = 10
STUCK_MIN        = 2
MAX_FAILURES     = 3
TOOL_TIMEOUT     = 30
CHECKPOINT_EVERY = 10
MEMORY_CRITICAL  = 85

# Tasks that require 3+ tool calls are considered complex → worth generating a skill
COMPLEX_THRESHOLD = 3

TOOL_LABELS = {
    "browser": "🌐", "terminal": "⚡", "run_python": "🐍",
    "run_bash": "📜", "file_editor": "📁", "memory": "🧠", "planning": "📋",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
}
SKILL_GATED_TOOLS = {"planning", "memory", "file_editor", "browser", "terminal", "run_bash", "run_python"}


@dataclass
class SessionMetrics:
    start_time: float
    goal: str         = ""
    iterations: int   = 0
    tools_called: int = 0
    success: int      = 0
    failures: int     = 0
    skills_used: int  = 0
    skills_generated: int = 0

    def summary(self) -> dict:
        return {
            "elapsed_sec":  round(time.time() - self.start_time, 1),
            "iterations":   self.iterations,
            "tools_called": self.tools_called,
            "success_rate": round(self.success / self.tools_called, 2) if self.tools_called else 0,
            "skills_used":  self.skills_used,
            "skills_gen":   self.skills_generated,
        }


class ResourceMonitor:
    def __init__(self):
        self._proc  = psutil.Process()
        self._start = time.time()

    def check(self) -> tuple[bool, str]:
        try:
            mem = self._proc.memory_percent()
            if mem > MEMORY_CRITICAL:
                return False, f"Memory critical: {mem:.1f}%"
        except Exception:
            pass
        return True, ""

    def stats(self) -> dict:
        try:
            return {
                "memory_mb":   round(self._proc.memory_info().rss / 1e6, 1),
                "elapsed_sec": round(time.time() - self._start, 1),
            }
        except Exception:
            return {}


class PenzerAgent:
    def __init__(self):
        self.llm      = LLM()
        self.tools    = {}
        self.memory   = load_memory() or {}
        self.history  = load_history()
        self.on_status: Callable[[str], None] = lambda m: None

        self._fn_cache: dict = {}
        self._reset()
        data = load_all_skills()
        self.core_skills, self.gen_skills = data["core"], data["generated"]

        self._monitor  = ResourceMonitor()
        self._metrics  = SessionMetrics(start_time=time.time())
        self._shutdown = False
        self._backoff  = 1.0

        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _reset(self):
        self._cache:           dict  = {}
        self._trace:           list  = []
        self._failures:        int   = 0
        self._goal:            str   = ""
        self._skills_dirty:    bool  = False
        self._matched_skills:  list  = []
        self._last_matched_skills: list = []   # cli.py compatibility alias
        self._active_skill:    str   = ""
        self._system_prompt:   str   = ""
        self._consec_errors:   dict  = defaultdict(int)
        self._iteration:       int   = 0
        self._novel_task:      bool  = False
        self._skill_gate_shown: bool = False

    def _handle_shutdown(self, signum, frame):
        self._shutdown = True

    async def async_init(self) -> "PenzerAgent":
        try:
            import tools.tools
        except Exception as e:
            logger.debug("tools.tools: %s", e)
        try:
            self.tools = await mcp.get_tools() or {}
        except Exception as e:
            logger.debug("MCP: %s", e)
        return self

    # ── Public ──────────────────────────────────────────────────────────────────

    async def run(self, user_input: str) -> str:
        self._reset()
        self._goal    = user_input
        self._metrics = SessionMetrics(start_time=time.time(), goal=user_input)
        self.history.append({"role": "user", "content": user_input})

        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )
        self._matched_skills      = (
            [s.name for s in self.core_skills if any(k.lower() in user_input.lower() for k in s.keywords)]
            + [s.name for s in matched_gen]
        )
        self._last_matched_skills = self._matched_skills   # alias
        self._active_skill        = self._matched_skills[0] if self._matched_skills else ""
        self._novel_task          = not bool(self._matched_skills)

        skills_hint = (
            f"SKILLS MATCHED: {', '.join(self._matched_skills)}\n"
            "Follow matched skill steps before any tool.\n"
        ) if self._matched_skills else (
            "NO SKILLS MATCHED — proceed, then generate skill only if task was complex (3+ tool calls).\n"
        )

        self._system_prompt = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            memory=self.memory,
            extra=get_memory_context(self.memory) + "\n\n" + skills_hint,
            goal=user_input,
        )

        result = await self._loop()

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]

        # Only remember meaningful tasks — not trivial exchanges like "hi"
        if self._trace:
            remember(self.memory, f"{user_input[:60]} → {result[:80]}")
        save_memory(self.memory)
        save_history(self.history)
        return result

    # ── Loop ────────────────────────────────────────────────────────────────────

    async def _loop(self) -> str:
        empty = 0

        for i in range(MAX_ITER):
            self._iteration          = i
            self._metrics.iterations = i

            if self._shutdown:
                save_memory(self.memory)
                save_history(self.history)
                return "Interrupted"

            ok, msg = self._monitor.check()
            if not ok:
                save_memory(self.memory)
                save_history(self.history)
                return f"Resource limit: {msg}"

            asyncio.ensure_future(self._trim())
            self.on_status("Thinking…" if i == 0 else f"Step {i+1}…")

            if (i + 1) % 5 == 0:
                matched_gen = search_generated_skills(
                    self._goal, self.gen_skills,
                    context=build_context_from_history(self.history),
                )
                self._system_prompt = build_system_prompt(
                    core_skills=self.core_skills,
                    generated_skills=matched_gen,
                    memory=self.memory,
                    goal=self._goal,
                )

            if (i + 1) % CHECKPOINT_EVERY == 0:
                await self._checkpoint(i)

            try:
                r = await asyncio.wait_for(
                    self.llm.chat(system=self._system_prompt, messages=self._msgs(i)),
                    timeout=30 * self._backoff,
                )
                self._backoff = max(1.0, self._backoff * 0.9)
            except asyncio.TimeoutError:
                self._backoff = min(3.0, self._backoff * 1.5)
                self.history.append({"role": "user",
                    "content": "Timeout. Continue or give final answer."})
                continue
            except Exception as e:
                logger.error("LLM error: %s", e)
                return f"LLM error: {e}"

            calls = r.get("tool_calls") or []
            text  = r.get("content", "").strip()

            if not calls:
                if text:
                    self.history.append({"role": "assistant", "content": text})
                    return text
                empty += 1
                if empty >= 2:
                    return "No response. Try rephrasing."
                if self._last_role() == "tool":
                    self.history.append({"role": "user", "content":
                        f"Goal: {self._goal}\nGive final answer or call next tool."})
                continue

            empty = 0
            self.history.append({
                "role": "assistant",
                "content": json.dumps({"reasoning": text, "tool_calls": calls}),
            })

            if len(self._trace) >= STUCK_MIN and self._stuck():
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    return f"Stuck after {MAX_FAILURES} attempts"
                self.history.append({"role": "user",
                    "content": f"[Recovery] {await self._reflect()}"})
                continue

            for c in calls:
                name = c["name"]

                # Skill gate — show once per task, not every call
                if name in SKILL_GATED_TOOLS and not self._active_skill and not self._skill_gate_shown:
                    self._skill_gate_shown = True
                    self.history.append({"role": "user", "content":
                        f"[Skill gate] No skill matched for '{name}'. "
                        "Check YOUR SKILLS. If nothing fits, proceed."})

                if name not in self.tools:
                    self.history.append({"role": "tool",
                        "tool_call_id": c.get("id", name),
                        "content": f"Unknown tool '{name}'."})
                    continue

                self.on_status(f"{TOOL_LABELS.get(name, name)} {self._fmt_action(name, c.get('arguments', {}))}")

                start = time.time()
                try:
                    raw = await asyncio.wait_for(
                        self._run(name, c.get("arguments", {})),
                        timeout=TOOL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    raw = f"Timeout after {TOOL_TIMEOUT}s"

                elapsed = round(time.time() - start, 2)
                ok      = not self._is_error(raw)
                etype   = self._categorize_error(raw) if not ok else None

                self._trace.append({
                    "step": i, "tool": name,
                    "args": c.get("arguments", {}),
                    "result": str(raw)[:300],
                    "success": ok, "error_type": etype,
                    "elapsed_sec": elapsed,
                })

                self._metrics.tools_called += 1
                if ok: self._metrics.success  += 1
                else:  self._metrics.failures += 1

                if ok: self._consec_errors[name] = 0
                else:  self._consec_errors[name] += 1

                if self._active_skill:
                    update_skill_metric(self._active_skill, ok)
                    self._metrics.skills_used += 1

                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", name),
                    "content": self._fmt_tool_output(name, c.get("arguments", {}), raw, ok, elapsed),
                })

                if name == "file_editor":
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp and fp.endswith(".skill.md"):
                        self._skills_dirty = True
                        self._metrics.skills_generated += 1

            # Skill generation gate: only trigger for novel + complex tasks
            if (
                self._novel_task
                and len(self._trace) >= COMPLEX_THRESHOLD
                and any(t["success"] for t in self._trace)
                and not self._skill_gate_shown
            ):
                self._skill_gate_shown = True
                self._inject_meta_skill_reminder()

        return "Iteration limit reached"

    # ── Skill generation injection ───────────────────────────────────────────────

    def _inject_meta_skill_reminder(self):
        self.history.append({"role": "user", "content": (
            "[Skill evolution] This was a complex novel task ("
            f"{len(self._trace)} tool calls). "
            "Before answering:\n"
            "1. Check agent/skills/generated/ for similar skills.\n"
            "2. If none exists, write a new .skill.md using file_editor.\n"
            "3. Follow core.meta format.\n"
            "Then give your final answer."
        )})

    # ── ReflAct ─────────────────────────────────────────────────────────────────

    def _msgs(self, step: int) -> list[dict]:
        if step == 0 or not self._trace:
            return self.history
        t      = self._trace[-1]
        recent = " → ".join(
            f"{s['tool']}({'✓' if s['success'] else '✗'})"
            for s in self._trace[-5:]
        )
        skill  = f"ACTIVE SKILL: {self._active_skill}\n" if self._active_skill else ""
        status = "✓ ok" if t["success"] else f"✗ {t['error_type']}"
        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{skill}"
            f"LAST: {t['tool']} → {status} ({t['elapsed_sec']}s) | {t['result'][:120]}\n"
            f"RECENT: {recent}\n\n"
            "Reflect in one sentence: progress? Then answer or call next tool."
        )
        return self.history + [{"role": "user", "content": inj}]

    # ── Tool execution ───────────────────────────────────────────────────────────

    async def _run(self, name: str, args: dict) -> str:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if key in self._cache:
            return self._cache[key]

        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' not available"

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
                logger.debug("%s attempt %d: %s", name, attempt + 1, e)
                if attempt == 1:
                    fb = FALLBACKS.get(name)
                    if fb and fb in self.tools:
                        self.on_status(f"Fallback → {fb}…")
                        cmd = args.get("command") or args.get("query") or args.get("code") or ""
                        return await self._run(fb, {"command": cmd})
                    return f"Error: {e}"
        return ""

    # ── Output formatting ────────────────────────────────────────────────────────

    def _fmt_action(self, name: str, args: dict) -> str:
        if name == "terminal":
            return f"→ {args.get('command', '')[:60]}"
        if name == "browser":
            return f"→ {args.get('action','')}: {(args.get('query') or args.get('url',''))[:50]}"
        if name == "file_editor":
            return f"→ {args.get('action','')}: {args.get('filepath','')}"
        if name == "memory":
            return f"→ {args.get('action','')}: {args.get('key','')}"
        if name == "planning":
            return f"→ plan: {args.get('goal','')[:50]}"
        return f"→ {json.dumps(args)[:60]}"

    def _fmt_tool_output(self, name: str, args: dict, raw: Any, ok: bool, elapsed: float) -> str:
        action = f"[{name}] {self._fmt_action(name, args)} ({elapsed}s) {'✓' if ok else '✗'}"

        if not ok:
            return f"{action}\nError: {self._brief(raw)}"

        if name == "terminal":
            lines = str(raw).strip().splitlines()
            if not lines:
                return f"{action}\n(no output)"
            preview = "\n".join(lines[:5])
            tail    = f"\n… ({len(lines)-5} more lines)" if len(lines) > 5 else ""
            return f"{action}\n{preview}{tail}"

        if name == "file_editor":
            action_type = args.get("action", "")
            if action_type in ("write", "create", "delete", "replace"):
                return f"{action}\nDone"
            return f"{action}\n{self._brief(raw)}"

        if name == "memory":
            if args.get("action") in ("store", "delete"):
                return f"{action}\nDone"
            return f"{action}\n{self._brief(raw)}"

        return f"{action}\n{self._brief(raw)}"

    def _brief(self, raw: Any) -> str:
        s = str(raw).strip() or "(empty)"
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                if d.get("status") == "error":
                    return f"Error: {d.get('message', s)}"
                for k in ("output", "content", "data", "result", "text"):
                    if k in d:
                        return str(d[k])[:250]
        except (json.JSONDecodeError, ValueError):
            pass
        return s[:250] + f" … [{len(s)-250} more]" if len(s) > 250 else s

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _is_error(self, r: Any) -> bool:
        return any(t in str(r).lower() for t in (
            "error", "failed", "exception", "traceback",
            "not found", "permission denied", "timeout",
        ))

    def _categorize_error(self, result: Any) -> str:
        s = str(result).lower()
        if "timeout" in s:    return "TIMEOUT"
        if "permission" in s: return "PERMISSION"
        if "not found" in s:  return "NOT_FOUND"
        if "syntax" in s:     return "SYNTAX"
        if "invalid" in s:    return "INVALID"
        return "ERROR"

    def _last_role(self) -> str:
        for m in reversed(self.history):
            if m.get("role") in ("user", "assistant", "tool"):
                return m["role"]
        return ""

    def _stuck(self) -> bool:
        w    = self.history[-6:]
        msgs = [m for m in w if m.get("role") == "tool"]
        if len(msgs) < STUCK_MIN:
            return False
        if len({str(m.get("content",""))[:80] for m in msgs}) == 1:
            return True
        names = []
        for m in w:
            if m.get("role") == "assistant":
                try:
                    names.extend(tc["name"] for tc in json.loads(m["content"]).get("tool_calls", []))
                except Exception:
                    pass
        if len(names) >= 3 and len(set(names)) == 1:
            return True
        recent = self._trace[-STUCK_MIN:]
        return len(recent) >= STUCK_MIN and all(not s["success"] for s in recent)

    async def _reflect(self) -> str:
        failed = "\n".join(
            f"  {s['tool']} → {s.get('error_type','?')}: {s['result'][:80]}"
            for s in self._trace[-3:] if not s["success"]
        ) or "  (none)"
        r = await self.llm.chat(
            system="Debug agent failures. Output: DIAGNOSIS, NEXT STEP.",
            messages=[{"role": "user",
                "content": f"GOAL: {self._goal}\nFAILED:\n{failed}\n\nDIAGNOSIS:\nNEXT:"}],
        )
        return r.get("content", "Try a completely different approach")

    async def _trim(self) -> None:
        if len(self.history) <= TRIM_AT:
            return
        first, mid, tail = self.history[:1], self.history[1:-KEEP_LAST], self.history[-KEEP_LAST:]
        if not mid:
            return
        try:
            r = await self.llm.chat(
                system="Summarize in 2 sentences: what was done, what worked.",
                messages=[{"role": "user", "content": "\n".join(
                    f"{m['role']}: {str(m.get('content',''))[:100]}" for m in mid
                )}],
            )
            self.history = first + [
                {"role": "assistant", "content": f"[Summary] {r.get('content','')}"}
            ] + tail
        except Exception:
            self.history = first + tail

    async def _checkpoint(self, iteration: int):
        try:
            add_checkpoint({
                "timestamp":   datetime.now().isoformat(),
                "iteration":   iteration,
                "goal":        self._goal,
                "history_len": len(self.history),
                "trace_len":   len(self._trace),
                "metrics":     self._metrics.summary(),
                "resources":   self._monitor.stats(),
            })
        except Exception as e:
            logger.debug("Checkpoint failed: %s", e)

    # ── Session ──────────────────────────────────────────────────────────────────

    def clear_session(self) -> None:
        self.history.clear()
        self._reset()
        clear_history()
        save_memory(self.memory)

    def get_metrics(self) -> dict:
        return self._metrics.summary()