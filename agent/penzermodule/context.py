"""Conversation context assembly, bounds, and compaction."""

import logging
from typing import Any

from agent.config import KEEP_LAST, TRIM_AT


logger = logging.getLogger(__name__)
MAX_LLM_HISTORY_MESSAGES = 12
TRIM_SUMMARY_MAX_TOKENS = 256


async def trim_history(agent: Any) -> None:
    """Compact old turns; fall back to truncation after summarizer failures."""
    if agent._trimming or len(agent.history) <= TRIM_AT:
        return
    agent._trimming = True
    version = agent._history_version
    snapshot_len = len(agent.history)
    first = agent.history[:1]
    middle = agent.history[1:-KEEP_LAST]
    tail = agent.history[-KEEP_LAST:]
    if not middle:
        agent._trimming = False
        return

    summary_content = None
    try:
        if agent._trim_failures < 3:
            try:
                response = await agent.llm.chat(
                    system=(
                        f"Compress history. GOAL: {agent._goal}\n"
                        "Keep goal-relevant facts only. 2-3 sentences: what tried, "
                        "what worked, what still needed."
                    ),
                    messages=[{
                        "role": "user",
                        "content": "\n".join(
                            f"{message['role']}: {str(message.get('content', ''))[:100]}"
                            for message in middle
                        ),
                    }],
                    max_tokens=TRIM_SUMMARY_MAX_TOKENS,
                )
                content = response.get("content")
                if response.get("error") or not isinstance(content, str) or not content.strip():
                    raise ValueError("history summarizer returned no usable summary")
                summary_content = content.strip()
                agent._trim_failures = 0
            except Exception:
                agent._trim_failures += 1
                if agent._trim_failures == 3:
                    agent._record_step(
                        "trim",
                        "Summarizer failed 3 times in a row — falling back to plain truncation for the rest of this run.",
                        reason="compaction_failures",
                    )
    finally:
        agent._trimming = False

    if agent._history_version != version:
        logger.info(
            "Discarding a trim result — history moved on (a new run/resume started) while this trim was pending."
        )
        return
    appended = agent.history[snapshot_len:]
    if summary_content is not None:
        checkpoint = (
            f"[Checkpoint] Iteration {agent._iteration}: attempted "
            f"{' → '.join(trace['tool'] for trace in agent._trace[-5:])}, "
            f"progress: {agent._belief['goal_progress']}. Summary: {summary_content[:150]}"
        )
        agent.history = first + [{"role": "assistant", "content": checkpoint}] + tail + appended
    else:
        agent.history = first + tail + appended


def bounded_history(agent: Any) -> list[dict]:
    """Retain the original user goal and the newest conversation turns."""
    if len(agent.history) <= MAX_LLM_HISTORY_MESSAGES:
        return agent.history
    first_user = next((message for message in agent.history if message.get("role") == "user"), None)
    recent = agent.history[-MAX_LLM_HISTORY_MESSAGES:]
    return ([first_user] if first_user and first_user not in recent else []) + recent


def build_messages(agent: Any, step: int) -> list[dict]:
    if step == 0 or not agent._trace:
        return bounded_history(agent)
    latest = agent._trace[-1]
    recent = " -> ".join(
        f"{entry['tool']}({'ok' if entry['success'] else 'x'})"
        for entry in agent._trace[-5:]
    )
    skills_line = f"ACTIVE SKILLS: {', '.join(agent._matched_skills)}\n" if agent._matched_skills else ""
    plan_line = agent._skill_plan_summary()
    working_memory = agent._working_mem_summary()
    status = "ok" if latest["success"] else f"error: {latest['error_type']}"
    instruction = (
        f"[ReflAct {step}] GOAL: {agent._goal}\n{skills_line}"
        f"{plan_line + chr(10) if plan_line else ''}{agent._belief_summary()}\n"
        f"{working_memory + chr(10) if working_memory else ''}"
        f"LAST: {latest['tool']} -> {status} ({latest['elapsed_sec']}s) | {latest['result'][:100]}\n"
        f"RECENT: {recent}\n\nGiven belief state, working memory, and skill plan — execute next pending step."
    )
    return bounded_history(agent) + [{"role": "user", "content": instruction}]