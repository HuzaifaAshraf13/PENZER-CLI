"""
PENZER — Research-Grade Agent

Implements:
  1. Belief State        — updated after every tool call (ReflAct paper)
  2. Episodic + Semantic — structured memory with scored retrieval
  3. Reflexion           — post-task verbal post-mortem (Shinn 2023)
  4. Planner/Executor    — Planner decomposes, Executor drives subtasks
  5. Trajectory Skills   — built from full winning tool trace
  6. Multi-skill         — ALL matched skills active, metrics tracked for each
  7. Parallel tools      — multiple tool calls run concurrently
"""
import json, logging, inspect, asyncio, signal, time, psutil
from typing import Any, Callable
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

MAX_ITER          = 15
TRIM_AT           = 35
KEEP_LAST         = 10
STUCK_MIN         = 2
MAX_FAILURES      = 3
TOOL_TIMEOUT      = 30
CHECKPOINT_EVERY  = 10
MEMORY_CRITICAL   = 85
COMPLEX_THRESHOLD = 3

TOOL_LABELS = {
    "browser": "🌐", "terminal": "⚡", "run_python": "🐍",
    "run_bash": "📜", "file_editor": "📁", "memory": "🧠", "planning": "📋",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
}
SKILL_GATED_TOOLS = {
    "planning", "memory", "file_editor", "browser",
    "terminal", "run_bash", "run_python",
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

        self._monitor  = ResourceMonitor()
        self._shutdown = False
        self._backoff  = 1.0

        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _reset(self):
        self._cache:               dict  = {}
        self._trace:               list  = []
        self._failures:            int   = 0
        self._goal:                str   = ""
        self._skills_dirty:        bool  = False
        self._matched_skills:      list  = []   # ALL matched skill names
        self._last_matched_skills: list  = []   # alias for cli.py
        self._active_skills:       list  = []   # ALL active skill objects
        self._system_prompt:       str   = ""
        self._consec_errors:       dict  = defaultdict(int)
        self._iteration:           int   = 0
        self._novel_task:          bool  = False
        self._skill_gate_shown:    bool  = False
        self._subtasks:            list  = []
        self._subtask_idx:         int   = 0
        self._current_subtask:     str   = ""
        self._belief: dict = {
            "goal_progress":  "not_started",
            "verified_facts": [],
            "assumptions":    [],
            "unknowns":       [],
            "last_action":    "",
            "last_outcome":   "",
        }
        # Multi-skill orchestration
        self._skill_plan:  list = []  # merged ordered steps from all active skills
        self._skill_steps: dict = {}  # {skill_name: current_step_index}
        self._skill_done:  set  = set()  # skills that completed all steps

    def _handle_shutdown(self, signum, frame):
        self._shutdown = True

    def _orchestrate_skills(self) -> None:
        """
        Merge all active skills into a unified execution plan.
        Each step is: {skill, step_num, instruction, tools_needed}
        Dependencies respected: skills with shared tools run sequentially not parallel.
        """
        self._skill_plan  = []
        self._skill_steps = {s.name: 0 for s in self._active_skills}
        self._skill_done  = set()

        for skill in self._active_skills:
            behavior = (skill.agent_behavior or "").strip()
            lines    = [l.strip() for l in behavior.splitlines()
                        if l.strip() and not l.strip().startswith("#")]
            tools    = set(skill.mcp_tools or [])
            for idx, line in enumerate(lines):
                self._skill_plan.append({
                    "skill":       skill.name,
                    "step":        idx,
                    "instruction": line,
                    "tools":       tools,
                    "done":        False,
                })

        # Sort: steps that share tools come together to avoid conflicts
        tool_order = ["memory", "planning", "browser", "terminal", "file_editor"]
        def sort_key(step):
            for i, t in enumerate(tool_order):
                if t in step["tools"]:
                    return i
            return len(tool_order)
        self._skill_plan.sort(key=sort_key)

    def _skill_plan_summary(self) -> str:
        """Return current skill plan progress for ReflAct injection."""
        if not self._skill_plan:
            return ""
        total = len(self._skill_plan)
        done  = sum(1 for s in self._skill_plan if s["done"])
        pending = [s for s in self._skill_plan if not s["done"]]
        next_steps = pending[:3]  # show next 3 pending steps

        lines = [f"SKILL PLAN [{done}/{total} steps done]"]
        for s in next_steps:
            lines.append(f"  [{s['skill']}] step {s['step']+1}: {s['instruction'][:80]}")
        if len(pending) > 3:
            lines.append(f"  … {len(pending)-3} more steps")
        return "\n".join(lines)

    def _mark_skill_step_done(self, tool_name: str) -> None:
        """After a tool runs, mark matching skill steps as done."""
        for step in self._skill_plan:
            if not step["done"] and tool_name in step["tools"]:
                step["done"] = True
                skill_name   = step["skill"]
                self._skill_steps[skill_name] = step["step"] + 1
                # Check if this skill is fully done
                skill_steps = [s for s in self._skill_plan if s["skill"] == skill_name]
                if all(s["done"] for s in skill_steps):
                    self._skill_done.add(skill_name)
                break  # one tool call marks one step

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

        past_memory  = get_relevant_memories(user_input, n=5)
        past_mortems = get_post_mortems(user_input, n=2)

        matched_gen = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )

        # Collect ALL matched skills — not just first one
        matched_core_skills = [
            s for s in self.core_skills
            if any(k.lower() in user_input.lower() for k in s.keywords)
        ]
        self._active_skills       = matched_core_skills + list(matched_gen)
        self._matched_skills      = [s.name for s in self._active_skills]
        self._last_matched_skills = self._matched_skills
        self._novel_task          = not bool(self._matched_skills)

        # Build merged execution plan from all matched skills
        if self._active_skills:
            self._orchestrate_skills()

        skills_hint = (
            f"SKILLS MATCHED: {', '.join(self._matched_skills)}\n"
            "All matched skills are active. Follow their combined agent_behavior steps.\n"
        ) if self._matched_skills else (
            "NO SKILLS MATCHED — proceed carefully. "
            "Generate a skill after if task needed 3+ tool calls.\n"
        )

        mortem_hint = ""
        if past_mortems:
            mortem_hint = "\n## Past Experience on Similar Tasks\n"
            for pm in past_mortems:
                mortem_hint += (
                    f"  [{pm['task_type']}]\n"
                    f"  Worked: {pm['what_worked']}\n"
                    f"  Failed: {pm['what_failed']}\n"
                    f"  Next time: {pm['next_time']}\n"
                )

        self._system_prompt = build_system_prompt(
            core_skills=self.core_skills,
            generated_skills=matched_gen,
            memory_context=past_memory,
            extra=skills_hint + mortem_hint,
            goal=user_input,
        )

        # Planner — decompose complex tasks into subtasks
        if await self._is_complex(user_input):
            self._subtasks = await self._plan_task(user_input)

        result = await self._loop()

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]

        # Episodic memory — always store if tools were used
        if self._trace:
            tool_seq = " → ".join(t["tool"] for t in self._trace)
            outcome  = "success" if any(t["success"] for t in self._trace) else "failure"
            remember_episodic(
                event=f"Goal: {user_input[:60]} | Tools: {tool_seq}",
                outcome=outcome,
                importance=min(1.0, len(self._trace) * 0.15),
                task_type=user_input[:40],
            )

        # Reflexion — post-mortem for complex tasks
        if len(self._trace) >= COMPLEX_THRESHOLD:
            await self._write_post_mortem(user_input, result)

        save_history(self.history)
        return result

    # ── Planner ──────────────────────────────────────────────────────────────────

    async def _is_complex(self, goal: str) -> bool:
        signals = [
            "build", "create", "setup", "install", "configure", "deploy",
            "write", "analyze", "research", "find and", "compare", "generate",
            "step", "multiple", "then", "after", "first", "finally",
        ]
        return any(s in goal.lower() for s in signals)

    async def _plan_task(self, goal: str) -> list[str]:
        self.on_status("Planning…")
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "You are a task planner. Break the goal into 3-6 concrete subtasks. "
                        "Return ONLY a JSON array of strings. No markdown. No explanation.\n"
                        'Example: ["Check system info", "Find open ports", "Save report"]'
                    ),
                    messages=[{"role": "user", "content": f"Goal: {goal}"}],
                ),
                timeout=15,
            )
            text     = r.get("content", "[]").strip()
            subtasks = json.loads(text)
            if isinstance(subtasks, list) and subtasks:
                logger.debug("Plan created: %s", subtasks)
                return subtasks
        except Exception as e:
            logger.debug("Planner failed: %s", e)
        return []

    # ── Executor — drives subtasks one by one ────────────────────────────────────

    async def _executor_next_subtask(self) -> str:
        if self._subtask_idx >= len(self._subtasks):
            return ""
        subtask = self._subtasks[self._subtask_idx]
        self._current_subtask = subtask
        self._subtask_idx    += 1
        return subtask

    def _all_subtasks_done(self) -> bool:
        return bool(self._subtasks) and self._subtask_idx >= len(self._subtasks)

    # ── Belief State ─────────────────────────────────────────────────────────────

    def _update_belief(self, tool: str, args: dict, result: str, ok: bool) -> None:
        self._belief["last_action"]  = f"{tool}({self._fmt_action(tool, args)})"
        self._belief["last_outcome"] = "ok" if ok else f"failed: {result[:80]}"
        if self._belief["goal_progress"] == "not_started":
            self._belief["goal_progress"] = "in_progress"
        if ok:
            fact = f"{tool}: {result[:100]}"
            if fact not in self._belief["verified_facts"]:
                self._belief["verified_facts"].append(fact)
                self._belief["verified_facts"] = self._belief["verified_facts"][-5:]
        else:
            self._belief["goal_progress"] = "blocked"

    def _belief_summary(self) -> str:
        b = self._belief
        lines = [f"BELIEF: {b['goal_progress'].upper()}"]
        if b["verified_facts"]:
            lines.append(f"  Know: {' | '.join(b['verified_facts'][-2:])}")
        if b["last_action"]:
            lines.append(f"  Last: {b['last_action']} → {b['last_outcome']}")
        return "\n".join(lines)

    # ── Reflexion ────────────────────────────────────────────────────────────────

    async def _write_post_mortem(self, goal: str, result: str) -> None:
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
                        "Write a brief post-mortem. Return JSON with keys: "
                        "what_worked, what_failed, next_time. "
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
            if any(t["success"] for t in self._trace):
                remember_semantic(
                    pattern=f"For '{goal[:40]}': use {worked}",
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

            # Executor: inject next subtask into context when previous done
            if self._subtasks and not self._all_subtasks_done():
                next_sub = await self._executor_next_subtask()
                if next_sub:
                    self.on_status(f"Subtask {self._subtask_idx}/{len(self._subtasks)}…")
                    self.history.append({"role": "user",
                        "content": f"[Executor] Next subtask: {next_sub}"})
            elif self._all_subtasks_done() and i > 0:
                self.history.append({"role": "user",
                    "content": "[Executor] All subtasks complete. Give final answer."})

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

            # Skill gate — shown once, lists ALL matched skills
            unknown = [c["name"] for c in calls
                       if c["name"] in SKILL_GATED_TOOLS and c["name"] in self.tools]
            if unknown and not self._matched_skills and not self._skill_gate_shown:
                self._skill_gate_shown = True
                self.history.append({"role": "user", "content":
                    f"[Skill gate] No skills matched. Proceeding with: {', '.join(unknown)}. "
                    "Check YOUR SKILLS first."})

            # Parallel tool execution
            results = await self._run_parallel(calls)

            # Process results
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

                # Track metrics for ALL matched skills
                for skill in self._active_skills:
                    update_skill_metric(skill.name, ok)

                self._update_belief(name, c.get("arguments", {}), str(raw), ok)
                if ok and self._skill_plan:
                    self._mark_skill_step_done(name)

                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", name),
                    "content": self._fmt_tool_output(
                        name, c.get("arguments", {}), raw, ok, elapsed
                    ),
                })

                if name == "file_editor":
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp and fp.endswith(".skill.md"):
                        self._skills_dirty = True

            # Trigger skill generation for novel complex tasks
            if (
                self._novel_task
                and len(self._trace) >= COMPLEX_THRESHOLD
                and any(t["success"] for t in self._trace)
                and not self._skill_gate_shown
            ):
                self._skill_gate_shown = True
                self._inject_meta_skill_reminder()

        return "Iteration limit reached"

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
                raw = await asyncio.wait_for(
                    self._run(name, args), timeout=TOOL_TIMEOUT
                )
            except asyncio.TimeoutError:
                raw = f"Timeout after {TOOL_TIMEOUT}s"
            return raw, round(time.time() - start, 2)

        return list(await asyncio.gather(*[run_one(c) for c in calls]))

    # ── ReflAct injection ────────────────────────────────────────────────────────

    def _msgs(self, step: int) -> list[dict]:
        if step == 0 or not self._trace:
            return self.history

        t      = self._trace[-1]
        recent = " → ".join(
            f"{s['tool']}({'✓' if s['success'] else '✗'})"
            for s in self._trace[-5:]
        )

        # All active skills shown
        skills_line = ""
        if self._matched_skills:
            skills_line = f"ACTIVE SKILLS: {', '.join(self._matched_skills)}\n"

        # Subtask progress
        subtask_line = ""
        if self._subtasks:
            done = self._subtask_idx
            total = len(self._subtasks)
            subtask_line = (
                f"PLAN [{done}/{total}]: {self._subtasks}\n"
                f"CURRENT: {self._current_subtask}\n"
            )

        status     = "✓ ok" if t["success"] else f"✗ {t['error_type']}"
        skill_plan = self._skill_plan_summary()
        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{subtask_line}"
            f"{skills_line}"
            f"{skill_plan}\n" if skill_plan else ""
            f"{self._belief_summary()}\n"
            f"LAST: {t['tool']} → {status} ({t['elapsed_sec']}s) | {t['result'][:120]}\n"
            f"RECENT: {recent}\n\n"
            "Follow the SKILL PLAN above. Next pending step — execute it."
        )
        return self.history + [{"role": "user", "content": inj}]

    # ── Meta-skill injection ─────────────────────────────────────────────────────

    def _inject_meta_skill_reminder(self):
        winning_seq = " → ".join(
            f"{t['tool']}({self._fmt_action(t['tool'], t['args'])})"
            for t in self._trace if t["success"]
        )
        self.history.append({"role": "user", "content": (
            "[Skill evolution] Complex novel task solved. "
            f"Winning sequence: {winning_seq}\n"
            "Before final answer:\n"
            "1. List agent/skills/generated/ — similar skill exists?\n"
            "2. If not: write .skill.md with this exact sequence + failure_modes.\n"
            "3. Include: name, description, keywords, agent_behavior (exact steps), "
            "failure_modes, mcp_tools.\n"
            "Then answer."
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
                        cmd = (args.get("command") or args.get("query")
                               or args.get("code") or "")
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
                        tc["name"] for tc in
                        json.loads(m["content"]).get("tool_calls", [])
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
            messages=[{"role": "user", "content":
                f"GOAL: {self._goal}\n{self._belief_summary()}\n"
                f"FAILED:\n{failed}\n\nDIAGNOSIS:\nNEXT:"}],
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
                "timestamp":   datetime.now().isoformat(),
                "iteration":   iteration,
                "goal":        self._goal,
                "belief":      self._belief["goal_progress"],
                "subtask":     f"{self._subtask_idx}/{len(self._subtasks)}",
                "trace_len":   len(self._trace),
                "resources":   self._monitor.stats(),
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
            "active_skills": self._matched_skills,
            "subtasks":      f"{self._subtask_idx}/{len(self._subtasks)}",
            "storage":       get_storage_summary(),
        }