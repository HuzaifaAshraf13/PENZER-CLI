"""
Skill loader.
Core skills → always loaded, always shown to agent.
Generated skills → loaded, searched only when many exist.
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
GEN_DIR     = SKILLS_DIR / "generated"
MAX_AGE     = 30  # days


def _quarantine_generated_skill(path: Path) -> None:
    """Move a malformed generated skill artifact out of the live scan set."""
    if not path.exists():
        return
    corrupt_dir = path.parent / ".corrupt"
    corrupt_dir.mkdir(parents=True, exist_ok=True)
    quarantine = corrupt_dir / f"{path.name}.corrupt"
    try:
        path.replace(quarantine)
        logger.warning("Quarantined malformed generated skill: %s", path.name)
    except Exception:
        try:
            path.unlink()
            logger.warning("Removed malformed generated skill: %s", path.name)
        except Exception:
            logger.warning("Could not quarantine malformed generated skill: %s", path.name)


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
        generated_at = meta.get("generated_at")

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


def load_generated_skills() -> List[Skill]:
    if not GEN_DIR.exists():
        GEN_DIR.mkdir(parents=True, exist_ok=True)
        return []

    skills: List[Skill] = []
    cutoff = datetime.now() - timedelta(days=MAX_AGE)
    seen_ids = set()

    for f in sorted(GEN_DIR.glob("*.skill.md")):
        try:
            date = datetime.strptime(f.stem.split("_")[0], "%Y-%m-%d")
            if date < cutoff:
                f.unlink()
                logger.info("Pruned: %s", f.name)
                continue
        except (ValueError, IndexError):
            pass

        s = _parse(f)
        if not s:
            _quarantine_generated_skill(f)
            continue
        if s.skill_id in seen_ids:
            logger.warning("Skipping duplicate generated skill id %s from %s", s.skill_id, f.name)
            continue
        seen_ids.add(s.skill_id)
        skills.append(s)

    logger.info("Generated skills: %s", len(skills))
    return skills


def load_all_skills() -> dict:
    """
    Returns:
        {
          "core":      List[Skill],   # always shown to agent
          "generated": List[Skill],   # searched per request
        }
    """
    core      = load_core_skills()
    generated = load_generated_skills()
    logger.info(f"Total: {len(core)} core · {len(generated)} generated")
    return {"core": core, "generated": generated}


def save_generated_skill(
    name:        str,
    description: str,
    behavior:    str,
    tools:       List[str],
    keywords:    List[str],
    priority:    float = 0.7,
) -> bool:
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    date  = datetime.now().strftime("%Y-%m-%d")
    slug  = name.lower().replace(" ", "_")
    path  = GEN_DIR / f"{date}_{slug}.skill.md"

    if path.exists():
        return False

    behavior_indented = "\n  ".join(behavior.strip().splitlines())
    content = f"""---
skill_id: generated.{slug}
name: {name}
description: {description}
keywords: {keywords}
mcp_tools: {tools}
agent_behavior: |
  {behavior_indented}
priority: {priority}
core: false
generated_at: {date}
---
# {name}
{description}
"""
    try:
        path.write_text(content, encoding="utf-8")
        logger.info(f"✨ Saved new skill: {path.name}")
        return True
    except Exception as e:
        logger.error(f"Failed to save skill: {e}")
        return False


def delete_generated_skill(skill_id: str) -> bool:
    slug = skill_id.replace("generated.", "")
    for f in GEN_DIR.glob("*.skill.md"):
        if slug in f.stem:
            f.unlink()
            logger.info(f"Deleted: {f.name}")
            return True
    return False