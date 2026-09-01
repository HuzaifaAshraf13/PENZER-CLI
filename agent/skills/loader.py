"""
Skill loader.

Core skills → always loaded, always shown to agent.
"""
import yaml
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta
from agent.skills.base import Skill

logger      = logging.getLogger(__name__)
SKILLS_DIR  = Path(__file__).parent
CORE_DIR    = SKILLS_DIR / "core"

def _parse(path: Path) -> Optional[Skill]:
    try:
        raw = path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) < 3:
            raise ValueError("missing frontmatter delimiter")

        meta = yaml.safe_load(parts[1])
        if not isinstance(meta, dict) or not meta:
            raise ValueError("frontmatter did not produce a mapping")

        skill_id = str(meta.get("skill_id") or path.stem)
        name = str(meta.get("name") or path.stem)
        description = str(meta.get("description") or "")
        keywords = meta.get("keywords") or []
        mcp_tools = meta.get("mcp_tools") or []
        agent_behavior = str(meta.get("agent_behavior") or "")
        priority = float(meta.get("priority", 0.5))
        core = bool(meta.get("core", False))
        version = str(meta.get("version", "1.0"))
        generated_at = None

        if not isinstance(keywords, list):
            raise ValueError("keywords must be a list")
        if not isinstance(mcp_tools, list):
            raise ValueError("mcp_tools must be a list")

        return Skill(
            skill_id=skill_id,
            name=name,
            description=description,
            keywords=keywords,
            mcp_tools=mcp_tools,
            agent_behavior=agent_behavior,
            priority=priority,
            core=core,
            version=version,
            generated_at=generated_at,
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