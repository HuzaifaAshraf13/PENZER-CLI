"""
PENZER — Research-Grade Agent

Research sources:
  ReflAct    (Kim 2025)       — belief-state injection, 27.7% improvement
  ExpeL      (Zhao AAAI 2024) — insight extraction, trajectory recall
  Reflexion  (Shinn 2023)     — verbal post-mortem, no gradient updates
  HyMem      (Zhao 2026)      — dual-tier retrieval, 92.6% cost reduction
  MemoryBank (Zhong 2024)     — Ebbinghaus decay, memory reinforcement
  Active CC  (Arxiv 2601)     — proactive context compression

Implements:
  1. Belief state           — updated every tool call (ReflAct)
  2. Dual-tier memory       — fast for simple, deep for complex (HyMem)
  3. Reflexion + ExpeL      — post-task post-mortem + insight extraction
  4. Planner/Executor       — decompose then drive subtasks
  5. Multi-skill plan       — merged ordered plan across ALL matched skills
  6. Skill-aware metrics    — only update skill if its tools were used
  7. Parallel tool exec     — asyncio.gather across concurrent calls
  8. Rate-limit retry       — exponential backoff + jitter
  9. Proactive compression  — trim before hitting context limit
"""
import json, logging, inspect, asyncio, signal, time, psutil, random
from typing import Any, Callable
from datetime import datetime
from collections import defaultdict

from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_history, save_history, clear_history,
    remember_episodic, remember_semantic,
    store_post_mortem, get_post_mortems,
    get_relevant_memories, get_insights, store_insight,
    get_similar_trajectories, score_complexity,
    update_skill_metric, add_checkpoint,
    get_storage_summary,
)
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history

logger = logging.getLogger(__name__)

MAX_ITER          = 15
TRIM_AT           = 30
KEEP_LAST         = 8
STUCK_MIN         = 2
MAX_FAILURES      = 3
TOOL_TIMEOUT      = 30
CHECKPOINT_EVERY  = 10
MEMORY_CRITICAL   = 85
COMPLEX_THRESHOLD = 3

RATE_LIMIT_BASE   = 5.0
RATE_LIMIT_MAX    = 60.0
RATE_LIMIT_JITTER = 2.0

TOOL_LABELS = {
    "browser": "🌐", "terminal": "⚡", "run_python": "🐍",
    "run_bash": "📜", "file_editor": "📁", "memory": "🧠", "planning": "📋",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
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

        self._monitor       = ResourceMonitor()
        self._shutdown      = False
        self._backoff       = 1.0
        self._rate_attempts = 0

        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _reset(self):
        self._cache:               dict  = {}
        self._trace:               list  = []
        self._failures:            int   = 0
        self._goal:                str   = ""
        self._skills_dirty:        bool  = False
        self._matched_skills:      list  = []
        self._last_matched_skills: list  = []
        self._active_skills:       list  = []
        self._system_prompt:       str   = ""
        self._consec_errors:       dict  = defaultdict(int)
        self._iteration:           int   = 0
        self._novel_task:          bool  = False
        self._is_complex_task:     bool  = False
        self._task_insights:       list  = []

        # Skill flags — kept separate so they don't block each other
        self._skill_gate_shown:    bool  = False  # "no skills" gate shown
        self._meta_skill_triggered:bool  = False  # meta-skill reminder injected

        # Skill plan (multi-skill orchestration)
        self._skill_plan:          list  = []
        self._skill_steps:         dict  = {}
        self._skill_done:          set   = set()

        # Subtask tracking
        self._subtasks:            list  = []
        self._subtask_idx:         int   = 0
        self._total_subtasks:      int   = 0   # stored at plan time, never changes
        self._current_subtask:     str   = ""

        # Belief state (ReflAct)
        self._belief: dict = {
            "goal_progress":  "not_started",
            "verified_facts": [],
            "assumptions":    [],
            "unknowns":       [],
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

        # Dual-tier memory (HyMem) — fast for simple, deep for complex
        self._is_complex_task = await self._is_complex(user_input)
        past_memory  = get_relevant_memories(user_input, n=5, deep=self._is_complex_task)
        past_mortems = get_post_mortems(user_input, n=2)

        # ExpeL: recall cross-task insights AND similar past trajectories
        self._task_insights     = get_insights(user_input, n=3)
        self._past_trajectories = get_similar_trajectories(user_input, n=2)

        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )
        matched_core = [
            s for s in self.core_skills
            if any(k.lower() in user_input.lower() for k in s.keywords)
        ]
        self._active_skills       = matched_core + list(matched_gen)
        self._matched_skills      = [s.name for s in self._active_skills]
        self._last_matched_skills = self._matched_skills
        self._novel_task          = not bool(self._matched_skills)

        # Build merged skill plan across ALL matched skills
        if self._active_skills:
            self._orchestrate_skills()

        skills_hint = (
            f"SKILLS MATCHED: {', '.join(self._matched_skills)}\n"
            "All matched skills active — follow their merged SKILL PLAN.\n"
        ) if self._matched_skills else (
            "NO SKILLS MATCHED — proceed carefully. "
            "Generate skill after if 3+ tool calls used.\n"
        )

        insight_hint = ""
        if self._task_insights:
            insight_hint = "\n## Recalled Insights\n" + "".join(
                f"- {i['insight']}\n" for i in self._task_insights
            )
        if self._past_trajectories:
            insight_hint += "\n## Similar Past Runs\n" + "".join(
                f"- {t['event']} → {t['outcome']}\n"
                for t in self._past_trajectories
            )

        mortem_hint = ""
        if past_mortems:
            mortem_hint = "\n## Past Experience\n" + "".join(
                f"  [{pm['task_type']}] "
                f"Worked: {pm['what_worked']} | "
                f"Failed: {pm['what_failed']} | "
                f"Next: {pm['next_time']}\n"
                for pm in past_mortems
            )

        self._system_prompt = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            memory_context=past_memory,
            extra=skills_hint + insight_hint + mortem_hint,
            goal=user_input,
        )

        # Planner for complex tasks
        if self._is_complex_task:
            self._subtasks       = await self._plan_task(user_input)
            self._total_subtasks = len(self._subtasks)

        result = await self._loop()

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]

        # Episodic memory — store if tools were used
        if self._trace:
            tool_seq = " → ".join(t["tool"] for t in self._trace)
            outcome  = "success" if any(t["success"] for t in self._trace) else "failure"
            remember_episodic(
                event=f"Goal: {user_input[:60]} | Tools: {tool_seq}",
                outcome=outcome,
                importance=min(1.0, len(self._trace) * 0.15),
                task_type=user_input[:40],
            )

        # Reflexion: evaluate completion, then write post-mortem + insights
        if len(self._trace) >= COMPLEX_THRESHOLD:
            completed, eval_reason = await self._evaluate_completion(user_input, result)
            if not completed:
                logger.info("Evaluator: task incomplete — %s", eval_reason)
                # Store as failure so future runs know to try harder
                result = f"{result} [Note: evaluator flagged as incomplete: {eval_reason}]"
            await self._write_post_mortem_and_insights(user_input, result)

        save_history(self.history)
        return result

    # ── Skill Orchestration ──────────────────────────────────────────────────────

    def _orchestrate_skills(self) -> None:
        """Merge all matched skills into one ordered execution plan."""
        self._skill_plan  = []
        self._skill_steps = {s.name: 0 for s in self._active_skills}
        self._skill_done  = set()

        for skill in self._active_skills:
            behavior = (skill.agent_behavior or "").strip()
            lines    = [
                l.strip() for l in behavior.splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
            tools = set(skill.mcp_tools or [])
            for idx, line in enumerate(lines):
                self._skill_plan.append({
                    "skill":       skill.name,
                    "step":        idx,
                    "instruction": line,
                    "tools":       tools,   # empty set = matches any tool
                    "done":        False,
                })

        # Sort: memory/planning first, browser second, terminal third, file_editor last
        tool_order = ["memory", "planning", "browser", "terminal", "file_editor"]
        self._skill_plan.sort(key=lambda s: next(
            (i for i, t in enumerate(tool_order) if t in s["tools"]),
            len(tool_order)  # no tools = end of list
        ))

    def _skill_plan_summary(self) -> str:
        if not self._skill_plan:
            return ""
        total   = len(self._skill_plan)
        done    = sum(1 for s in self._skill_plan if s["done"])
        pending = [s for s in self._skill_plan if not s["done"]][:3]
        lines   = [f"SKILL PLAN [{done}/{total} steps]"]
        for s in pending:
            lines.append(f"  [{s['skill']}] step {s['step']+1}: {s['instruction'][:80]}")
        return "\n".join(lines)

    def _mark_skill_step_done(self, tool_name: str) -> None:
        """
        Mark the first pending step whose tools include tool_name as done.
        If a step has no tools defined, match it to any tool call.
        """
        for step in self._skill_plan:
            if step["done"]:
                continue
            # Match if tool in step's tools, OR step has no tools defined
            if not step["tools"] or tool_name in step["tools"]:
                step["done"]  = True
                skill_name    = step["skill"]
                self._skill_steps[skill_name] = step["step"] + 1
                skill_steps   = [s for s in self._skill_plan if s["skill"] == skill_name]
                if all(s["done"] for s in skill_steps):
                    self._skill_done.add(skill_name)
                break

    def _skills_for_tool(self, tool_name: str) -> list:
        """Return active skills whose mcp_tools include this tool (or have no tools)."""
        matched = []
        for skill in self._active_skills:
            tools = set(skill.mcp_tools or [])
            if not tools or tool_name in tools:
                matched.append(skill)
        return matched

    # ── Planner/Executor ─────────────────────────────────────────────────────────

    async def _is_complex(self, goal: str) -> bool:
        """Use numerical complexity score (HyMem) instead of keyword heuristic."""
        return score_complexity(goal) >= 0.4

    async def _plan_task(self, goal: str) -> list[str]:
        self.on_status("Planning…")
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "Task planner. Break goal into 3-6 concrete subtasks. "
                        "Return ONLY a JSON array of strings. No markdown.\n"
                        'Example: ["Check network info", "Scan ports", "Save results"]'
                    ),
                    messages=[{"role": "user", "content": f"Goal: {goal}"}],
                ),
                timeout=15,
            )
            subtasks = json.loads(r.get("content", "[]").strip())
            if isinstance(subtasks, list) and subtasks:
                return subtasks
        except Exception as e:
            logger.debug("Planner: %s", e)
        return []

    # ── Belief State ─────────────────────────────────────────────────────────────

    def _update_belief(self, tool: str, args: dict, result: str, ok: bool) -> None:
        self._belief["last_action"]  = f"{tool}({self._fmt_action(tool, args)})"
        self._belief["last_outcome"] = "ok" if ok else f"failed: {result[:60]}"
        if self._belief["goal_progress"] == "not_started":
            self._belief["goal_progress"] = "in_progress"
        if ok:
            fact = f"{tool}: {result[:80]}"
            if fact not in self._belief["verified_facts"]:
                self._belief["verified_facts"].append(fact)
                self._belief["verified_facts"] = self._belief["verified_facts"][-5:]
        else:
            self._belief["goal_progress"] = "blocked"

    def _belief_summary(self) -> str:
        b     = self._belief
        lines = [f"BELIEF: {b['goal_progress'].upper()}"]
        if b["verified_facts"]:
            lines.append(f"  Know: {' | '.join(b['verified_facts'][-2:])}")
        if b["last_action"]:
            lines.append(f"  Last: {b['last_action']} → {b['last_outcome']}")
        return "\n".join(lines)

    # ── Task Completion Evaluator (Reflexion) ───────────────────────────────────

    async def _evaluate_completion(self, goal: str, result: str) -> tuple[bool, str]:
        """
        Reflexion's evaluator: did we actually solve the goal?
        Returns (completed: bool, reason: str)
        """
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "Evaluate if the goal was achieved. "
                        "Return JSON: {\"completed\": true/false, \"reason\": \"one sentence\"}. "
                        "Be strict — partial results count as not completed."
                    ),
                    messages=[{"role": "user", "content":
                        f"GOAL: {goal}\nRESULT: {result[:200]}\n"
                        f"TOOLS USED: {' → '.join(t['tool'] for t in self._trace)}"
                    }],
                ),
                timeout=10,
            )
            ev = json.loads(r.get("content", "{}").strip())
            return bool(ev.get("completed", True)), ev.get("reason", "")
        except Exception:
            return True, ""  # default: assume completed if evaluator fails

    # ── Reflexion + ExpeL ────────────────────────────────────────────────────────

    async def _write_post_mortem_and_insights(self, goal: str, result: str) -> None:
        worked = " → ".join(
            f"{t['tool']}({self._fmt_action(t['tool'], t['args'])})"
            for t in self._trace if t["success"]
        )[:200] or "none"
        failed = " → ".join(
            f"{t['tool']}({t.get('error_type','?')})"
            for t in self._trace if not t["success"]
        )[:200] or "none"

        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "Write a post-mortem AND extract a reusable insight. "
                        "Return JSON with keys: what_worked, what_failed, next_time, insight. "
                        "insight = one general rule for future similar tasks. "
                        "One sentence each. No markdown."
                    ),
                    messages=[{"role": "user", "content":
                        f"Goal: {goal}\nOutcome: {result[:80]}\n"
                        f"Worked: {worked}\nFailed: {failed}"
                    }],
                ),
                timeout=15,
            )
            pm = json.loads(r.get("content", "{}").strip())
            store_post_mortem(
                task_type=goal[:40],
                what_worked=pm.get("what_worked", worked),
                what_failed=pm.get("what_failed", failed),
                next_time=pm.get("next_time", ""),
            )
            if pm.get("insight"):
                store_insight(
                    insight=pm["insight"],
                    source_tasks=[goal[:40]],
                    confidence=0.7 if any(t["success"] for t in self._trace) else 0.4,
                )
            if any(t["success"] for t in self._trace):
                remember_semantic(
                    pattern=f"For '{goal[:40]}': {worked}",
                    confidence=0.7,
                )
        except Exception as e:
            logger.debug("Post-mortem: %s", e)

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

            # Executor: inject next subtask
            if self._subtasks and self._subtask_idx < len(self._subtasks):
                subtask = self._subtasks[self._subtask_idx]
                self._current_subtask = subtask
                self._subtask_idx    += 1
                self.history.append({"role": "user", "content":
                    f"[Executor] Subtask {self._subtask_idx}/{self._total_subtasks}: {subtask}"})
            elif self._subtasks and self._subtask_idx >= len(self._subtasks):
                self.history.append({"role": "user",
                    "content": "[Executor] All subtasks complete. Give final answer."})
                self._subtasks = []

            if (i + 1) % 5 == 0:
                matched_gen = search_generated_skills(
                    self._goal, self.gen_skills,
                    context=build_context_from_history(self.history),
                )
                self._system_prompt = build_system_prompt(
                    core_skills=self.core_skills,
                    generated_skills=matched_gen,
                    memory_context=get_relevant_memories(
                        self._goal, n=3, deep=self._is_complex_task
                    ),
                    goal=self._goal,
                )

            if (i + 1) % CHECKPOINT_EVERY == 0:
                await self._checkpoint(i)

            r = await self._llm_with_retry(i)
            if r is None:
                return "Rate limit exceeded. Try again in a moment."

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
                    return "Stuck after max attempts"
                self.history.append({"role": "user",
                    "content": f"[Recovery] {await self._reflect()}"})
                continue

            # Skill gate — shown once when no skills matched
            if not self._matched_skills and not self._skill_gate_shown:
                self._skill_gate_shown = True
                self.history.append({"role": "user",
                    "content": "[Skill gate] No skills matched. Check YOUR SKILLS first."})

            # Parallel tool execution
            results = await self._run_parallel(calls)

            for c, (raw, elapsed) in zip(calls, results):
                name  = c["name"]
                ok    = not self._is_error(raw)
                etype = self._categorize_error(raw) if not ok else None

                self._trace.append({
                    "step": i, "tool": name,
                    "args": c.get("arguments", {}),
                    "result": str(raw)[:300],
                    "success": ok, "error_type": etype,
                    "elapsed_sec": elapsed,
                })

                if ok: self._consec_errors[name] = 0
                else:  self._consec_errors[name] += 1

                # Only update metrics for skills whose tools match this tool call
                for skill in self._skills_for_tool(name):
                    update_skill_metric(skill.name, ok)

                self._update_belief(name, c.get("arguments", {}), str(raw), ok)

                if ok and self._skill_plan:
                    self._mark_skill_step_done(name)

                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", name),
                    "content": self._fmt_tool_output(name, c.get("arguments", {}), raw, ok, elapsed),
                })

                if name == "file_editor":
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp and fp.endswith(".skill.md"):
                        self._skills_dirty = True

            # Meta-skill: novel + complex + not yet triggered (separate from gate flag)
            if (
                self._novel_task
                and len(self._trace) >= COMPLEX_THRESHOLD
                and any(t["success"] for t in self._trace)
                and not self._meta_skill_triggered
            ):
                self._meta_skill_triggered = True
                self._inject_meta_skill_reminder()

        return "Iteration limit reached"

    # ── Rate-limit retry ─────────────────────────────────────────────────────────

    async def _llm_with_retry(self, step: int, max_attempts: int = 4) -> dict | None:
        delay = RATE_LIMIT_BASE
        for attempt in range(max_attempts):
            try:
                r = await asyncio.wait_for(
                    self.llm.chat(system=self._system_prompt, messages=self._msgs(step)),
                    timeout=45,
                )
                self._backoff       = max(1.0, self._backoff * 0.9)
                self._rate_attempts = 0
                return r
            except asyncio.TimeoutError:
                self._backoff = min(3.0, self._backoff * 1.5)
                self.history.append({"role": "user",
                    "content": "Timeout. Continue or give final answer."})
                return None
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ("rate", "429", "quota", "limit")):
                    self._rate_attempts += 1
                    jitter = random.uniform(0, RATE_LIMIT_JITTER)
                    wait   = min(RATE_LIMIT_MAX, delay * (2 ** attempt) + jitter)
                    self.on_status(f"Rate limit — waiting {wait:.0f}s…")
                    await asyncio.sleep(wait)
                    continue
                logger.error("LLM error: %s", e)
                return None
        return None

    # ── Parallel Tool Execution ──────────────────────────────────────────────────

    async def _run_parallel(self, calls: list) -> list[tuple[str, float]]:
        async def run_one(c: dict) -> tuple[str, float]:
            name  = c["name"]
            args  = c.get("arguments", {})
            start = time.time()
            if name not in self.tools:
                return f"Unknown tool '{name}'.", 0.0
            self.on_status(f"{TOOL_LABELS.get(name, name)} {self._fmt_action(name, args)}")
            try:
                raw = await asyncio.wait_for(self._run(name, args), timeout=TOOL_TIMEOUT)
            except asyncio.TimeoutError:
                raw = f"Timeout after {TOOL_TIMEOUT}s"
            return raw, round(time.time() - start, 2)

        return list(await asyncio.gather(*[run_one(c) for c in calls]))

    # ── ReflAct injection ────────────────────────────────────────────────────────

    def _msgs(self, step: int) -> list[dict]:
        if step == 0 or not self._trace:
            return self.history

        t       = self._trace[-1]
        recent  = " → ".join(
            f"{s['tool']}({'✓' if s['success'] else '✗'})"
            for s in self._trace[-5:]
        )
        skills_line  = f"ACTIVE SKILLS: {', '.join(self._matched_skills)}\n" if self._matched_skills else ""
        plan_line    = self._skill_plan_summary()
        subtask_line = (
            f"SUBTASK [{self._subtask_idx}/{self._total_subtasks}]: {self._current_subtask}\n"
        ) if self._current_subtask and self._total_subtasks else ""
        status = "✓ ok" if t["success"] else f"✗ {t['error_type']}"

        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{subtask_line}"
            f"{skills_line}"
            f"{plan_line + chr(10) if plan_line else ''}"
            f"{self._belief_summary()}\n"
            f"LAST: {t['tool']} → {status} ({t['elapsed_sec']}s) | {t['result'][:120]}\n"
            f"RECENT: {recent}\n\n"
            "Given belief state and skill plan — execute the next pending step."
        )
        return self.history + [{"role": "user", "content": inj}]

    # ── Meta-skill injection ─────────────────────────────────────────────────────

    def _inject_meta_skill_reminder(self):
        winning = " → ".join(
            f"{t['tool']}({self._fmt_action(t['tool'], t['args'])})"
            for t in self._trace if t["success"]
        )
        self.history.append({"role": "user", "content": (
            "[Skill evolution] Complex novel task solved. "
            f"Winning sequence: {winning}\n"
            "Before final answer:\n"
            "1. List agent/skills/generated/ — similar skill exists?\n"
            "2. If not: write .skill.md with exact sequence + failure_modes.\n"
            "3. Include: name, description, keywords, agent_behavior, "
            "failure_modes, mcp_tools.\n"
            "Then give your final answer."
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

    # ── Output ───────────────────────────────────────────────────────────────────

    def _fmt_action(self, name: str, args: dict) -> str:
        if name == "terminal":
            return f"→ {args.get('command','')[:60]}"
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
        hdr = f"[{name}] {self._fmt_action(name, args)} ({elapsed}s) {'✓' if ok else '✗'}"
        if not ok:
            return f"{hdr}\nError: {self._brief(raw)}"
        if name == "terminal":
            lines = str(raw).strip().splitlines()
            if not lines: return f"{hdr}\n(no output)"
            preview = "\n".join(lines[:5])
            tail    = f"\n… ({len(lines)-5} more)" if len(lines) > 5 else ""
            return f"{hdr}\n{preview}{tail}"
        if name == "file_editor" and args.get("action") in ("write","create","delete","replace"):
            return f"{hdr}\nDone"
        if name == "memory" and args.get("action") in ("store","delete"):
            return f"{hdr}\nDone"
        return f"{hdr}\n{self._brief(raw)}"

    def _brief(self, raw: Any) -> str:
        s = str(raw).strip() or "(empty)"
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                if d.get("status") == "error":
                    return f"Error: {d.get('message', s)}"
                for k in ("output","content","data","result","text"):
                    if k in d: return str(d[k])[:250]
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
                except Exception: pass
        if len(names) >= 3 and len(set(names)) == 1: return True
        recent = self._trace[-STUCK_MIN:]
        return len(recent) >= STUCK_MIN and all(not s["success"] for s in recent)

    async def _reflect(self) -> str:
        failed = "\n".join(
            f"  {s['tool']} → {s.get('error_type','?')}: {s['result'][:80]}"
            for s in self._trace[-3:] if not s["success"]
        ) or "  (none)"
        r = await self.llm.chat(
            system="Debug agent failures. DIAGNOSIS then NEXT STEP.",
            messages=[{"role": "user", "content":
                f"GOAL: {self._goal}\n{self._belief_summary()}\nFAILED:\n{failed}\n\nDIAGNOSIS:\nNEXT:"}],
        )
        return r.get("content", "Try completely different approach")

    async def _trim(self) -> None:
        """
        Goal-aware compression (Active CC).
        Summarizes with explicit focus on what is still relevant to the current goal.
        """
        if len(self.history) <= TRIM_AT: return
        first, mid, tail = self.history[:1], self.history[1:-KEEP_LAST], self.history[-KEEP_LAST:]
        if not mid: return
        try:
            r = await self.llm.chat(
                system=(
                    "Compress this conversation history. "
                    f"GOAL: {self._goal}\n"
                    "Rules: keep facts relevant to the goal, discard irrelevant exchanges. "
                    "Output 2-3 sentences max. Focus on: what was tried, what worked, "
                    "what is still needed to complete the goal."
                ),
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
                "belief":      self._belief["goal_progress"],
                "subtask":     f"{self._subtask_idx}/{self._total_subtasks}",
                "trace_len":   len(self._trace),
                "resources":   self._monitor.stats(),
            })
        except Exception as e:
            logger.debug("Checkpoint: %s", e)

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
            "active_skills": self._matched_skills,
            "insights_used": len(self._task_insights),
            "storage":       get_storage_summary(),
        }