"""Base class and utilities for skill modules."""

from typing import Dict, List, Any, Optional
from enum import Enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict


class PentestPhase(Enum):
    """Pentest workflow phases"""
    SCAN = "scan"
    ENUMERATION = "enumeration"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post_exploitation"
    REPORTING = "reporting"
    UNKNOWN = "unknown"


@dataclass
class SkillInput:
    """Structured input for skill execution."""
    target: str
    context: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 60.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillOutput:
    """Structured output from skill execution."""
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    next_recommended_skills: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
        next_phase: Optional[str] = None,
        supports_async: bool = True,
        version: str = "1.0",
        author: str = "Penzer",
        priority: float = 0.5
    ):
        self.skill_id = skill_id
        self.name = name
        self.phase = phase
        self.description = description
        self.keywords = keywords
        self.mcp_tools = mcp_tools
        self.agent_behavior = agent_behavior
        self.next_phase = next_phase or "unknown"
        self.supports_async = supports_async
        self.version = version
        self.author = author
        self.priority = max(0.0, min(1.0, priority))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "type": "skill",
            "version": self.version,
            "author": self.author,
            "keywords": self.keywords,
            "mcp_tools": self.mcp_tools,
            "agent_behavior": self.agent_behavior,
            "next_phase": self.next_phase,
            "phase": self.phase.value,
            "supports_async": self.supports_async,
            "priority": self.priority
        }


class SkillModule(ABC):
    """Base class for skill modules."""
    
    phase: PentestPhase
    
    @classmethod
    @abstractmethod
    def get_skills(cls) -> List[Skill]:
        pass
    
    @classmethod
    async def execute_skill(cls, skill_id: str, skill_input: SkillInput) -> SkillOutput:
        return SkillOutput(
            success=False,
            error="Not implemented for base SkillModule"
        )
