"""Markdown skill loader for PENZER skills

This module scans agent/skills for *.skill.md files with YAML frontmatter and
converts them into Skill objects for the agent to consume.
"""
from __future__ import annotations

import glob
import os
import yaml
from typing import List

from agent.skills.base import Skill, SkillModule, PentestPhase, SkillInput, SkillOutput


FRONT_MATTER_DELIM = "---"


def _parse_front_matter(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if text.startswith(FRONT_MATTER_DELIM):
        parts = text.split(FRONT_MATTER_DELIM)
        if len(parts) >= 3:
            meta_raw = parts[1]
            try:
                meta = yaml.safe_load(meta_raw) or {}
            except Exception:
                meta = {}
            return meta
    # Fallback: no front matter
    return {}


class MarkdownSkillModule(SkillModule):
    """Loads all *.skill.md files in the skills directory as Skill objects."""

    phase = PentestPhase.UNKNOWN

    @classmethod
    def get_skills(cls) -> List[Skill]:
        skills = []
        base_dir = os.path.dirname(__file__)
        pattern = os.path.join(base_dir, "*.skill.md")
        for path in glob.glob(pattern):
            meta = _parse_front_matter(path)
            if not meta:
                continue
            skill_id = meta.get("skill_id") or meta.get("id") or os.path.basename(path)
            name = meta.get("name") or skill_id
            phase_name = meta.get("phase", "unknown")
            # Map phase string to PentestPhase if possible
            phase = PentestPhase.__members__.get(phase_name.upper(), PentestPhase.UNKNOWN) if hasattr(PentestPhase, "__members__") else PentestPhase.UNKNOWN
            description = meta.get("description", "")
            keywords = meta.get("keywords", []) or []
            mcp_tools = meta.get("mcp_tools", []) or []
            agent_behavior = meta.get("agent_behavior", "")
            next_phase = meta.get("next_phase")
            priority = float(meta.get("priority", 0.5))
            version = meta.get("version", "1.0")
            author = meta.get("author", "Penzer")

            s = Skill(
                skill_id=str(skill_id),
                name=str(name),
                phase=phase,
                description=str(description),
                keywords=keywords,
                mcp_tools=mcp_tools,
                agent_behavior=str(agent_behavior),
                next_phase=next_phase,
                priority=priority,
                version=version,
                author=author,
            )
            skills.append(s)
        return skills
