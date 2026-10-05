"""Agent iteration orchestration and per-iteration safety policies."""

import itertools
import logging
import time
from typing import Any

from agent.config import (
    ABSOLUTE_MAX_ITER,
    CHECKPOINT_EVERY,
    ITER_EXTENSION_SIZE,
    MAX_RUNTIME_SECONDS,
    MAX_TOKENS_PER_RUN,
    STUCK_MIN,
    TRIM_AT,
)
from agent.system_prompts import build_system_prompt
from session.memory import get_relevant_memories, save_history
from agent.penzermodule import execution


logger = logging.getLogger(__name__)


def check_stop_conditions(agent: Any, iteration: int) -> str | None:
    """Enforce wall-clock, iteration, memory, shutdown, and token budgets."""
    elapsed = time.time() - agent._run_start_time
    if elapsed > MAX_RUNTIME_SECONDS:
        agent._belief["goal_progress"] = "failed"
        reason = f"time budget exceeded ({MAX_RUNTIME_SECONDS}s)"
        agent._record_step("give_up", f"Stopping: {reason}.", reason="stop_condition", stop_reason=reason)
        agent._persist_all()
        save_history(agent.history)
        return f"Stopped: {reason}. Use resume to continue."

    if iteration >= agent._max_iter:
        if agent._can_extend_iterations():
            agent._max_iter += ITER_EXTENSION_SIZE
            elapsed = int(time.time() - agent._run_start_time)
            agent._record_step(
                "extend",
                f"Hit the iteration budget but still making progress — extending by {ITER_EXTENSION_SIZE} "
                f"({elapsed}s elapsed of {MAX_RUNTIME_SECONDS}s budget).",
            )
        else:
            if not agent._budget_prompted:
                agent._budget_prompted = True
                details = {
                    "iteration": agent._iteration,
                    "max_iter": agent._max_iter,
                    "message": "Iteration budget reached. Resume to continue.",
                }
                try:
                    if agent.on_budget_prompt(details):
                        agent._max_iter += ITER_EXTENSION_SIZE
                        agent._record_step(
                            "extend",
                            f"Iteration budget extended by {ITER_EXTENSION_SIZE} on request.",
                        )
                        return None
                except Exception:
                    logger.exception("on_budget_prompt callback raised")
            reason, has_specific_reason = "iteration limit reached", False
            if time.time() - agent._run_start_time > MAX_RUNTIME_SECONDS:
                reason, has_specific_reason = f"time budget exceeded ({MAX_RUNTIME_SECONDS}s)", True
            elif agent._iteration >= ABSOLUTE_MAX_ITER:
                reason, has_specific_reason = "absolute iteration ceiling reached", True
            elif agent._trace and not any(entry["success"] for entry in agent._trace[-3:]):
                reason, has_specific_reason = "stopped making progress", True
            agent._belief["goal_progress"] = "failed"
            agent._record_step("give_up", f"Stopping: {reason}.", reason="stop_condition", stop_reason=reason)
            agent._persist_all()
            return f"Stopped: {reason}" if has_specific_reason else "Iteration limit reached. Use resume to continue."

    if agent._shutdown:
        agent._persist_all()
        save_history(agent.history)
        return "Interrupted"

    try:
        ok, message = agent._monitor.check()
    except MemoryError:
        agent._record_step(
            "give_up", "Stopping: out of memory",
            reason="out_of_memory", stop_reason="out of memory",
        )
        agent._persist_all()
        save_history(agent.history)
        return "Out of memory — stopping run"
    except Exception as exc:
        logger.error("Resource monitor crashed: %s", exc, exc_info=True)
        agent._record_step(
            "give_up", f"Stopping: resource monitor unavailable ({type(exc).__name__})",
            reason="resource_monitor_unavailable",
            stop_reason="resource monitor unavailable",
        )
        agent._persist_all()
        save_history(agent.history)
        return f"Internal error: resource monitor unavailable ({type(exc).__name__})"
    if not ok:
        agent._persist_all()
        save_history(agent.history)
        return f"Resource limit: {message}"

    tokens_used = getattr(agent.llm, "token_estimate", 0) - agent._tokens_before_run
    if tokens_used > MAX_TOKENS_PER_RUN:
        agent._record_step(
            "give_up",
            f"Stopping: token budget exceeded ({tokens_used}/{MAX_TOKENS_PER_RUN} tokens).",
            reason="token_budget",
            stop_reason=f"token budget exceeded ({tokens_used} tokens)",
        )
        agent._persist_all()
        return f"Stopped: token budget exceeded ({tokens_used} tokens)"
    return None


async def pre_iteration_tasks(agent: Any, iteration: int) -> None:
    if len(agent.history) > TRIM_AT and not agent._trimming:
        agent._spawn_background(agent._trim(), "trim")
    agent._safe_status("Planning next action…" if iteration == 0 else "Choosing next action…")
    if (iteration + 1) % 5 == 0:
        agent._system_prompt = build_system_prompt(
            core_skills=agent._active_skills,
            memory_context=get_relevant_memories(agent._goal, n=3, deep=agent._is_complex_task),
            extra=agent._prompt_extra,
            goal=agent._goal,
            plugin_tools=agent.get_plugin_tool_descriptions(),
        )
    if (iteration + 1) % CHECKPOINT_EVERY == 0:
        await agent._checkpoint(iteration)


def apply_belief_updates(agent: Any, response: dict) -> None:
    """Apply optional model belief fields; omitted fields remain unchanged."""
    if response.get("assumptions"):
        agent._belief["assumptions"] = [str(item)[:120] for item in response["assumptions"]][:5]
    if response.get("unknowns"):
        agent._belief["unknowns"] = [str(item)[:120] for item in response["unknowns"]][:5]


async def run_loop(agent: Any) -> str:
    """Run model -> tool -> result iterations until a terminal outcome."""
    empty = 0
    start_at = agent._iteration + 1 if agent._trace else 0
    for iteration in itertools.count(start_at):
        agent._iteration = iteration
        stop = agent._check_stop_conditions(iteration)
        if stop is not None:
            return stop
        await agent._pre_iteration_tasks(iteration)
        response = await agent._llm_with_retry(iteration)
        if agent._shutdown:
            stop = agent._check_stop_conditions(iteration)
            if stop is not None:
                return stop
        if response is None:
            return agent._llm_failure_result()

        calls = response.get("tool_calls") or []
        text = response.get("content", "").strip()
        calls = execution.route_web_requests_to_browser(agent._goal, calls)
        agent._apply_belief_updates(response)
        agent._apply_skill_selection(response.get("skill_used"))
        if not calls:
            result, empty = agent._handle_empty_calls(text, empty)
            if result is not None:
                return result
            continue

        empty = 0
        agent._append_assistant_turn(text, calls)
        if len(agent._trace) - agent._resume_boundary_trace_len >= STUCK_MIN and agent._stuck():
            stuck_result = await agent._handle_stuck()
            if stuck_result is not None:
                return stuck_result
            continue
        agent._maybe_show_skill_gate()
        filtered_calls = agent._filter_by_confidence(calls)
        if not filtered_calls:
            agent.history.append({"role": "user", "content": "All proposed tools had low confidence. Rethink approach."})
            continue
        direct_result = await agent._execute_tool_calls(filtered_calls, iteration)
        if direct_result is not None:
            return direct_result
    return "Stopped: internal error (loop exited without a result)"