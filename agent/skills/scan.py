# agent/skills/scan.py
"""Scan phase skills: network reconnaissance and discovery."""

from agent.skills.base import Skill, SkillModule, PentestPhase


class ScanSkills(SkillModule):
    """Skills for SCAN phase: host discovery, port scanning, network mapping."""
    
    phase = PentestPhase.SCAN
    
    @classmethod
    def get_skills(cls) -> list:
        return [
            Skill(
                skill_id="pentest_scan_discovery",
                name="Host Discovery & Network Scanning",
                phase=PentestPhase.SCAN,
                description="Performs network reconnaissance: ping sweeps, host discovery, port scanning (nmap)",
                keywords=["scan", "nmap", "host discovery", "port scan", "network", "reconnaissance", "ping"],
                mcp_tools=["execute_system_command", "check_available_tools"],
                agent_behavior="""
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
- masscan: Fast port scanning for large ranges

OUTPUT EXPECTATIONS:
- List of discovered hosts (IP addresses)
- Open ports on each host
- Service names and versions
- OS detection if available
- Potential vulnerabilities based on services
""",
                next_phase="enumeration"
            ),
        ]
