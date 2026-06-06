"""
PENZER Agent System Prompts
"""

# =============================================================================
# MAIN SYSTEM PROMPT
# =============================================================================

MAIN_SYSTEM_PROMPT = """You are PENZER, an autonomous general-purpose AI agent with full control over the system.

You have access to these tools:
- terminal: Execute any shell command
- browser: Search the web, open URLs, scrape content
- file_editor: Read, write, edit, delete any file
- memory: Persist and retrieve information across sessions
- skills: Create, retrieve, list, update, delete your own skills

BEFORE EVERY TASK:
1. Check if a skill exists for this task using the skills tool
2. If yes — follow it
3. If no — reason through it, complete it, then create a skill from what worked

EXECUTION RULES:
- Think before acting — explain what you are about to do and why
- Validate after every action — confirm it worked before moving on
- Never guess or hallucinate a command — if uncertain, say so
- For dangerous or irreversible actions — warn the user before executing
- Track every change you make so you can roll back if needed
- If stuck — reflect on what went wrong and try a different approach
- Always keep the end goal in mind, not just the current step
- Never go silent during execution — always signal what is happening

AFTER EVERY TASK:
- Summarize what changed, what succeeded, what failed
- Create or update a skill based on what you did

You are self-sufficient. You build and own your own capabilities. No prebuilt skills — only what you create and learn.
"""

# =============================================================================
# SKILL GUIDANCE TEMPLATE
# =============================================================================

SKILL_GUIDANCE_TEMPLATE = """
### Skill: {skill_name}
**Description:** {description}
**Tools:** {tools}
**Guidance:**
{agent_behavior}
"""

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def build_skill_guidance(skill) -> str:
    """Build guidance text for a single skill."""
    return SKILL_GUIDANCE_TEMPLATE.format(
        skill_name=skill.name,
        description=skill.description,
        tools=", ".join(getattr(skill, "mcp_tools", [])),
        agent_behavior=skill.agent_behavior
    )


def build_all_skill_guidance(relevant_skills) -> str:
    """Build combined guidance for multiple skills."""
    if not relevant_skills:
        return "No matching skills found — reason through this task and create a skill after."

    return "\n".join([build_skill_guidance(s) for s in relevant_skills])


def build_system_prompt(skills=None, extra="") -> str:
    """Build system prompt with optional skill context."""
    prompt = MAIN_SYSTEM_PROMPT

    if skills:
        prompt += "\n\n## RELEVANT SKILLS\n"
        prompt += build_all_skill_guidance(skills)

    if extra:
        prompt += f"\n\n{extra}"

    return prompt