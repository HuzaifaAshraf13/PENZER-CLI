"""
Claude Agent Skills System - Phase-Specific Pentest Skills
Implements pentest workflow stages: Scan → Enumerate → Exploit → Post-Exploit → Report
"""

import json
import os
import re
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum


class PentestPhase(Enum):
    """Pentest workflow phases"""
    SCAN = "scan"
    ENUMERATION = "enumeration"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post_exploitation"
    REPORTING = "reporting"
    UNKNOWN = "unknown"


class PhaseSpecificSkillsRegistry:
    """
    Manages phase-specific Claude Agent Skills.
    Each phase has dedicated skills that handle that stage of the pentest.
    """
    
    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.beta_headers = {
            "anthropic-beta": "code-execution-2025-08-25,skills-2025-10-02,files-api-2025-04-14"
        }
        # Phase-to-skills mapping with metadata
        self.phase_skills = self._init_phase_skills()
    
    def _init_phase_skills(self) -> Dict[PentestPhase, List[Dict[str, Any]]]:
        """
        Initialize phase-specific skill registry.
        Each skill metadata includes: id, name, description, version.
        Full instructions are loaded on-demand by Claude.
        """
        return {
            PentestPhase.SCAN: [
                {
                    "skill_id": "pentest_scan_discovery",
                    "name": "Host Discovery & Network Scanning",
                    "description": "Performs network reconnaissance: ping sweeps, host discovery, port scanning (nmap)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["scan", "nmap", "host discovery", "port scan", "network", "reconnaissance", "ping"]
                },
            ],
            PentestPhase.ENUMERATION: [
                {
                    "skill_id": "pentest_enum_services",
                    "name": "Service Enumeration & Version Detection",
                    "description": "Enumerates services on discovered ports, detects versions, identifies technologies",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["enumerate", "service", "version", "detection", "banner", "probe", "identify"]
                },
                {
                    "skill_id": "pentest_enum_active_directory",
                    "name": "Active Directory Enumeration",
                    "description": "Enumerates AD users, groups, SPNs, trusts (ldapsearch, enum4linux, rpcclient)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["active directory", "ldap", "kerberos", "samba", "enum4linux", "smb", "users", "groups"]
                },
                {
                    "skill_id": "pentest_enum_web",
                    "name": "Web Application Enumeration",
                    "description": "Enumerates web apps: directories, endpoints, technologies (nikto, burp)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["web", "http", "directory", "enumeration", "nikto", "burp", "endpoint", "technology"]
                },
            ],
            PentestPhase.EXPLOITATION: [
                {
                    "skill_id": "pentest_exploit_search",
                    "name": "Exploit Research & Discovery",
                    "description": "Searches for exploits: CVE databases, Exploit-DB, PoC code, GitHub repositories",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["exploit", "cve", "vulnerability", "poc", "metasploit", "searchsploit", "research"]
                },
                {
                    "skill_id": "pentest_exploit_execution",
                    "name": "Exploit Execution & Payload Delivery",
                    "description": "Executes exploits, generates payloads, handles reverse shells (msfvenom, custom exploits)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["exploit", "execute", "payload", "shell", "msfvenom", "reverse", "delivery"]
                },
            ],
            PentestPhase.POST_EXPLOITATION: [
                {
                    "skill_id": "pentest_post_privilege_escalation",
                    "name": "Privilege Escalation",
                    "description": "Escalates privileges: exploits kernel vulns, weak perms, sudo misconfigs (linpeas, winpeas)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["privilege escalation", "privesc", "sudo", "kernel", "weak permissions", "linpeas", "winpeas"]
                },
                {
                    "skill_id": "pentest_post_pivoting",
                    "name": "Lateral Movement & Pivoting",
                    "description": "Pivots to internal systems, uses compromised hosts as jump servers, establishes persistence",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["pivot", "lateral movement", "jump server", "persistence", "persistence mechanism"]
                },
                {
                    "skill_id": "pentest_post_exfiltration",
                    "name": "Data Extraction & Exfiltration",
                    "description": "Extracts sensitive data, cracks hashes, exfiltrates via secure channels",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["exfiltration", "data extraction", "dump", "hash", "credentials", "sensitive data"]
                },
            ],
            PentestPhase.REPORTING: [
                {
                    "skill_id": "pentest_report_generation",
                    "name": "Pentest Report Generation",
                    "description": "Generates comprehensive pentest reports: findings, severity, remediation, executive summary",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["report", "findings", "remediation", "summary", "executive", "vulnerability"]
                },
            ],
        }
    
    def get_skills_for_phase(self, phase: PentestPhase) -> List[Dict[str, Any]]:
        """Get all skills for a specific pentest phase."""
        return self.phase_skills.get(phase, [])
    
    def get_all_skills_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get flat dictionary of all skills metadata (for matching)."""
        all_skills = {}
        for phase, skills in self.phase_skills.items():
            for skill in skills:
                all_skills[skill["skill_id"]] = skill
        return all_skills


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
    def detect_phase(user_request: str) -> PentestPhase:
        """
        Detect which pentest phase the request is for.
        
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
        
        # Return phase with highest score
        best_phase = max(phase_scores.items(), key=lambda x: x[1])
        return best_phase[0] if best_phase[1] > 0 else PentestPhase.UNKNOWN


class SkillSelector:
    """
    Selects the most relevant skill for a given user request and phase.
    Implements Claude Agent Skills API pattern with on-demand instruction loading.
    """
    
    def __init__(self, registry: PhaseSpecificSkillsRegistry):
        self.registry = registry
        self.all_skills = registry.get_all_skills_metadata()
    
    def score_skill_relevance(self, skill: Dict[str, Any], keywords: List[str]) -> float:
        """
        Score how relevant a skill is to the request keywords.
        Matches against skill description and keywords.
        
        Returns:
            Score 0-1 where 1 is perfect match
        """
        score = 0.0
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
    
    def select_skill_for_phase(self, phase: PentestPhase, user_request: str) -> Optional[Dict[str, Any]]:
        """
        Select the best skill for the user request within the detected phase.
        
        Returns:
            Selected skill metadata or None if no good match
        """
        phase_skills = self.registry.get_skills_for_phase(phase)
        if not phase_skills:
            return None
        
        keywords = PentestPhaseDetector.extract_keywords(user_request)
        keyword_set = set(keywords)
        
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
    
    def select_skill(self, user_request: str) -> Tuple[Optional[Dict[str, Any]], PentestPhase]:
        """
        Select the best skill for the user request (full workflow).
        
        Returns:
            Tuple of (selected_skill_metadata, detected_phase)
        """
        # 1. Detect phase
        phase = PentestPhaseDetector.detect_phase(user_request)
        
        # 2. Select best skill for that phase
        skill = self.select_skill_for_phase(phase, user_request)
        
        # 3. Fallback: if no clear phase detected, use reporting skill
        if phase == PentestPhase.UNKNOWN or not skill:
            reporting_skills = self.registry.get_skills_for_phase(PentestPhase.REPORTING)
            if reporting_skills:
                skill = reporting_skills[0]
                phase = PentestPhase.REPORTING
        
        return skill, phase


class ClaudeSkillsAPIBuilder:
    """
    Builds Claude API calls with skills container.
    Follows the official Claude Agent Skills API pattern.
    """
    
    @staticmethod
    def build_skill_container(skill: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the container parameter for Claude API call.
        Skill instructions are loaded on-demand by Claude.
        
        Returns:
            Container dict with skill_id, type, and version
        """
        return {
            "type": "skill",
            "skill_id": skill.get("skill_id"),
            "version": skill.get("version", "latest")
        }
    
    @staticmethod
    def get_beta_headers() -> Dict[str, str]:
        """
        Return required beta headers for Claude Agent Skills API.
        """
        return {
            "anthropic-beta": "code-execution-2025-08-25,skills-2025-10-02,files-api-2025-04-14"
        }
    
    @staticmethod
    def build_system_prompt_for_skill(skill: Dict[str, Any], base_context: str = "") -> str:
        """
        Build system prompt that acknowledges the selected skill.
        Full skill instructions are loaded by Claude runtime.
        
        Args:
            skill: Selected skill metadata
            base_context: Base context/system prompt
        
        Returns:
            System prompt with skill context
        """
        skill_context = f"""
# === ACTIVE PENTEST SKILL ===
Skill: {skill.get('name', 'Unknown')}
ID: {skill.get('skill_id')}
Phase: {skill.get('description', 'No description')}

Full skill instructions and tools are loaded by Claude's runtime.
Focus on executing this phase's objectives.
"""
        return f"{base_context}\n{skill_context}".strip() if base_context else skill_context


def create_skill_aware_system_prompt(
    skill: Dict[str, Any],
    base_context: str = ""
) -> str:
    """
    Build system prompt for selected skill.
    Full skill instructions loaded by Claude runtime (progressive disclosure).
    
    Args:
        skill: Selected skill metadata
        base_context: Base system context
    
    Returns:
        System prompt with skill awareness
    """
    return ClaudeSkillsAPIBuilder.build_system_prompt_for_skill(skill, base_context)