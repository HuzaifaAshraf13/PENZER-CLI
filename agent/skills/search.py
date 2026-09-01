"""
Skill search — DEPRECATED. Generated skills are disabled in the runtime.

This module is kept for backward compatibility only.
"""
from typing import List, Dict, Tuple
from agent.skills.base import Skill
import logging

logger = logging.getLogger(__name__)


def search_generated_skills(
    user_request: str,
    generated:    List[Skill],
    top_k:        int  = 3,
    context:      str  = "",
) -> List[Skill]:
    """Search generated skills by relevance. Core skills skip this."""
    if not generated:
        return []

    combined = f"{user_request} {context}".lower()
    tokens   = set(combined.split())
    scored: List[Tuple[Skill, float]] = []

    for skill in generated:
        score = 0.0
        if skill.name.lower() in combined:
            score += 15.0
        for kw in skill.keywords:
            if kw.lower() in combined:
                score += 5.0
        desc_tokens = set(skill.description.lower().split())
        score += len(tokens & desc_tokens) * 1.0
        score += skill.priority * 2.0
        if score > 1.0:
            scored.append((skill, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [s for s, _ in scored[:top_k]]

    if result:
        logger.info(f"Generated skills matched: {[s.name for s in result]}")

    return result


def build_context_from_history(history: list, last_n: int = 6) -> str:
    parts = []
    for msg in history[-last_n:]:
        role    = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ["tool", "assistant"]:
            continue
        if isinstance(content, str):
            parts.append(content[:300])
        elif isinstance(content, dict):
            text = content.get("answer") or content.get("thought") or ""
            if text:
                parts.append(str(text)[:300])
    return " ".join(parts)