# agent/skills/enumeration.py
"""Enumeration phase skills: service discovery, version detection, AD enumeration."""

from agent.skills.base import Skill, SkillModule, PentestPhase


class EnumerationSkills(SkillModule):
    """Skills for ENUMERATION phase: services, versions, technologies, AD."""
    
    phase = PentestPhase.ENUMERATION
    
    @classmethod
    def get_skills(cls) -> list:
        return [
            Skill(
                skill_id="pentest_enum_services",
                name="Service Enumeration & Version Detection",
                phase=PentestPhase.ENUMERATION,
                description="Enumerates services on discovered ports, detects versions, identifies technologies",
                keywords=["enumerate", "service", "version", "detection", "banner", "probe", "identify"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
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
- Recommended next steps based on services
""",
                next_phase="exploitation"
            ),
            Skill(
                skill_id="pentest_enum_active_directory",
                name="Active Directory Enumeration",
                phase=PentestPhase.ENUMERATION,
                description="Enumerates AD users, groups, SPNs, trusts (ldapsearch, enum4linux, rpcclient)",
                keywords=["active directory", "ldap", "kerberos", "samba", "enum4linux", "smb", "users", "groups"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
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
- Security policies and hardening status
""",
                next_phase="exploitation"
            ),
            Skill(
                skill_id="pentest_enum_web",
                name="Web Application Enumeration",
                phase=PentestPhase.ENUMERATION,
                description="Enumerates web apps: directories, endpoints, technologies (nikto, gobuster)",
                keywords=["web", "http", "directory", "enumeration", "nikto", "burp", "endpoint", "technology"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
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
- Technology stack and dependencies
""",
                next_phase="exploitation"
            ),
        ]
