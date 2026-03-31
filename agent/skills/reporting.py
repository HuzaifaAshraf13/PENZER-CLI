# agent/skills/reporting.py
"""Reporting phase skills: report generation, findings compilation, remediation."""

from typing import List
from agent.skills.base import Skill, SkillModule, PentestPhase


class ReportingSkills(SkillModule):
    """Skills for REPORTING phase: comprehensive pentest reports and remediation."""
    
    phase = PentestPhase.REPORTING
    
    @classmethod
    def get_skills(cls) -> List[Skill]:
        return [
            Skill(
                skill_id="pentest_report_generation",
                name="Pentest Report Generation",
                phase=PentestPhase.REPORTING,
                description="Generates comprehensive pentest reports: findings, severity, remediation, executive summary",
                keywords=["report", "findings", "remediation", "summary", "executive", "vulnerability"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
OBJECTIVE: Generate comprehensive penetration testing report.

FULL AUTONOMY - Create reports as needed:
- Compile all findings
- Document vulnerabilities
- Provide remediation guidance
- Generate report output
- Make independent decisions on report format

RESPONSE FORMAT:
Use standard ReAct format: reason about next step in REASON phase.
For actions, respond with proper JSON for ACT phase:
{"tool_name": "execute_system_command", "arguments": {"command": "command"}}
""",
                next_phase="reporting",
                priority=0.75,
                version="1.1"
            ),
        ]
