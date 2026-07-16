"""PENZER — Planner
Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""
import logging
import asyncio, json
from session.memory import get_relevant_kv_facts
from agent.config import ITER_BY_COMPLEXITY
logger = logging.getLogger(__name__)
class Planner:
    @staticmethod
    def _max_iter_for_complexity(score: float) -> int:
        if score < 0.3:
            return ITER_BY_COMPLEXITY["simple"]
        if score < 0.6:
            return ITER_BY_COMPLEXITY["medium"]
        return ITER_BY_COMPLEXITY["complex"]
    def _looks_like_memory_query(self, agent, query: str) -> bool:
        q = query.lower()
        return any(cue in q for cue in (
            "remember", "memory", "recall", "stored", "last time", "as before",
            "what do you know", "what did you", "my ", "me ", "preference",
            "path", "project", "config", "env", "ip", "address", "name",
            "email", "phone",
        ))
    def _match_core_skills(self, agent, user_input: str) -> list:
        lowered = user_input.lower()
        matched = []
        facts = get_relevant_kv_facts(user_input, n=3)
        for skill in agent.core_skills:
            skill_name = (skill.name or "").lower()
            keyword_hit = any(k.lower() in lowered for k in skill.keywords or [])
            if keyword_hit:
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
        for step in agent._skill_plan:
            if step["done"]:
                continue
            if not step["tools"] or tool_name in step["tools"]:
                step["done"] = True
                skill_name   = step["skill"]
                agent._skill_steps[skill_name] = step["step"] + 1
                if all(s["done"] for s in agent._skill_plan if s["skill"] == skill_name):
                    agent._skill_done.add(skill_name)
                break
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
                return plan
        except Exception as e:
            logger.debug("Hierarchical planner: %s", e)
        agent._record_step("planning", "No hierarchical plan needed — proceeding directly.")
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
    def _build_execution_queue(self, agent) -> None:
        agent._execution_queue = []
        agent._execution_index = 0
        agent._active_execution_item = None
        agent._execution_complete = False
        if not agent._milestones:
            agent._execution_complete = True
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
    def _claim_next_execution_item(self, agent) -> dict | None:
        if agent._execution_complete:
            return None
        if agent._execution_index >= len(agent._execution_queue):
            agent._execution_complete = True
            agent._active_execution_item = None
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
        return item
    def _complete_current_execution_item(self, agent, success: bool = True) -> None:
        if agent._active_execution_item is None:
            return
        agent._active_execution_item = None
        agent._execution_complete = agent._execution_index >= len(agent._execution_queue)
        if not success:
            agent._execution_complete = False
        if agent._execution_complete:
            # Previously this only happened at the top of the *next*
            # iteration's loop body — so a task that gave its final
            # answer in the same iteration its last step completed would
            # transition to DONE with `_milestones` still populated.
            agent._milestones = []