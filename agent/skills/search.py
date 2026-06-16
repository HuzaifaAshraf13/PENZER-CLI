"""Semantic search for relevant skills based on user request and context."""
from typing import List, Dict, Tuple, Optional
from pathlib import Path
from agent.skills.base import Skill, PentestPhase
import logging
import yaml

logger = logging.getLogger(__name__)


# ── Skill Loader ──────────────────────────────────────────────────────────────

def _parse_phase(phase_str: str) -> PentestPhase:
    """Map any phase string to a PentestPhase, defaulting to UNKNOWN."""
    try:
        return PentestPhase(phase_str.lower())
    except ValueError:
        return PentestPhase.UNKNOWN


def load_skills_from_markdown(skills_dir: Optional[str] = None) -> List[Skill]:
    """
    Scan skills_dir for *.skill.md files, parse YAML frontmatter,
    and return a list of Skill objects.
    """
    if skills_dir is None:
        skills_dir = Path(__file__).parent
    else:
        skills_dir = Path(skills_dir)

    skills: List[Skill] = []

    for md_file in sorted(skills_dir.glob("*.skill.md")):
        try:
            raw = md_file.read_text(encoding="utf-8")

            if not raw.startswith("---"):
                logger.warning(f"No frontmatter in {md_file.name}, skipping")
                continue

            parts = raw.split("---", 2)
            if len(parts) < 3:
                logger.warning(f"Malformed frontmatter in {md_file.name}, skipping")
                continue

            meta = yaml.safe_load(parts[1])
            if not meta or not isinstance(meta, dict):
                logger.warning(f"Empty or invalid YAML in {md_file.name}, skipping")
                continue

            skill = Skill(
                skill_id=meta.get("skill_id", md_file.stem),
                name=meta.get("name", md_file.stem),
                phase=_parse_phase(meta.get("phase", "unknown")),
                description=meta.get("description", ""),
                keywords=meta.get("keywords", []),
                mcp_tools=meta.get("mcp_tools", []),
                agent_behavior=meta.get("agent_behavior", ""),
                next_phase=meta.get("next_phase"),
                supports_async=meta.get("supports_async", True),
                version=str(meta.get("version", "1.0")),
                author=meta.get("author", "Penzer"),
                priority=float(meta.get("priority", 0.5)),
            )
            skills.append(skill)
            logger.debug(f"Loaded skill: {skill.skill_id} from {md_file.name}")

        except Exception as e:
            logger.error(f"Failed to load {md_file.name}: {e}")

    logger.info(f"Loaded {len(skills)} skills from {skills_dir}")
    return skills


# ── Semantic Search ───────────────────────────────────────────────────────────

def semantic_search_skills(
    user_request: str,
    available_skills: List[Skill],
    memory: Dict = None,
    top_k: int = 3,
    context: str = ""
) -> List[Skill]:
    if not available_skills:
        return []

    combined = f"{user_request} {context}".lower()
    tokens = set(combined.split())

    skill_scores: List[Tuple[Skill, float]] = []

    for skill in available_skills:
        score = 0.0

        if skill.name.lower() in combined:
            score += 10.0

        if skill.keywords:
            score += sum(3.0 for kw in skill.keywords if kw.lower() in combined)

        desc_lower = skill.description.lower()
        score += sum(0.5 for t in tokens if len(t) > 2 and t in desc_lower)

        score += skill.priority * 0.1

        if score > 0:
            skill_scores.append((skill, score))

    skill_scores.sort(key=lambda x: x[1], reverse=True)

    threshold = 1.5
    result = [s for s, sc in skill_scores if sc >= threshold]

    if result:
        logger.info(f"Semantic search found {len(result)} relevant skills")
    else:
        logger.debug("No matching skills — agent will reason directly")

    return result


# ── Context & Prompt Helpers ──────────────────────────────────────────────────

def build_context_from_history(history: list, last_n: int = 6) -> str:
    """Extract recent messages as context — handles both str and dict content."""
    context_parts = []
    for msg in history[-last_n:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ["tool", "assistant"]:
            continue
        if isinstance(content, str):
            context_parts.append(content[:300])
        elif isinstance(content, dict):
            text = content.get("thought") or content.get("content", "")
            if text:
                context_parts.append(str(text)[:300])
    return " ".join(context_parts)


def format_relevant_skills_for_prompt(skills: List[Skill]) -> str:
    if not skills:
        return ""
    lines = ["RELEVANT SKILLS:"]
    for i, skill in enumerate(skills, 1):
        lines.append(f"\n{i}. {skill.name}")
        lines.append(f"   {skill.description}")
        if skill.agent_behavior:
            lines.append(f"   Guidance: {skill.agent_behavior}")
        if skill.mcp_tools:
            lines.append(f"   Tools: {', '.join(skill.mcp_tools)}")
    return "\n".join(lines)