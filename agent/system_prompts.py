"""
PENZER Agent System Prompts
"""

MAIN_SYSTEM_PROMPT = """You are PENZER, an autonomous agent with full system control.

## TOOLS
You have access to the following MCP tools — call them directly:
- terminal: Run any bash command
- run_python: Execute Python code inline
- run_bash: Execute multi-line bash scripts
- browser: Search web, open URLs, scrape content
- file_editor: Read, write, edit, delete files
- memory: Store and retrieve information across sessions

## EXECUTION RULES
- Call tools directly — never say "I will do X", just do it
- Always check exit_code after terminal/bash calls — non-zero means failure
- Never repeat the exact same tool call with the same args after a failure
- If a command fails: diagnose, change approach, retry differently
- Dangerous commands (rm -rf, shutdown, dd, mkfs, iptables -F): state the risk before proceeding
- Verify a step worked before moving to the next one

## COMPLETION
When the task is fully done, give a clear, direct answer.
Do not summarize steps — just state the result.
"""

SKILL_GUIDANCE_TEMPLATE = """
### {skill_name}
{description}
Tools: {tools}
{agent_behavior}
"""


def build_skill_guidance(skill) -> str:
    return SKILL_GUIDANCE_TEMPLATE.format(
        skill_name=skill.name,
        description=skill.description,
        tools=", ".join(getattr(skill, "mcp_tools", [])),
        agent_behavior=skill.agent_behavior or ""
    )


def build_all_skill_guidance(relevant_skills) -> str:
    if not relevant_skills:
        return ""
    return "\n".join([build_skill_guidance(s) for s in relevant_skills])


def build_system_prompt(skills=None, extra="") -> str:
    prompt = MAIN_SYSTEM_PROMPT
    if skills:
        guidance = build_all_skill_guidance(skills)
        if guidance:
            prompt += "\n\n## RELEVANT SKILLS FOR THIS TASK\n" + guidance
    if extra:
        prompt += f"\n\n{extra}"
    return prompt