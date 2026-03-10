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
        Each skill includes: id, name, description, behavior instructions, and keywords.
        """
        return {
            PentestPhase.SCAN: [
                {
                    "skill_id": "pentest_scan_discovery",
                    "name": "Host Discovery & Network Scanning",
                    "description": "Performs network reconnaissance: ping sweeps, host discovery, port scanning (nmap)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["scan", "nmap", "host discovery", "port scan", "network", "reconnaissance", "ping"],
                    "mcp_tools": ["execute_system_command", "check_available_tools"],
                    "agent_behavior": """
OBJECTIVE: Discover live hosts and open ports on the target network.

WORKFLOW:
1. Parse the target network/IP from user request (e.g., 192.168.1.0/24, 10.0.0.1)
2. Start with a ping sweep to discover live hosts (nmap -sn <target>)
3. For each discovered host, perform a port scan (nmap -p- <host> or nmap -sV <host>)
4. Record open ports, services, and versions
5. If user wants detailed scanning, use nmap -A for aggressive scanning

TOOLS TO USE:
- nmap: Network mapping and port scanning
- ping: Basic host discovery

OUTPUT EXPECTATIONS:
- List of discovered hosts (IP addresses)
- Open ports on each host
- Service names and versions
- OS detection if available
""",
                    "next_phase": "enumeration"
                },
            ],
            PentestPhase.ENUMERATION: [
                {
                    "skill_id": "pentest_enum_services",
                    "name": "Service Enumeration & Version Detection",
                    "description": "Enumerates services on discovered ports, detects versions, identifies technologies",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["enumerate", "service", "version", "detection", "banner", "probe", "identify"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Identify services, versions, and technologies running on open ports.

WORKFLOW:
1. For each discovered open port from SCAN phase:
2. Connect to the service (telnet, nc) to grab banners
3. Use nmap -sV for detailed version detection
4. Probe specific service ports (e.g., 80=HTTP, 445=SMB, 389=LDAP)
5. Identify technologies (web frameworks, databases, AD servers)
6. Build a detailed inventory of all services and versions

TOOLS TO USE:
- nmap -sV: Service version detection
- telnet/nc: Banner grabbing
- http probes: For web services
- smb-related tools: For SMB/CIFS services
- ldap tools: For directory services

OUTPUT EXPECTATIONS:
- Service name and version for each port
- Technology stack identification
- Potential vulnerabilities based on versions
- Configuration details (if available)
""",
                    "next_phase": "exploitation"
                },
                {
                    "skill_id": "pentest_enum_active_directory",
                    "name": "Active Directory Enumeration",
                    "description": "Enumerates AD users, groups, SPNs, trusts (ldapsearch, enum4linux, rpcclient)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["active directory", "ldap", "kerberos", "samba", "enum4linux", "smb", "users", "groups"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Extract Active Directory structure, users, groups, and security information.

WORKFLOW:
1. Detect if target is Windows/AD environment (port 389 LDAP, 445 SMB, 88 Kerberos)
2. Use enum4linux to enumerate shares, users, groups, and policies
3. Use ldapsearch to query LDAP structure, users, and SPN records
4. Use rpcclient to enumerate users and RID cycling
5. Identify privileged users, service accounts, and domain admins
6. Map out trust relationships and security policies

TOOLS TO USE:
- enum4linux: Primary AD enumeration tool
- ldapsearch: LDAP directory queries
- rpcclient: RPC enumeration
- crackmapexec: AD scanning and verification

OUTPUT EXPECTATIONS:
- Complete user list with descriptions
- Group memberships and roles
- Service Principal Names (SPNs)
- Domain trust relationships
- Potentially weak passwords or misconfigurations
""",
                    "next_phase": "exploitation"
                },
                {
                    "skill_id": "pentest_enum_web",
                    "name": "Web Application Enumeration",
                    "description": "Enumerates web apps: directories, endpoints, technologies (nikto, burp)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["web", "http", "directory", "enumeration", "nikto", "burp", "endpoint", "technology"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Map web application structure, technologies, and potential vulnerabilities.

WORKFLOW:
1. Identify web service (HTTP/HTTPS on ports 80, 443, 8080, etc.)
2. Use nikto for vulnerability scanning
3. Use directory bruting (dirbuster, ffuf, gobuster) to discover hidden endpoints
4. Identify web technologies (cms, framework, server software)
5. Check for common misconfigurations (directory listing, .git, .env files)
6. Analyze SSL/TLS certificates
7. Test for basic web vulnerabilities (SQL injection indicators, XSS, etc.)

TOOLS TO USE:
- nikto: Web vulnerability scanner
- gobuster/ffuf/dirbuster: Directory bruting
- curl/wget: Manual probing
- ssl_scan: SSL/TLS analysis

OUTPUT EXPECTATIONS:
- Complete list of discovered endpoints
- Identified web technologies and versions
- Potential vulnerabilities
- SSL/TLS certificate information
- Configuration issues and misconfigurations
""",
                    "next_phase": "exploitation"
                },
            ],
            PentestPhase.EXPLOITATION: [
                {
                    "skill_id": "pentest_exploit_search",
                    "name": "Exploit Research & Discovery",
                    "description": "Searches for exploits: CVE databases, Exploit-DB, PoC code, GitHub repositories",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["exploit", "cve", "vulnerability", "poc", "metasploit", "searchsploit", "research"],
                    "mcp_tools": ["search_exploit_db", "search_github_repository", "execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Research and identify applicable exploits for discovered vulnerabilities.

WORKFLOW:
1. Identify vulnerable service/version from enumeration phase
2. Search CVE databases for matching vulnerabilities
3. Use searchsploit to find existing exploits (Exploit-DB)
4. Check metasploit modules for the vulnerability
5. Search GitHub for public PoCs
6. Evaluate exploit reliability and prerequisites
7. Prioritize high-impact, reliable exploits

TOOLS TO USE:
- searchsploit: Local exploit database search
- cve lookup tools: CVE details and CVSS scores
- metasploit: Exploit module lookup
- GitHub: Public PoC discovery
- CVE databases (NVD, cvedetails)

OUTPUT EXPECTATIONS:
- List of applicable exploits with details
- CVE identifiers and severity scores
- PoC availability and reliability
- Prerequisites and target conditions
- Recommended exploit selection
""",
                    "next_phase": "exploitation"
                },
                {
                    "skill_id": "pentest_exploit_execution",
                    "name": "Exploit Execution & Payload Delivery",
                    "description": "Executes exploits, generates payloads, handles reverse shells (msfvenom, custom exploits)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["exploit", "execute", "payload", "shell", "msfvenom", "reverse", "delivery"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Generate and deliver exploitation payloads to gain code execution.

WORKFLOW:
1. Select appropriate exploit from research phase
2. Generate payload (msfvenom for multi-stage payloads)
3. Deliver payload via appropriate method (web upload, email, network delivery)
4. Establish reverse shell connection (Netcat, meterpreter, or bash reverse shell)
5. Stabilize the shell (TTY allocation, shell upgrade)
6. Verify code execution on the target
7. Handle exploit failures and fallback methods

TOOLS TO USE:
- msfvenom: Payload generation
- metasploit: Multi-protocol exploitation
- custom scripts: For specific vulnerabilities
- reverse shells: bash, nc, python, perl
- handler listener: Catch reverse shells

OUTPUT EXPECTATIONS:
- Successful code execution on target
- Reverse shell connection established
- Current user and system information
- Proof of compromise (file access, system info)
- Path to privilege escalation
""",
                    "next_phase": "post_exploitation"
                },
            ],
            PentestPhase.POST_EXPLOITATION: [
                {
                    "skill_id": "pentest_post_privilege_escalation",
                    "name": "Privilege Escalation",
                    "description": "Escalates privileges: exploits kernel vulns, weak perms, sudo misconfigs (linpeas, winpeas)",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["privilege escalation", "privesc", "sudo", "kernel", "weak permissions", "linpeas", "winpeas"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Escalate from current user to root/administrator privileges.

WORKFLOW:
1. Enumerate current user and system information
2. Check sudo permissions and SUID binaries
3. Look for kernel vulnerabilities using tools (linpeas, winpeas)
4. Identify weak file permissions and setuid files
5. Search for credentials in files, environment variables, bash history
6. Test sudo misconfigurations and bypass techniques
7. Exploit identified privilege escalation vectors
8. Verify root/admin access

TOOLS TO USE:
- linpeas/winpeas: Privilege escalation enumeration
- find: Locate SUID binaries and weak permissions
- sudo -l: Check sudo permissions
- Custom kernel exploit scripts
- Password cracking if needed

OUTPUT EXPECTATIONS:
- Root/Administrator shell obtained
- Privilege escalation method documented
- Full system control
- Proof of root access (id, whoami, etc.)
""",
                    "next_phase": "post_exploitation"
                },
                {
                    "skill_id": "pentest_post_pivoting",
                    "name": "Lateral Movement & Pivoting",
                    "description": "Pivots to internal systems, uses compromised hosts as jump servers, establishes persistence",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["pivot", "lateral movement", "jump server", "persistence", "persistence mechanism"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Move laterally across the network to reach additional targets.

WORKFLOW:
1. From compromised host, enumerate internal network (arp scan, nmap from inside)
2. Identify additional targets and services on internal network
3. Use compromised host as pivot point/jump server
4. Forward ports or establish tunnel (SSH, socat, chisel)
5. Establish persistence (backdoor, cron job, scheduled task)
6. Repeat exploitation process on newly discovered internal targets
7. Map network topology and trust relationships

TOOLS TO USE:
- arp, route: Network discovery
- nmap (from inside): Internal scanning
- SSH tunneling/forwarding: Pivot setup
- chisel/socat: Tunnel establishment
- Persistence mechanisms: Cron, systemd, Task Scheduler
- crackmapexec: Internal network scanning

OUTPUT EXPECTATIONS:
- Internal network mapped
- Additional targets identified and compromised
- Persistence established
- Lateral movement chain documented
- Multiple footholds in network
""",
                    "next_phase": "post_exploitation"
                },
                {
                    "skill_id": "pentest_post_exfiltration",
                    "name": "Data Extraction & Exfiltration",
                    "description": "Extracts sensitive data, cracks hashes, exfiltrates via secure channels",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["exfiltration", "data extraction", "dump", "hash", "credentials", "sensitive data"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Extract and exfiltrate sensitive data from compromised systems.

WORKFLOW:
1. Identify sensitive data locations (databases, file shares, backups)
2. Dump system hashes and credentials (SAM, shadow, NTDS.dit)
3. Extract web server credentials and application data
4. Locate and copy confidential files
5. Crack recovered password hashes offline
6. Exfiltrate data via secure channel (encrypted, anonymous)
7. Document all extracted data for reporting

TOOLS TO USE:
- hashcat/John: Password cracking
- mimikatz: Credential extraction (Windows)
- secretsdump.py: SAM/NTDS dumping
- scp/sftp: Secure file transfer
- base64/tar: Data packaging
- curl/wget: Data exfiltration over HTTP(S)

OUTPUT EXPECTATIONS:
- Extracted passwords and hashes
- Database credentials and data
- Sensitive files and documents
- Clear evidence of data breach
- All data securely exfiltrated
""",
                    "next_phase": "reporting"
                },
            ],
            PentestPhase.REPORTING: [
                {
                    "skill_id": "pentest_report_generation",
                    "name": "Pentest Report Generation",
                    "description": "Generates comprehensive pentest reports: findings, severity, remediation, executive summary",
                    "type": "skill",
                    "version": "latest",
                    "keywords": ["report", "findings", "remediation", "summary", "executive", "vulnerability"],
                    "mcp_tools": ["execute_system_command"],
                    "agent_behavior": """
OBJECTIVE: Create comprehensive penetration testing report for stakeholders.

WORKFLOW:
1. Gather all findings from previous phases
2. Organize vulnerabilities by severity (Critical, High, Medium, Low)
3. Document each vulnerability with:
   - Description and impact
   - CVSS score
   - Proof of concept / exploitation path
   - Remediation steps
4. Calculate overall risk assessment
5. Create executive summary for management
6. Provide technical details for IT/security teams
7. Include timeline and methodology
8. Generate final PDF/document report

REPORT SECTIONS:
- Executive Summary (high-level findings)
- Methodology (tools, techniques used)
- Findings (organized by severity)
- Risk Assessment (overall security posture)
- Remediation Roadmap (prioritized fixes)
- Appendices (technical details, logs)

OUTPUT FORMAT:
- Professional report document
- Risk ratings and prioritization
- Clear remediation guidance
- Timeline for fixes
""",
                    "next_phase": "scan"
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
        Includes agent behavior instructions and available MCP tools.
        
        Args:
            skill: Selected skill metadata
            base_context: Base context/system prompt
        
        Returns:
            System prompt with skill context and behavior instructions
        """
        skill_name = skill.get('name', 'Unknown')
        skill_id = skill.get('skill_id')
        description = skill.get('description', 'No description')
        agent_behavior = skill.get('agent_behavior', 'No specific instructions defined')
        mcp_tools = skill.get('mcp_tools', [])
        next_phase = skill.get('next_phase', 'unknown')
        
        tools_section = ""
        if mcp_tools:
            tools_section = f"\n## AVAILABLE MCP TOOLS FOR THIS SKILL\n" + "\n".join(f"- {tool}" for tool in mcp_tools)
        
        skill_context = f"""
# === ACTIVE PENTEST SKILL ===
Skill: {skill_name}
Skill ID: {skill_id}
Phase Description: {description}

## AGENT BEHAVIOR INSTRUCTIONS
{agent_behavior}
{tools_section}

## WORKFLOW CONTINUATION
After completing this phase, recommend proceeding to: {next_phase.upper()}
"""
        combined = f"{base_context}\n{skill_context}".strip() if base_context else skill_context
        return combined


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