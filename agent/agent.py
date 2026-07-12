"""
PENZER — Research-Grade Agent (thin orchestrator)

Research sources:
  ReflAct    (Kim 2025)       — belief-state injection, 27.7% improvement
  ExpeL      (Zhao AAAI 2024) — insight extraction, trajectory recall
  Reflexion  (Shinn 2023)     — verbal post-mortem + evaluator
  HyMem      (Zhao 2026)      — dual-tier retrieval, complexity scoring
  MemoryBank (Zhong 2024)     — Ebbinghaus decay + spaced repetition
  Active CC  (Arxiv 2601)     — goal-aware context compression

MODULARIZATION (this pass): the previous single-file agent.py (~1700
lines) is split into penzermodule/ — belief_manager, memory_manager,
planner, execution_manager, reflection_manager, persistence_manager,
resource_monitor. State ownership didn't change (PenzerAgent still holds
every instance attribute exactly as before), only where the *behavior*
lives: each manager takes the owning PenzerAgent explicitly as `agent`
in its methods and reads/writes its state directly. PenzerAgent keeps
every original method name as a one-line delegate (e.g. `self._transition(...)`
still works exactly as before), so nothing calling the agent — including
the existing test suite — needed to change. `_loop`, `run`, `_finalize`,
`resume_last_task`, `_msgs`, `_llm_with_retry`, and the small pure
formatting helpers (`_fmt_action` etc.) stay here since they ARE the
orchestration — deciding when to call planner vs execution vs reflection
vs belief vs persistence is the one thing that doesn't belong in any
single manager.
"""
import json, logging, inspect, asyncio, signal, time, psutil, random, re, itertools
from typing import Any, Callable
from datetime import datetime
from collections import defaultdict, deque
from agent.core import mcp
from agent.llm import LLM
from session.memory import (
    load_history, save_history, clear_history,
    remember_episodic, remember_semantic,
    remember_user_facts, store_post_mortem, get_post_mortems,
    get_relevant_memories, get_insights, store_insight,
    get_similar_trajectories, get_episode_replay,
    score_complexity, should_consolidate, consolidate_memory,
    update_skill_metric, get_skill_metric, add_checkpoint,
    get_storage_summary, get_relevant_kv_facts,
    kv_store, kv_get, kv_list, kv_delete,
    save_last_run, load_last_run, clear_last_run,
    append_steps as _append_steps_to_disk,
    get_steps as _get_persisted_steps,
    clear_steps as _clear_persisted_steps,
    estimate_iterations_needed,
)
from agent.system_prompts import build_system_prompt
from agent.skills import load_all_skills, search_generated_skills, build_context_from_history
from tools.plugins import create_plugin_tool, load_plugin_tools
from tools.executor import get_execution_state, update_execution_state, set_execution_state

from agent.penzermodule import (
    Phase, PHASE_TRANSITIONS, PHASE_TO_GOAL_PROGRESS,
    BeliefManager, MemoryManager, Planner, ExecutionManager,
    ReflectionManager, PersistenceManager, ResourceMonitor,
)

logger = logging.getLogger(__name__)

# Adaptive iteration limits by complexity
ITER_BY_COMPLEXITY = {
    "simple":  5,
    "medium":  10,
    "complex": 20,
}
TRIM_AT              = 30
KEEP_LAST            = 8
STUCK_MIN            = 2
MAX_FAILURES         = 3
ITER_EXTENSION_SIZE  = 8
MAX_RUNTIME_SECONDS  = 21600  # 6 hours
ABSOLUTE_MAX_ITER    = 5000
MAX_TOKENS_PER_RUN   = 2000000
TOOL_TIMEOUT         = 30
CHECKPOINT_EVERY     = 10
MEMORY_CRITICAL      = 85
COMPLEX_THRESHOLD    = 3
RATE_LIMIT_BASE      = 5.0
RATE_LIMIT_MAX       = 60.0
RATE_LIMIT_JITTER    = 2.0

WORKING_MEMORY_SIZE = 7

TOOL_LABELS = {
    "browser": "\U0001F310", "terminal": "\u26A1", "run_python": "\U0001F40D",
    "run_bash": "\U0001F4DC", "file_editor": "\U0001F4C1", "memory": "\U0001F9E0", "planning": "\U0001F4CB",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
}

ERROR_PATTERNS = [
    ("timeout", "TIMEOUT"),
    ("permission", "PERMISSION"),
    ("not found", "NOT_FOUND"),
    ("syntax", "SYNTAX"),
    ("invalid", "INVALID"),
]

ACTION_FORMATTERS: dict = {
    "terminal":    lambda a: f"-> {a.get('command', '')[:60]}",
    "browser":     lambda a: f"-> {a.get('action', '')}: {(a.get('query') or a.get('url', ''))[:50]}",
    "file_editor": lambda a: f"-> {a.get('action', '')}: {a.get('filepath', '')}",
    "memory":      lambda a: f"-> {a.get('action', '')}: {a.get('key', '')}",
    "planning":    lambda a: f"-> plan: {a.get('goal', '')[:50]}",
}


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
        # Managers hold no state of their own — they take `self` (this
        # agent) explicitly in every method and operate on its state
        # directly. See penzermodule/__init__.py.
        self.belief      = BeliefManager()
        self.memory      = MemoryManager()
        self.planner     = Planner()
        self.execution   = ExecutionManager()
        self.reflection  = ReflectionManager()
        self.persistence = PersistenceManager()
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
        self._run_start_time:      float = time.time()
        self._tokens_before_run:   int   = 0
        self._resume_boundary_trace_len: int = 0
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
        # Single source of truth for coordination — see Phase docstring above.
        self._phase: Phase = Phase.PLANNING
        # Structured, retrievable step log (distinct from `history`, the
        # raw LLM transcript). `_steps` is the full in-memory log for THIS
        # run; `_pending_steps` is the not-yet-flushed-to-disk tail of it.
        self._steps:         list = []
        self._pending_steps: list = []
        self._run_id:        str  = f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
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
    def _max_iter_for_complexity(self, score: float) -> int:
        return self.planner._max_iter_for_complexity(score)
    def _transition(self, to: Phase, reason: str = "") -> None:
        return self.belief._transition(self, to, reason)
    def _record_step(self, kind: str, description: str, **extra) -> dict:
        return self.memory._record_step(self, kind, description, **extra)
    def _flush_steps(self) -> None:
        return self.memory._flush_steps(self)
    def get_steps(self, n: int = 50) -> list[dict]:
        return self.memory.get_steps(self, n)
    def get_persisted_steps(self, run_id: str | None = None, n: int = 100) -> list[dict]:
        return self.memory.get_persisted_steps(self, run_id, n)
    def clear_run_steps(self, run_id: str | None = None) -> int:
        return self.memory.clear_run_steps(self, run_id)
    def _extract_json(self, text: str, default: str = "{}"):
        return self.reflection._extract_json(text, default)
    def _restore_snapshot(self, snapshot: dict) -> None:
        return self.persistence._restore_snapshot(self, snapshot)
    async def resume_last_task(self) -> str:
        snapshot = load_last_run()
        if not snapshot:
            return "No interrupted task to resume."
        if snapshot.get("execution_queue") is None and snapshot.get("trace"):
            return "No resumable execution state found."
        self._restore_snapshot(snapshot)
        self._max_iter = self._max_iter_for_complexity(self._complexity_score)
        self._run_start_time = time.time()
        self._tokens_before_run = getattr(self.llm, "token_estimate", 0)
        # Restored `_trace`/`history` are the pre-crash record. Without
        # this boundary, the stuck-detector's window includes those stale
        # entries and can trip on the very first turn of the resumed
        # attempt — a false "stuck" from a failure that happened in a
        # different context before the crash, not from anything this
        # resumed attempt has actually done yet.
        self._resume_boundary_trace_len = len(self._trace)
        result = await self._loop()
        return await self._finalize(self._goal, result)
    def list_plugin_tools(self) -> list[str]:
        return self.execution.list_plugin_tools(self)
    def _looks_like_memory_query(self, query: str) -> bool:
        return self.planner._looks_like_memory_query(self, query)
    def _match_core_skills(self, user_input: str) -> list:
        return self.planner._match_core_skills(self, user_input)
    async def run(self, user_input: str) -> str:
        self._reset()
        self._goal             = user_input
        self._complexity_score = score_complexity(user_input)
        self._is_complex_task  = self._complexity_score >= 0.4
        self._max_iter         = self._max_iter_for_complexity(self._complexity_score)
        self._tokens_before_run = getattr(self.llm, "token_estimate", 0)
        # score_complexity() is purely lexical — "check open ports" scores
        # as simple by word-pattern alone even though it took 7 real tool
        # calls last time. If similar past tasks needed more than the
        # lexical guess, use that instead (never less — this only raises
        # the floor, so a bad historical sample can't under-budget a task
        # the lexical heuristic already sized correctly).
        historical_estimate = estimate_iterations_needed(user_input[:40])
        if historical_estimate and historical_estimate > self._max_iter:
            self._max_iter = historical_estimate

        self.history.append({"role": "user", "content": user_input})
        remember_user_facts(user_input)
        past_memory        = get_relevant_memories(user_input, n=5, deep=self._is_complex_task)
        past_mortems       = get_post_mortems(user_input, n=2)
        self._task_insights     = get_insights(user_input, n=3)
        self._past_trajectories = get_similar_trajectories(user_input, n=2)
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
                f"- {t['event']} -> {t['outcome']}\n" for t in self._past_trajectories
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
            plugin_tools=self.get_plugin_tool_descriptions(),
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
        self._persist_resume_snapshot()

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
            self._record_step("planning", "Simple task — no milestone breakdown needed.")

        self._transition(Phase.EXECUTING, reason="initial planning complete")
        self._flush_steps()
        result = await self._loop()
        return await self._finalize(user_input, result)
    async def _finalize(self, user_input: str, result: str) -> str:
        """
        Shared post-loop wrap-up for both `run()` and `resume_last_task()`.
        Previously `resume_last_task()` returned straight from `_loop()`
        and skipped all of this — a successfully-resumed task never got
        recorded to episodic memory, never went through the completion
        evaluator or post-mortem writer, and never had `save_history` or
        `clear_last_run` called on it, leaving the stale snapshot on disk
        even after the task had actually finished.

        Also: the snapshot is now only cleared when the task actually
        reached completion (`goal_progress == "complete"`), not
        unconditionally. Previously `clear_last_run()` fired after every
        `_loop()` return — including "Interrupted", "Rate limit
        exceeded...", "Resource limit: ...", and "Iteration limit
        reached" — which are exactly the cases you'd want to resume
        from, so the snapshot was being deleted before `resume_last_task`
        could ever be useful for them.
        """
        # Catches steps from any return path that exits _loop() before its
        # own per-iteration flush point (e.g. an immediate final answer on
        # iteration 0 never reaches the tool-execution block at all).
        self._flush_steps()

        if self._skills_dirty:
            data = load_all_skills()
            self.core_skills, self.gen_skills = data["core"], data["generated"]
        if self._trace:
            tool_seq = " -> ".join(t["tool"] for t in self._trace)
            outcome  = "success" if any(t["success"] for t in self._trace) else "failure"
            remember_episodic(
                event=f"Goal: {user_input[:60]} | Tools: {tool_seq}",
                outcome=outcome,
                importance=min(1.0, len(self._trace) * 0.15),
                task_type=user_input[:40],
                iterations_used=self._iteration + 1,
            )

        if self._belief["goal_progress"] == "complete":
            clear_last_run()

        if len(self._trace) >= COMPLEX_THRESHOLD:
            completed, eval_reason = await self._evaluate_completion(user_input, result)
            if not completed:
                result = f"{result} [Note: incomplete — {eval_reason}]"
            await self._write_post_mortem_and_insights(user_input, result)

        if should_consolidate():
            asyncio.ensure_future(consolidate_memory(self.llm))
        save_history(self.history)
        return result
    def _orchestrate_skills(self) -> None:
        return self.planner._orchestrate_skills(self)
    def _skill_plan_summary(self) -> str:
        return self.planner._skill_plan_summary(self)
    def _mark_skill_step_done(self, tool_name: str) -> None:
        return self.planner._mark_skill_step_done(self, tool_name)
    def _skills_for_tool(self, tool_name: str) -> list:
        return self.planner._skills_for_tool(self, tool_name)
    def _update_working_memory(self, tool: str, result: str, ok: bool) -> None:
        return self.memory._update_working_memory(self, tool, result, ok)
    def _persist_resume_snapshot(self) -> None:
        return self.persistence._persist_resume_snapshot(self)
    def _working_mem_summary(self) -> str:
        return self.memory._working_mem_summary(self)
    async def _plan_hierarchical(self, goal: str) -> list[dict]:
        return await self.planner._plan_hierarchical(self, goal)
    async def _replan_milestone(self, milestone: str, reason: str) -> list[str]:
        return await self.planner._replan_milestone(self, milestone, reason)
    def _tool_confidence(self, tool_name: str, args: dict) -> float:
        return self.execution._tool_confidence(self, tool_name, args)
    def _update_belief(self, tool: str, args: dict, result: str, ok: bool) -> None:
        return self.belief._update_belief(self, tool, args, result, ok)
    def _belief_summary(self) -> str:
        return self.belief._belief_summary(self)
    def _check_consistency(self) -> list[str]:
        return self.belief._check_consistency(self)
    async def _evaluate_completion(self, goal: str, result: str) -> tuple[bool, str]:
        return await self.reflection._evaluate_completion(self, goal, result)
    async def _write_post_mortem_and_insights(self, goal: str, result: str) -> None:
        return await self.reflection._write_post_mortem_and_insights(self, goal, result)
    def _build_execution_queue(self) -> None:
        return self.planner._build_execution_queue(self)
    def _claim_next_execution_item(self) -> dict | None:
        return self.planner._claim_next_execution_item(self)
    def _complete_current_execution_item(self, success: bool = True) -> None:
        return self.planner._complete_current_execution_item(self, success)
    def _can_extend_iterations(self) -> bool:
        return self.reflection._can_extend_iterations(self)
    async def _loop(self) -> str:
        empty = 0
        for i in itertools.count():
            if i >= self._max_iter:
                if self._can_extend_iterations():
                    self._max_iter += ITER_EXTENSION_SIZE
                    elapsed = int(time.time() - self._run_start_time)
                    self._record_step(
                        "extend",
                        f"Hit the iteration budget but still making progress — "
                        f"extending by {ITER_EXTENSION_SIZE} "
                        f"({elapsed}s elapsed of {MAX_RUNTIME_SECONDS}s budget).",
                    )
                else:
                    reason = "iteration limit reached"
                    if time.time() - self._run_start_time > MAX_RUNTIME_SECONDS:
                        reason = f"time budget exceeded ({MAX_RUNTIME_SECONDS}s)"
                    elif self._iteration >= ABSOLUTE_MAX_ITER:
                        reason = "absolute iteration ceiling reached"
                    elif self._trace and not any(t["success"] for t in self._trace[-3:]):
                        reason = "stopped making progress"
                    if self._phase not in (Phase.DONE, Phase.FAILED):
                        self._transition(Phase.FAILED, reason=reason)
                    self._record_step("give_up", f"Stopping: {reason}.")
                    set_execution_state({"state": self._resume_state})
                    self._persist_resume_snapshot()
                    self._flush_steps()
                    return f"Stopped: {reason}" if reason != "iteration limit reached" else "Iteration limit reached"
            self._iteration = i
            if self._shutdown:
                save_history(self.history)
                return "Interrupted"
            ok, msg = self._monitor.check()
            if not ok:
                save_history(self.history)
                return f"Resource limit: {msg}"
            tokens_used = getattr(self.llm, "token_estimate", 0) - self._tokens_before_run
            if tokens_used > MAX_TOKENS_PER_RUN:
                self._record_step(
                    "give_up",
                    f"Stopping: token budget exceeded ({tokens_used}/{MAX_TOKENS_PER_RUN} tokens).",
                )
                set_execution_state({"state": self._resume_state})
                self._persist_resume_snapshot()
                self._flush_steps()
                return f"Stopped: token budget exceeded ({tokens_used} tokens)"
            if len(self.history) > TRIM_AT and not self._trimming:
                asyncio.ensure_future(self._trim())

            self.on_status("Reasoning about next step…" if i == 0 else "Continuing…")

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
                    plugin_tools=self.get_plugin_tool_descriptions(),
                )
            if (i + 1) % CHECKPOINT_EVERY == 0:
                await self._checkpoint(i)

            r = await self._llm_with_retry(i)
            if r is None:
                return "Rate limit exceeded. Try again in a moment."

            calls = r.get("tool_calls") or []
            text  = r.get("content", "").strip()

            # ReflAct belief-state fields — optional in the model's JSON
            # output. Only overwrite when the model actually provides a
            # non-empty list; omitting them means "unchanged" (per the
            # system prompt's "omit them entirely if nothing's changed"),
            # so they persist across turns instead of getting silently
            # cleared to [] every time the model doesn't restate them.
            new_assumptions = r.get("assumptions")
            if new_assumptions:
                self._belief["assumptions"] = [str(a)[:120] for a in new_assumptions][:5]
            new_unknowns = r.get("unknowns")
            if new_unknowns:
                self._belief["unknowns"] = [str(u)[:120] for u in new_unknowns][:5]

            if not calls:
                if text:
                    self.history.append({"role": "assistant", "content": text})
                    self._record_step("final_answer", text[:200])
                    self._transition(Phase.DONE, reason="final answer given")
                    # Reaching DONE closes out queue/milestone bookkeeping even
                    # if the LLM finished before formally exhausting every
                    # planned item — otherwise a stale, non-empty `_milestones`
                    # survives into DONE (and into a resume snapshot).
                    if self._active_execution_item is not None:
                        self._complete_current_execution_item(success=True)
                    self._execution_complete = True
                    self._milestones = []
                    violations = self._check_consistency()
                    if violations:
                        logger.warning("State consistency violations at completion: %s", violations)
                    return text
                empty += 1
                if empty >= 2:
                    return "No response. Try rephrasing."
                if self._last_role() == "tool":
                    self.history.append({"role": "user", "content":
                        f"Goal: {self._goal}\nGive final answer or call next tool."})
                continue
            empty = 0
            if text:
                self._record_step("reasoning", text[:200])

            self.history.append({
                "role": "assistant",
                "content": json.dumps({"reasoning": text, "tool_calls": calls}),
            })

            if len(self._trace) - self._resume_boundary_trace_len >= STUCK_MIN and self._stuck():
                self._failures += 1
                self._transition(Phase.REFLECTING, reason="stuck detected")
                self._record_step("recovery", f"Stuck detected (attempt {self._failures}/{MAX_FAILURES}) — looking for a way forward.")
                if self._failures >= MAX_FAILURES:
                    self._transition(Phase.FAILED, reason="max failures reached")
                    self._record_step("give_up", "Giving up after max failed attempts.")
                    set_execution_state({"state": self._resume_state})
                    self._persist_resume_snapshot()
                    self._flush_steps()
                    return "Stuck after max attempts"
                if self._milestones and self._milestone_idx < len(self._milestones):
                    ms       = self._milestones[self._milestone_idx]
                    new_steps = await self._replan_milestone(
                        ms.get("milestone", ""), self._belief["last_outcome"]
                    )
                    if new_steps:
                        self._subtasks    = new_steps
                        self._subtask_idx = 0
                        self._transition(Phase.EXECUTING, reason="replanned milestone")
                        self._record_step(
                            "recovery",
                            f"Replanned '{ms.get('milestone','')}': {'; '.join(new_steps)}"[:200],
                        )
                        self.history.append({"role": "user",
                            "content": f"[Replan] New steps for '{ms.get('milestone','')}': "
                                       f"{new_steps}"})
                        continue
                diagnosis = await self._reflect()
                self.history.append({"role": "user",
                    "content": f"[Recovery] {diagnosis}"})
                self._record_step("recovery", diagnosis[:200])
                self._transition(Phase.EXECUTING, reason="recovery attempted")
                continue

            if not self._matched_skills and not self._skill_gate_shown:
                self._skill_gate_shown = True
                self.history.append({"role": "user",
                    "content": "[Skill gate] No skills matched. Check YOUR SKILLS first."})

            filtered_calls = []
            for c in calls:
                conf = self._tool_confidence(c["name"], c.get("arguments", {}))
                if conf < 0.5:
                    self.history.append({"role": "tool",
                        "tool_call_id": c.get("id", c["name"]),
                        "content": f"[Skipped] {c['name']} confidence {conf:.0%} too low. Try different approach."})
                else:
                    filtered_calls.append(c)

            if not filtered_calls:
                self.history.append({"role": "user",
                    "content": "All proposed tools had low confidence. Rethink approach."})
                continue

            for c in filtered_calls:
                self._record_step(
                    "tool_call",
                    f"{c['name']} {self._fmt_action(c['name'], c.get('arguments', {}))}",
                    tool=c["name"], args=c.get("arguments", {}),
                )

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
                action_desc = self._fmt_action(name, c.get("arguments", {}))
                self._resume_state["current_step"] = action_desc
                self._resume_state["completed_steps"] = self._resume_state.get("completed_steps", []) + [action_desc]
                self._resume_state["next_action"] = "Continue to the next step if needed"
                if not ok:
                    self._resume_state["blocked_steps"] = self._resume_state.get("blocked_steps", []) + [action_desc]

                if ok:
                    self._consec_errors[name] = 0
                else:
                    self._consec_errors[name] += 1
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
                self._record_step(
                    "tool_result",
                    f"{name} {'done' if ok else 'failed'} ({elapsed}s): {self._brief(raw)[:100]}",
                    tool=name, success=ok, elapsed_sec=elapsed,
                )

            # Persist once per iteration rather than once per tool call.
            set_execution_state({"state": self._resume_state})
            self._persist_resume_snapshot()
            self._flush_steps()

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
                    self._record_step(
                        "rate_limit",
                        f"Rate limited — waiting {wait:.0f}s before retry {attempt + 1}/{max_attempts}",
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error("LLM error: %s", e)
                return None
        return None
    async def _execute_single_tool(self, call: dict) -> tuple[str, float]:
        return await self.execution._execute_single_tool(self, call)
    async def _run_speculative(self, calls: list) -> list[tuple[str, float]]:
        return await self.execution._run_speculative(self, calls)
    async def _run_race(self, calls: list) -> list[tuple[str, float]]:
        return await self.execution._run_race(self, calls)
    def _fallback_tool(self, tool_name: str) -> str | None:
        return self.execution._fallback_tool(self, tool_name)
    async def _run_with_fallback(self, call: dict) -> tuple[str, float]:
        return await self.execution._run_with_fallback(self, call)
    async def _run_parallel(self, calls: list) -> list[tuple[str, float]]:
        return await self.execution._run_parallel(self, calls)
    def _msgs(self, step: int) -> list[dict]:
        if step == 0 or not self._trace:
            return self.history
        t       = self._trace[-1]
        recent  = " -> ".join(
            f"{s['tool']}({'ok' if s['success'] else 'x'})"
            for s in self._trace[-5:]
        )
        skills_line   = f"ACTIVE SKILLS: {', '.join(self._matched_skills)}\n" if self._matched_skills else ""
        plan_line     = self._skill_plan_summary()
        subtask_line  = (
            f"ACTIVE ITEM: {self._active_execution_item.get('title', self._current_subtask)}\n"
        ) if self._active_execution_item else ""
        wm_line = self._working_mem_summary()
        status  = "ok" if t["success"] else f"error: {t['error_type']}"
        inj = (
            f"[ReflAct {step}] GOAL: {self._goal}\n"
            f"{subtask_line}"
            f"{skills_line}"
            f"{plan_line + chr(10) if plan_line else ''}"
            f"{self._belief_summary()}\n"
            f"{wm_line + chr(10) if wm_line else ''}"
            f"LAST: {t['tool']} -> {status} ({t['elapsed_sec']}s) | {t['result'][:100]}\n"
            f"RECENT: {recent}\n\n"
            "Given belief state, working memory, and skill plan — execute next pending step."
        )
        return self.history + [{"role": "user", "content": inj}]
    def _inject_meta_skill_reminder(self):
        return self.reflection._inject_meta_skill_reminder(self)
    async def _run(self, name: str, args: dict) -> str:
        return await self.execution._run(self, name, args)
    def _run_memory_tool(self, args: dict) -> str:
        return self.execution._run_memory_tool(self, args)
    def _maybe_auto_create_plugin(self) -> bool:
        return self.execution._maybe_auto_create_plugin(self)
    def get_plugin_tool_descriptions(self) -> dict[str, str]:
        return self.execution.get_plugin_tool_descriptions(self)
    def _run_plugin_tool(self, args: dict) -> str:
        return self.execution._run_plugin_tool(self, args)
    def _fmt_action(self, name: str, args: dict) -> str:
        fmt = ACTION_FORMATTERS.get(name)
        return fmt(args) if fmt else f"-> {json.dumps(args)[:60]}"
    def _fmt_tool_output(self, name: str, args: dict, raw: Any, ok: bool, elapsed: float) -> str:
        hdr = f"[{name}] {self._fmt_action(name, args)} ({elapsed}s) {'ok' if ok else 'FAILED'}"
        if not ok:
            return f"{hdr}\nError: {self._brief(raw)}"
        if name == "terminal":
            lines = str(raw).strip().splitlines()
            if not lines:
                return f"{hdr}\n(no output)"
            preview = "\n".join(lines[:5])
            tail    = f"\n… ({len(lines)-5} more)" if len(lines) > 5 else ""
            return f"{hdr}\n{preview}{tail}"
        if name == "file_editor" and args.get("action") in ("write", "create", "delete", "replace"):
            return f"{hdr}\nDone"
        if name == "memory" and args.get("action") in ("store", "delete"):
            return f"{hdr}\nDone"
        return f"{hdr}\n{self._brief(raw)}"
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
    def _is_error(self, r: Any) -> bool:
        return any(t in str(r).lower() for t in (
            "error", "failed", "exception", "traceback",
            "not found", "unknown tool", "permission denied", "timeout",
        ))
    def _categorize_error(self, result: Any) -> str:
        s = str(result).lower()
        for pattern, label in ERROR_PATTERNS:
            if pattern in s:
                return label
        return "ERROR"
    def _last_role(self) -> str:
        for m in reversed(self.history):
            if m.get("role") in ("user", "assistant", "tool"):
                return m["role"]
        return ""
    def _stuck(self) -> bool:
        return self.reflection._stuck(self)
    async def _reflect(self) -> str:
        return await self.reflection._reflect(self)
    async def _trim(self) -> None:
        return await self.persistence._trim(self)
    async def _checkpoint(self, iteration: int):
        return await self.persistence._checkpoint(self, iteration)
    def clear_session(self) -> None:
        self.history.clear()
        self._reset()
        clear_history()
        clear_last_run()
    def get_metrics(self) -> dict:
        return {
            "goal":            self._goal,
            "run_id":          self._run_id,
            "phase":           self._phase.value,
            "belief":          self._belief["goal_progress"],
            "complexity":      round(self._complexity_score, 2),
            "max_iter":        self._max_iter,
            "tools_called":    len(self._trace),
            "success_count":   sum(1 for t in self._trace if t["success"]),
            "active_skills":   self._matched_skills,
            "working_mem":     list(self._working_mem),
            "insights_used":   len(self._task_insights),
            "recent_steps":    [s["description"] for s in self.get_steps(5)],
            "storage":         get_storage_summary(),
        }