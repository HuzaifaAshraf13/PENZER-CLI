# agent/skills/__init__.py
"""Skill modules for pentesting phases."""

from agent.skills.base import Skill, PentestPhase, SkillModule
from agent.skills.scan import ScanSkills
from agent.skills.enumeration import EnumerationSkills
from agent.skills.exploitation import ExploitationSkills
from agent.skills.post_exploitation import PostExploitationSkills
from agent.skills.reporting import ReportingSkills

# Map phases to their skill modules
PHASE_SKILL_MODULES = {
    PentestPhase.SCAN: ScanSkills,
    PentestPhase.ENUMERATION: EnumerationSkills,
    PentestPhase.EXPLOITATION: ExploitationSkills,
    PentestPhase.POST_EXPLOITATION: PostExploitationSkills,
    PentestPhase.REPORTING: ReportingSkills,
}


def load_skills_for_phase(phase: PentestPhase) -> list:
    """Load all skills for a given phase."""
    module = PHASE_SKILL_MODULES.get(phase)
    if module:
        return module.get_skills()
    return []


def load_all_skills() -> dict:
    """Load all skills across all phases."""
    all_skills = {}
    for phase, module in PHASE_SKILL_MODULES.items():
        all_skills[phase] = module.get_skills()
    return all_skills


__all__ = [
    "Skill",
    "PentestPhase",
    "SkillModule",
    "ScanSkills",
    "EnumerationSkills",
    "ExploitationSkills",
    "PostExploitationSkills",
    "ReportingSkills",
    "load_skills_for_phase",
    "load_all_skills",
]
