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
    if not available_skills:
        return []

    combined = f"{user_request} {context}".lower()
    tokens = set(combined.split())
    skill_scores: List[Tuple[Skill, float]] = []

    for skill in available_skills:
        score = 0.0
        if skill.name.lower() in combined:
            score += 10.0
        if skill.keywords:
            score += sum(3.0 for kw in skill.keywords if kw.lower() in combined)
        desc_lower = skill.description.lower()
        score += sum(0.5 for t in tokens if len(t) > 2 and t in desc_lower)
        score += skill.priority * 0.1
        if score > 0:
            skill_scores.append((skill, score))

    skill_scores.sort(key=lambda x: x[1], reverse=True)

    threshold = 1.5
    result = [s for s, sc in skill_scores if sc >= threshold]

    if result:
        logger.info(f"Semantic search found {len(result)} relevant skills")
    else:
        logger.debug("No matching skills — agent will reason directly")

    return result


def build_context_from_history(history: list, last_n: int = 6) -> str:
    """Extract recent messages as context — handles both str and dict content."""
    context_parts = []
    for msg in history[-last_n:]:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role not in ["tool", "assistant"]:
            continue

        if isinstance(content, str):
            context_parts.append(content[:300])
        elif isinstance(content, dict):
            # Extract thought or content from dict responses
            text = content.get("thought") or content.get("content", "")
            if text:
                context_parts.append(str(text)[:300])

    return " ".join(context_parts)


def format_relevant_skills_for_prompt(skills: List[Skill]) -> str:
    if not skills:
        return ""
    lines = ["RELEVANT SKILLS:"]
    for i, skill in enumerate(skills, 1):
        lines.append(f"\n{i}. {skill.name}")
        lines.append(f"   {skill.description}")
        if skill.agent_behavior:
            lines.append(f"   Guidance: {skill.agent_behavior}")
        if skill.mcp_tools:
            lines.append(f"   Tools: {', '.join(skill.mcp_tools)}")
    return "\n".join(lines)