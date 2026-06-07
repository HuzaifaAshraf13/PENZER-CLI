"""Semantic search for relevant skills based on user request and context."""
from typing import List, Dict, Tuple
from agent.skills.base import Skill
import logging

logger = logging.getLogger(__name__)


def semantic_search_skills(
    user_request: str,
    available_skills: List[Skill],
    memory: Dict = None,
    top_k: int = 3,
    context: str = ""
) -> List[Skill]:
    """
    Find relevant skills for a request. Searches against user request + live context
    so skills evolve as the task progresses across iterations.

    Args:
        user_request: The original user request
        available_skills: All available Skill objects
        memory: Optional memory dict for context
        top_k: Max skills to return (no hard cap — returns all scoring above threshold)
        context: Live context from current iteration (tool results, history summary)
    """
    if not available_skills:
        return []

    # Combine request + live context for richer matching
    combined = f"{user_request} {context}".lower()
    tokens = set(combined.split())

    skill_scores: List[Tuple[Skill, float]] = []

    for skill in available_skills:
        score = 0.0

        # Exact name match
        if skill.name.lower() in combined:
            score += 10.0

        # Keyword matches against combined context
        if skill.keywords:
            score += sum(3.0 for kw in skill.keywords if kw.lower() in combined)

        # Description token matches
        desc_lower = skill.description.lower()
        score += sum(0.5 for t in tokens if len(t) > 2 and t in desc_lower)

        # Priority tiebreaker
        score += skill.priority * 0.1

        if score > 0:
            skill_scores.append((skill, score))

    skill_scores.sort(key=lambda x: x[1], reverse=True)

    # Return all skills above threshold OR top_k minimum — whichever is more
    threshold = 1.5
    above = [s for s, sc in skill_scores if sc >= threshold]
    result = above if len(above) >= top_k else [s for s, _ in skill_scores[:top_k]]

    if result:
        logger.info(f"Semantic search found {len(result)} relevant skills for request")

    return result


def build_context_from_history(history: list, last_n: int = 6) -> str:
    """Extract recent tool results and assistant thoughts as context for skill search."""
    context_parts = []
    for msg in history[-last_n:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str) and role in ["tool", "assistant"]:
            context_parts.append(content[:300])
    return " ".join(context_parts)


def format_relevant_skills_for_prompt(skills: List[Skill]) -> str:
    if not skills:
        return "No relevant skills found — reason through the task and create a skill after."

    lines = ["RELEVANT SKILLS:"]
    for i, skill in enumerate(skills, 1):
        lines.append(f"\n{i}. {skill.name}")
        lines.append(f"   {skill.description}")
        if skill.agent_behavior:
            lines.append(f"   Guidance: {skill.agent_behavior}")
        if skill.mcp_tools:
            lines.append(f"   Tools: {', '.join(skill.mcp_tools)}")

    return "\n".join(lines)