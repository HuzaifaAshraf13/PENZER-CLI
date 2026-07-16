"""PENZER — BeliefManager
Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""
from enum import Enum
import logging
logger = logging.getLogger(__name__)
class Phase(Enum):
    PLANNING   = "planning"    # building/replanning milestones, no tool calls yet
    EXECUTING  = "executing"   # normal steady state — calling tools, making progress
    REFLECTING = "reflecting"  # stuck-recovery: replanning a branch or running _reflect()
    BLOCKED    = "blocked"     # last tool call failed, not yet recovered
    DONE       = "done"        # final answer given
    FAILED     = "failed"      # gave up (max failures / resource limit)
PHASE_TRANSITIONS: dict[Phase, set[Phase]] = {
    # PLANNING -> DONE: covers a narrow crash-recovery window in run() —
    # _persist_resume_snapshot() is called while phase is still PLANNING,
    # just before the transition to EXECUTING. If the process crashes in
    # that window, a resumed run can land straight on a final answer
    # (no tool calls) with phase still PLANNING.
    Phase.PLANNING:   {Phase.EXECUTING, Phase.FAILED, Phase.DONE},
    Phase.EXECUTING:  {Phase.REFLECTING, Phase.BLOCKED, Phase.DONE, Phase.FAILED},
    # BLOCKED -> DONE: _update_belief() sets BLOCKED on ANY single tool
    # failure, not just after _stuck() confirms a repeated pattern. It's
    # entirely normal for the model to fail one tool call and then just
    # answer directly ("couldn't do X, but here's Y") — which drives
    # _handle_empty_calls() to transition straight to DONE. Without this
    # edge, that common, non-buggy flow logged (or under _phase_strict,
    # raised on) an "invalid transition" every time.
    Phase.BLOCKED:    {Phase.EXECUTING, Phase.REFLECTING, Phase.FAILED, Phase.DONE},
    # REFLECTING -> DONE: closes the same gap defensively. Not currently
    # reachable given how _handle_stuck() drives phase (it always moves
    # to EXECUTING before the model gets another turn), but a resumed
    # snapshot could in principle land here, and there's no reason to
    # leave this edge missing only to rediscover it later.
    Phase.REFLECTING: {Phase.EXECUTING, Phase.PLANNING, Phase.BLOCKED, Phase.FAILED, Phase.DONE},
    Phase.DONE:       set(),                  # terminal — completed runs clear their
                                               # snapshot in _finalize(), so DONE never
                                               # needs a way back out via resume.
    # FAILED is NOT fully terminal: _finalize() only clears the snapshot
    # on DONE, so a FAILED run's snapshot is still resumable. A resumed
    # run can then: (a) succeed its first tool call -> EXECUTING,
    # (b) fail its first tool call again -> BLOCKED, or (c) get an
    # immediate final answer with no tool call -> DONE. All three need
    # to be legal, not just the success case.
    Phase.FAILED:     {Phase.EXECUTING, Phase.BLOCKED, Phase.DONE},
}
# Mirrors Phase into the existing free-form belief string so every
# pre-existing reader of self._belief["goal_progress"] (prompt text,
# _finalize()'s completion check, get_metrics()) keeps working unchanged.
PHASE_TO_GOAL_PROGRESS = {
    Phase.PLANNING:   "not_started",
    Phase.EXECUTING:  "in_progress",
    Phase.REFLECTING: "in_progress",
    Phase.BLOCKED:    "blocked",
    Phase.DONE:       "complete",
    Phase.FAILED:     "failed",
}
class BeliefManager:
    def _transition(self, agent, to: Phase, reason: str = "") -> None:
        """
        The only place agent._phase (and its agent._belief["goal_progress"]
        mirror) gets written. An invalid transition is logged loudly —
        it means two parts of the agent disagree about what's
        happening — but is still applied rather than raised, so a
        coordination bug degrades to a log line instead of taking down
        a live run. Tests can set `PenzerAgent._phase_strict = True` on
        the class to make invalid transitions raise instead, to catch
        these during development.
        """
        if to != agent._phase and to not in PHASE_TRANSITIONS.get(agent._phase, set()):
            msg = f"Invalid phase transition {agent._phase.value} -> {to.value} ({reason})"
            if getattr(agent, "_phase_strict", False):
                raise ValueError(msg)
            logger.warning(msg)
        agent._phase = to
        agent._belief["goal_progress"] = PHASE_TO_GOAL_PROGRESS[to]
    def _update_belief(self, agent, tool: str, args: dict, result: str, ok: bool) -> None:
        agent._belief["last_action"]  = f"{tool}({agent._fmt_action(tool, args)})"
        agent._belief["last_outcome"] = "ok" if ok else f"failed: {result[:60]}"
        if ok:
            fact = f"{tool}: {result[:80]}"
            if fact not in agent._belief["verified_facts"]:
                agent._belief["verified_facts"].append(fact)
                agent._belief["verified_facts"] = agent._belief["verified_facts"][-5:]
            if agent._phase in (Phase.PLANNING, Phase.BLOCKED, Phase.REFLECTING, Phase.FAILED):
                agent._transition(Phase.EXECUTING, reason=f"{tool} succeeded")
        else:
            if agent._phase != Phase.BLOCKED:
                agent._transition(Phase.BLOCKED, reason=f"{tool} failed: {result[:60]}")
    def _belief_summary(self, agent) -> str:
        b     = agent._belief
        lines = [f"BELIEF: {b['goal_progress'].upper()}"]
        if b["verified_facts"]:
            lines.append(f"  Know: {' | '.join(b['verified_facts'][-2:])}")
        if b["assumptions"]:
            lines.append(f"  Assuming: {' | '.join(b['assumptions'][:2])}")
        if b["unknowns"]:
            lines.append(f"  Unknown: {' | '.join(b['unknowns'][:2])}")
        if b["last_action"]:
            lines.append(f"  Last: {b['last_action']} -> {b['last_outcome']}")
        return "\n".join(lines)
    def _check_consistency(self, agent) -> list[str]:
        """
        Cross-checks phase against the two other structures that track
        "are we done yet" independently — the execution queue and the
        skill plan. Returns a list of human-readable violations; doesn't
        raise or log itself, so callers (checkpoints, tests) decide what
        to do with it. This is read-only — it reports drift, it doesn't
        try to correct it, since silently overwriting one structure to
        match another could paper over the actual bug that caused them
        to disagree in the first place.
        """
        violations = []
        if agent._phase == Phase.DONE:
            if agent._active_execution_item is not None:
                violations.append(
                    f"phase=DONE but execution item still active: {agent._active_execution_item.get('title', '?')}"
                )
            if agent._milestones:
                violations.append("phase=DONE but hierarchical milestones were not cleared")
        if agent._phase in (Phase.PLANNING, Phase.BLOCKED):
            if agent._execution_complete and agent._milestones:
                violations.append(
                    f"phase={agent._phase.value} but the execution queue already reports complete"
                )
        if (
            agent._is_complex_task
            and agent._execution_complete
            and agent._active_execution_item is None
            and agent._milestones == []
            and agent._phase not in (Phase.DONE, Phase.FAILED, Phase.PLANNING)
        ):
            violations.append(
                f"execution queue is fully drained but phase is still {agent._phase.value}, "
                "not DONE/FAILED"
            )
        if agent._skill_plan and agent._matched_skills:
            all_skill_steps_done = all(s["done"] for s in agent._skill_plan)
            if all_skill_steps_done and agent._phase == Phase.PLANNING:
                violations.append("skill plan fully complete but phase never left PLANNING")
        if agent._phase == Phase.BLOCKED and agent._belief["last_outcome"].startswith("ok"):
            violations.append("phase=BLOCKED but the last recorded belief outcome was 'ok'")
        return violations