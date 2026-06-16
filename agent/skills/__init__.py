# agent/skills/__init__.py
"""Skill modules for Penzer."""
from agent.skills.base import Skill, PentestPhase, SkillModule
from agent.skills.search import load_skills_from_markdown, semantic_search_skills

# Load all skills from *.skill.md files at startup — single source of truth
ALL_SKILLS: list[Skill] = load_skills_from_markdown()


def load_skills_for_phase(phase: PentestPhase) -> list[Skill]:
    """Return skills matching a specific phase."""
    return [s for s in ALL_SKILLS if s.phase == phase]


def load_all_skills() -> list[Skill]:
    """Return all loaded skills."""
    return ALL_SKILLS


__all__ = [
    "Skill",
    "PentestPhase",
    "SkillModule",
    "ALL_SKILLS",
    "load_skills_for_phase",
    "load_all_skills",
    "semantic_search_skills",
]