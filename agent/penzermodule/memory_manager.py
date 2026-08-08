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

# Bounds in-memory step-log growth for very long-running tasks. Unlike
# `history` (bounded by _trim), `_steps` had no cap at all — the durable
# copy already lives on disk via _flush_steps, so trimming the in-memory
# tail here only affects get_steps()'s recency window, not durability.
_MAX_IN_MEMORY_STEPS = 500


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
        if len(agent._steps) > _MAX_IN_MEMORY_STEPS:
            agent._steps = agent._steps[-_MAX_IN_MEMORY_STEPS:]
        agent._pending_steps.append(step)
        agent._safe_status(description)
        return step

    def _flush_steps(self, agent) -> None:
        """Write any not-yet-persisted steps to disk in one batch. Called
        once per iteration (not once per step — see append_steps' own
        docstring for why) plus once more in `_finalize()` to catch steps
        from any early-return path that skips the per-iteration point.

        NOTE (flagged, not changed): on exception, `agent._pending_steps`
        is deliberately left un-cleared so the same batch is retried on
        the next flush rather than silently dropped — that's the right
        call if `_append_steps_to_disk` only ever fails before writing
        anything. Whether that's actually true (as opposed to a failure
        that can occur after a partial on-disk write, which retrying here
        would re-append and duplicate) depends on session.memory.
        append_steps' own implementation, which this module doesn't have
        visibility into. Worth confirming there rather than guessing at a
        fix here.
        """
        if not agent._pending_steps:
            return
        try:
            _append_steps_to_disk(agent._run_id, agent._pending_steps)
            agent._pending_steps = []
        except Exception as e:
            logger.error("Flush steps: %s", e)

    def get_steps(self, agent, n: int = 50) -> list[dict]:
        """In-memory steps for the current run — fast, no disk access.

        n <= 0 returns an empty list. Without this guard, `n=0` fell
        through to `agent._steps[-0:]` — in Python, -0 == 0, so a slice
        of `[-0:]` is identical to `[0:]`, i.e. the FULL list, not an
        empty one. A caller passing n=0 meaning "give me nothing" (e.g.
        a computed count that can legitimately be zero) got every step
        in memory instead — silently, with no error to signal the
        mismatch between intent and result.
        """
        if n <= 0:
            return []
        return agent._steps[-n:]

    def get_persisted_steps(self, agent, run_id: str | None = None, n: int = 100) -> list[dict]:
        """Disk-backed steps — survive a crash/restart, retrievable from a
        different process or agent instance. Defaults to this agent's own
        run (which stays the same run_id across a resume)."""
        return _get_persisted_steps(run_id=run_id or agent._run_id, n=n)

    def get_run_trace(self, agent, run_id: str | None = None, n: int = 100) -> list[dict]:
        """Replay a run's persisted step trace."""
        return self.get_persisted_steps(agent, run_id=run_id, n=n)

    def render_run_trace(self, agent, run_id: str | None = None, n: int = 100) -> str:
        """Render a replayable summary of a persisted run trace."""
        steps = self.get_run_trace(agent, run_id=run_id, n=n)
        lines = []
        for s in steps:
            lines.append(
                f"{s.get('iteration', '?'):>3} "
                f"{s.get('phase', '?'):<10} "
                f"{s.get('kind', '?'):<12} "
                f"{s.get('description', '')}"
            )
        return "\n".join(lines)

    def clear_run_steps(self, agent, run_id: str | None = None) -> int:
        return _clear_persisted_steps(run_id=run_id or agent._run_id)

    def _update_working_memory(self, agent, tool: str, result: str, ok: bool) -> None:
        """Keep last WORKING_MEMORY_SIZE relevant facts from tool results."""
        if ok and result:
            agent._working_mem.append(f"{tool}: {result[:80]}")

    def _working_mem_summary(self, agent) -> str:
        if not agent._working_mem:
            return ""
        return "WORKING MEM: " + " | ".join(list(agent._working_mem)[-3:])