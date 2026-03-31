# agent/skills/scan.py
"""Scan phase skills: network reconnaissance and discovery."""

from typing import List
from agent.skills.base import Skill, SkillModule, PentestPhase


class ScanSkills(SkillModule):
    """Skills for SCAN phase: host discovery, port scanning, network mapping."""
    
    phase = PentestPhase.SCAN
    
    @classmethod
    def get_skills(cls) -> List[Skill]:
        return [
            Skill(
                skill_id="pentest_scan_discovery",
                name="Host Discovery & Network Scanning",
                phase=PentestPhase.SCAN,
                description="Performs network reconnaissance: ping sweeps, host discovery, port scanning (nmap)",
                keywords=["scan", "nmap", "host discovery", "port scan", "network", "reconnaissance", "ping"],
                mcp_tools=["execute_system_command", "check_available_tools"],
                agent_behavior="""
OBJECTIVE: Scan and discover network information about the target.

FULL AUTONOMY - You can use ANY available tool:
- Use execute_system_command for direct system commands (nmap, ping, netstat, etc.)
- Use check_available_tools to see what tools are installed
- Run multiple commands in sequence to gather data
- Don't ask for permission or confirmation

WORKFLOW:
1. Determine what information is needed
2. Choose the best tools available on the system
3. Execute commands to scan/discover
4. Analyze results and provide findings
5. Return comprehensive report when done

EXAMPLES OF VALID COMMANDS:
- ping <target>
- nmap -sV <target>
- nmap -sC <target>
- netstat -an
- arp-scan -l
- masscan <range>
- shodan search <query>

RESPONSE FORMAT:
Use standard ReAct format: reason about next step in REASON phase.
For actions, respond with proper JSON for ACT phase:
{"tool_name": "execute_system_command", "arguments": {"command": "command to run"}}
""",
                next_phase="enumeration",
                priority=0.9,
                version="1.0"
            ),
        ]
