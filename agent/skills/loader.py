"""
Skill loader.

Core skills → always loaded, always shown to agent.
"""
import yaml
import logging
import math
import re
from pathlib import Path
from typing import List, Optional
from agent.skills.base import Skill

logger      = logging.getLogger(__name__)
SKILLS_DIR  = Path(__file__).parent
CORE_DIR    = SKILLS_DIR / "core"
VALID_SKILL_TOOLS = {
    "browser", "browser_info", "browser_close", "browser_list", "browser_close_all", "browser_abort",
    "file_editor", "memory", "terminal", "terminal_check_job", "terminal_list_jobs", "terminal_kill",
    "run_bash", "run_python", "plugin_tool",
}
_SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")


def _required_text(meta: dict, key: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _string_list(meta: dict, key: str) -> List[str]:
    value = meta.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must contain only non-empty strings")
    cleaned = [item.strip() for item in value]
    if len({item.lower() for item in cleaned}) != len(cleaned):
        raise ValueError(f"{key} must not contain duplicates")
    return cleaned


def _workflow_list(meta: dict, allowed_tools: set[str]) -> List[dict]:
    workflow = meta.get("workflow", [])
    if not isinstance(workflow, list):
        raise ValueError("workflow must be a list")
    if len(workflow) > 12:
        raise ValueError("workflow must contain at most 12 steps")

    cleaned = []
    for index, raw_step in enumerate(workflow, 1):
        if not isinstance(raw_step, dict):
            raise ValueError(f"workflow step {index} must be a mapping")
        title = raw_step.get("title")
        success_criteria = raw_step.get("success_criteria")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"workflow step {index} title must be non-empty text")
        if not isinstance(success_criteria, str) or not success_criteria.strip():
            raise ValueError(f"workflow step {index} success_criteria must be non-empty text")
        step_tools = raw_step.get("tools", [])
        if not isinstance(step_tools, list) or any(
            not isinstance(tool, str) or not tool.strip() for tool in step_tools
        ):
            raise ValueError(f"workflow step {index} tools must be a list of tool names")
        normalized_tools = [tool.strip() for tool in step_tools]
        unknown_tools = sorted(set(normalized_tools) - allowed_tools)
        if unknown_tools:
            raise ValueError(
                f"workflow step {index} uses unknown tools: {', '.join(unknown_tools)}"
            )
        if len({tool.lower() for tool in normalized_tools}) != len(normalized_tools):
            raise ValueError(f"workflow step {index} tools must not contain duplicates")
        cleaned.append({
            "title": title.strip(),
            "tools": normalized_tools,
            "success_criteria": success_criteria.strip(),
        })
    return cleaned

def _parse(path: Path) -> Optional[Skill]:
    try:
        raw = path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) < 3:
            raise ValueError("missing frontmatter delimiter")

        meta = yaml.safe_load(parts[1])
        if not isinstance(meta, dict) or not meta:
            raise ValueError("frontmatter did not produce a mapping")

        skill_id = _required_text(meta, "skill_id")
        if not _SKILL_ID_RE.fullmatch(skill_id):
            raise ValueError("skill_id must use lowercase letters, digits, '.', '_' or '-'")
        name = _required_text(meta, "name")
        description = _required_text(meta, "description")
        keywords = _string_list(meta, "keywords")
        tools = _string_list(meta, "tools")
        agent_behavior = _required_text(meta, "agent_behavior")
        workflow = _workflow_list(meta, VALID_SKILL_TOOLS)
        unknown_tools = sorted(set(tools) - VALID_SKILL_TOOLS)
        if unknown_tools:
            raise ValueError(f"tools contains unknown tool names: {', '.join(unknown_tools)}")
        raw_priority = meta.get("priority", 0.5)
        if isinstance(raw_priority, bool) or not isinstance(raw_priority, (int, float)):
            raise ValueError("priority must be a number between 0 and 1")
        priority = float(raw_priority)
        if not math.isfinite(priority) or not 0 <= priority <= 1:
            raise ValueError("priority must be a finite number between 0 and 1")
        core = meta.get("core", False)
        if not isinstance(core, bool):
            raise ValueError("core must be a boolean")
        version = _required_text(meta, "version") if "version" in meta else "1.0"
        generated_at = None

        return Skill(
            skill_id=skill_id,
            name=name,
            description=description,
            keywords=keywords,
            tools=tools,
            agent_behavior=agent_behavior,
            priority=priority,
            core=core,
            version=version,
            generated_at=generated_at,
            workflow=workflow,
        )
    except Exception as e:
        logger.error("Failed to parse %s: %s", path.name, e)
        return None


def load_core_skills() -> List[Skill]:
    if not CORE_DIR.exists():
        return []

    seen_ids = set()
    skills: List[Skill] = []
    for f in sorted(CORE_DIR.glob("*.skill.md")):
        parsed = _parse(f)
        if not parsed:
            continue
        if parsed.skill_id in seen_ids:
            logger.warning("Skipping duplicate skill id %s from %s", parsed.skill_id, f.name)
            continue
        seen_ids.add(parsed.skill_id)
        skills.append(parsed)

    logger.info("Core skills: %s", [s.name for s in skills])
    return skills


def load_all_skills() -> dict:
    """
    Returns the core skill set for the live runtime. Generated skills are
    intentionally disabled here: the agent should rely on the fixed core
    toolchain and planning behavior instead of runtime-generated skill
    mutation.
    """
    core = load_core_skills()
    generated = []
    logger.info(f"Total: {len(core)} core skills loaded")
    return {"core": core, "generated": generated}


def save_generated_skill(
    name:        str,
    description: str,
    behavior:    str,
    tools:       List[str],
    keywords:    List[str],
    priority:    float = 0.7,
) -> bool:
    """Stub: Generated skills are disabled."""
    return False


def delete_generated_skill(skill_id: str) -> bool:
    slug = skill_id.replace("generated.", "")
    for f in GEN_DIR.glob("*.skill.md"):
        if slug in f.stem:
            f.unlink()
            logger.info(f"Deleted: {f.name}")
            return True
    return False