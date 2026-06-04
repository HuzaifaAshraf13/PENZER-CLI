# agent/skills/__init__.py
"""Skill modules for pentesting phases."""

from agent.skills.base import Skill, PentestPhase, SkillModule

# Legacy prebuilt modules are intentionally not registered by default.
# The MarkdownSkillModule loads all .skill.md definitions as Skill objects.
from agent.skills.md_loader import MarkdownSkillModule

# Register a single loader that sources skills from markdown files.
PHASE_SKILL_MODULES = {
    PentestPhase.UNKNOWN: MarkdownSkillModule,
}


def load_skills_for_phase(phase: PentestPhase) -> list:
    """Load all skills for a given phase."""
    module = PHASE_SKILL_MODULES.get(phase)
    if module:
        return module.get_skills()
    return []


def load_all_skills() -> dict:
    """Load all skills across all phases."""
    # Return a dict with a single key (UNKNOWN) containing all markdown skills
    all_skills = {}
    for phase, module in PHASE_SKILL_MODULES.items():
        all_skills[phase] = module.get_skills()
    return all_skills


__all__ = [
    "Skill",
    "PentestPhase",
    "SkillModule",
    "MarkdownSkillModule",
    "load_skills_for_phase",
    "load_all_skills",
]
