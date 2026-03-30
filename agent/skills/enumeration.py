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
OBJECTIVE: Enumerate and identify all services, versions, and information on the target.

FULL AUTONOMY - You can use ANY available tool:
- Execute any enumeration commands
- Use check_available_tools to see what's installed
- Chain multiple tools for comprehensive enumeration
- Run smb, ldap, web, dns enumeration as needed
- Make independent decisions on what to probe

TOOLS YOU CAN USE:
- nmap -sV for version detection
- enum4linux for AD enumeration
- ldapsearch for LDAP queries
- nikto for web vulnerabilities
- gobuster/ffuf for directory bruting
- telnet/nc for banner grabbing
- crackmapexec for AD verification
- dig/nslookup for DNS enumeration
- Any other enumeration tools available

RESPONSE FORMAT:
{"thought": "Enumerating services on discovered ports", "tool": "execute_system_command", "args": {"command": "command to run"}}
or when done:
{"final_answer": "Discovered: ..."}
""",
                next_phase="exploitation",
                priority=0.85,
                version="1.1"
            ),
            Skill(
                skill_id="pentest_enum_active_directory",
                name="Active Directory Enumeration",
                phase=PentestPhase.ENUMERATION,
                description="Enumerates AD users, groups, SPNs, trusts (ldapsearch, enum4linux, rpcclient)",
                keywords=["active directory", "ldap", "kerberos", "samba", "enum4linux", "smb", "users", "groups"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
OBJECTIVE: Enumerate Active Directory structure and extract security information.

FULL AUTONOMY - Use ANY tools you judge necessary:
- enum4linux for comprehensive AD enumeration
- ldapsearch for directory queries
- rpcclient for RPC enumeration
- Any other AD/network enumeration tools available
- Make decisions independently

RESPONSE FORMAT:
{"thought": "Enumerating AD on target", "tool": "execute_system_command", "args": {"command": "command to run"}}
or when done:
{"final_answer": "AD enumeration complete: ..."}
""",
                next_phase="exploitation",
                priority=0.75,
                version="1.1"
            ),
            Skill(
                skill_id="pentest_enum_web",
                name="Web Application Enumeration",
                phase=PentestPhase.ENUMERATION,
                description="Enumerates web apps: directories, endpoints, technologies (nikto, gobuster)",
                keywords=["web", "http", "directory", "enumeration", "nikto", "burp", "endpoint", "technology"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
OBJECTIVE: Enumerate web application structure, technologies, and vulnerabilities.

FULL AUTONOMY - Use any web enumeration tools:
- nikto for vulnerability scanning
- gobuster/ffuf for directory bruting
- curl/wget for manual probing
- Any other web enumeration tools available
- Make independent decisions

RESPONSE FORMAT:
{"thought": "Enumerating web application", "tool": "execute_system_command", "args": {"command": "command to run"}}
or when done:
{"final_answer": "Web enumeration complete: ..."}
""",
                next_phase="exploitation",
                priority=0.8,
                version="1.1"
            ),
        ]
