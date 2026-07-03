"""
PENZER — Research-Grade Agent

Research sources:
  ReflAct    (Kim 2025)       — belief-state injection, 27.7% improvement
  ExpeL      (Zhao AAAI 2024) — insight extraction, trajectory recall
  Reflexion  (Shinn 2023)     — verbal post-mortem + evaluator
  HyMem      (Zhao 2026)      — dual-tier retrieval, complexity scoring
  MemoryBank (Zhong 2024)     — Ebbinghaus decay + spaced repetition
  Active CC  (Arxiv 2601)     — goal-aware context compression

Implements:
  1.  Belief state            — updated every tool call
  2.  Working memory buffer   — 7-item active context (Miller's Law)
  3.  Dual-tier memory        — fast for simple, deep for complex
  4.  Episodic replay         — compressed narrative of past similar runs
  5.  Reflexion + ExpeL       — post-mortem + insight extraction
  6.  Task completion eval    — did we actually solve it?
  7.  Execution queue         — state machine for milestones → steps
  8.  Hierarchical planning   — milestones → steps, replan per branch
  9.  Tool confidence scoring — score before calling, skip if < 0.5
  10. Multi-skill orchestration — merged ordered plan across ALL skills
  11. Skill-aware metrics     — only update skill if its tools were used
  12. Parallel + speculative  — concurrent tools, race independent branches
  13. Rate-limit retry        — exponential backoff + jitter
  14. Proactive compression   — goal-aware trim with concurrency lock
  15. Memory consolidation    — episodic → semantic on schedule
"""
import json, logging, inspect, asyncio, signal, time, psutil, random, re
from typing import Any, Callable
from datetime import datetime
from collections import defaultdict, deque

from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_history, save_history, clear_history,
    remember_episodic, remember_semantic,
    store_post_mortem, get_post_mortems,
    get_relevant_memories, get_insights, store_insight,
    get_similar_trajectories, get_episode_replay,
    score_complexity, should_consolidate, consolidate_memory,
    update_skill_metric, add_checkpoint,
    get_storage_summary, get_relevant_kv_facts,
    kv_store, kv_get, kv_list, kv_delete,
)
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history
from tools.plugins import create_plugin_tool, load_plugin_tools
from tools.executor import get_execution_state, update_execution_state, set_execution_state

logger = logging.getLogger(__name__)

# Adaptive iteration limits by complexity
ITER_BY_COMPLEXITY = {
    "simple":  5,   # 0.0–0.3
    "medium":  10,  # 0.3–0.6
    "complex": 20,  # 0.6–1.0
}
TRIM_AT          = 30
KEEP_LAST        = 8
STUCK_MIN        = 2
MAX_FAILURES     = 3
TOOL_TIMEOUT     = 30
CHECKPOINT_EVERY = 10
MEMORY_CRITICAL  = 85
COMPLEX_THRESHOLD = 3

RATE_LIMIT_BASE   = 5.0
RATE_LIMIT_MAX    = 60.0
RATE_LIMIT_JITTER = 2.0

# Working memory: max 7 items (Miller's Law)
WORKING_MEMORY_SIZE = 7

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
        self._resume_state  = {}
        self._plugin_tools  = load_plugin_tools()

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
        self._complexity_score:    float = 0.0
        self._max_iter:            int   = 10
        self._task_insights:       list  = []
        self._past_trajectories:   list  = []
        self._trimming:            bool  = False

        # Skill flags — separate to avoid blocking each other
        self._skill_gate_shown:    bool  = False
        self._meta_skill_triggered:bool  = False

        # Multi-skill plan
        self._skill_plan:          list  = []
        self._skill_steps:         dict  = {}
        self._skill_done:          set   = set()

        # Subtask tracking (hierarchical planner)
        self._milestones:          list  = []  # high-level goals
        self._subtasks:            list  = []  # current milestone's steps
        self._subtask_idx:         int   = 0
        self._milestone_idx:       int   = 0
        self._total_subtasks:      int   = 0
        self._current_subtask:     str   = ""

        # Execution state machine
        self._execution_queue:     list  = []
        self._execution_index:     int   = 0
        self._active_execution_item: dict | None = None
        self._execution_complete:  bool  = False

        # Working memory (Miller's Law — max 7 items)
        self._working_mem: deque = deque(maxlen=WORKING_MEMORY_SIZE)

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
        self.tools.setdefault("memory", "builtin")
        return self

    def list_plugin_tools(self) -> list[str]:
        """Return sorted available plugin tool names."""
        return sorted((getattr(self, "_plugin_tools", {}) or {}).keys())

    def _looks_like_memory_query(self, query: str) -> bool:
        q = query.lower()
        if any(cue in q for cue in (
            "remember", "memory", "recall", "stored", "last time", "as before",
            "what do you know", "what did you", "my ", "me ", "preference",
            "path", "project", "config", "env", "ip", "address", "name",
            "email", "phone",
        )):
            return True
        return False

    def _match_core_skills(self, user_input: str) -> list:
        lowered = user_input.lower()
        matched = []
        facts = get_relevant_kv_facts(user_input, n=3)
        for skill in self.core_skills:
            skill_name = (skill.name or "").lower()
            keyword_hit = any(k.lower() in lowered for k in skill.keywords or [])
            if keyword_hit:
                matched.append(skill)
                continue
            if "memory" in skill_name and (facts or self._looks_like_memory_query(lowered)):
                matched.append(skill)
        return matched

    # ── Public ──────────────────────────────────────────────────────────────────

    async def run(self, user_input: str) -> str:
        self._reset()
        self._goal             = user_input
        self._complexity_score = score_complexity(user_input)
        self._is_complex_task  = self._complexity_score >= 0.4

        # Adaptive MAX_ITER based on complexity
        if self._complexity_score < 0.3:
            self._max_iter = ITER_BY_COMPLEXITY["simple"]
        elif self._complexity_score < 0.6:
            self._max_iter = ITER_BY_COMPLEXITY["medium"]
        else:
            self._max_iter = ITER_BY_COMPLEXITY["complex"]

        self.history.append({"role": "user", "content": user_input})

        # Dual-tier memory retrieval
        past_memory        = get_relevant_memories(user_input, n=5, deep=self._is_complex_task)
        past_mortems       = get_post_mortems(user_input, n=2)
        self._task_insights     = get_insights(user_input, n=3)
        self._past_trajectories = get_similar_trajectories(user_input, n=2)

        # Episodic replay for complex tasks
        episode_replay = get_episode_replay(user_input, n=3) if self._is_complex_task else ""

        matched_gen  = search_generated_skills(
            user_input, self.gen_skills,
            context=build_context_from_history(self.history),
        )
        matched_core = self._match_core_skills(user_input)
        self._active_skills       = matched_core + list(matched_gen)
        self._matched_skills      = [s.name for s in self._active_skills]
        self._last_matched_skills = self._matched_skills
        self._novel_task          = not bool(self._matched_skills)

        if self._active_skills:
            self._orchestrate_skills()

        skills_hint = (
            f"SKILLS MATCHED: {', '.join(self._matched_skills)}\n"
            "All matched skills active — follow their merged SKILL PLAN.\n"
        ) if self._matched_skills else (
            "NO SKILLS MATCHED — proceed. Generate skill after if 3+ tools used.\n"
        )

        insight_hint = ""
        if self._task_insights:
            insight_hint += "\n## Recalled Insights\n" + "".join(
                f"- {i['insight']}\n" for i in self._task_insights
            )
        if self._past_trajectories:
            insight_hint += "\n## Similar Past Runs\n" + "".join(
                f"- {t['event']} → {t['outcome']}\n" for t in self._past_trajectories
            )
        if episode_replay:
            insight_hint += f"\n{episode_replay}\n"

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
        self._resume_state = {
            "goal": user_input,
            "current_step": "Start",
            "completed_steps": [],
            "blocked_steps": [],
            "next_action": "Reason about the next tool call",
            "needs_confirmation": False,
            "confirmation_reason": "",
        }
        set_execution_state({"state": self._resume_state})

        # Hierarchical planner for complex tasks
        if self._is_complex_task:
            self._milestones = await self._plan_hierarchical(user_input)
            if self._milestones:
                self._subtasks       = self._milestones[0].get("steps", [])
                self._total_subtasks = sum(len(m.get("steps", [])) for m in self._milestones)
            self._build_execution_queue()
        else:
            self._execution_queue = []
            self._execution_index = 0
            self._active_execution_item = None
            self._execution_complete = True

        result = await self._loop()

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]

        if self._trace:
            tool_seq = " → ".join(t["tool"] for t in self._trace)
            outcome  = "success" if any(t["success"] for t in self._trace) else "failure"
            remember_episodic(
                event=f"Goal: {user_input[:60]} | Tools: {tool_seq}",
                outcome=outcome,
                importance=min(1.0, len(self._trace) * 0.15),
                task_type=user_input[:40],
            )

        if len(self._trace) >= COMPLEX_THRESHOLD:
            completed, eval_reason = await self._evaluate_completion(user_input, result)
            if not completed:
                result = f"{result} [Note: incomplete — {eval_reason}]"
            await self._write_post_mortem_and_insights(user_input, result)

        # Memory consolidation on schedule
        if should_consolidate():
            asyncio.ensure_future(consolidate_memory(self.llm))

        save_history(self.history)
        return result

    # ── Skill Orchestration ──────────────────────────────────────────────────────

    def _orchestrate_skills(self) -> None:
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
                    "skill": skill.name, "step": idx,
                    "instruction": line, "tools": tools, "done": False,
                })
        tool_order = ["memory", "planning", "browser", "terminal", "file_editor"]
        self._skill_plan.sort(key=lambda s: next(
            (i for i, t in enumerate(tool_order) if t in s["tools"]), len(tool_order)
        ))

    def _skill_plan_summary(self) -> str:
        if not self._skill_plan: return ""
        total   = len(self._skill_plan)
        done    = sum(1 for s in self._skill_plan if s["done"])
        pending = [s for s in self._skill_plan if not s["done"]][:3]
        lines   = [f"SKILL PLAN [{done}/{total} steps]"]
        for s in pending:
            lines.append(f"  [{s['skill']}] step {s['step']+1}: {s['instruction'][:80]}")
        return "\n".join(lines)

    def _mark_skill_step_done(self, tool_name: str) -> None:
        for step in self._skill_plan:
            if step["done"]: continue
            if not step["tools"] or tool_name in step["tools"]:
                step["done"] = True
                skill_name   = step["skill"]
                self._skill_steps[skill_name] = step["step"] + 1
                if all(s["done"] for s in self._skill_plan if s["skill"] == skill_name):
                    self._skill_done.add(skill_name)
                break

    def _skills_for_tool(self, tool_name: str) -> list:
        return [s for s in self._active_skills
                if not set(s.mcp_tools or []) or tool_name in set(s.mcp_tools or [])]

    # ── Working Memory ───────────────────────────────────────────────────────────

    def _update_working_memory(self, tool: str, result: str, ok: bool) -> None:
        """Keep last WORKING_MEMORY_SIZE relevant facts from tool results."""
        if ok and result and result != "(empty)":
            fact = f"{tool}: {result[:80]}"
            self._working_mem.append(fact)

    def _working_mem_summary(self) -> str:
        if not self._working_mem: return ""
        return "WORKING MEM: " + " | ".join(list(self._working_mem)[-3:])

    # ── Hierarchical Planner ─────────────────────────────────────────────────────

    async def _is_complex(self, goal: str) -> bool:
        return score_complexity(goal) >= 0.4

    async def _plan_hierarchical(self, goal: str) -> list[dict]:
        """
        Level 1: 3-5 high-level milestones
        Level 2: each milestone → 2-3 executable steps
        Returns: [{milestone, steps: [str]}]
        """
        self.on_status("Planning…")
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "Create a hierarchical plan. Return JSON array of objects: "
                        '[{"milestone": "...", "steps": ["step1", "step2"]}]. '
                        "2-4 milestones, 2-3 steps each. No markdown."
                    ),
                    messages=[{"role": "user", "content": f"Goal: {goal}"}],
                ),
                timeout=15,
            )
            raw  = r.get("content", "[]").strip().strip("```").lstrip("json").strip()
            plan = json.loads(raw)
            if isinstance(plan, list) and plan:
                return plan
        except Exception as e:
            logger.debug("Hierarchical planner: %s", e)
        return []

    async def _replan_milestone(self, milestone: str, reason: str) -> list[str]:
        """Replan only the failed milestone branch — not the whole task."""
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system="Task replanner. Return JSON array of 2-3 new steps. No markdown.",
                    messages=[{"role": "user", "content":
                        f"Milestone: {milestone}\nFailed because: {reason}\n"
                        f"Context: {self._belief_summary()}\nNew steps:"}],
                ),
                timeout=10,
            )
            raw   = r.get("content", "[]").strip().strip("```").lstrip("json").strip()
            steps = json.loads(raw)
            if isinstance(steps, list) and steps:
                return steps
        except Exception as e:
            logger.debug("Replan: %s", e)
        return []

    # ── Tool Confidence Scoring ───────────────────────────────────────────────────

    def _tool_confidence(self, tool_name: str, args: dict) -> float:
        """
        Score 0.0-1.0 confidence that this tool will succeed.
        Factors: past success rate + belief state match + consecutive error penalty
        """
        from session.memory import get_skill_metric
        # Base confidence
        score = 0.7

        # Penalty for consecutive errors with this tool
        consec = self._consec_errors.get(tool_name, 0)
        score -= consec * 0.15

        # Bonus if belief state is not blocked
        if self._belief["goal_progress"] != "blocked":
            score += 0.1

        # Penalty if we've tried this exact call before
        key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        if key in self._cache:
            score -= 0.3  # cached = already tried

        # Skill success rate bonus
        for skill in self._skills_for_tool(tool_name):
            m = get_skill_metric(skill.name)
            score += m.get("success_rate", 0) * 0.1

        return min(1.0, max(0.0, score))

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

    # ── Task Completion Evaluator (Reflexion) ─────────────────────────────────────

    async def _evaluate_completion(self, goal: str, result: str) -> tuple[bool, str]:
        try:
            r = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "Evaluate if goal was achieved. "
                        'Return JSON: {"completed": true/false, "reason": "one sentence"}. '
                        "Be strict — partial = not completed."
                    ),
                    messages=[{"role": "user", "content":
                        f"GOAL: {goal}\nRESULT: {result[:200]}\n"
                        f"TOOLS: {' → '.join(t['tool'] for t in self._trace)}"}],
                ),
                timeout=10,
            )
            raw = r.get("content", "{}").strip().strip("```").lstrip("json").strip()
            ev  = json.loads(raw)
            return bool(ev.get("completed", True)), ev.get("reason", "")
        except Exception:
            return True, ""

    # ── Reflexion + ExpeL ─────────────────────────────────────────────────────────

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
                        "Post-mortem + extract insight. "
                        "JSON keys: what_worked, what_failed, next_time, insight. "
                        "insight = one general rule for future tasks. One sentence each."
                    ),
                    messages=[{"role": "user", "content":
                        f"Goal: {goal}\nOutcome: {result[:80]}\n"
                        f"Worked: {worked}\nFailed: {failed}"}],
                ),
                timeout=15,
            )
            raw = r.get("content", "{}").strip().strip("```").lstrip("json").strip()
            pm  = json.loads(raw)
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

    def _build_execution_queue(self) -> None:
        self._execution_queue = []
        self._execution_index = 0
        self._active_execution_item = None
        self._execution_complete = False

        if not self._milestones:
            self._execution_complete = True
            return

        for milestone_idx, milestone in enumerate(self._milestones):
            milestone_name = milestone.get("milestone", "").strip()
            if milestone_name:
                self._execution_queue.append({
                    "kind": "milestone",
                    "title": milestone_name,
                    "milestone_idx": milestone_idx,
                    "step_index": None,
                })
            for step_idx, step in enumerate(milestone.get("steps", []) or []):
                if step:
                    self._execution_queue.append({
                        "kind": "step",
                        "title": step,
                        "milestone_idx": milestone_idx,
                        "step_index": step_idx,
                    })

    def _claim_next_execution_item(self) -> dict | None:
        if self._execution_complete:
            return None
        if self._execution_index >= len(self._execution_queue):
            self._execution_complete = True
            self._active_execution_item = None
            return None

        item = self._execution_queue[self._execution_index]
        self._execution_index += 1
        self._active_execution_item = item
        self._current_subtask = item.get("title", "")
        self._milestone_idx = item.get("milestone_idx", getattr(self, "_milestone_idx", 0))
        if self._milestones and self._milestone_idx < len(self._milestones):
            self._subtasks = self._milestones[self._milestone_idx].get("steps", [])
        else:
            self._subtasks = getattr(self, "_subtasks", [])
        return item

    def _complete_current_execution_item(self, success: bool = True) -> None:
        if self._active_execution_item is None:
            return
        self._active_execution_item = None
        self._execution_complete = self._execution_index >= len(self._execution_queue)
        if not success:
            self._execution_complete = False

    # ── Main Loop ────────────────────────────────────────────────────────────────

    async def _loop(self) -> str:
        empty = 0

        for i in range(self._max_iter):
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

            # Executor: drive the next pending execution item from the state machine
            if self._active_execution_item is None:
                item = self._claim_next_execution_item()
                if item is not None:
                    self.history.append({"role": "user", "content":
                        f"[Executor] {item['kind'].title()} — {item['title']}"})
                elif self._execution_complete:
                    self.history.append({"role": "user",
                        "content": "[Executor] All planned work complete. Give final answer."})
                    self._milestones = []

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
                    if self._active_execution_item is not None:
                        self._complete_current_execution_item(success=True)
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
                # Replan current milestone if in hierarchical mode
                if self._milestones and self._milestone_idx < len(self._milestones):
                    ms       = self._milestones[self._milestone_idx]
                    new_steps = await self._replan_milestone(
                        ms.get("milestone", ""), self._belief["last_outcome"]
                    )
                    if new_steps:
                        self._subtasks    = new_steps
                        self._subtask_idx = 0
                        self.history.append({"role": "user",
                            "content": f"[Replan] New steps for '{ms.get('milestone','')}': "
                                       f"{new_steps}"})
                        continue
                self.history.append({"role": "user",
                    "content": f"[Recovery] {await self._reflect()}"})
                continue

            if not self._matched_skills and not self._skill_gate_shown:
                self._skill_gate_shown = True
                self.history.append({"role": "user",
                    "content": "[Skill gate] No skills matched. Check YOUR SKILLS first."})

            # Tool confidence check — skip low-confidence tools
            filtered_calls = []
            for c in calls:
                conf = self._tool_confidence(c["name"], c.get("arguments", {}))
                if conf < 0.4:
                    self.history.append({"role": "tool",
                        "tool_call_id": c.get("id", c["name"]),
                        "content": f"[Skipped] {c['name']} confidence {conf:.0%} too low. Try different approach."})
                else:
                    filtered_calls.append(c)

            if not filtered_calls:
                self.history.append({"role": "user",
                    "content": "All proposed tools had low confidence. Rethink approach."})
                continue

            # Speculative execution for independent subtasks
            results = await self._run_speculative(filtered_calls)
            if any(self._is_error(raw) for raw, _ in results):
                fallback_results = []
                for call, (raw, elapsed) in zip(filtered_calls, results):
                    if self._is_error(raw):
                        fb_raw, fb_elapsed = await self._run_with_fallback(call)
                        fallback_results.append((fb_raw, fb_elapsed))
                    else:
                        fallback_results.append((raw, elapsed))
                results = fallback_results

            for c, (raw, elapsed) in zip(filtered_calls, results):
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
                self._resume_state["current_step"] = self._fmt_action(name, c.get("arguments", {}))
                self._resume_state["completed_steps"] = self._resume_state.get("completed_steps", []) + [self._fmt_action(name, c.get("arguments", {}))]
                self._resume_state["next_action"] = "Continue to the next step if needed"
                if not ok:
                    self._resume_state["blocked_steps"] = self._resume_state.get("blocked_steps", []) + [self._fmt_action(name, c.get("arguments", {}))]
                set_execution_state({"state": self._resume_state})

                if ok: self._consec_errors[name] = 0
                else:  self._consec_errors[name] += 1

                for skill in self._skills_for_tool(name):
                    update_skill_metric(skill.name, ok)

                self._update_belief(name, c.get("arguments", {}), str(raw), ok)
                self._update_working_memory(name, str(raw), ok)

                if ok and self._skill_plan:
                    self._mark_skill_step_done(name)

                if ok and name == "terminal" and self._maybe_auto_create_plugin():
                    self.history.append({"role": "user", "content": "[Auto-plugin] Repeated terminal workflow detected; created a reusable plugin tool."})

                self.history.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", name),
                    "content": self._fmt_tool_output(name, c.get("arguments", {}), raw, ok, elapsed),
                })

                if name == "file_editor":
                    fp = str(c.get("arguments", {}).get("filepath", ""))
                    if "skills/generated" in fp and fp.endswith(".skill.md"):
                        self._skills_dirty = True

            if self._active_execution_item is not None:
                any_success = any(t["success"] for t in self._trace[-len(filtered_calls):]) if filtered_calls else False
                self._complete_current_execution_item(success=any_success)

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

    # ── Speculative Execution ────────────────────────────────────────────────────

    async def _run_speculative(self, calls: list) -> list[tuple[str, float]]:
        """
        For independent tool calls: race them and take the first success.
        For dependent calls (share file/env): run sequentially.
        Otherwise: run in parallel.
        """
        if len(calls) <= 1:
            return await self._run_parallel(calls)

        # Independence check: calls are independent if they don't share file targets
        def get_target(c):
            args = c.get("arguments", {})
            return args.get("filepath") or args.get("command", "")[:20]

        targets = [get_target(c) for c in calls]
        unique  = len(set(t for t in targets if t)) == len([t for t in targets if t])

        if unique and all(c["name"] in ("browser", "terminal", "run_bash") for c in calls):
            # Speculative: race independent calls, take first success
            return await self._run_race(calls)

        return await self._run_parallel(calls)

    async def _run_race(self, calls: list) -> list[tuple[str, float]]:
        """Launch all calls, cancel losers when first succeeds."""
        results = [("(cancelled)", 0.0)] * len(calls)

        async def run_and_report(idx: int, c: dict, done_event: asyncio.Event):
            name  = c["name"]
            args  = c.get("arguments", {})
            start = time.time()
            if name == "memory":
                raw = self._run_memory_tool(args)
            elif name not in self.tools:
                raw = f"Unknown tool '{name}'."
            else:
                self.on_status(f"{TOOL_LABELS.get(name, name)} {self._fmt_action(name, args)}")
                try:
                    raw = await asyncio.wait_for(self._run(name, args), timeout=TOOL_TIMEOUT)
                except asyncio.TimeoutError:
                    raw = f"Timeout after {TOOL_TIMEOUT}s"
            elapsed        = round(time.time() - start, 2)
            results[idx]   = (raw, elapsed)
            if not self._is_error(raw):
                done_event.set()  # signal first success

        done  = asyncio.Event()
        tasks = [asyncio.create_task(run_and_report(i, c, done)) for i, c in enumerate(calls)]
        try:
            await asyncio.wait_for(done.wait(), timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return results

    def _fallback_tool(self, tool_name: str) -> str | None:
        return FALLBACKS.get(tool_name)

    async def _run_with_fallback(self, call: dict) -> tuple[str, float]:
        name = call.get("name")
        if not name:
            return "No tool name provided", 0.0

        results = await self._run_parallel([call])
        raw, elapsed = results[0] if results else ("", 0.0)
        if raw and not self._is_error(raw):
            return raw, elapsed

        fallback = self._fallback_tool(name)
        if fallback and fallback in self.tools:
            self.on_status(f"Fallback → {fallback}…")
            fb_call = {**call, "name": fallback}
            fb_results = await self._run_parallel([fb_call])
            fb_raw, fb_elapsed = fb_results[0] if fb_results else ("", 0.0)
            if fb_raw and not self._is_error(fb_raw):
                return fb_raw, fb_elapsed
            return fb_raw, fb_elapsed
        return raw, elapsed

    async def _run_parallel(self, calls: list) -> list[tuple[str, float]]:
        async def run_one(c: dict) -> tuple[str, float]:
            name  = c["name"]
            args  = c.get("arguments", {})
            start = time.time()
            if name == "memory":
                self.on_status(f"🧠 {self._fmt_action(name, args)}")
                raw = self._run_memory_tool(args)
                return raw, round(time.time() - start, 2)
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
        skills_line   = f"ACTIVE SKILLS: {', '.join(self._matched_skills)}\n" if self._matched_skills else ""
        plan_line     = self._skill_plan_summary()
        subtask_line  = (
            f"ACTIVE ITEM: {self._active_execution_item.get('title', self._current_subtask)}\n"
        ) if self._active_execution_item else ""
        wm_line = self._working_mem_summary()
        status  = "✓ ok" if t["success"] else f"✗ {t['error_type']}"
        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{subtask_line}"
            f"{skills_line}"
            f"{plan_line + chr(10) if plan_line else ''}"
            f"{self._belief_summary()}\n"
            f"{wm_line + chr(10) if wm_line else ''}"
            f"LAST: {t['tool']} → {status} ({t['elapsed_sec']}s) | {t['result'][:100]}\n"
            f"RECENT: {recent}\n\n"
            "Given belief state, working memory, and skill plan — execute next pending step."
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
            "3. Include: name, description, keywords, agent_behavior, failure_modes, mcp_tools.\n"
            "Then give your final answer."
        )})

    # ── Tool execution ───────────────────────────────────────────────────────────

    async def _run(self, name: str, args: dict) -> str:
        tools = getattr(self, "tools", {}) or {}
        if name == "memory" or tools.get(name) == "builtin":
            return self._run_memory_tool(args)
        if name == "plugin_tool":
            return self._run_plugin_tool(args)
        if name in self._plugin_tools:
            try:
                return str(self._plugin_tools[name](**args))
            except Exception as e:
                return f"Plugin error: {e}"
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
                kw  = {k: v for k, v in args.items() if k in sig.parameters}
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

    def _run_memory_tool(self, args: dict) -> str:
        action = args.get("action", "")
        key    = args.get("key", "")
        value  = args.get("value", "")
        if action == "get":    return kv_get(key)
        if action == "store":  return kv_store(key, value)
        if action == "list":   return kv_list()
        if action == "delete": return kv_delete(key)
        return f"Unknown memory action '{action}'. Use: get, store, list, delete"

    def _maybe_auto_create_plugin(self) -> bool:
        """Reuse an existing plugin when possible; otherwise create one for a repeated terminal workflow."""
        if not getattr(self, "_trace", None):
            return False

        repeated = []
        for item in self._trace:
            tool = item.get("tool")
            args = item.get("args") or {}
            if tool == "terminal" and item.get("success"):
                command = str(args.get("command", "")).strip()
                if command:
                    repeated.append(command)

        if len(repeated) < 2:
            return False

        counts = {}
        for command in repeated:
            counts[command] = counts.get(command, 0) + 1

        recurring = [cmd for cmd, count in counts.items() if count >= 2]
        if not recurring:
            return False

        command = recurring[0]
        if command in {""}:
            return False

        slug = re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_") or "terminal_command"
        name = slug[:40]
        existing_tools = getattr(self, "_plugin_tools", {}) or {}
        if name in existing_tools:
            return True

        description = f"Reusable helper for: {command[:80]}"
        code = (
            "import subprocess\n\n"
            f"def {name}(**kwargs):\n"
            f"    return subprocess.check_output({command!r}, shell=True, text=True)"
        )
        try:
            create_plugin_tool(name=name, description=description, code=code)
            self._plugin_tools = load_plugin_tools()
            return name in self._plugin_tools
        except Exception:
            return False

    def _run_plugin_tool(self, args: dict) -> str:
        action = (args.get("action") or "").strip().lower()
        if action == "create":
            name = str(args.get("name", "")).strip()
            description = str(args.get("description", "")).strip()
            code = str(args.get("code", "")).strip()
            if not name or not code:
                return "Plugin creation requires a name and code"
            try:
                result = create_plugin_tool(name=name, description=description or "Generated plugin", code=code)
            except Exception as exc:
                return f"Plugin creation failed: {exc}"
            self._plugin_tools = load_plugin_tools()
            tool_name = result.get("name", name)
            if tool_name in self._plugin_tools:
                return f"Plugin created successfully: {tool_name}"
            return f"Plugin created but not yet available: {tool_name}"
        return "Unknown plugin action"

    # ── Output ───────────────────────────────────────────────────────────────────

    def _fmt_action(self, name: str, args: dict) -> str:
        if name == "terminal":    return f"→ {args.get('command','')[:60]}"
        if name == "browser":     return f"→ {args.get('action','')}: {(args.get('query') or args.get('url',''))[:50]}"
        if name == "file_editor": return f"→ {args.get('action','')}: {args.get('filepath','')}"
        if name == "memory":      return f"→ {args.get('action','')}: {args.get('key','')}"
        if name == "planning":    return f"→ plan: {args.get('goal','')[:50]}"
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
                if d.get("status") == "error": return f"Error: {d.get('message', s)}"
                for k in ("output","content","data","result","text"):
                    if k in d: return str(d[k])[:250]
        except (json.JSONDecodeError, ValueError): pass
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
            if m.get("role") in ("user", "assistant", "tool"): return m["role"]
        return ""

    def _stuck(self) -> bool:
        w    = self.history[-6:]
        msgs = [m for m in w if m.get("role") == "tool"]
        if len(msgs) < STUCK_MIN: return False
        if len({str(m.get("content",""))[:80] for m in msgs}) == 1: return True
        names = []
        for m in w:
            if m.get("role") == "assistant":
                try: names.extend(tc["name"] for tc in json.loads(m["content"]).get("tool_calls", []))
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
        if self._trimming or len(self.history) <= TRIM_AT: return
        self._trimming = True
        first, mid, tail = self.history[:1], self.history[1:-KEEP_LAST], self.history[-KEEP_LAST:]
        if not mid:
            self._trimming = False
            return
        try:
            r = await self.llm.chat(
                system=(
                    f"Compress history. GOAL: {self._goal}\n"
                    "Keep goal-relevant facts only. 2-3 sentences: what tried, what worked, what still needed."
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
        finally:
            self._trimming = False

    async def _checkpoint(self, iteration: int):
        try:
            add_checkpoint({
                "timestamp":   datetime.now().isoformat(),
                "iteration":   iteration,
                "goal":        self._goal,
                "belief":      self._belief["goal_progress"],
                "milestone":   f"{self._milestone_idx}/{len(self._milestones)}",
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
            "goal":            self._goal,
            "belief":          self._belief["goal_progress"],
            "complexity":      round(self._complexity_score, 2),
            "max_iter":        self._max_iter,
            "tools_called":    len(self._trace),
            "success_count":   sum(1 for t in self._trace if t["success"]),
            "active_skills":   self._matched_skills,
            "working_mem":     list(self._working_mem),
            "insights_used":   len(self._task_insights),
            "storage":         get_storage_summary(),
        }