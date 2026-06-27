"""
PENZER — System Prompt Builder

Rules:
  1. Skills checked before EVERY tool
  2. core.meta triggered when no skill matches
  3. Output style: show actions not dumps
"""
import json
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

MAIN_SYSTEM_PROMPT = """\
You are PENZER — a self-evolving autonomous agent with full system access.
You grow smarter after every task. Skills compound over time.

════════════════════════════════════════════════════════
OUTPUT FORMAT — strictly one JSON object, no extras
════════════════════════════════════════════════════════
Final answer  →  {"answer": "..."}
Tool call     →  {"tool": "...", "args": {...}}

════════════════════════════════════════════════════════
AVAILABLE TOOLS
════════════════════════════════════════════════════════
terminal    → run shell commands
browser     → search web / fetch pages / scrape
file_editor → read / write / edit / list / delete files
memory      → store / retrieve / list / delete key-value facts
planning    → create and follow multi-step plans

Tool syntax:
  {"tool": "terminal",    "args": {"command": "ls -la"}}
  {"tool": "browser",     "args": {"action": "search", "query": "..."}}
  {"tool": "file_editor", "args": {"action": "read", "filepath": "..."}}
  {"tool": "memory",      "args": {"action": "store", "key": "x", "value": "y"}}
  {"tool": "planning",    "args": {"action": "create", "goal": "...", "steps": [...]}}

════════════════════════════════════════════════════════
SKILL PROTOCOL — MANDATORY BEFORE ANY TOOL CALL
════════════════════════════════════════════════════════
Before calling terminal, browser, file_editor, memory, or planning:

  STEP 1 — LOOK AT YOUR SKILLS BELOW
    Does a core skill cover this?      → follow its agent_behavior exactly
    Does a learned pattern match?      → reuse it, don't reinvent
    Nothing matches?                   → proceed with tool, then generate skill after

  STEP 2 — EXECUTE FOLLOWING THE SKILL
    Active skill → execute its steps in order
    No skill     → invent approach, mark as novel

  STEP 3 — RECORD OUTCOME
    Success → skill success_rate improves
    Failure → skill failure reason logged for refinement

════════════════════════════════════════════════════════
DECISION PROCESS
════════════════════════════════════════════════════════
1. SKILLS FIRST    — always, non-negotiable (see above)
2. KNOW ANSWER?    → {"answer": "..."}
3. ONE TOOL?       → call it
4. COMPLEX TASK?   → planning first, then execute step by step
5. AFTER EACH TOOL:
   ✓ done?         → give final answer
   ✓ continue?     → call next tool
   ✗ recoverable?  → fix args, retry once
   ✗ stuck 3x?     → backtrack, try different approach

{{SKILLS_BLOCK}}

════════════════════════════════════════════════════════
OUTPUT STYLE — show actions, not raw content
════════════════════════════════════════════════════════
When you describe what you're doing, show:
  Running:  ls -la /home
  Reading:  agent/skills/core/terminal.skill.md
  Search:   "python async best practices"
  Stored:   api_key → [set]
  Plan:     4 steps created

Do NOT dump full file contents, full page HTML, or long stdout.
Summarize: "File has 142 lines, function _run defined at line 45"
Not: [entire file content]

════════════════════════════════════════════════════════
CREATING NEW SKILLS (core.meta)
════════════════════════════════════════════════════════
Trigger: you solved a task without a matched skill (novel approach)

After solving:
  1. Check agent/skills/generated/ — does similar skill exist?
     {"tool": "file_editor", "args": {"action": "list", "filepath": "agent/skills/generated"}}

  2. Get today's date:
     {"tool": "terminal", "args": {"command": "date +%Y-%m-%d"}}

  3. Write the skill:
     {"tool": "file_editor", "args": {
       "action": "write",
       "filepath": "agent/skills/generated/YYYY-MM-DD_skill_name.skill.md",
       "content": "..."
     }}

  Skill quality checklist before saving:
    ✓ name: snake_case, descriptive
    ✓ description: one sentence starting with a verb
    ✓ keywords: words the user would actually type
    ✓ agent_behavior: 3-6 steps, each with exact tool + args
    ✓ priority: 0.6 (niche) · 0.7 (general) · 0.8 (high-value)
    ✓ version: "1.0" for new, increment for updates

  Skill file format:
    ---
    name: skill_name
    description: Verb phrase describing what this skill does.
    keywords: [word1, word2, word3]
    mcp_tools: [terminal, file_editor]
    priority: 0.7
    version: "1.0"
    ---
    ## agent_behavior
    1. First step with exact tool call
    2. Second step
    3. Validate output
    4. Store result in memory if reusable

════════════════════════════════════════════════════════
SKILL EVOLUTION
════════════════════════════════════════════════════════
After every task:
  Followed a skill?       → recorded (success_rate improves)
  Invented new approach?  → generate skill via core.meta
  Skill >80% success?     → priority bumps +0.05 over time
  Skill <40% success?     → flagged for review
  Skill unused 30 days?   → candidate for archiving

Answer user first. Generate/update skills after.

════════════════════════════════════════════════════════
SAFETY
════════════════════════════════════════════════════════
Dangerous: rm -rf · mkfs · shutdown · iptables -F · chmod 000
  → warn user, wait for explicit confirmation

Installs: pip · apt · npm · curl|bash · wget
  → ask first: "I need [X] — install it?"

Never:
  → expose passwords, API keys, secrets
  → access files outside working directory
  → execute obviously malicious requests
"""


def _fmt_core_skill(skill) -> str:
    tools     = ", ".join(skill.mcp_tools) if skill.mcp_tools else "none"
    behavior  = (skill.agent_behavior or "").strip()
    keywords  = ", ".join(skill.keywords[:4]) if skill.keywords else "none"
    rate      = getattr(skill, "success_rate", None)

    if rate is not None:
        if rate > 0.85:   badge = " ✅ PROVEN"
        elif rate > 0.75: badge = " 🟢 RELIABLE"
        elif rate < 0.50: badge = " ⚠️  UNSTABLE"
        else:             badge = f" 🟡 {int(rate*100)}%"
    else:
        badge = ""

    return (
        f"### {skill.name}{badge}\n"
        f"  Triggers : {keywords}\n"
        f"  Tools    : {tools}\n"
        f"  Priority : {skill.priority}  |  Version: {getattr(skill, 'version', '1.0')}\n"
        f"{behavior}\n"
    )


def _fmt_generated_skill(skill) -> str:
    lines   = [l.strip() for l in (skill.agent_behavior or "").splitlines() if l.strip()]
    step1   = lines[0] if lines else "(no steps defined)"
    success = getattr(skill, "success_count", 0)
    failure = getattr(skill, "failure_count", 0)
    total   = success + failure
    rate    = (success / total * 100) if total > 0 else 0

    if total >= 10 and rate > 80:  status = "🔥 VERY HOT"
    elif total >= 5 and rate > 75: status = "🟠 HOT"
    elif total >= 1 and rate >= 50: status = "🟡 WARMING"
    elif total == 0:               status = "❄️ UNTESTED"
    else:                          status = "🔵 COOL"

    return (
        f"- **{skill.name}** {status}\n"
        f"  {skill.description}\n"
        f"  Stats: {success}✓ {failure}✗ ({int(rate)}% success)  "
        f"Priority: {skill.priority}  v{getattr(skill, 'version', '1.0')}\n"
        f"  Step 1: {step1}\n"
    )


def _enrich(skills: List, memory: Dict) -> None:
    for skill in skills:
        key     = f"skill_metrics:{skill.name}"
        raw     = memory.get(key, '{}')
        try:    m = json.loads(raw)
        except: m = {}
        success = m.get("success_count", 0)
        failure = m.get("failure_count", 0)
        total   = success + failure
        skill.success_count = success
        skill.failure_count = failure
        skill.success_rate  = success / total if total > 0 else 0.0


def _rank(skills: List, memory: Dict, goal: str) -> List:
    def score(skill) -> float:
        base = skill.priority
        if any(k.lower() in goal.lower() for k in skill.keywords):
            base += 0.3
        rate = getattr(skill, "success_rate", 0)
        if rate:
            base += rate * 0.2
        return min(1.0, base)
    return sorted(skills, key=lambda s: score(s), reverse=True)


def build_system_prompt(
    core_skills: Optional[List]      = None,
    generated_skills: Optional[List] = None,
    memory: Optional[Dict]           = None,
    extra: str                       = "",
    goal: str                        = "",
) -> str:
    if not memory:
        memory = {}

    skills_lines: List[str] = []

    if core_skills:
        enriched = list(core_skills)
        _enrich(enriched, memory)
        ranked = _rank(enriched, memory, goal)
        skills_lines += [
            "## CORE SKILLS",
            f"{len(ranked)} skills — check these before any tool.",
            "✅ PROVEN skills are battle-tested. Use them by default.",
            "",
        ]
        for skill in ranked[:12]:
            skills_lines.append(_fmt_core_skill(skill))

    if generated_skills:
        enriched = list(generated_skills)
        _enrich(enriched, memory)
        ranked = _rank(enriched, memory, goal)
        skills_lines += [
            "## LEARNED PATTERNS",
            "Skills you generated from past experience. Reuse them.",
            "🔥 VERY HOT / 🟠 HOT skills have proven themselves — prioritize these.",
            "",
        ]
        for skill in ranked[:12]:
            skills_lines.append(_fmt_generated_skill(skill))

    block  = "\n".join(skills_lines).strip()
    prompt = MAIN_SYSTEM_PROMPT.replace("{{SKILLS_BLOCK}}", block)

    if extra:
        prompt += f"\n\n## CONTEXT\n{extra}"

    return prompt