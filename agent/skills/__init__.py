"""
Penzer Skill System

Core skills    → agent/skills/core/        always visible to agent

Generated skills are intentionally disabled in the runtime. The agent
relies on a fixed core skill set and planning behavior instead of
runtime-mutated skill definitions.
"""
from agent.skills.base   import Skill
from agent.skills.loader import load_all_skills
from agent.skills.search import build_context_from_history

__all__ = [
    "Skill",
    "load_all_skills",
    "build_context_from_history",
]