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
OBJECTIVE: Discover live hosts on the target network and identify open ports.

WORKFLOW:
1. Parse target network or IP range from user input (e.g., 192.168.1.0/24, 10.0.0.1)
   - If no specific target given: use localhost (127.0.0.1) or local subnet (192.168.1.0/24)
2. Use appropriate scanning tools based on availability:
   - For single IP: ping -c 1 <IP> or use execute_system_command
   - For network range: use nmap -sn (ping scan) or similar
3. Identify open ports with nmap -p- or nmap -p 1-65535
4. Document all findings with IP addresses and port numbers
5. Return results immediately

IMPORTANT:
- You MUST take action - do not ask for clarification
- If target is ambiguous, use localhost (127.0.0.1) as default
- Always use execute_system_command to run actual commands
- Return results in JSON format with discovered hosts/ports

RESPONSE FORMAT:
{"thought": "Running ping/nmap scan on target", "tool": "execute_system_command", "args": {"command": "ping -c 1 127.0.0.1"}}
or after scan:
{"final_answer": "Scanned 127.0.0.1 - Host is up, ports: 22, 80, 443"}
""",
                next_phase="enumeration",
                priority=0.9,
                version="1.0"
            ),
        ]
