"""Skill dataclass for Penzer."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Skill:
    skill_id:       str
    name:           str
    description:    str
    keywords:       List[str]
    mcp_tools:      List[str]
    agent_behavior: str
    priority:       float = 0.5
    core:           bool  = False
    version:        str   = "1.0"
    generated_at:   Optional[str] = None