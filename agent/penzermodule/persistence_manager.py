"""PENZER — PersistenceManager

Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""

import time, logging
from datetime import datetime
from session.memory import (
    save_last_run, add_checkpoint,
)
from tools.executor import set_execution_state

logger = logging.getLogger(__name__)

TRIM_AT = 30
KEEP_LAST = 8
CHECKPOINT_EVERY = 10

from agent.penzermodule.belief_manager import Phase, PHASE_TRANSITIONS, PHASE_TO_GOAL_PROGRESS


class PersistenceManager:
    def _restore_snapshot(self, agent, snapshot: dict) -> None:
        agent._goal = snapshot.get("goal", agent._goal)
        agent._run_id = snapshot.get("run_id", agent._run_id)
        agent.history = snapshot.get("history", agent.history)
        agent._trace = snapshot.get("trace", agent._trace)
        agent._resume_state = snapshot.get("resume_state", agent._resume_state)
        agent._milestones = snapshot.get("milestones", agent._milestones)
        agent._execution_queue = snapshot.get("execution_queue", agent._execution_queue)
        agent._execution_index = snapshot.get("execution_index", agent._execution_index)
        agent._active_execution_item = snapshot.get("active_execution_item", agent._active_execution_item)
        agent._belief = snapshot.get("belief", agent._belief)
        try:
            agent._phase = Phase(snapshot.get("phase", agent._phase.value))
        except ValueError:
            agent._phase = Phase.PLANNING
        agent._complexity_score = snapshot.get("complexity_score", agent._complexity_score)
        agent._is_complex_task = snapshot.get("is_complex_task", agent._is_complex_task)
        agent._max_iter = snapshot.get("max_iter", agent._max_iter)
        agent._matched_skills = snapshot.get("matched_skills", agent._matched_skills)
        agent._last_matched_skills = snapshot.get("last_matched_skills", agent._last_matched_skills)
        agent._system_prompt = snapshot.get("system_prompt", agent._system_prompt)
        agent._subtasks = snapshot.get("subtasks", agent._subtasks)
        agent._subtask_idx = snapshot.get("subtask_idx", agent._subtask_idx)
        agent._milestone_idx = snapshot.get("milestone_idx", agent._milestone_idx)
        agent._total_subtasks = snapshot.get("total_subtasks", agent._total_subtasks)
        agent._current_subtask = snapshot.get("current_subtask", agent._current_subtask)
        agent._execution_complete = snapshot.get("execution_complete", agent._execution_complete)
        if agent._resume_state:
            set_execution_state({"state": agent._resume_state})

    def _persist_resume_snapshot(self, agent) -> None:
        try:
            save_last_run({
                "goal": agent._goal,
                "run_id": agent._run_id,
                "history": agent.history,
                "trace": agent._trace,
                "resume_state": agent._resume_state,
                "milestones": agent._milestones,
                "execution_queue": agent._execution_queue,
                "execution_index": agent._execution_index,
                "active_execution_item": agent._active_execution_item,
                "belief": agent._belief,
                "phase": agent._phase.value,
                "complexity_score": agent._complexity_score,
                "is_complex_task": agent._is_complex_task,
                "max_iter": agent._max_iter,
                "matched_skills": agent._matched_skills,
                "last_matched_skills": agent._last_matched_skills,
                "system_prompt": agent._system_prompt,
                "subtasks": agent._subtasks,
                "subtask_idx": agent._subtask_idx,
                "milestone_idx": agent._milestone_idx,
                "total_subtasks": agent._total_subtasks,
                "current_subtask": agent._current_subtask,
                "execution_complete": agent._execution_complete,
            })
        except Exception as e:
            logger.error("Persist snapshot: %s", e)

    async def _trim(self, agent) -> None:
        if agent._trimming or len(agent.history) <= TRIM_AT:
            return
        agent._trimming = True
        first, mid, tail = agent.history[:1], agent.history[1:-KEEP_LAST], agent.history[-KEEP_LAST:]
        if not mid:
            agent._trimming = False
            return
        try:
            r = await agent.llm.chat(
                system=(
                    f"Compress history. GOAL: {agent._goal}\n"
                    "Keep goal-relevant facts only. 2-3 sentences: what tried, what worked, what still needed."
                ),
                messages=[{"role": "user", "content": "\n".join(
                    f"{m['role']}: {str(m.get('content',''))[:100]}" for m in mid
                )}],
            )
            agent.history = first + [
                {"role": "assistant", "content": f"[Summary] {r.get('content','')}"}
            ] + tail
        except Exception:
            agent.history = first + tail
        finally:
            agent._trimming = False

    async def _checkpoint(self, agent, iteration: int):
        try:
            violations = agent._check_consistency()
            if violations:
                logger.warning("State consistency violations at iter %d: %s", iteration, violations)
            add_checkpoint({
                "timestamp":   datetime.now().isoformat(),
                "iteration":   iteration,
                "goal":        agent._goal,
                "belief":      agent._belief["goal_progress"],
                "phase":       agent._phase.value,
                "milestone":   f"{agent._milestone_idx}/{len(agent._milestones)}",
                "trace_len":   len(agent._trace),
                "resources":   agent._monitor.stats(),
                "consistency_violations": violations,
            })
        except Exception as e:
            logger.debug("Checkpoint: %s", e)