"""
Penzer Skill System

Core skills     → agent/skills/core/        always visible to agent
Generated skills → agent/skills/generated/  searched per request, pruned after 30 days
"""
from agent.skills.base   import Skill
from agent.skills.loader import (
    load_all_skills,
    save_generated_skill,
    delete_generated_skill,
)
from agent.skills.search import search_generated_skills, build_context_from_history

__all__ = [
    "Skill",
    "load_all_skills",
    "save_generated_skill",
    "delete_generated_skill",
    "search_generated_skills",
    "build_context_from_history",
]