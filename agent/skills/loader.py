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


def _parse(path: Path) -> Optional[Skill]:
    try:
        raw   = path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) < 3:
            return None
        meta = yaml.safe_load(parts[1])
        if not meta:
            return None
        return Skill(
            skill_id       = meta.get("skill_id", path.stem),
            name           = meta.get("name", path.stem),
            description    = meta.get("description", ""),
            keywords       = meta.get("keywords", []),
            mcp_tools      = meta.get("mcp_tools", []),
            agent_behavior = meta.get("agent_behavior", ""),
            priority       = float(meta.get("priority", 0.5)),
            core           = bool(meta.get("core", False)),
            version        = str(meta.get("version", "1.0")),
            generated_at   = meta.get("generated_at"),
        )
    except Exception as e:
        logger.error(f"Failed to parse {path.name}: {e}")
        return None


def load_core_skills() -> List[Skill]:
    if not CORE_DIR.exists():
        return []
    skills = [s for f in sorted(CORE_DIR.glob("*.skill.md")) if (s := _parse(f))]
    logger.info(f"Core skills: {[s.name for s in skills]}")
    return skills


def load_generated_skills() -> List[Skill]:
    if not GEN_DIR.exists():
        GEN_DIR.mkdir(parents=True, exist_ok=True)
        return []

    skills  = []
    cutoff  = datetime.now() - timedelta(days=MAX_AGE)

    for f in sorted(GEN_DIR.glob("*.skill.md")):
        # Prune old skills
        try:
            date = datetime.strptime(f.stem.split("_")[0], "%Y-%m-%d")
            if date < cutoff:
                f.unlink()
                logger.info(f"Pruned: {f.name}")
                continue
        except (ValueError, IndexError):
            pass

        s = _parse(f)
        if s:
            skills.append(s)

    logger.info(f"Generated skills: {len(skills)}")
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