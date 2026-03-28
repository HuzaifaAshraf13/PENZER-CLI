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
OBJECTIVE: Discover live hosts on the target network.

INSTRUCTIONS:
1. Parse the target network or IP range from the user input (e.g., 192.168.1.0/24, 10.0.0.1)
2. Use 'ping' tool to discover active hosts:
   - For single IP: execute_system_command with "ping -c 1 <IP>"
   - For network range: use ping or check_available_tools to see what's available
3. Build a list of responding hosts
4. Return results in JSON format with discovered hosts

AVAILABLE TOOLS:
- execute_system_command: Run ping or other basic network commands
- check_available_tools: Check what tools are installed (nmap, masscan, etc)

JSON RESPONSE FORMAT:
{
  "thought": "Analysis of the scan results",
  "tool": "execute_system_command",
  "args": {"command": "ping -c 1 <target>"},
  "final_answer": "List of discovered hosts: [IPs]" (only when done)
}
""",
                next_phase="enumeration"
            ),
        ]
