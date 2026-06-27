"""
PENZER — Research-Grade Agent

Implements:
  1. Belief State       — what agent believes is true right now (ReflAct paper)
  2. Episodic + Semantic Memory — structured memory with scored retrieval
  3. Reflexion          — post-task verbal post-mortem (Shinn 2023)
  4. Planner/Executor   — split complex tasks into plan then execute
  5. Trajectory Skills  — skills built from full tool trace not just answer
"""
import json, logging, inspect, asyncio, signal, time, psutil
from typing import Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_history, save_history, clear_history,
    remember_episodic, remember_semantic,
    store_post_mortem, get_post_mortems,
    get_relevant_memories,
    update_skill_metric, add_checkpoint,
    get_storage_summary,
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
COMPLEX_THRESHOLD = 3   # tool calls needed before task is "complex"

TOOL_LABELS = {
    "browser": "🌐", "terminal": "⚡", "run_python": "🐍",
    "run_bash": "📜", "file_editor": "📁", "memory": "🧠", "planning": "📋",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
}
SKILL_GATED_TOOLS = {"planning", "memory", "file_editor", "browser", "terminal", "run_bash", "run_python"}


class ResourceMonitor:
    def __init__(self):
        self._proc  = psutil.Process()
        self._start = time.time()

    def check(self) -> tuple[bool, str]:
        try:
            mem = self._proc.memory_percent()
            if mem > MEMORY_CRITICAL:
                return False, f"Memory critical: {mem:.1f}%"
            if mem > 70:
                logger.warning("Memory high: %.1f%%", mem)
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
        self.history  = load_history()
        self.on_status: Callable[[str], None] = lambda m: None

        self._fn_cache: dict = {}
        self._reset()
        data = load_all_skills()
        self.core_skills, self.gen_skills = data["core"], data["generated"]

        self._monitor  = ResourceMonitor()
        self._shutdown = False
        self._backoff  = 1.0

        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _reset(self):
        self._cache:              dict  = {}
        self._trace:              list  = []
        self._failures:           int   = 0
        self._goal:               str   = ""
        self._skills_dirty:       bool  = False
        self._matched_skills:     list  = []
        self._last_matched_skills:list  = []
        self._active_skill:       str   = ""
        self._system_prompt:      str   = ""
        self._consec_errors:      dict  = defaultdict(int)
        self._iteration:          int   = 0
        self._novel_task:         bool  = False
        self._skill_gate_shown:   bool  = False
        self._subtasks:           list  = []  # from planner
        self._current_subtask:    str   = ""

        # Belief state — updated after every tool call
        self._belief: dict = {
            "goal_progress": "not_started",   # not_started | in_progress | blocked | complete
            "verified_facts": [],             # things confirmed true
            "assumptions":    [],             # things we're assuming
            "unknowns":       [],             # still need to find out
            "last_action":    "",
            "last_outcome":   "",
        }

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
        self._goal = user_input
        self.history.append({"role": "user", "content": user_input})

        # Retrieve relevant past memories for this goal
        past_memory  = get_relevant_memories(user_input, n=5)
        past_mortems = get_post_mortems(user_input, n=2)

        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )
        self._matched_skills      = (
            [s.name for s in self.core_skills
             if any(k.lower() in user_input.lower() for k in s.keywords)]
            + [s.name for s in matched_gen]
        )
        self._last_matched_skills = self._matched_skills
        self._active_skill        = self._matched_skills[0] if self._matched_skills else ""
        self._novel_task          = not bool(self._matched_skills)

        skills_hint = (
            f"SKILLS MATCHED: {', '.join(self._matched_skills)}\n"
            "Follow matched skill steps before any tool.\n"
        ) if self._matched_skills else (
            "NO SKILLS MATCHED — proceed, generate skill only if 3+ tool calls used.\n"
        )

        mortem_hint = ""
        if past_mortems:
            mortem_hint = "\n## Past Experience on Similar Tasks\n"
            for pm in past_mortems:
                mortem_hint += (
                    f"Task type: {pm['task_type']}\n"
                    f"  What worked: {pm['what_worked']}\n"
                    f"  What failed: {pm['what_failed']}\n"
                    f"  Next time: {pm['next_time']}\n"
                )

        self._system_prompt = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            memory_context=past_memory,
            extra=skills_hint + mortem_hint,
            goal=user_input,
        )

        # Planner/Executor split for complex tasks
        is_complex = await self._is_complex(user_input)
        if is_complex:
            self._subtasks = await self._plan_task(user_input)

        result = await self._loop()

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]

        # Store episodic memory for this task
        if self._trace:
            tool_seq = " → ".join(t["tool"] for t in self._trace)
            outcome  = "success" if any(t["success"] for t in self._trace) else "failure"
            remember_episodic(
                event=f"Goal: {user_input[:60]} | Tools: {tool_seq}",
                outcome=outcome,
                importance=min(1.0, len(self._trace) * 0.15),
                task_type=user_input[:40],
            )

        # Reflexion: post-task verbal post-mortem for complex tasks
        if len(self._trace) >= COMPLEX_THRESHOLD:
            await self._write_post_mortem(user_input, result)

        save_history(self.history)
        return result

    # ── Planner/Executor Split ───────────────────────────────────────────────────

    async def _is_complex(self, goal: str) -> bool:
        complexity_signals = [
            "build", "create", "setup", "install", "configure", "deploy",
            "write", "analyze", "research", "find and", "compare", "generate",
            "step", "multiple", "then", "after", "first", "finally",
        ]
        return any(s in goal.lower() for s in complexity_signals)

    async def _plan_task(self, goal: str) -> list[str]:
        self.on_status("Planning…")
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "You are a task planner. Break the goal into 3-5 concrete subtasks. "
                        "Return ONLY a JSON array of strings. No markdown, no explanation.\n"
                        "Example: [\"Find the IP address\", \"Check open ports\", \"Save results\"]"
                    ),
                    messages=[{"role": "user", "content": f"Goal: {goal}"}],
                ),
                timeout=15,
            )
            text = r.get("content", "[]").strip()
            subtasks = json.loads(text)
            if isinstance(subtasks, list) and subtasks:
                logger.debug("Plan: %s", subtasks)
                return subtasks
        except Exception as e:
            logger.debug("Planner failed: %s", e)
        return []

    # ── Belief State ─────────────────────────────────────────────────────────────

    def _update_belief(self, tool: str, args: dict, result: str, ok: bool) -> None:
        self._belief["last_action"]  = f"{tool}({self._fmt_action(tool, args)})"
        self._belief["last_outcome"] = "success" if ok else f"failed: {result[:80]}"
        self._belief["goal_progress"] = (
            "in_progress" if self._belief["goal_progress"] == "not_started" else
            self._belief["goal_progress"]
        )
        if ok and len(self._trace) > 0:
            fact = f"{tool} returned: {result[:100]}"
            if fact not in self._belief["verified_facts"]:
                self._belief["verified_facts"].append(fact)
                self._belief["verified_facts"] = self._belief["verified_facts"][-5:]
        if not ok:
            self._belief["goal_progress"] = "blocked"

    def _belief_summary(self) -> str:
        b = self._belief
        lines = [f"BELIEF STATE: {b['goal_progress'].upper()}"]
        if b["verified_facts"]:
            lines.append(f"  Know: {' | '.join(b['verified_facts'][-2:])}")
        if b["last_action"]:
            lines.append(f"  Last: {b['last_action']} → {b['last_outcome']}")
        return "\n".join(lines)

    # ── Reflexion ────────────────────────────────────────────────────────────────

    async def _write_post_mortem(self, goal: str, result: str) -> None:
        successful = [t for t in self._trace if t["success"]]
        failed     = [t for t in self._trace if not t["success"]]

        worked_steps = " → ".join(
            f"{t['tool']}({self._fmt_action(t['tool'], t['args'])})"
            for t in successful[:4]
        ) or "none"
        failed_steps = " → ".join(
            f"{t['tool']} ({t.get('error_type','?')})"
            for t in failed[:3]
        ) or "none"

        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "Write a brief post-mortem in JSON with keys: "
                        "what_worked, what_failed, next_time. "
                        "Be specific. 1 sentence each. No markdown."
                    ),
                    messages=[{"role": "user", "content":
                        f"Goal: {goal}\n"
                        f"Outcome: {result[:100]}\n"
                        f"Succeeded: {worked_steps}\n"
                        f"Failed: {failed_steps}"
                    }],
                ),
                timeout=15,
            )
            text = r.get("content", "{}").strip()
            pm   = json.loads(text)
            store_post_mortem(
                task_type=goal[:40],
                what_worked=pm.get("what_worked", worked_steps),
                what_failed=pm.get("what_failed", failed_steps),
                next_time=pm.get("next_time", ""),
            )
            # Distil into semantic memory if task succeeded
            if any(t["success"] for t in self._trace):
                remember_semantic(
                    pattern=f"For '{goal[:40]}': use {worked_steps}",
                    confidence=0.7,
                )
        except Exception as e:
            logger.debug("Post-mortem failed: %s", e)

    # ── Main Loop ────────────────────────────────────────────────────────────────

    async def _loop(self) -> str:
        empty = 0

        for i in range(MAX_ITER):
            self._iteration = i

            if self._shutdown:
                save_history(self.history)
                return "Interrupted"

            ok, msg = self._monitor.check()
            if not ok:
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
                    memory_context=get_relevant_memories(self._goal, n=3),
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
                    self._belief["goal_progress"] = "complete"
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

                if ok: self._consec_errors[name] = 0
                else:  self._consec_errors[name] += 1

                if self._active_skill:
                    update_skill_metric(self._active_skill, ok)

                # Update belief state after every tool call
                self._update_belief(name, c.get("arguments", {}), str(raw), ok)

                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", name),
                    "content": self._fmt_tool_output(name, c.get("arguments", {}), raw, ok, elapsed),
                })

                if name == "file_editor":
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp and fp.endswith(".skill.md"):
                        self._skills_dirty = True

            # Trajectory-informed skill generation: only for novel + complex tasks
            if (
                self._novel_task
                and len(self._trace) >= COMPLEX_THRESHOLD
                and any(t["success"] for t in self._trace)
                and not self._skill_gate_shown
            ):
                self._skill_gate_shown = True
                self._inject_meta_skill_reminder()

        return "Iteration limit reached"

    # ── ReflAct injection with Belief State ──────────────────────────────────────

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

        # Subtask awareness
        subtask_line = ""
        if self._subtasks:
            done = len([tr for tr in self._trace if tr["success"]])
            subtask_line = f"PLAN: {self._subtasks} | Done: {done}/{len(self._subtasks)}\n"

        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{subtask_line}"
            f"{skill}"
            f"{self._belief_summary()}\n"
            f"LAST: {t['tool']} → {status} ({t['elapsed_sec']}s) | {t['result'][:120]}\n"
            f"RECENT: {recent}\n\n"
            "Reflect: given your belief state and goal, what is the next action?"
        )
        return self.history + [{"role": "user", "content": inj}]

    # ── Meta-skill injection (trajectory-informed) ────────────────────────────────

    def _inject_meta_skill_reminder(self):
        tool_seq = " → ".join(
            f"{t['tool']}({self._fmt_action(t['tool'], t['args'])})"
            for t in self._trace if t["success"]
        )
        self.history.append({"role": "user", "content": (
            "[Skill evolution] Complex novel task completed. "
            f"Winning tool sequence: {tool_seq}\n"
            "Before final answer:\n"
            "1. Check agent/skills/generated/ for similar skills.\n"
            "2. If none: write .skill.md capturing this exact sequence.\n"
            "3. Include: name, description, keywords, agent_behavior (step-by-step), "
            "failure_modes (what failed), mcp_tools used.\n"
            "Then give final answer."
        )})

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

    # ── Output formatting ─────────────────────────────────────────────────────────

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
            lines   = str(raw).strip().splitlines()
            if not lines: return f"{action}\n(no output)"
            preview = "\n".join(lines[:5])
            tail    = f"\n… ({len(lines)-5} more lines)" if len(lines) > 5 else ""
            return f"{action}\n{preview}{tail}"

        if name == "file_editor":
            if args.get("action") in ("write", "create", "delete", "replace"):
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

    # ── Helpers ───────────────────────────────────────────────────────────────────

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
        if len(msgs) < STUCK_MIN: return False
        if len({str(m.get("content",""))[:80] for m in msgs}) == 1: return True
        names = []
        for m in w:
            if m.get("role") == "assistant":
                try:
                    names.extend(
                        tc["name"] for tc in json.loads(m["content"]).get("tool_calls", [])
                    )
                except Exception:
                    pass
        if len(names) >= 3 and len(set(names)) == 1: return True
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
                "content": f"GOAL: {self._goal}\n{self._belief_summary()}\nFAILED:\n{failed}\n\nDIAGNOSIS:\nNEXT:"}],
        )
        return r.get("content", "Try completely different approach")

    async def _trim(self) -> None:
        if len(self.history) <= TRIM_AT: return
        first, mid, tail = self.history[:1], self.history[1:-KEEP_LAST], self.history[-KEEP_LAST:]
        if not mid: return
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
                "timestamp": datetime.now().isoformat(),
                "iteration": iteration,
                "goal":      self._goal,
                "belief":    self._belief["goal_progress"],
                "trace_len": len(self._trace),
                "resources": self._monitor.stats(),
            })
        except Exception as e:
            logger.debug("Checkpoint failed: %s", e)

    def clear_session(self) -> None:
        self.history.clear()
        self._reset()
        clear_history()

    def get_metrics(self) -> dict:
        return {
            "goal":          self._goal,
            "belief":        self._belief["goal_progress"],
            "tools_called":  len(self._trace),
            "success_count": sum(1 for t in self._trace if t["success"]),
            "storage":       get_storage_summary(),
        }