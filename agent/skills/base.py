# agent/skills/base.py
"""Base class and utilities for skill modules."""

from typing import Dict, List, Any, Optional
from enum import Enum
from abc import ABC, abstractmethod


class PentestPhase(Enum):
    """Pentest workflow phases"""
    SCAN = "scan"
    ENUMERATION = "enumeration"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post_exploitation"
    REPORTING = "reporting"
    UNKNOWN = "unknown"


class Skill:
    """Represents a single pentest skill/capability."""
    
    def __init__(
        self,
        skill_id: str,
        name: str,
        phase: PentestPhase,
        description: str,
        keywords: List[str],
        mcp_tools: List[str],
        agent_behavior: str,
        next_phase: Optional[str] = None
    ):
        self.skill_id = skill_id
        self.name = name
        self.phase = phase
        self.description = description
        self.keywords = keywords
        self.mcp_tools = mcp_tools
        self.agent_behavior = agent_behavior
        self.next_phase = next_phase or "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert skill to dictionary for API usage."""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "type": "skill",
            "version": "latest",
            "keywords": self.keywords,
            "mcp_tools": self.mcp_tools,
            "agent_behavior": self.agent_behavior,
            "next_phase": self.next_phase
        }


class SkillModule(ABC):
    """Base class for phase-specific skill modules."""
    
    phase: PentestPhase
    
    @classmethod
    @abstractmethod
    def get_skills(cls) -> List[Skill]:
        """Return list of skills for this phase."""
        pass
