"""
Skill Selection System - Phase-Specific Pentest Skills
Implements pentest workflow stages: Scan → Enumerate → Exploit → Post-Exploit → Report

Features:
- Phase detection via keyword matching
- Dynamic skill prioritization with confidence scores
- Support for both Skill objects and legacy dict format
- Skill ranking based on relevance and priority
"""

import json
import os
import re
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# Import unified PentestPhase enum from modular skills system
from agent.skills import PentestPhase

logger = logging.getLogger("penzer.skill_selector")


@dataclass
class SkillScore:
    """Represents a skill with its relevance score and metadata."""
    skill: Any
    relevance_score: float  # 0.0-1.0
    priority_score: float   # 0.0-1.0 from skill.priority
    confidence: float       # Combined confidence score
    keywords_matched: List[str]
    
    def __lt__(self, other):
        """For sorting by confidence (descending)."""
        return self.confidence > other.confidence


class PentestPhaseDetector:
    """
    Detects which pentest phase the user request belongs to.
    Uses keyword matching on request intent with weighted scoring.
    """
    
    # Phase-specific keywords with weights
    PHASE_KEYWORDS = {
        PentestPhase.SCAN: [
            "scan", "nmap", "host discovery", "reconnaissance", "recon", 
            "port scan", "ping", "network mapping", "shodan", "masscan"
        ],
        PentestPhase.ENUMERATION: [
            "enumerate", "service", "version", "detect", "ldap", "smb", "web", 
            "directory", "banner", "fingerprint", "active directory", "services"
        ],
        PentestPhase.EXPLOITATION: [
            "exploit", "cve", "vulnerability", "execute", "payload", "shell", 
            "metasploit", "poc", "rce", "compromise"
        ],
        PentestPhase.POST_EXPLOITATION: [
            "privilege escalation", "privesc", "pivot", "lateral", "persistence", 
            "exfiltrate", "data", "escalate", "movement"
        ],
        PentestPhase.REPORTING: [
            "report", "findings", "remediation", "summary", "documentation", 
            "summary", "conclusion", "risk"
        ],
    }
    
    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """Extract keywords from text, preserving multi-word phrases."""
        text_lower = text.lower()
        keywords = []
        
        # First, check for multi-word phrases from PHASE_KEYWORDS
        for phase, phrases in PentestPhaseDetector.PHASE_KEYWORDS.items():
            for phrase in phrases:
                if len(phrase.split()) > 1 and phrase in text_lower:
                    keywords.append(phrase)
        
        # Then add single words
        words = re.findall(r'\b\w+\b', text_lower)
        keywords.extend(words)
        
        return list(set(keywords))  # Remove duplicates
    
    @staticmethod
    def detect_phase(user_request: str, current_phase: Optional[PentestPhase] = None) -> Tuple[PentestPhase, float]:
        """
        Detect which pentest phase the request is for.
        Uses keyword matching but respects context.
        
        Args:
            user_request: The user's request text
            current_phase: The current phase context (optional)
            
        Returns:
            Tuple of (detected_phase, confidence_score 0.0-1.0)
        """
        request_lower = user_request.lower()
        keywords = PentestPhaseDetector.extract_keywords(user_request)
        
        # Score each phase based on keyword matches
        phase_scores = {}
        for phase, phase_keywords in PentestPhaseDetector.PHASE_KEYWORDS.items():
            # Count matches, giving more weight to phrase matches
            matches = 0
            for kw in phase_keywords:
                if kw in request_lower:
                    matches += 2 if len(kw.split()) > 1 else 1  # Bonus for phrases
            phase_scores[phase] = matches
        
        # Find phases with matches
        best_phase, best_score = max(phase_scores.items(), key=lambda x: x[1])
        
        # Calculate confidence (0.0-1.0)
        max_possible = max(len(kws) * 2 for kws in PentestPhaseDetector.PHASE_KEYWORDS.values())
        confidence = min(1.0, best_score / max(max_possible, 1))
        
        # If no clear match (score == 0) and we have context, stay in current phase
        if best_score == 0 and current_phase and current_phase != PentestPhase.UNKNOWN:
            logger.debug(f"No phase match, using current phase: {current_phase.value}")
            return current_phase, 0.5
        
        # If match found, return it
        if best_score > 0:
            logger.debug(f"Detected phase: {best_phase.value} (confidence: {confidence:.2f})")
            return best_phase, confidence
        
        # Default to current phase if available, else UNKNOWN
        default_phase = current_phase if current_phase and current_phase != PentestPhase.UNKNOWN else PentestPhase.UNKNOWN
        return default_phase, 0.3


class SkillSelector:
    """
    Selects the most relevant skill for a given user request and phase.
    Implements dynamic prioritization with confidence scores and relevance ranking.
    Works with modular skill system (Dict[PentestPhase, List[Skill]])
    """
    
    def __init__(self, all_skills: Dict[PentestPhase, List[Any]]):
        """
        Initialize with modular skills dictionary from agent.skills.load_all_skills()
        
        Args:
            all_skills: Dict mapping PentestPhase to List[Skill] objects with new metadata
        """
        self.all_skills_by_phase = all_skills
        
        # Flatten for quick lookup: skill_id -> Skill
        self.all_skills_flat = {}
        skill_count = 0
        for phase, skills in all_skills.items():
            for skill in skills:
                skill_id = skill.skill_id if hasattr(skill, 'skill_id') else skill.get('skill_id')
                self.all_skills_flat[skill_id] = skill
                skill_count += 1
        
        logger.info(f"SkillSelector initialized with {skill_count} total skills")
    
    def score_skill_relevance(self, skill: Any, keywords: List[str]) -> Tuple[float, List[str]]:
        """
        Score how relevant a skill is to the request keywords.
        Works with both old dict format and new Skill objects with priority support.
        
        Args:
            skill: Skill object or dict
            keywords: List of keywords from request
            
        Returns:
            Tuple of (relevance_score 0.0-1.0, matched_keywords)
        """
        score = 0.0
        matched_keywords = []
        
        # Support both old dict format and new Skill objects
        if hasattr(skill, 'description'):  # New Skill object
            skill_text = (skill.description + " " + skill.name).lower()
            skill_keywords = skill.keywords
        else:  # Old dict format
            skill_text = (skill.get("description", "") + " " + skill.get("name", "")).lower()
            skill_keywords = skill.get("keywords", [])
        
        # Direct keyword matches (highest weight)
        for kw in skill_keywords:
            if kw in keywords or any(kw in k for k in keywords):
                score += 0.7
                matched_keywords.append(kw)
            elif kw in skill_text:
                score += 0.3
        
        # Substring matches in description (lower weight)
        for keyword in keywords:
            if keyword in skill_text and keyword not in matched_keywords:
                score += 0.1
                matched_keywords.append(keyword)
        
        # Normalize to 0-1 range
        if len(skill_keywords) > 0:
            score = min(1.0, score / len(skill_keywords))
        
        logger.debug(f"Skill relevance: {self._skill_name(skill)} = {score:.2f} (matched: {matched_keywords})")
        return score, matched_keywords
    
    def _skill_name(self, skill: Any) -> str:
        """Extract skill name safely."""
        if hasattr(skill, 'name'):
            return skill.name
        elif isinstance(skill, dict):
            return skill.get('name', 'Unknown')
        return str(skill)
    
    def _skill_priority(self, skill: Any) -> float:
        """Extract skill priority (0.0-1.0)."""
        if hasattr(skill, 'priority'):
            return max(0.0, min(1.0, skill.priority))
        elif isinstance(skill, dict):
            return max(0.0, min(1.0, skill.get('priority', 0.5)))
        return 0.5
    
    def select_skill_for_phase(self, phase: PentestPhase, user_request: str) -> Optional[Tuple[Any, float]]:
        """
        Select the best skill for the user request within the detected phase.
        Uses relevance + priority scoring for ranking.
        
        Returns:
            Tuple of (selected_skill, confidence_score) or None if no skills available
        """
        phase_skills = self.all_skills_by_phase.get(phase, [])
        if not phase_skills:
            logger.warning(f"No skills available for phase: {phase.value}")
            return None
        
        keywords = PentestPhaseDetector.extract_keywords(user_request)
        logger.debug(f"Scoring {len(phase_skills)} skills for phase {phase.value}")
        
        # Score all skills in the phase
        scored_skills: List[SkillScore] = []
        
        for skill in phase_skills:
            relevance_score, matched_kws = self.score_skill_relevance(skill, keywords)
            priority_score = self._skill_priority(skill)
            
            # Combined confidence: 70% relevance + 30% priority
            confidence = (relevance_score * 0.7) + (priority_score * 0.3)
            
            scored_skills.append(SkillScore(
                skill=skill,
                relevance_score=relevance_score,
                priority_score=priority_score,
                confidence=confidence,
                keywords_matched=matched_kws
            ))
        
        # Sort by confidence descending
        scored_skills.sort()
        
        if scored_skills:
            best = scored_skills[0]
            logger.info(
                f"Selected skill: {self._skill_name(best.skill)} "
                f"(confidence: {best.confidence:.2f}, relevance: {best.relevance_score:.2f})"
            )
            return best.skill, best.confidence
        
        # Fallback to first skill if no scoring available
        logger.warning(f"No good skill match found, using first available skill")
        return phase_skills[0], 0.3
    
    def select_skill(self, user_request: str) -> Tuple[Optional[Any], PentestPhase, float]:
        """
        Select the best skill for the user request (full workflow).
        Works with modular skill system with enhanced scoring.
        
        Returns:
            Tuple of (selected_skill_object, detected_phase, confidence_score)
        """
        # 1. Detect phase with confidence
        phase, phase_confidence = PentestPhaseDetector.detect_phase(user_request)
        
        # 2. Select best skill for that phase
        skill_result = self.select_skill_for_phase(phase, user_request)
        
        if skill_result:
            skill, skill_confidence = skill_result
            # Combine phase and skill confidence
            combined_confidence = (phase_confidence + skill_confidence) / 2
            return skill, phase, combined_confidence
        
        # 3. Fallback: if no clear phase detected, use reporting skill
        if phase == PentestPhase.UNKNOWN or not skill_result:
            logger.warning(f"Phase detection unclear, falling back to reporting skills")
            reporting_skills = self.all_skills_by_phase.get(PentestPhase.REPORTING, [])
            if reporting_skills:
                return reporting_skills[0], PentestPhase.REPORTING, 0.3
        
        return None, phase, 0.0
    
    def skill_to_dict(self, skill: Any) -> Dict[str, Any]:
        """
        Convert Skill object to dict format for backward compatibility.
        Handles both new Skill objects (with priority/version/author) and old dict format.
        
        Returns:
            Dictionary representation of skill with all metadata
        """
        if hasattr(skill, 'to_dict'):
            result = skill.to_dict()
        elif isinstance(skill, dict):
            result = skill.copy()
        else:
            # Fallback: create dict from attributes
            result = {
                "skill_id": getattr(skill, 'skill_id', 'unknown'),
                "name": getattr(skill, 'name', 'Unknown'),
                "description": getattr(skill, 'description', ''),
                "type": "skill",
                "version": getattr(skill, 'version', '1.0'),
                "author": getattr(skill, 'author', 'Penzer'),
                "keywords": getattr(skill, 'keywords', []),
                "mcp_tools": getattr(skill, 'mcp_tools', []),
                "agent_behavior": getattr(skill, 'agent_behavior', ''),
                "next_phase": getattr(skill, 'next_phase', 'unknown'),
                "priority": getattr(skill, 'priority', 0.5),
                "phase": getattr(skill, 'phase', PentestPhase.UNKNOWN).value if hasattr(getattr(skill, 'phase', None), 'value') else 'unknown',
                "supports_async": getattr(skill, 'supports_async', True)
            }
        
        # Ensure all new fields are present
        defaults = {
            "type": "skill",
            "version": "1.0",
            "author": "Penzer",
            "priority": 0.5,
            "supports_async": True
        }
        for key, default_val in defaults.items():
            if key not in result:
                result[key] = default_val
        
        return result
    
    def get_skills_by_phase(self, phase: PentestPhase) -> List[Dict[str, Any]]:
        """Get all skills for a phase as dicts."""
        phase_skills = self.all_skills_by_phase.get(phase, [])
        return [self.skill_to_dict(skill) for skill in phase_skills]


# Exported function for creating skill-aware system prompts
def create_skill_aware_system_prompt(current_skill: Dict[str, Any], available_tools: Dict[str, Any] = None, base_context: str = "") -> str:
    """
    Create a system prompt that gives LLM full tool access and autonomy.
    
    Args:
        current_skill: Skill dict from skill_to_dict() (can be None for full autonomy)
        available_tools: Dict of all available tools {name: {args: [...]}}
        base_context: Additional context to include
        
    Returns:
        Formatted system prompt string
    """
    # Build tools reference for LLM
    tools_reference = ""
    if available_tools:
        tools_reference = "\n## AVAILABLE TOOLS:\n"
        for tool_name, tool_info in available_tools.items():
            args = ", ".join(tool_info.get("args", []))
            tools_reference += f"- {tool_name}({args})\n"
    
    if not current_skill:
        prompt = f"""You are Penzer, an autonomous pentesting agent with full autonomy.

You have complete access to the system. Analyze the user's request and decide the best course of action.
You can use ANY available tool based on your judgment.

{tools_reference}

USER REQUEST ANALYSIS:
- Understand what the user is asking
- Decide which tools are most appropriate
- Execute tools in logical sequence
- Provide clear findings

{base_context}

RESPONSE FORMAT (ONLY valid JSON):
{{"thought": "your analysis", "tool": "tool_name", "args": {{...}}, "final_answer": "..."}}

Rules:
- tool: name of tool to use (optional if providing final_answer)
- args: dictionary of arguments for the tool (required if tool is specified)
- final_answer: provide when task is complete (optional)
- Always include "thought" explaining your reasoning
"""
    else:
        phase = current_skill.get('phase', 'unknown').upper()
        skill_name = current_skill.get('name', 'Unknown')
        
        prompt = f"""You are Penzer, an autonomous pentesting agent.

CURRENT PHASE: {phase}
CURRENT SKILL: {skill_name}

You have full access to all available tools. Use your judgment to accomplish the task.
{current_skill.get('agent_behavior', '')}

{tools_reference}

{base_context}

RESPONSE FORMAT (ONLY valid JSON):
{{"thought": "your analysis", "tool": "tool_name", "args": {{...}}, "final_answer": "..."}}

Rules:
- Use any tool you deem necessary based on the task
- tool: name of tool to use (optional if providing final_answer)
- args: dictionary of arguments for the tool (required if tool is specified)
- final_answer: provide when task is complete (optional)
- Always include "thought" explaining your reasoning
"""
    
    return prompt

