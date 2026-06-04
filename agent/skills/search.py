"""Semantic search for relevant skills based on user request and context."""

from typing import List, Dict, Tuple
from agent.skills.base import Skill
import logging

logger = logging.getLogger(__name__)


def semantic_search_skills(
    user_request: str,
    available_skills: List[Skill],
    memory: Dict = None,
    top_k: int = 3
) -> List[Skill]:
    """
    Perform semantic search to find the top-k most relevant skills for a user request.
    
    Uses simple keyword matching on skill metadata (keywords, description, name).
    Can be extended with embedding-based similarity if needed.
    
    Args:
        user_request: The user's pentesting request
        available_skills: List of available Skill objects
        memory: Optional memory dictionary for context
        top_k: Number of top skills to return
        
    Returns:
        List of top-k most relevant skills sorted by relevance score
    """
    if not available_skills:
        logger.warning("No skills available for semantic search")
        return []
    
    # Normalize request text for matching
    request_lower = user_request.lower()
    request_tokens = set(request_lower.split())
    
    # Score each skill based on relevance to the request
    skill_scores: List[Tuple[Skill, float]] = []
    
    for skill in available_skills:
        score = 0.0
        
        # 1. Exact name match (highest weight)
        if skill.name.lower() in request_lower:
            score += 10.0
        
        # 2. Keywords match (medium weight)
        if skill.keywords:
            keyword_matches = sum(1 for kw in skill.keywords if kw.lower() in request_lower)
            score += keyword_matches * 3.0
        
        # 3. Description contains request tokens (lower weight)
        description_lower = skill.description.lower()
        token_matches = sum(1 for token in request_tokens if token in description_lower and len(token) > 2)
        score += token_matches * 0.5
        
        # 4. Priority as tiebreaker
        score += skill.priority * 0.1
        
        if score > 0:
            skill_scores.append((skill, score))
    
    # Sort by score descending
    skill_scores.sort(key=lambda x: x[1], reverse=True)
    
    # Return top-k skills
    result = [skill for skill, _ in skill_scores[:top_k]]
    
    if result:
        logger.info(f"Semantic search found {len(result)} relevant skills for request")
        for i, skill in enumerate(result, 1):
            logger.debug(f"  {i}. {skill.name} (id={skill.skill_id})")
    
    return result


def format_relevant_skills_for_prompt(skills: List[Skill]) -> str:
    """
    Format a list of relevant skills into prompt-friendly text.
    
    Args:
        skills: List of Skill objects to format
        
    Returns:
        Formatted string suitable for injection into system/user prompts
    """
    if not skills:
        return "No relevant skills identified. Perform basic reconnaissance."
    
    lines = ["RELEVANT SKILLS FOR THIS REQUEST:"]
    for i, skill in enumerate(skills, 1):
        lines.append(f"\n{i}. {skill.name} (skill_id: {skill.skill_id})")
        lines.append(f"   Description: {skill.description}")
        if skill.keywords:
            lines.append(f"   Keywords: {', '.join(skill.keywords)}")
        if skill.agent_behavior:
            lines.append(f"   Guidance: {skill.agent_behavior}")
        if skill.mcp_tools:
            lines.append(f"   Tools: {', '.join(skill.mcp_tools)}")
    
    return "\n".join(lines)
