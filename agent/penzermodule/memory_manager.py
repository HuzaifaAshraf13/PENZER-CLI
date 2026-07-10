"""PENZER — MemoryManager

Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""

import logging
from session.memory import (
    append_steps as _append_steps_to_disk,
    get_steps as _get_persisted_steps,
    clear_steps as _clear_persisted_steps,
)

logger = logging.getLogger(__name__)

from agent.penzermodule.belief_manager import Phase, PHASE_TRANSITIONS, PHASE_TO_GOAL_PROGRESS


class MemoryManager:
    def _record_step(self, agent, kind: str, description: str, **extra) -> dict:
        """
        Append one structured, human-readable step. `kind` is intentionally
        open-ended ("reasoning", "tool_call", "tool_result", "planning",
        "recovery", "final_answer", "rate_limit", "give_up", ...) so a new
        step type is just a new string at the call site, not a change to
        this method or to storage. Also drives the live `on_status`
        callback, so this replaces the old generic "Step N…" messages with
        actual content.
        """
        step = {
            "iteration":   agent._iteration,
            "phase":       agent._phase.value,
            "kind":        kind,
            "description": description,
            **extra,
        }
        agent._steps.append(step)
        agent._pending_steps.append(step)
        agent.on_status(description)
        return step

    def _flush_steps(self, agent) -> None:
        """Write any not-yet-persisted steps to disk in one batch. Called
        once per iteration (not once per step — see append_steps' own
        docstring for why) plus once more in `_finalize()` to catch steps
        from any early-return path that skips the per-iteration point."""
        if not agent._pending_steps:
            return
        try:
            _append_steps_to_disk(agent._run_id, agent._pending_steps)
            agent._pending_steps = []
        except Exception as e:
            logger.error("Flush steps: %s", e)

    def get_steps(self, agent, n: int = 50) -> list[dict]:
        """In-memory steps for the current run — fast, no disk access."""
        return agent._steps[-n:]

    def get_persisted_steps(self, agent, run_id: str | None = None, n: int = 100) -> list[dict]:
        """Disk-backed steps — survive a crash/restart, retrievable from a
        different process or agent instance. Defaults to this agent's own
        run (which stays the same run_id across a resume)."""
        return _get_persisted_steps(run_id=run_id or agent._run_id, n=n)

    def clear_run_steps(self, agent, run_id: str | None = None) -> int:
        return _clear_persisted_steps(run_id=run_id or agent._run_id)

    def _update_working_memory(self, agent, tool: str, result: str, ok: bool) -> None:
        """Keep last WORKING_MEMORY_SIZE relevant facts from tool results."""
        if ok and result and result != "(empty)":
            agent._working_mem.append(f"{tool}: {result[:80]}")

    def _working_mem_summary(self, agent) -> str:
        if not agent._working_mem:
            return ""
        return "WORKING MEM: " + " | ".join(list(agent._working_mem)[-3:])