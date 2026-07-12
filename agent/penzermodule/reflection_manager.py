"""PENZER — ReflectionManager

Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""

import asyncio, json, time, logging
from typing import Any
from session.memory import remember_semantic, store_insight, store_post_mortem

logger = logging.getLogger(__name__)

STUCK_MIN = 2
MAX_FAILURES = 3
ABSOLUTE_MAX_ITER = 5000
MAX_RUNTIME_SECONDS = 21600
MAX_TOKENS_PER_RUN = 2000000

from agent.penzermodule.belief_manager import Phase, PHASE_TRANSITIONS, PHASE_TO_GOAL_PROGRESS


class ReflectionManager:
    @staticmethod
    def _extract_json(text: str, default: str = "{}") -> Any:
        """Strip code-fence/`json` noise the LLM sometimes wraps JSON in, then parse.
        Used by the planner, replanner, completion evaluator, and post-mortem writer,
        which previously each repeated this cleanup inline."""
        raw = (text or default).strip().strip("```").lstrip("json").strip()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return json.loads(default)

    async def _evaluate_completion(self, agent, goal: str, result: str) -> tuple[bool, str]:
        try:
            r = await asyncio.wait_for(
                agent.llm.chat(
                    system=(
                        "Evaluate if goal was achieved. "
                        'Return JSON: {"completed": true/false, "reason": "one sentence"}. '
                        "Be strict — partial = not completed."
                    ),
                    messages=[{"role": "user", "content":
                        f"GOAL: {goal}\nRESULT: {result[:200]}\n"
                        f"TOOLS: {' -> '.join(t['tool'] for t in agent._trace)}"}],
                ),
                timeout=10,
            )
            ev = agent._extract_json(r.get("content", ""), default="{}")
            return bool(ev.get("completed", True)), ev.get("reason", "")
        except Exception:
            return True, ""

    async def _write_post_mortem_and_insights(self, agent, goal: str, result: str) -> None:
        worked = " -> ".join(
            f"{t['tool']}({agent._fmt_action(t['tool'], t['args'])})"
            for t in agent._trace if t["success"]
        )[:200] or "none"
        failed = " -> ".join(
            f"{t['tool']}({t.get('error_type','?')})"
            for t in agent._trace if not t["success"]
        )[:200] or "none"
        try:
            r = await asyncio.wait_for(
                agent.llm.chat(
                    system=(
                        "Post-mortem + extract insight. "
                        "JSON keys: what_worked, what_failed, next_time, insight. "
                        "insight = one general rule for future tasks. One sentence each."
                    ),
                    messages=[{"role": "user", "content":
                        f"Goal: {goal}\nOutcome: {result[:80]}\n"
                        f"Worked: {worked}\nFailed: {failed}"}],
                ),
                timeout=15,
            )
            pm = agent._extract_json(r.get("content", ""), default="{}")
            store_post_mortem(
                task_type=goal[:40],
                what_worked=pm.get("what_worked", worked),
                what_failed=pm.get("what_failed", failed),
                next_time=pm.get("next_time", ""),
            )
            if pm.get("insight"):
                store_insight(
                    insight=pm["insight"],
                    source_tasks=[goal[:40]],
                    confidence=0.7 if any(t["success"] for t in agent._trace) else 0.4,
                )
            if any(t["success"] for t in agent._trace):
                remember_semantic(
                    pattern=f"For '{goal[:40]}': {worked}",
                    confidence=0.7,
                )
        except Exception as e:
            logger.debug("Post-mortem: %s", e)

    def _inject_meta_skill_reminder(self, agent):
        winning = " -> ".join(
            f"{t['tool']}({agent._fmt_action(t['tool'], t['args'])})"
            for t in agent._trace if t["success"]
        )
        agent.history.append({"role": "user", "content": (
            "[Skill evolution] Complex novel task solved. "
            f"Winning sequence: {winning}\n"
            "Before final answer:\n"
            "1. List agent/skills/generated/ — similar skill exists?\n"
            "2. If not: write .skill.md with exact sequence + failure_modes.\n"
            "3. Include: name, description, keywords, agent_behavior, failure_modes, mcp_tools.\n"
            "Then give your final answer."
        )})

    def _stuck(self, agent) -> bool:
        w    = agent.history[-6:]
        msgs = [m for m in w if m.get("role") == "tool"]
        if len(msgs) < STUCK_MIN:
            return False
        if len({str(m.get("content", ""))[:80] for m in msgs}) == 1:
            return True
        # Fixed: was checking tool NAME repetition only, so calling
        # "terminal" 3x with 3 different commands (e.g. `which ss`,
        # `which netstat`, `which lsof` — genuinely varied progress,
        # not repetition) falsely tripped this. Now checks the full
        # name+arguments signature, matching the same cache-key shape
        # used elsewhere (_tool_confidence) — only the exact same call
        # repeated counts as being stuck.
        signatures = []
        for m in w:
            if m.get("role") == "assistant":
                try:
                    for tc in json.loads(m["content"]).get("tool_calls", []):
                        signatures.append(
                            f"{tc.get('name')}:{json.dumps(tc.get('arguments', {}), sort_keys=True)}"
                        )
                except Exception:
                    pass
        if len(signatures) >= 3 and len(set(signatures)) == 1:
            return True
        recent = agent._trace[-STUCK_MIN:]
        return len(recent) >= STUCK_MIN and all(not s["success"] for s in recent)

    async def _reflect(self, agent) -> str:
        failed = "\n".join(
            f"  {s['tool']} -> {s.get('error_type','?')}: {s['result'][:80]}"
            for s in agent._trace[-3:] if not s["success"]
        ) or "  (none)"
        r = await agent.llm.chat(
            system="Debug agent failures. DIAGNOSIS then NEXT STEP.",
            messages=[{"role": "user", "content":
                f"GOAL: {agent._goal}\n{agent._belief_summary()}\nFAILED:\n{failed}\n\nDIAGNOSIS:\nNEXT:"}],
        )
        return r.get("content", "Try completely different approach")

    def _can_extend_iterations(self, agent) -> bool:
        """
        Called only when about to hit the current iteration cap. Iteration
        count is an arbitrary proxy — `score_complexity()` can't know a
        plainly-worded task ("check open ports") needs several discovery
        calls, so budgeting by iteration count alone means either
        over-granting for genuinely simple tasks or hard-stopping
        legitimate ones partway through. The real questions are: is it
        still making progress, is it burning too much wall-clock time,
        and is it burning too many resources — not "has it called a tool
        N times". Extension is unlimited in *count*; these are the actual
        backstops.
        """
        if agent._phase in (Phase.DONE, Phase.FAILED):
            return False
        if agent._iteration >= ABSOLUTE_MAX_ITER:
            return False
        if time.time() - agent._run_start_time > MAX_RUNTIME_SECONDS:
            return False
        if getattr(agent.llm, "token_estimate", 0) - agent._tokens_before_run > MAX_TOKENS_PER_RUN:
            return False
        resource_ok, _ = agent._monitor.check()
        if not resource_ok:
            return False
        if not agent._trace:
            return False
        return any(t["success"] for t in agent._trace[-3:]) and not agent._stuck()