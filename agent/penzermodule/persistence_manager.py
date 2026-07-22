"""PENZER — PersistenceManager
Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""
import logging
from collections import defaultdict, deque
from datetime import datetime
from session.memory import (
    save_last_run, add_checkpoint,
)
from tools.executor import set_execution_state
from agent.config import TRIM_AT, KEEP_LAST, MAX_CONSISTENCY_VIOLATIONS
logger = logging.getLogger(__name__)
from agent.penzermodule.belief_manager import Phase
class PersistenceManager:
    def _restore_snapshot(self, agent, snapshot: dict) -> None:
        agent._goal = snapshot.get("goal", agent._goal)
        agent._run_id = snapshot.get("run_id", agent._run_id)
        # Restoring iteration count lets a resumed run continue counting
        # from where it left off (see _loop's start-point logic in
        # agent.py) instead of silently restarting the iteration budget
        # at 0 — without this, ABSOLUTE_MAX_ITER and friends only ever
        # measured "iterations since the most recent resume," not total
        # iterations spent on the task, defeating the point of a ceiling.
        agent._iteration = snapshot.get("iteration", agent._iteration)
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
        # NOTE: this is the single source of truth for max_iter on resume.
        # agent.py's resume_last_task() must NOT recompute/overwrite this
        # afterward — it used to, which silently discarded any iteration
        # extensions the run had already earned before crashing/being
        # interrupted, forcing it back down to the base complexity-based
        # budget every time. If the snapshot has no max_iter (older
        # snapshot format), the caller-supplied default here is used.
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
        # Fix #5: the snapshot previously omitted these — on an agent
        # instance reused across multiple run()/resume_last_task() calls
        # (agent.py now also calls _reset() before restoring, as a second
        # line of defense), a restore that skips them silently keeps
        # whatever the PREVIOUS run left in these fields. Concretely this
        # meant e.g. _meta_skill_triggered=True from an earlier run could
        # suppress the meta-skill reminder on a "fresh" resumed run, and
        # _failures could start above 0, hitting MAX_FAILURES early.
        agent._failures = snapshot.get("failures", agent._failures)
        agent._consec_errors = defaultdict(int, snapshot.get("consec_errors", dict(agent._consec_errors)))
        agent._skill_plan = snapshot.get("skill_plan", agent._skill_plan)
        agent._skill_steps = snapshot.get("skill_steps", agent._skill_steps)
        agent._skill_done = set(snapshot.get("skill_done", list(agent._skill_done)))
        agent._working_mem = deque(
            snapshot.get("working_mem", list(agent._working_mem)),
            maxlen=agent._working_mem.maxlen,
        )
        agent._novel_task = snapshot.get("novel_task", agent._novel_task)
        agent._meta_skill_triggered = snapshot.get("meta_skill_triggered", agent._meta_skill_triggered)
        agent._skill_gate_shown = snapshot.get("skill_gate_shown", agent._skill_gate_shown)
        agent._force_stop_reason = snapshot.get("force_stop_reason", agent._force_stop_reason)
        agent._consistency_violation_streak = snapshot.get(
            "consistency_violation_streak", agent._consistency_violation_streak
        )
        agent._steps = snapshot.get("steps", agent._steps)
        if agent._resume_state:
            set_execution_state({"state": agent._resume_state})
    def _persist_resume_snapshot(self, agent) -> None:
        try:
            save_last_run({
                "goal": agent._goal,
                "run_id": agent._run_id,
                "iteration": agent._iteration,
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
                "failures": agent._failures,
                "consec_errors": dict(agent._consec_errors),
                "skill_plan": agent._skill_plan,
                "skill_steps": agent._skill_steps,
                "skill_done": list(agent._skill_done),
                "working_mem": list(agent._working_mem),
                "novel_task": agent._novel_task,
                "meta_skill_triggered": agent._meta_skill_triggered,
                "skill_gate_shown": agent._skill_gate_shown,
                "force_stop_reason": agent._force_stop_reason,
                "consistency_violation_streak": agent._consistency_violation_streak,
                "steps": agent._steps,
            })
        except Exception as e:
            logger.error("Persist snapshot: %s", e)
    async def _trim(self, agent) -> None:
        if agent._trimming or len(agent.history) <= TRIM_AT:
            return
        agent._trimming = True
        # Snapshot the version before the only await point in this method.
        # agent._reset() bumps _history_version every time it runs, which
        # happens at the start of every run()/resume_last_task() call — if
        # that happens while we're still awaiting the summarizer below,
        # the version will have moved on and we must not write back a
        # summary computed from history that's no longer current.
        version = agent._history_version
        first, mid, tail = agent.history[:1], agent.history[1:-KEEP_LAST], agent.history[-KEEP_LAST:]
        if not mid:
            agent._trimming = False
            return
        summary_content = None
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
            summary_content = r.get("content", "")
        except Exception:
            pass
        finally:
            agent._trimming = False
        if agent._history_version != version:
            logger.info(
                "Discarding a trim result — history moved on (a new run/resume "
                "started) while this trim was still awaiting the summarizer."
            )
            return
        if summary_content is not None:
            agent.history = first + [{"role": "assistant", "content": f"[Summary] {summary_content}"}] + tail
        else:
            agent.history = first + tail
    async def _checkpoint(self, agent, iteration: int):
        try:
            violations = agent._check_consistency()
            if violations:
                logger.warning("State consistency violations at iter %d: %s", iteration, violations)
                agent._consistency_violation_streak = getattr(agent, "_consistency_violation_streak", 0) + 1
                if (
                    agent._consistency_violation_streak >= MAX_CONSISTENCY_VIOLATIONS
                    and agent._force_stop_reason is None
                ):
                    agent._force_stop_reason = (
                        f"internal state consistency violated "
                        f"{agent._consistency_violation_streak} checkpoints in a row — "
                        "stopping to avoid compounding a coordination bug"
                    )
                    logger.error(
                        "Circuit breaker tripped at iter %d after %d consecutive "
                        "consistency-violation checkpoints: %s",
                        iteration, agent._consistency_violation_streak, violations,
                    )
            else:
                agent._consistency_violation_streak = 0
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
            logger.warning("Checkpoint failed at iter %d: %s", iteration, e)