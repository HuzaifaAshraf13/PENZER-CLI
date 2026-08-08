"""PENZER — ReflectionManager

Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""
import asyncio, json, re, time, logging
from typing import Any

from session.memory import remember_semantic, store_insight, store_post_mortem
from agent.config import STUCK_MIN, ABSOLUTE_MAX_ITER, MAX_RUNTIME_SECONDS, MAX_TOKENS_PER_RUN

logger = logging.getLogger(__name__)

from agent.penzermodule.belief_manager import Phase

# Matches a leading/trailing ``` or ```json fence. Previously this used
# `.strip("```").lstrip("json")`, which strips any of the *characters*
# {`,j,s,o,n} from the string ends rather than the literal tokens —
# harmless for the common "```json\n[...]\n```" case, but wrong in
# principle (a differently-cased "```JSON" fence isn't stripped; real
# content that happens to start with those letters after fence removal
# gets silently eaten). A regex anchored to the actual fence syntax is
# correct instead of coincidentally-usually-correct.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class ReflectionManager:
    @staticmethod
    def _extract_json(text: str, default: str = "{}") -> Any:
        """Strip code-fence noise the LLM sometimes wraps JSON in, then parse.
        Used by the planner, replanner, completion evaluator, and post-mortem writer,
        which previously each repeated this cleanup inline."""
        raw = _FENCE_RE.sub("", (text or default).strip()).strip()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            try:
                return json.loads(default)
            except (json.JSONDecodeError, ValueError):
                # default itself was malformed — fall back to an empty
                # value of the shape the default implied, rather than
                # raising out of what every caller treats as a safe parse.
                return {} if default.strip().startswith("{") else []

    async def _evaluate_completion(self, agent, goal: str, result: str) -> tuple[bool | None, str]:
        """
        Returns (completed, reason). `completed` is a tri-state:
          True  — evaluator explicitly confirmed the goal was met
          False — evaluator explicitly confirmed it was not
          None  — evaluator timed out / errored / returned junk; unknown
        Previously any exception (including a timeout) returned `True`,
        i.e. a hung or erroring evaluator silently asserted success —
        exactly backwards for a check whose entire purpose is catching
        incomplete work. Callers must treat None as "don't know", not
        as either outcome; they should neither append an "incomplete"
        note nor treat the run as confirmed-complete.
        """
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
            if "completed" not in ev:
                return None, "evaluator response missing 'completed' field"
            return bool(ev["completed"]), ev.get("reason", "")
        except asyncio.TimeoutError:
            logger.warning("Completion evaluator timed out")
            return None, "evaluator timed out"
        except Exception as e:
            logger.debug("Evaluate completion: %s", e)
            return None, "evaluator unavailable"

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
        """
        Detects two kinds of stuck-ness: (a) the same tool result content
        repeating, or (b) the same tool+arguments signature repeating 3+
        times, within a small trailing window — plus a fallback of "the
        last STUCK_MIN trace entries all failed".

        The window is bounded by agent._resume_boundary_history_len (set
        alongside agent._resume_boundary_trace_len whenever a run is
        resumed) so this can't be fooled by stale pre-crash history.
        Without that bound, this used only `agent.history[-6:]`
        unconditionally — the caller's own gate
        (`len(trace) - resume_boundary_trace_len >= STUCK_MIN`) only
        protects *whether* _stuck() gets called, not *what it looks at*
        once called. Since one post-resume LLM turn can add several tool
        calls at once, that gate can open after a single new iteration,
        at which point history[-6:] could still contain 1-2 leftover
        pre-crash messages mixed with the new ones — exactly the false
        "stuck from a different context" scenario resume_last_task's own
        comment describes, just reintroduced on the history side instead
        of the trace side.
        """
        boundary = getattr(agent, "_resume_boundary_history_len", 0)
        w    = agent.history[boundary:][-6:]
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
                    entry = json.loads(m["content"])
                    # agent.py's _loop() writes each assistant turn's tool
                    # calls back into history using the SAME "tool"/"args"
                    # shape the system prompt teaches the model to
                    # produce (see system_prompts.py's OUTPUT FORMAT /
                    # Tool syntax sections) — not the internal
                    # "name"/"arguments" normalization llm.py uses
                    # between itself and agent.py. Reading the wrong key
                    # pair here doesn't error, it just silently returns
                    # an empty signatures list every time, so this
                    # specific repeated-call check never fires — it
                    # degrades to relying only on the "all recent trace
                    # entries failed" fallback below, missing genuine
                    # stuck-in-a-loop cases that haven't outright failed
                    # yet (e.g. re-running the same successful-but-
                    # useless command over and over). Accept both key
                    # pairs defensively — "tool"/"args" (current,
                    # canonical) and "name"/"arguments" (older history
                    # entries written before this fix, or a model that
                    # drifted into echoing the internal shape) — so this
                    # keeps working across both old and new transcripts.
                    for tc in entry.get("tools", []):
                        name = tc.get("tool") or tc.get("name") or ""
                        args = tc.get("args") if "args" in tc else tc.get("arguments", {})
                        signatures.append(f"{name}:{json.dumps(args, sort_keys=True)}")
                except Exception:
                    pass
        if len(signatures) >= 3 and len(set(signatures)) == 1:
            return True
        # Already safe without a boundary check: the caller's gate
        # (len(trace) - resume_boundary_trace_len >= STUCK_MIN) guarantees
        # the trailing STUCK_MIN trace entries are entirely post-resume by
        # construction, unlike the history-based checks above.
        recent = agent._trace[-STUCK_MIN:]
        return len(recent) >= STUCK_MIN and all(not s["success"] for s in recent)

    async def _reflect(self, agent) -> str:
        failed = "\n".join(
            f"  {s['tool']} -> {s.get('error_type','?')}: {s['result'][:80]}"
            for s in agent._trace[-3:] if not s["success"]
        ) or "  (none)"
        try:
            r = await asyncio.wait_for(
                agent.llm.chat(
                    system="Debug agent failures. DIAGNOSIS then NEXT STEP.",
                    messages=[{"role": "user", "content":
                        f"GOAL: {agent._goal}\n{agent._belief_summary()}\nFAILED:\n{failed}\n\nDIAGNOSIS:\nNEXT:"}],
                ),
                timeout=15,
            )
            return r.get("content", "Try completely different approach")
        except asyncio.TimeoutError:
            logger.warning("_reflect timed out after 15s")
            return "Reflection timed out — try a completely different approach."
        except Exception as e:
            logger.debug("Reflect: %s", e)
            return "Try a completely different approach."

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
        # FIX: agent._monitor.check() is documented elsewhere in this
        # codebase (agent.py's _check_stop_conditions, a few lines after
        # this method is called) as a call that CAN raise — that's
        # exactly why that other call site wraps it in try/except and
        # reports a specific "Resource limit: Resource monitor
        # unavailable (...)" message. This call site called the same
        # method completely unguarded. _can_extend_iterations() is itself
        # called unguarded from _check_stop_conditions (there's no
        # try/except around `if self._can_extend_iterations():`), so a
        # transient monitor failure here — psutil losing track of a
        # process, a permissions hiccup, whatever the other call site is
        # defending against — would propagate all the way up through
        # _loop() and only get caught by _run_loop_safely()'s generic
        # top-level handler, ending the run with an opaque "Stopped:
        # internal error (...)" instead of the same graceful, specific
        # message the sibling call site already produces for this exact
        # failure. Fail closed here too: if we can't confirm resources
        # are OK, don't extend — the normal iteration-limit stop message
        # fires cleanly instead of a raw exception derailing the run.
        try:
            resource_ok, _ = agent._monitor.check()
        except Exception as exc:
            logger.warning("Resource monitor check failed during extension check: %s", exc)
            return False
        if not resource_ok:
            return False
        if not agent._trace:
            return False
        return any(t["success"] for t in agent._trace[-3:]) and not agent._stuck()