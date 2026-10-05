"""Runtime skill matching, orchestration, and progress helpers."""

import re
from typing import Any

from agent.system_prompts import _tokenize


def match_core_skills(agent: Any, user_input: str) -> list:
    """Select eligible core skills and order them by relevance then priority."""
    lowered = user_input.lower()
    goal_tokens = _tokenize(user_input)
    scored = []
    for index, skill in enumerate(agent.core_skills):
        skill_name = (skill.name or "").lower()
        keyword_tokens = set()
        keyword_hits = 0
        for keyword in skill.keywords or []:
            phrase_tokens = _tokenize(keyword)
            keyword_tokens.update(phrase_tokens)
            if phrase_tokens and phrase_tokens.issubset(goal_tokens):
                keyword_hits += 1
        name_desc_tokens = _tokenize(skill.name or "") | _tokenize(skill.description or "")
        weak_overlap = goal_tokens & (name_desc_tokens - keyword_tokens)
        eligible = keyword_hits > 0 or len(weak_overlap) >= 2
        if not eligible and "memory" in skill_name and agent._looks_like_memory_query(lowered):
            eligible = True
        if not eligible:
            continue

        relevance = keyword_hits * 3.0 + len(weak_overlap) * 0.5
        priority = float(getattr(skill, "priority", 0.5))
        scored.append((relevance, priority, index, skill))

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[3] for item in scored]


def skill_plan_sections(skill: Any) -> list[str]:
    workflow = getattr(skill, "workflow", None) or []
    if workflow:
        return [step["title"] for step in workflow]
    behavior_lines = (skill.agent_behavior or "").splitlines()
    step_heading = re.compile(r"^STEP\s+\d+(?:\.\d+)?[A-Za-z]?\s*[—-]\s*.+$", re.IGNORECASE)
    section_heading = re.compile(r"^[A-Z][A-Z0-9 &/()'-]{2,}$")
    headings = [
        line.strip() for line in behavior_lines
        if line and line == line.lstrip()
        and (step_heading.fullmatch(line.strip()) or section_heading.fullmatch(line.strip()))
    ]
    steps = [heading for heading in headings if step_heading.fullmatch(heading)]
    if not steps:
        steps = headings
    if not steps:
        steps = [getattr(skill, "description", "Follow the skill procedure")]
    return steps[:12]


def orchestrate_skills(agent: Any) -> None:
    agent._skill_plan = []
    agent._skill_steps = {skill.name: 0 for skill in agent._active_skills}
    agent._skill_done = set()
    for skill in agent._active_skills:
        workflow = getattr(skill, "workflow", None) or []
        if workflow:
            steps = [
                {
                    "instruction": step["title"],
                    "tools": set(step["tools"]),
                    "success_criteria": step["success_criteria"],
                }
                for step in workflow
            ]
        else:
            default_tools = set(skill.tools or [])
            steps = [
                {"instruction": title, "tools": default_tools, "success_criteria": ""}
                for title in skill_plan_sections(skill)
            ]
        for index, step in enumerate(steps):
            if len(agent._skill_plan) >= 24:
                break
            agent._skill_plan.append({
                "skill": skill.name,
                "step": index,
                "instruction": step["instruction"],
                "tools": step["tools"],
                "success_criteria": step["success_criteria"],
                "done": False,
            })
    tool_order = ["memory", "planning", "browser", "terminal", "file_editor"]
    agent._skill_plan.sort(
        key=lambda step: next(
            (index for index, tool in enumerate(tool_order) if tool in step["tools"]),
            len(tool_order),
        )
    )


def mark_skill_step_done(agent: Any, tool_name: str) -> None:
    touched = set()
    for step in agent._skill_plan:
        if step["done"] or step["skill"] in touched:
            continue
        if not step["tools"] or tool_name in step["tools"]:
            step["done"] = True
            touched.add(step["skill"])
            agent._skill_steps[step["skill"]] = step["step"] + 1
            if all(item["done"] for item in agent._skill_plan if item["skill"] == step["skill"]):
                agent._skill_done.add(step["skill"])


def skills_for_tool(agent: Any, tool_name: str) -> list:
    return [
        skill for skill in agent._active_skills
        if not set(skill.tools or []) or tool_name in set(skill.tools or [])
    ]


def apply_skill_selection(agent: Any, skill_used: str | None) -> None:
    """Promote a model-selected core skill omitted by initial matching."""
    if not skill_used or skill_used in agent._matched_skills:
        return
    by_name = {skill.name: skill for skill in agent.core_skills}
    skill = by_name.get(skill_used)
    if skill is None:
        return
    agent._active_skills.append(skill)
    agent._matched_skills.append(skill.name)
    agent._novel_task = False
    skill_heading = f"## SELF-SELECTED CORE SKILL: {skill.name}"
    if skill_heading not in agent._system_prompt:
        agent._system_prompt = (
            f"{agent._system_prompt.rstrip()}\n\n{skill_heading}\n"
            f"{(skill.agent_behavior or '').strip()}"
        )
    agent._emit_activity(
        "skill", "Skill selected",
        message=f"Model self-selected skill '{skill.name}'.",
        status="success", details={"skill": skill.name},
    )
    agent._record_step(
        "recovery", f"Model self-selected skill '{skill.name}' — promoting into the active plan."
    )
    agent._orchestrate_skills()