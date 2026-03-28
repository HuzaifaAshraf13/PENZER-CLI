"""
Claude Agent Skills System - Phase-Specific Pentest Skills
Implements pentest workflow stages: Scan → Enumerate → Exploit → Post-Exploit → Report
"""

import json
import os
import re
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum

# Import unified PentestPhase enum from modular skills system
from agent.skills import PentestPhase


class PentestPhaseDetector:
    """
    Detects which pentest phase the user request belongs to.
    Uses keyword matching on request intent.
    """
    
    # Phase-specific keywords
    PHASE_KEYWORDS = {
        PentestPhase.SCAN: ["scan", "nmap", "host discovery", "reconnaissance", "recon", "port scan", "ping"],
        PentestPhase.ENUMERATION: ["enumerate", "service", "version", "detect", "ldap", "smb", "web", "directory", "banner"],
        PentestPhase.EXPLOITATION: ["exploit", "cve", "vulnerability", "execute", "payload", "shell"],
        PentestPhase.POST_EXPLOITATION: ["privilege escalation", "privesc", "pivot", "lateral", "persistence", "exfiltrate", "data"],
        PentestPhase.REPORTING: ["report", "findings", "remediation", "summary", "documentation"],
    }
    
    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """Extract keywords from text."""
        words = re.findall(r'\b\w+\b', text.lower())
        return words
    
    @staticmethod
    def detect_phase(user_request: str, current_phase: PentestPhase = None) -> PentestPhase:
        """
        Detect which pentest phase the request is for.
        Uses keyword matching but respects context.
        
        Args:
            user_request: The user's request text
            current_phase: The current phase context (optional)
            
        Returns:
            PentestPhase enum value
        """
        request_lower = user_request.lower()
        keywords = PentestPhaseDetector.extract_keywords(user_request)
        
        # Score each phase based on keyword matches
        phase_scores = {}
        for phase, phase_keywords in PentestPhaseDetector.PHASE_KEYWORDS.items():
            matches = sum(1 for kw in phase_keywords if kw in request_lower)
            phase_scores[phase] = matches
        
        # Find phases with matches
        best_phase, best_score = max(phase_scores.items(), key=lambda x: x[1])
        
        # If no clear match (score == 0) and we have context, stay in current phase
        if best_score == 0 and current_phase and current_phase != PentestPhase.UNKNOWN:
            return current_phase
        
        # If match found, return it
        if best_score > 0:
            return best_phase
        
        # Default to current phase if available, else UNKNOWN
        return current_phase if current_phase and current_phase != PentestPhase.UNKNOWN else PentestPhase.UNKNOWN


class SkillSelector:
    """
    Selects the most relevant skill for a given user request and phase.
    Implements Claude Agent Skills API pattern with on-demand instruction loading.
    Works with modular skill system (Dict[PentestPhase, List[Skill]])
    """
    
    def __init__(self, all_skills: Dict[PentestPhase, List[Any]]):
        """
        Initialize with modular skills dictionary from agent.skills.load_all_skills()
        
        Args:
            all_skills: Dict mapping PentestPhase to List[Skill] objects
        """
        self.all_skills_by_phase = all_skills
        # Flatten for quick lookup: skill_id -> Skill
        self.all_skills_flat = {}
        for phase, skills in all_skills.items():
            for skill in skills:
                skill_id = skill.skill_id if hasattr(skill, 'skill_id') else skill.get('skill_id')
                self.all_skills_flat[skill_id] = skill
    
    def score_skill_relevance(self, skill: Any, keywords: List[str]) -> float:
        """
        Score how relevant a skill is to the request keywords.
        Works with both old dict format and new Skill objects.
        
        Returns:
            Score 0-1 where 1 is perfect match
        """
        score = 0.0
        
        # Support both old dict format and new Skill objects
        if hasattr(skill, 'description'):  # New Skill object
            skill_text = (skill.description + " " + skill.name).lower()
            skill_keywords = skill.keywords
        else:  # Old dict format
            skill_text = (skill.get("description", "") + " " + skill.get("name", "")).lower()
            skill_keywords = skill.get("keywords", [])
        
        keyword_set = set(keywords)
        
        # Direct keyword matches (highest weight)
        for kw in skill_keywords:
            if kw in keyword_set or any(kw in k for k in keywords):
                score += 0.7
        
        # Substring matches in description
        for keyword in keywords:
            if keyword in skill_text:
                score += 0.1
        
        # Normalize
        if len(skill_keywords) > 0:
            score = min(1.0, score / len(skill_keywords))
        
        return score
    
    def select_skill_for_phase(self, phase: PentestPhase, user_request: str) -> Optional[Any]:
        """
        Select the best skill for the user request within the detected phase.
        Works with modular skill system.
        
        Returns:
            Selected Skill object or None if no good match
        """
        phase_skills = self.all_skills_by_phase.get(phase, [])
        if not phase_skills:
            return None
        
        keywords = PentestPhaseDetector.extract_keywords(user_request)
        
        # Score all skills in the phase
        best_skill = None
        best_score = 0.0
        
        for skill in phase_skills:
            score = self.score_skill_relevance(skill, keywords)
            if score > best_score:
                best_score = score
                best_skill = skill
        
        # Return best skill if it has any relevance
        return best_skill if best_score > 0 else (phase_skills[0] if phase_skills else None)
    
    def select_skill(self, user_request: str) -> Tuple[Optional[Any], PentestPhase]:
        """
        Select the best skill for the user request (full workflow).
        Works with modular skill system.
        
        Returns:
            Tuple of (selected_skill_object, detected_phase)
        """
        # 1. Detect phase
        phase = PentestPhaseDetector.detect_phase(user_request)
        
        # 2. Select best skill for that phase
        skill = self.select_skill_for_phase(phase, user_request)
        
        # 3. Fallback: if no clear phase detected, use reporting skill
        if phase == PentestPhase.UNKNOWN or not skill:
            reporting_skills = self.all_skills_by_phase.get(PentestPhase.REPORTING, [])
            if reporting_skills:
                skill = reporting_skills[0]
                phase = PentestPhase.REPORTING
        
        return skill, phase
    
    def skill_to_dict(self, skill: Any) -> Dict[str, Any]:
        """
        Convert Skill object to dict format for backward compatibility.
        Handles both new Skill objects and old dict format.
        
        Returns:
            Dictionary representation of skill
        """
        if hasattr(skill, 'to_dict'):
            return skill.to_dict()
        elif isinstance(skill, dict):
            return skill
        else:
            # Fallback: create dict from attributes
            return {
                "skill_id": getattr(skill, 'skill_id', 'unknown'),
                "name": getattr(skill, 'name', 'Unknown'),
                "description": getattr(skill, 'description', ''),
                "type": "skill",
                "version": "latest",
                "keywords": getattr(skill, 'keywords', []),
                "mcp_tools": getattr(skill, 'mcp_tools', []),
                "agent_behavior": getattr(skill, 'agent_behavior', ''),
                "next_phase": getattr(skill, 'next_phase', 'unknown')
            }

