# agent/skills/reporting.py
"""Reporting phase skills: report generation, findings compilation, remediation."""

from agent.skills.base import Skill, SkillModule, PentestPhase


class ReportingSkills(SkillModule):
    """Skills for REPORTING phase: comprehensive pentest reports and remediation."""
    
    phase = PentestPhase.REPORTING
    
    @classmethod
    def get_skills(cls) -> list:
        return [
            Skill(
                skill_id="pentest_report_generation",
                name="Pentest Report Generation",
                phase=PentestPhase.REPORTING,
                description="Generates comprehensive pentest reports: findings, severity, remediation, executive summary",
                keywords=["report", "findings", "remediation", "summary", "executive", "vulnerability"],
                mcp_tools=["execute_system_command"],
                agent_behavior="""
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
- Follow-up testing recommendations
""",
                next_phase="reporting",
                priority=0.75,
                version="1.1"
            ),
        ]
