"""PENZER — Planner

Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""
import logging
import asyncio, re

from session.memory import get_relevant_kv_facts
from agent.config import ITER_BY_COMPLEXITY
from agent.system_prompts import _tokenize, _skill_token_set

logger = logging.getLogger(__name__)

# Fix #12: the old check was `cue in q` (plain substring), so "my " and
# "me " matched inside completely unrelated words — "army", "academy",
# "enemy", "summer" all contain one of those substrings, so e.g. "email
# the army contact list" false-positived a memory-skill match. Word
# boundaries fix that. Multi-word phrases ("last time", "as before") work
# fine inside \b...\b since \b only anchors the very start/end of the
# alternative that matched.
_MEMORY_CUE_RE = re.compile(
    r"\b(remember|memory|recall|stored|last time|as before|what do you know|"
    r"what did you|my|me|preference|path|project|config|env|ip|address|"
    r"name|email|phone)\b",
    re.IGNORECASE,
)


class Planner:
    @staticmethod
    def _max_iter_for_complexity(score: float) -> int:
        if score < 0.3:
            return ITER_BY_COMPLEXITY["simple"]
        if score < 0.6:
            return ITER_BY_COMPLEXITY["medium"]
        return ITER_BY_COMPLEXITY["complex"]

    def _looks_like_memory_query(self, agent, query: str) -> bool:
        return bool(_MEMORY_CUE_RE.search(query))

    def _match_core_skills(self, agent, user_input: str) -> list:
        """
        A skill is activated by one of two signals, weighted differently:

          - STRONG: any overlap with the skill's `keywords` list. Keywords
            are curated specifically to signal "this task is in my
            domain" — a single hit there is a deliberate, meaningful
            signal, not noise.
          - WEAK: overlap with the skill's name/description text alone
            (no keyword involved) requires AT LEAST 2 shared words, not
            just 1. A single incidental shared word between a task and
            an unrelated skill's description (e.g. a common verb like
            "check" or "look" appearing in both, purely by coincidence)
            was enough to activate that skill under the original
            single-token-overlap rule — and _orchestrate_skills() merges
            every matched skill's FULL agent_behavior into one combined
            plan, so a coincidental match doesn't just add noise to a
            hint, it makes the model actually try to follow an unrelated
            skill's steps as part of the task. That's what caused a
            simple "look at my network" task to pull in Memory Manager
            and Plugin Tool Creator alongside Terminal Executor and
            balloon into 16 LLM calls / ~75k tokens for work that should
            have taken 2-3 calls.

          Requiring 2+ words for the weak signal keeps the description-
          based matching (added specifically to fix RECALL — real
          matches that keyword lists alone were missing) while cutting
          the false-positive rate that same change introduced. Real
          matches typically share more than one meaningful word with a
          skill's own description; coincidental ones usually don't.
        """
        lowered = user_input.lower()
        goal_tokens = _tokenize(user_input)
        matched = []
        facts = get_relevant_kv_facts(user_input, n=3)
        for skill in agent.core_skills:
            skill_name = (skill.name or "").lower()
            keyword_tokens = set()
            for kw in skill.keywords or []:
                keyword_tokens |= _tokenize(kw)
            name_desc_tokens = _tokenize(skill.name or "") | _tokenize(
                getattr(skill, "description", "") or ""
            )
            keyword_hit = bool(goal_tokens & keyword_tokens)
            # Don't double-count a word that's both a keyword and also
            # happens to appear in the name/description — only count it
            # toward the weak signal if it ISN'T already a keyword hit.
            weak_overlap = goal_tokens & (name_desc_tokens - keyword_tokens)
            if keyword_hit or len(weak_overlap) >= 2:
                matched.append(skill)
                continue
            if "memory" in skill_name and (facts or agent._looks_like_memory_query(lowered)):
                matched.append(skill)
        return matched

    def _orchestrate_skills(self, agent) -> None:
        agent._skill_plan  = []
        agent._skill_steps = {s.name: 0 for s in agent._active_skills}
        agent._skill_done  = set()
        for skill in agent._active_skills:
            behavior = (skill.agent_behavior or "").strip()
            lines    = [l.strip() for l in behavior.splitlines()
                        if l.strip() and not l.strip().startswith("#")]
            tools    = set(skill.mcp_tools or [])
            for idx, line in enumerate(lines):
                agent._skill_plan.append({
                    "skill": skill.name, "step": idx,
                    "instruction": line, "tools": tools, "done": False,
                })
        tool_order = ["memory", "planning", "browser", "terminal", "file_editor"]
        agent._skill_plan.sort(key=lambda s: next(
            (i for i, t in enumerate(tool_order) if t in s["tools"]), len(tool_order)
        ))

    def _skill_plan_summary(self, agent) -> str:
        if not agent._skill_plan:
            return ""
        total   = len(agent._skill_plan)
        done    = sum(1 for s in agent._skill_plan if s["done"])
        pending = [s for s in agent._skill_plan if not s["done"]][:3]
        lines   = [f"SKILL PLAN [{done}/{total} steps]"]
        for s in pending:
            lines.append(f"  [{s['skill']}] step {s['step']+1}: {s['instruction'][:80]}")
        return "\n".join(lines)

    def _mark_skill_step_done(self, agent, tool_name: str) -> None:
        """Credits progress per-skill instead of stopping at the first
        match in the merged, tool-sorted plan. The merged plan interleaves
        steps from every active skill sorted by tool category, so "first
        pending step matching tool_name" is not the same thing as "the
        step this tool call actually satisfies" — two different skills can
        each have a pending step keyed on the same tool, and the old code
        credited whichever happened to sort first, silently starving the
        other skill's progress tracking. Each active skill now gets at
        most one of its own steps advanced per call, independently."""
        touched_skills = set()
        for step in agent._skill_plan:
            if step["done"]:
                continue
            skill_name = step["skill"]
            if skill_name in touched_skills:
                continue
            if not step["tools"] or tool_name in step["tools"]:
                step["done"] = True
                touched_skills.add(skill_name)
                agent._skill_steps[skill_name] = step["step"] + 1
                if all(s["done"] for s in agent._skill_plan if s["skill"] == skill_name):
                    agent._skill_done.add(skill_name)

    def _skills_for_tool(self, agent, tool_name: str) -> list:
        return [s for s in agent._active_skills
                if not set(s.mcp_tools or []) or tool_name in set(s.mcp_tools or [])]

    async def _plan_hierarchical(self, agent, goal: str) -> list[dict]:
        """
        Level 1: 3-5 high-level milestones
        Level 2: each milestone -> 2-3 executable steps
        Returns: [{milestone, steps: [str]}]
        """
        agent._safe_status("Planning…")
        activity_id = agent._emit_activity("thinking", "Planning task", message="Creating a plan for the requested work", details={"goal": goal[:160]})
        try:
            r = await asyncio.wait_for(
                agent.llm.chat(
                    system=(
                        "Create a hierarchical plan. Return JSON array of objects: "
                        '[{"milestone": "...", "steps": ["step1", "step2"]}]. '
                        "2-4 milestones, 2-3 steps each. No markdown."
                    ),
                    messages=[{"role": "user", "content": f"Goal: {goal}"}],
                ),
                timeout=15,
            )
            plan = agent._extract_json(r.get("content", ""), default="[]")
            if isinstance(plan, list) and plan:
                agent._record_step(
                    "planning",
                    f"Planned {len(plan)} milestones: " +
                    "; ".join(m.get("milestone", "") for m in plan)[:200],
                )
                agent._update_activity(activity_id, status="success", message="Plan created")
                return plan
        except Exception as e:
            logger.debug("Hierarchical planner: %s", e)
        agent._record_step("planning", "No hierarchical plan needed — proceeding directly.")
        if activity_id:
            agent._update_activity(activity_id, status="success", message="No hierarchical plan needed")
        return []

    async def _replan_milestone(self, agent, milestone: str, reason: str) -> list[str]:
        """Replan only the failed milestone branch — not the whole task."""
        try:
            r = await asyncio.wait_for(
                agent.llm.chat(
                    system="Task replanner. Return JSON array of 2-3 new steps. No markdown.",
                    messages=[{"role": "user", "content":
                        f"Milestone: {milestone}\nFailed because: {reason}\n"
                        f"Context: {agent._belief_summary()}\nNew steps:"}],
                ),
                timeout=10,
            )
            steps = agent._extract_json(r.get("content", ""), default="[]")
            if isinstance(steps, list) and steps:
                return steps
        except Exception as e:
            logger.debug("Replan: %s", e)
        return []

    def _requeue_milestone_steps(self, agent, milestone_idx: int, new_steps: list[str]) -> None:
        """
        Splices newly-replanned steps for `milestone_idx` into the live
        execution queue at the current position, so _claim_next_execution_item
        actually serves them on the next turn.

        Previously a successful replan only wrote to agent._subtasks /
        agent._subtask_idx, which nothing in the claim path reads —
        _claim_next_execution_item re-derives _subtasks wholesale from
        agent._milestones[...]["steps"] whenever it claims a milestone-kind
        item, clobbering whatever a replan had written. So a replan had no
        effect on what tool actually got tried next; it only appeared as a
        `[Replan] ...` message in history for the model to notice on its
        own. It also interacted badly with _complete_current_execution_item:
        when the last queued item failed, that method tried to keep
        execution_complete False so a replan/retry could continue, but
        _claim_next_execution_item unconditionally recomputes
        execution_complete from index-vs-length on its very next call and
        flips it back to True — which then injected "[Executor] All planned
        work complete. Give final answer." even though the last step had
        just failed, and cleared agent._milestones, silently disabling
        milestone-based replanning for the rest of the run.

        This method fixes the root cause by actually inserting the new
        steps into agent._execution_queue (and keeping agent._milestones'
        own step list in sync, since that's what re-derives _subtasks
        later), and clears execution_complete so the queue is genuinely
        reopened rather than just hoping the next claim call agrees.
        """
        if milestone_idx < len(agent._milestones):
            agent._milestones[milestone_idx]["steps"] = new_steps
        new_items = [
            {"kind": "step", "title": step, "milestone_idx": milestone_idx, "step_index": idx}
            for idx, step in enumerate(new_steps) if step
        ]
        agent._execution_queue[agent._execution_index:agent._execution_index] = new_items
        agent._execution_complete = False
        # FIX: a milestone being replanned is, by definition, not
        # finished — its previous step allocation just got fully resolved
        # (successfully or via exhausted retries, see
        # _complete_current_execution_item) and is now being replaced
        # with a fresh attempt. If this milestone_idx had already been
        # recorded as "completed" (see the premature-completion fix in
        # _complete_current_execution_item below — the OLD per-step
        # exhaustion counter could reach the old step total right as the
        # last old step gets resolved, right before this replan runs),
        # that record needs to be cleared along with its resolved-step
        # counter, or the progress readout would keep reporting this
        # milestone as done while it's actually being reattempted with
        # new steps.
        agent._completed_milestone_indices.discard(milestone_idx)
        counts = getattr(agent, "_milestone_resolved_step_counts", None)
        if counts is not None:
            counts[milestone_idx] = 0
        agent._update_execution_progress()

    def _build_execution_queue(self, agent) -> None:
        agent._execution_queue = []
        agent._execution_index = 0
        agent._active_execution_item = None
        agent._execution_complete = False
        agent._completed_milestone_indices = set()
        # Fresh counters for the premature-milestone-completion fix below
        # — a brand-new execution queue means no steps have resolved yet
        # for any milestone.
        agent._milestone_resolved_step_counts = {}
        if not agent._milestones:
            agent._execution_complete = True
            agent._update_execution_progress()
            return
        for milestone_idx, milestone in enumerate(agent._milestones):
            milestone_name = milestone.get("milestone", "").strip()
            if milestone_name:
                agent._execution_queue.append({
                    "kind": "milestone",
                    "title": milestone_name,
                    "milestone_idx": milestone_idx,
                    "step_index": None,
                })
            for step_idx, step in enumerate(milestone.get("steps", []) or []):
                if step:
                    agent._execution_queue.append({
                        "kind": "step",
                        "title": step,
                        "milestone_idx": milestone_idx,
                        "step_index": step_idx,
                    })
        agent._update_execution_progress()

    def _claim_next_execution_item(self, agent) -> dict | None:
        if agent._execution_complete:
            agent._update_execution_progress()
            return None
        if agent._execution_index >= len(agent._execution_queue):
            agent._execution_complete = True
            agent._active_execution_item = None
            agent._update_execution_progress()
            return None
        item = agent._execution_queue[agent._execution_index]
        agent._execution_index += 1
        agent._active_execution_item = item
        agent._current_subtask = item.get("title", "")
        agent._milestone_idx = item.get("milestone_idx", getattr(agent, "_milestone_idx", 0))
        if agent._milestones and agent._milestone_idx < len(agent._milestones):
            agent._subtasks = agent._milestones[agent._milestone_idx].get("steps", [])
        else:
            agent._subtasks = getattr(agent, "_subtasks", [])
        agent._update_execution_progress()
        return item

    _MAX_ITEM_RETRIES = 1

    def _complete_current_execution_item(self, agent, success: bool = True) -> None:
        if agent._active_execution_item is None:
            return
        item = agent._active_execution_item
        agent._active_execution_item = None
        if not success:
            retries = item.get("_retries", 0)
            if retries < self._MAX_ITEM_RETRIES:
                # Actually re-queue it at the current position so the next
                # claim call serves it again — previously execution_complete
                # was set False here with nothing behind it, so the retry
                # never happened; the item was just gone and the queue
                # reported "done" on the very next claim regardless.
                requeued = {**item, "_retries": retries + 1}
                agent._execution_queue.insert(agent._execution_index, requeued)
                agent._execution_complete = False
                return
            # Retries exhausted for this item — let the queue finish
            # draining normally. _handle_stuck's milestone-replan / general
            # reflection path is the real recovery mechanism; this cap just
            # stops a permanently-broken step from looping forever.
        milestone_idx = item.get("milestone_idx")
        if item.get("kind") == "step" and milestone_idx is not None:
            # FIX: previously this called
            # agent._completed_milestone_indices.add(milestone_idx)
            # unconditionally here — i.e. the instant ANY ONE step
            # belonging to a milestone resolved (success, or failure with
            # retries exhausted), the WHOLE milestone was marked complete
            # in _completed_milestone_indices, which agent.py's
            # _update_execution_progress uses as the numerator for the
            # "X/Y milestones completed" progress readout. Since set.add()
            # is idempotent, a 3-step milestone was reported "done" the
            # moment its first step resolved, and the count never
            # meaningfully tracked real completion after that — verified
            # by simulation: after step 1 of 3, the old code already
            # reported 1/1 milestones complete.
            #
            # Fix: track how many of this milestone's steps have resolved
            # (success or exhausted-failure — same "resolved" semantics
            # the original code used, just counted instead of assumed),
            # and only mark the milestone complete once that count reaches
            # its CURRENT total step count. Reading the total live from
            # agent._milestones[milestone_idx]["steps"] (rather than
            # caching it once) means this stays correct across a mid-run
            # replan, which can change a milestone's step count via
            # _requeue_milestone_steps — that method also resets this
            # counter and un-marks the milestone as completed when it
            # reopens it with new steps, so a replanned milestone can't
            # stay stuck showing as "done" from its pre-replan attempt.
            counts = getattr(agent, "_milestone_resolved_step_counts", None)
            if counts is None:
                counts = {}
                agent._milestone_resolved_step_counts = counts
            counts[milestone_idx] = counts.get(milestone_idx, 0) + 1
            total_steps = 0
            if agent._milestones and milestone_idx < len(agent._milestones):
                total_steps = len([
                    s for s in (agent._milestones[milestone_idx].get("steps") or []) if s
                ])
            if total_steps and counts[milestone_idx] >= total_steps:
                agent._completed_milestone_indices.add(milestone_idx)
        agent._execution_complete = agent._execution_index >= len(agent._execution_queue)
        agent._update_execution_progress()
        if agent._execution_complete:
            # Previously this only happened at the top of the *next*
            # iteration's loop body — so a task that gave its final
            # answer in the same iteration its last step completed would
            # transition to DONE with `_milestones` still populated.
            agent._milestones = []
            agent._update_execution_progress()