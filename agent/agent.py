"""
PENZER — Research-Grade Agent (thin orchestrator)
Research sources:
  ReflAct    (Kim 2025)       — belief-state injection, 27.7% improvement
  ExpeL      (Zhao AAAI 2024) — insight extraction, trajectory recall
  Reflexion  (Shinn 2023)     — verbal post-mortem + evaluator
  HyMem      (Zhao 2026)      — dual-tier retrieval, complexity scoring
  MemoryBank (Zhong 2024)     — Ebbinghaus decay + spaced repetition
  Active CC  (Arxiv 2601)     — goal-aware context compression
"""
import json, logging, inspect, asyncio, signal, time, psutil, random, re, itertools, weakref
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
from agent.config import (
    ITER_BY_COMPLEXITY, TRIM_AT, KEEP_LAST, STUCK_MIN, MAX_FAILURES,
    ITER_EXTENSION_SIZE, MAX_RUNTIME_SECONDS, ABSOLUTE_MAX_ITER,
    MAX_TOKENS_PER_RUN, CHECKPOINT_EVERY, MEMORY_CRITICAL,
    COMPLEX_THRESHOLD, RATE_LIMIT_BASE, RATE_LIMIT_MAX, RATE_LIMIT_JITTER,
    WORKING_MEMORY_SIZE, TOOL_LABELS, FALLBACKS, ERROR_PATTERNS,
    ACTION_FORMATTERS, MAX_CONSISTENCY_VIOLATIONS,
)
# NOTE: agent.config.TOOL_TIMEOUT was imported here but never referenced
# — a dead import left over from before the manager-module split. Removed
# rather than left in place because it shares a name with (but is
# entirely unrelated to) execution_manager.py's own module-level
# TOOL_TIMEOUT = 30, which is the constant that actually controls
# per-call timeouts (see _call_timeout there). Keeping an unused
# same-named import here risked someone "fixing" a timeout issue by
# editing agent.config.TOOL_TIMEOUT and finding nothing changes.

logger = logging.getLogger(__name__)

# Every manager PenzerAgent owns, wired up automatically in __init__ as
# self.<attr_name> = <Class>(). To add a new manager module, add one
# line here — see "ADDING A NEW MANAGER MODULE" in the module docstring.
MANAGER_REGISTRY: list[tuple[str, type]] = [
    ("belief",      BeliefManager),
    ("memory",      MemoryManager),
    ("planner",     Planner),
    ("execution",   ExecutionManager),
    ("reflection",  ReflectionManager),
    ("persistence", PersistenceManager),
]

# Fix #9: signal.signal(SIGINT, ...) sets a single process-wide handler.
# Registering it inside __init__ meant every new PenzerAgent silently
# stole SIGINT from whichever instance registered before it — only the
# most-recently-constructed agent ever actually saw Ctrl+C. It also
# raises ValueError if a PenzerAgent is ever constructed off the main
# thread (signal.signal is main-thread-only), which would crash
# construction entirely in, e.g., a threaded web framework. Instead:
# install ONE process-wide handler that fans out to every live agent via
# a weak registry (so agents that get garbage collected drop out
# automatically), and degrade to a no-op (rather than raising) if we're
# not on the main thread — callers in that situation should wire up
# their own shutdown path via PenzerAgent.request_shutdown().
_LIVE_AGENTS: "weakref.WeakSet[PenzerAgent]" = weakref.WeakSet()
_signal_installed = False


def _install_shutdown_dispatcher() -> None:
    global _signal_installed
    if _signal_installed:
        return

    def _dispatch(signum, frame):
        for a in list(_LIVE_AGENTS):
            try:
                a._handle_shutdown(signum, frame)
            except Exception:
                logger.exception("Error dispatching shutdown to an agent instance")

    try:
        signal.signal(signal.SIGINT, _dispatch)
        _signal_installed = True
    except ValueError:
        logger.debug(
            "Could not install SIGINT handler (not the main thread) — "
            "call PenzerAgent.request_shutdown() explicitly instead."
        )


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
        # Guards plugin creation (auto- and explicit) so two concurrent
        # creation flows can't race on writing the same generated plugin
        # file/name — e.g. two parallel terminal calls in the same batch
        # both qualifying for auto-plugin creation, or an explicit
        # plugin_tool create landing mid-way through an auto-create.
        self._plugin_lock   = asyncio.Lock()
        # Managers hold no state of their own — they take `self` (this
        # agent) explicitly in every method and operate on its state
        # directly. See MANAGER_REGISTRY above and penzermodule/__init__.py.
        for attr, cls in MANAGER_REGISTRY:
            setattr(self, attr, cls())
        _LIVE_AGENTS.add(self)
        _install_shutdown_dispatcher()

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
        # Mirrors _resume_boundary_trace_len but for agent.history — see
        # reflection_manager.py's _stuck() docstring for why history needs
        # its own boundary (it isn't 1:1 with trace entries, so the trace
        # boundary alone doesn't protect the history-based stuck checks).
        self._resume_boundary_history_len: int = 0
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
        # Tracks *why* the last _llm_with_retry() call returned None, so
        # the loop can react differently to a timeout (recoverable —
        # just continue) vs. a genuine rate-limit exhaustion or an
        # unexpected exception (both of which should end the run, but
        # with an accurate message). See _llm_with_retry / _loop.
        self._last_llm_error:      str   = ""
        # Circuit breaker for _check_consistency() violations — see
        # persistence_manager.py's _checkpoint() and
        # _check_stop_conditions() below.
        self._consistency_violation_streak: int = 0
        self._force_stop_reason:   str | None = None
        # ResourceMonitor is created once in __init__ and can outlive
        # many run() calls on the same agent instance — reset its elapsed
        # timer each run so stats()["elapsed_sec"] in checkpoints reflects
        # this task, not cumulative time since the agent object was
        # constructed. Guarded because _reset() also runs once during
        # __init__, before self._monitor exists yet.
        if hasattr(self, "_monitor"):
            self._monitor.reset_timer()
        # Fix #4: bumped every reset so persistence_manager._trim() can
        # detect "history moved on since I started awaiting the
        # summarizer" and refuse to write back a stale result — without
        # this a trim left over from a previous run() call on this same
        # instance could clobber messages the new run has already
        # appended by the time the old trim's LLM call finally returns.
        self._history_version = getattr(self, "_history_version", 0) + 1
        # Fix (background task hygiene): cancel anything still running
        # from a previous run() on this instance before starting a new
        # one, and reset the tracking set. Previously _trim/consolidate_
        # memory were launched via bare asyncio.ensure_future() with
        # nothing holding a reference — any exception in them vanished
        # into asyncio's default handler, and nothing stopped a straggler
        # from a prior run touching state a new run has already reset.
        for t in getattr(self, "_background_tasks", ()):
            if not t.done():
                t.cancel()
        self._background_tasks: set = set()

    def _handle_shutdown(self, signum, frame):
        self._shutdown = True

    def request_shutdown(self) -> None:
        """Public entry point for hosts that manage their own signal
        handling (or run agents off the main thread, where this process
        can't install a SIGINT handler at all) to request a graceful stop."""
        self._handle_shutdown(None, None)

    def _safe_status(self, message: str) -> None:
        """Wraps the user-supplied on_status callback. Now that
        _run_loop_safely exists, a bug in the callback (a broken UI hook,
        a logging error) wouldn't kill the whole run — but unguarded it
        would still abort whatever step was mid-flight for no reason
        related to the actual task. Isolates the blast radius to "this
        one status update got dropped and logged" instead."""
        try:
            self.on_status(message)
        except Exception:
            logger.exception("on_status callback raised")

    def _spawn_background(self, coro, name: str) -> None:
        """Launch a fire-and-forget coroutine with actual tracking:
        added to self._background_tasks (so _reset() can cancel
        stragglers from a previous run — see fix #4/_trim above) and
        given a done-callback that surfaces any exception via logger
        instead of letting it vanish into asyncio's default handler,
        which only prints to stderr and is easy to miss in production."""
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.error("Background task '%s' failed: %s", name, exc)

        task.add_done_callback(_on_done)

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

    # ------------------------------------------------------------------
    # Persistence helper — used at every point the run might stop, get
    # interrupted, or hand off to a resume, so state on disk never goes
    # stale relative to what the loop actually did.
    # ------------------------------------------------------------------
    def _persist_all(self) -> None:
        """Single point that persists resume state + the step log.
        Replaces what used to be a copy-pasted three-call sequence
        (set_execution_state / _persist_resume_snapshot / _flush_steps)
        at four separate spots in _loop."""
        set_execution_state({"state": self._resume_state})
        self._persist_resume_snapshot()
        self._flush_steps()

    # ------------------------------------------------------------------
    # Belief / memory / planner / execution / reflection / persistence
    # delegates — thin pass-throughs so callers keep using self._foo(...)
    # regardless of which manager actually implements it.
    # ------------------------------------------------------------------
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
        # Fix #5: clear any state left over from a previous run()/resume on
        # this same agent instance before restoring. _restore_snapshot only
        # overwrites the keys present in the snapshot — anything it doesn't
        # know about (now a much shorter list thanks to the expanded
        # snapshot schema, but still a real risk for any future field
        # someone adds to _reset() without also adding it to the snapshot)
        # would otherwise silently carry over from whatever this instance
        # was doing before. _reset() does not touch self.history, so
        # conversation continuity across resumes/runs on the same instance
        # is unaffected.
        self._reset()
        self._restore_snapshot(snapshot)
        # _restore_snapshot brings back _matched_skills (just names — the
        # snapshot is JSON, and skill objects aren't trivially
        # serializable) but not _active_skills/_skill_plan/_skill_steps/
        # _skill_done, which _orchestrate_skills builds from the actual
        # skill objects. Without this, a resumed run kept telling the
        # model "SKILLS MATCHED: X, Y" (baked into the restored
        # _system_prompt) while the structures that track per-step skill
        # progress and drive update_skill_metric were silently empty.
        # Re-derive the objects from the restored names and rebuild the
        # plan the same way run() does.
        if self._matched_skills:
            by_name = {s.name: s for s in list(self.core_skills) + list(self.gen_skills)}
            self._active_skills = [by_name[n] for n in self._matched_skills if n in by_name]
            if self._active_skills:
                self._orchestrate_skills()
        self._run_start_time = time.time()
        self._tokens_before_run = getattr(self.llm, "token_estimate", 0)
        # Restored `_trace`/`history` are the pre-crash record. Without
        # this boundary, the stuck-detector's window includes those stale
        # entries and can trip on the very first turn of the resumed
        # attempt — a false "stuck" from a failure that happened in a
        # different context before the crash, not from anything this
        # resumed attempt has actually done yet.
        self._resume_boundary_trace_len = len(self._trace)
        self._resume_boundary_history_len = len(self.history)
        result = await self._run_loop_safely()
        return await self._finalize(self._goal, result)

    def list_plugin_tools(self) -> list[str]:
        return self.execution.list_plugin_tools(self)

    def _looks_like_memory_query(self, query: str) -> bool:
        return self.planner._looks_like_memory_query(self, query)

    def _match_core_skills(self, user_input: str) -> list:
        return self.planner._match_core_skills(self, user_input)

    async def _run_loop_safely(self) -> str:
        """Runs _loop() behind a top-level exception guard.
        Every known failure mode inside _loop already returns a clean
        string (timeouts, resource limits, rate limits, stuck-after-max-
        attempts, ...). This catches anything NOT already anticipated — a
        bug surfacing from a manager, an unexpected exception from a tool
        that slipped past execution_manager's own guards, a serialization
        error — so the run degrades to a reported error instead of an
        unhandled traceback. Without this, such a crash would propagate
        out of run()/resume_last_task() entirely, skipping _finalize()
        and losing episodic memory, post-mortem writing, and even
        save_history() for a run that may have made real progress before
        the crash.
        """
        try:
            return await self._loop()
        except Exception as e:
            logger.exception("Unhandled exception in _loop")
            try:
                self._record_step("give_up", f"Stopped: unexpected internal error — {e}")
            except Exception:
                logger.exception("Failed to record give_up step after unhandled loop exception")
            try:
                self._persist_all()
            except Exception:
                logger.exception("Failed to persist state after unhandled loop exception")
            return f"Stopped: internal error ({type(e).__name__}: {e})"

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
        result = await self._run_loop_safely()
        return await self._finalize(user_input, result)

    async def _finalize(self, user_input: str, result: str) -> str:
        """
        Shared post-loop wrap-up for both `run()` and `resume_last_task()`.
        A successfully-resumed task must still get recorded to episodic
        memory, go through the completion evaluator / post-mortem writer,
        and have `save_history` / `clear_last_run` called on it — skipping
        this after resume would leave a stale snapshot on disk even after
        the task had actually finished.
        The snapshot is only cleared when the task actually reached
        completion (`goal_progress == "complete"`), not unconditionally —
        clearing it after every _loop() return (including "Interrupted",
        "Rate limit exceeded...", "Resource limit: ...", and "Iteration
        limit reached") would delete the snapshot before resume_last_task
        could ever be useful for exactly those cases.
        """
        # Catches steps from any return path that exits _loop() before its
        # own per-iteration flush point (e.g. an immediate final answer on
        # iteration 0 never reaches the tool-execution block at all).
        self._flush_steps()
        try:
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
                if completed is False:
                    result = f"{result} [Note: incomplete — {eval_reason}]"
                elif completed is None:
                    logger.debug("Completion evaluator unavailable (%s) — leaving result unannotated", eval_reason)
                await self._write_post_mortem_and_insights(user_input, result)
            if should_consolidate():
                self._spawn_background(consolidate_memory(self.llm), "consolidate_memory")
            save_history(self.history)
        except Exception:
            # Bookkeeping (episodic memory, post-mortem, history save)
            # failing shouldn't cost the caller a real result the loop
            # already produced — log it and still return what _loop()
            # actually accomplished rather than letting an unrelated
            # persistence error mask a successful run.
            logger.exception("Error during _finalize bookkeeping")
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

    async def _evaluate_completion(self, goal: str, result: str) -> tuple[bool | None, str]:
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

    # ------------------------------------------------------------------
    # Main loop and its per-iteration helpers. Each helper owns exactly
    # one job so a future change (new stop condition, new recovery path,
    # a different tool-execution strategy) has one obvious place to go
    # instead of needing surgery on one long function.
    # ------------------------------------------------------------------
    async def _loop(self) -> str:
        empty = 0
        # A fresh run always starts with an empty _trace (set by _reset()
        # right before _loop() is called), so this starts counting at 0
        # as before. A resumed run restores a non-empty _trace along with
        # the iteration count it had reached pre-crash/interruption — in
        # that case counting continues from there instead of silently
        # restarting at 0, so ABSOLUTE_MAX_ITER and the iteration budget
        # reflect total work done on the task, not just this attempt.
        start_at = self._iteration + 1 if self._trace else 0
        for i in itertools.count(start_at):
            self._iteration = i
            stop = self._check_stop_conditions(i)
            if stop is not None:
                return stop
            await self._pre_iteration_tasks(i)
            r = await self._llm_with_retry(i)
            if r is None:
                # A timeout injects a "Timeout. Continue or give final
                # answer." message into history and should just let the
                # loop go around again so the model can see it. A genuine
                # unexpected error gets its own message rather than being
                # mislabeled as a rate limit; only real rate-limit
                # exhaustion ends the run with that message.
                reason = self._last_llm_error or "rate_limit"
                if reason == "timeout":
                    continue
                if reason == "error":
                    return "LLM request failed. Try again in a moment."
                return "Rate limit exceeded. Try again in a moment."
            calls = r.get("tool_calls") or []
            text  = r.get("content", "").strip()
            self._apply_belief_updates(r)
            if not calls:
                result, empty = self._handle_empty_calls(text, empty)
                if result is not None:
                    return result
                continue
            empty = 0
            if text:
                self._record_step("reasoning", text[:200])
            # Two things have to match what llm.py's chat() actually
            # parses back out on the model's *next* turn:
            #   1. the wrapper key — "tools", not "tool_calls".
            #   2. the shape of each call inside it — {"tool", "args"},
            #      not {"name", "arguments"}.
            # `calls` here uses "name"/"arguments" because that's llm.py's
            # own internal normalization (used regardless of whether the
            # model's response came in old single-tool, new tools-array,
            # or XML form). Writing that internal shape straight into
            # history would show the model a DIFFERENT schema than the
            # one it's actually taught to produce ("tool"/"args") — and a
            # model that pattern-matches its own prior turns (very
            # common) can drift into echoing "name"/"arguments" back,
            # which chat() doesn't recognize, causing that turn to fall
            # through every schema check. Converting back to the taught
            # shape here keeps the transcript internally consistent with
            # what the system prompt teaches, closing that loophole too.
            self.history.append({
                "role": "assistant",
                "content": json.dumps({
                    "reasoning": text,
                    "tools": [
                        {"tool": c.get("name", ""), "args": c.get("arguments", {})}
                        for c in calls
                    ],
                }),
            })
            if len(self._trace) - self._resume_boundary_trace_len >= STUCK_MIN and self._stuck():
                stuck_result = await self._handle_stuck()
                if stuck_result is not None:
                    return stuck_result
                continue
            if not self._matched_skills and not self._skill_gate_shown:
                self._skill_gate_shown = True
                self.history.append({"role": "user",
                    "content": "[Skill gate] No skills matched. Check YOUR SKILLS first."})
            filtered_calls = self._filter_by_confidence(calls)
            if not filtered_calls:
                self.history.append({"role": "user",
                    "content": "All proposed tools had low confidence. Rethink approach."})
                continue
            await self._execute_tool_calls(filtered_calls, i)
        # Defensive only: itertools.count() never raises StopIteration, so
        # in normal operation this loop only ever ends via a `return`
        # inside its body. Reaching this line means some future change
        # added a loop-body exit path without one — log loudly rather than
        # silently returning None (which would crash whatever the caller
        # does with the result, e.g. _finalize's `result[:200]`).
        logger.error("_loop() fell through its for-loop body — this should be unreachable")
        return "Stopped: internal error (loop exited without a result)"

    def _check_stop_conditions(self, i: int) -> str | None:
        """Checks iteration/time/token/shutdown/resource limits for this
        iteration. Returns a terminal result string if the loop should
        stop now, otherwise None. May mutate self._max_iter when
        extending the budget (in which case it still returns None — the
        iteration continues normally, it just gets more room)."""
        if self._force_stop_reason is not None:
            # Tripped by persistence_manager.py's _checkpoint() after
            # MAX_CONSISTENCY_VIOLATIONS consecutive checkpoints found
            # _check_consistency() violations — a coordination bug
            # somewhere is making the phase/queue/belief structures
            # disagree, and letting the run keep going only compounds it.
            if self._phase not in (Phase.DONE, Phase.FAILED):
                self._transition(Phase.FAILED, reason=self._force_stop_reason)
            self._record_step("give_up", f"Stopping: {self._force_stop_reason}.")
            self._persist_all()
            return f"Stopped: {self._force_stop_reason}"
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
                reason, has_specific_reason = "iteration limit reached", False
                if time.time() - self._run_start_time > MAX_RUNTIME_SECONDS:
                    reason, has_specific_reason = f"time budget exceeded ({MAX_RUNTIME_SECONDS}s)", True
                elif self._iteration >= ABSOLUTE_MAX_ITER:
                    reason, has_specific_reason = "absolute iteration ceiling reached", True
                elif self._trace and not any(t["success"] for t in self._trace[-3:]):
                    reason, has_specific_reason = "stopped making progress", True
                if self._phase not in (Phase.DONE, Phase.FAILED):
                    self._transition(Phase.FAILED, reason=reason)
                self._record_step("give_up", f"Stopping: {reason}.")
                self._persist_all()
                return f"Stopped: {reason}" if has_specific_reason else "Iteration limit reached"
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
            self._persist_all()
            return f"Stopped: token budget exceeded ({tokens_used} tokens)"
        return None

    async def _pre_iteration_tasks(self, i: int) -> None:
        """Housekeeping that runs once per iteration before the LLM call:
        background history trim, status callback, claiming the next
        queued execution item, periodic skill/system-prompt refresh, and
        periodic checkpointing."""
        if len(self.history) > TRIM_AT and not self._trimming:
            self._spawn_background(self._trim(), "trim")
        self._safe_status("Reasoning about next step…" if i == 0 else "Continuing…")
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

    def _apply_belief_updates(self, r: dict) -> None:
        """ReflAct belief-state fields are optional in the model's JSON
        output. Only overwrite when the model actually provides a
        non-empty list; omitting them means "unchanged" (per the system
        prompt's "omit them entirely if nothing's changed"), so they
        persist across turns instead of getting silently cleared to []
        every time the model doesn't restate them."""
        new_assumptions = r.get("assumptions")
        if new_assumptions:
            self._belief["assumptions"] = [str(a)[:120] for a in new_assumptions][:5]
        new_unknowns = r.get("unknowns")
        if new_unknowns:
            self._belief["unknowns"] = [str(u)[:120] for u in new_unknowns][:5]

    def _handle_empty_calls(self, text: str, empty: int) -> tuple[str | None, int]:
        """Handles a model turn that proposed no tool calls: either a
        final answer (closes out the run) or a non-actionable turn
        (nudges the model to continue, or gives up after two empty turns
        in a row). Returns (result, empty) — result is the string to
        return from _loop() when non-None; when None, the loop should
        `continue` with the updated empty counter."""
        if text:
            self.history.append({"role": "assistant", "content": text})
            self._record_step("final_answer", text[:200])
            self._transition(Phase.DONE, reason="final answer given")
            # Reaching DONE closes out queue/milestone bookkeeping even if
            # the LLM finished before formally exhausting every planned
            # item — otherwise a stale, non-empty `_milestones` survives
            # into DONE (and into a resume snapshot).
            if self._active_execution_item is not None:
                self._complete_current_execution_item(success=True)
            # Fix #14: capture what, if anything, was still unclaimed
            # BEFORE clearing it — previously this information was wiped
            # unconditionally with no record it ever existed, so "model
            # gave a final answer having skipped half its own plan" and
            # "model finished every planned item" were indistinguishable
            # after the fact.
            unfinished = [] if self._execution_complete else self._execution_queue[self._execution_index:]
            if unfinished:
                self._record_step(
                    "give_up",
                    f"Final answer given with {len(unfinished)} planned item(s) still "
                    f"unclaimed: " + "; ".join(i.get("title", "") for i in unfinished[:3])[:200],
                )
            self._execution_complete = True
            self._milestones = []
            violations = self._check_consistency()
            if violations:
                logger.warning("State consistency violations at completion: %s", violations)
            return text, empty
        empty += 1
        if empty >= 2:
            return "No response. Try rephrasing.", empty
        if self._last_role() == "tool":
            self.history.append({"role": "user", "content":
                f"Goal: {self._goal}\nGive final answer or call next tool."})
        return None, empty

    async def _handle_stuck(self) -> str | None:
        """Called once _stuck() has already been confirmed true. Tries
        milestone replanning first, then falls back to a general
        reflection pass. Returns a terminal result string if max
        failures is hit; otherwise None, meaning the caller should
        `continue` the loop (a replan or reflection has already injected
        its own guidance into history)."""
        self._failures += 1
        self._transition(Phase.REFLECTING, reason="stuck detected")
        self._record_step("recovery", f"Stuck detected (attempt {self._failures}/{MAX_FAILURES}) — looking for a way forward.")
        if self._failures >= MAX_FAILURES:
            self._transition(Phase.FAILED, reason="max failures reached")
            self._record_step("give_up", "Giving up after max failed attempts.")
            self._persist_all()
            return "Stuck after max attempts"
        if self._milestones and self._milestone_idx < len(self._milestones):
            ms = self._milestones[self._milestone_idx]
            new_steps = await self._replan_milestone(
                ms.get("milestone", ""), self._belief["last_outcome"]
            )
            if new_steps:
                # Actually wires the new steps into the live execution
                # queue (see planner.py's _requeue_milestone_steps
                # docstring) — previously only _subtasks/_subtask_idx
                # were written here, which nothing in the claim path
                # reads, so a replan had no effect on what tool actually
                # got tried next.
                self.planner._requeue_milestone_steps(self, self._milestone_idx, new_steps)
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
                return None
        diagnosis = await self._reflect()
        self.history.append({"role": "user",
            "content": f"[Recovery] {diagnosis}"})
        self._record_step("recovery", diagnosis[:200])
        self._transition(Phase.EXECUTING, reason="recovery attempted")
        return None

    def _filter_by_confidence(self, calls: list) -> list:
        """Drops proposed tool calls below the confidence threshold,
        logging a [Skipped] tool message into history for each one so
        the model sees why."""
        filtered_calls = []
        for c in calls:
            conf = self._tool_confidence(c["name"], c.get("arguments", {}))
            if conf < 0.5:
                self.history.append({"role": "tool",
                    "tool_call_id": c.get("id", c["name"]),
                    "content": f"[Skipped] {c['name']} confidence {conf:.0%} too low. Try different approach."})
            else:
                filtered_calls.append(c)
        return filtered_calls

    async def _execute_tool_calls(self, filtered_calls: list, i: int) -> None:
        """Runs the given tool calls (with speculative execution + a
        per-call fallback on error), records each result into
        trace/history/belief/working-memory/skill-metrics, persists run
        state once for the whole batch, and fires end-of-iteration
        triggers (execution-queue completion, meta-skill reminder)."""
        for c in filtered_calls:
            self._record_step(
                "tool_call",
                f"{c['name']} {self._fmt_action(c['name'], c.get('arguments', {}))}",
                tool=c["name"], args=c.get("arguments", {}),
            )
        results = await self._run_speculative(filtered_calls)
        if any(self._is_error(raw) and not self._is_timeout(raw) for raw, _ in results):
            fallback_results = []
            for call, (raw, elapsed) in zip(filtered_calls, results):
                if self._is_error(raw) and not self._is_timeout(raw):
                    # Pass the result we already have — _run_with_fallback
                    # no longer re-executes an already-failed call from
                    # scratch (see execution_manager.py).
                    fb_raw, fb_elapsed = await self._run_with_fallback(call, prior_result=(raw, elapsed))
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
            if ok and name == "terminal" and await self._maybe_auto_create_plugin():
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
        self._persist_all()
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

    # ------------------------------------------------------------------
    # LLM call wrapper
    # ------------------------------------------------------------------
    def _validate_llm_response(self, r: Any) -> dict:
        """Normalizes/validates the raw LLM response shape. _loop does
        `r.get("tool_calls") or []` and `r.get("content", "").strip()`
        right after _llm_with_retry returns — if a provider ever returns
        something malformed (not a dict, or content/tool_calls of the
        wrong type), those calls raise AttributeError/TypeError deep
        inside _loop, past the point _llm_with_retry's own try/except can
        catch it. Raising here instead, from inside that try block, means
        it's treated as a normal LLM-call failure (retried or reported)
        rather than an unhandled crash."""
        if not isinstance(r, dict):
            raise ValueError(f"LLM response was {type(r).__name__}, expected dict")
        tool_calls = r.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ValueError(f"LLM response 'tool_calls' was {type(tool_calls).__name__}, expected list")
        content = r.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(f"LLM response 'content' was {type(content).__name__}, expected str")
        return r

    async def _llm_with_retry(self, step: int, max_attempts: int = 4) -> dict | None:
        delay = RATE_LIMIT_BASE
        for attempt in range(max_attempts):
            try:
                r = await asyncio.wait_for(
                    self.llm.chat(system=self._system_prompt, messages=self._msgs(step)),
                    timeout=45,
                )
                r = self._validate_llm_response(r)
                self._backoff        = max(1.0, self._backoff * 0.9)
                self._rate_attempts  = 0
                self._last_llm_error = ""
                return r
            except asyncio.TimeoutError:
                self._backoff = min(3.0, self._backoff * 1.5)
                self.history.append({"role": "user",
                    "content": "Timeout. Continue or give final answer."})
                self._last_llm_error = "timeout"
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
                self._last_llm_error = "error"
                return None
        self._last_llm_error = "rate_limit"
        return None

    # ------------------------------------------------------------------
    # Execution delegates
    # ------------------------------------------------------------------
    async def _execute_single_tool(self, call: dict) -> tuple[str, float]:
        return await self.execution._execute_single_tool(self, call)

    async def _run_speculative(self, calls: list) -> list[tuple[str, float]]:
        return await self.execution._run_speculative(self, calls)

    async def _run_race(self, calls: list) -> list[tuple[str, float]]:
        return await self.execution._run_race(self, calls)

    def _fallback_tool(self, tool_name: str) -> str | None:
        return self.execution._fallback_tool(self, tool_name)

    async def _run_with_fallback(self, call: dict, prior_result: tuple[str, float] | None = None) -> tuple[str, float]:
        return await self.execution._run_with_fallback(self, call, prior_result)

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

    async def _maybe_auto_create_plugin(self) -> bool:
        return await self.execution._maybe_auto_create_plugin(self)

    def get_plugin_tool_descriptions(self) -> dict[str, str]:
        return self.execution.get_plugin_tool_descriptions(self)

    async def _run_plugin_tool(self, args: dict) -> str:
        return await self.execution._run_plugin_tool(self, args)

    # ------------------------------------------------------------------
    # Small pure formatting / classification helpers
    # ------------------------------------------------------------------
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
        """
        Prefers a structured signal when the tool returned one: if `r`
        parses as JSON with a "status" key, that field is authoritative
        (matching _brief()'s own convention) instead of falling back to
        naive substring matching against the whole string. Substring
        matching alone misclassifies perfectly successful results as
        errors whenever a word like "error"/"failed" appears anywhere in
        the payload as DATA — e.g. {"status": "ok", "output": "3 passed,
        0 failed"}. That's not just a cosmetic mislabel: it drives real
        control flow — it triggers _run_with_fallback (re-running or
        fallback-routing a tool that actually succeeded), marks the trace
        entry failed (feeding _tool_confidence's penalty and
        _update_belief's transition to BLOCKED), and counts toward
        _stuck()'s failure-streak detection. A structured tool opts into
        this by returning {"status": "ok"|"error", ...}; anything else
        (plain text, or JSON without a "status" key) still falls back to
        substring matching exactly as before.
        """
        s = str(r)
        try:
            d = json.loads(s.strip())
            if isinstance(d, dict) and "status" in d:
                return d.get("status") == "error"
        except (json.JSONDecodeError, ValueError):
            pass
        return any(t in s.lower() for t in (
            "error", "failed", "exception", "traceback",
            "not found", "unknown tool", "permission denied", "timeout",
        ))

    def _is_timeout(self, r: Any) -> bool:
        """True specifically for the string _execute_single_tool produces
        on asyncio.TimeoutError ("Timeout after {N}s") — used to exclude
        timeouts from the fallback-tool path (see _execute_tool_calls). A
        timeout means the command needed more time, not that the tool was
        wrong; routing to a different tool just re-runs the same slow
        command over a different transport and times out again, doubling
        the wait before anything gets reported back. Deliberately narrower
        than _is_error's generic "timeout" substring match (which would
        also catch e.g. a tool reporting "connection timeout" as part of
        its own error text — that IS a case where trying a different tool
        can plausibly help, so it should still be eligible for fallback)."""
        return bool(re.match(r"^Timeout after \d+s$", str(r).strip()))

    def _categorize_error(self, result: Any) -> str:
        """Prefers an explicit "error_type" from a structured {"status":
        "error", ...} envelope — a tool can be specific about its own
        failure mode — over the generic keyword-pattern guess used as a
        fallback for unstructured output."""
        s = str(result)
        try:
            d = json.loads(s.strip())
            if isinstance(d, dict) and d.get("status") == "error" and d.get("error_type"):
                return str(d["error_type"])
        except (json.JSONDecodeError, ValueError):
            pass
        sl = s.lower()
        for pattern, label in ERROR_PATTERNS:
            if pattern in sl:
                return label
        return "ERROR"

    def _last_role(self) -> str:
        for m in reversed(self.history):
            if m.get("role") in ("user", "assistant", "tool"):
                return m["role"]
        return ""

    # ------------------------------------------------------------------
    # Reflection / persistence delegates
    # ------------------------------------------------------------------
    def _stuck(self) -> bool:
        return self.reflection._stuck(self)

    async def _reflect(self) -> str:
        return await self.reflection._reflect(self)

    async def _trim(self) -> None:
        return await self.persistence._trim(self)

    async def _checkpoint(self, iteration: int):
        return await self.persistence._checkpoint(self, iteration)

    # ------------------------------------------------------------------
    # Session lifecycle / metrics
    # ------------------------------------------------------------------
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
            "last_llm_error":  self._last_llm_error,
        }