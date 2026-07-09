"""
PENZER — System Prompt Builder

Fixes:
  1. _enrich() now loads real metrics from storage
  2. _rank() uses loaded success_rate correctly
  3. Generated skills show failure_modes
  4. Skill display includes usage stats from memory — fixed the field
     mapping. `get_skill_metric()` returns {"uses", "successes",
     "success_rate"}; this module was reading `success_count`/
     `failure_count`, which don't exist in that dict, so both silently
     read as 0 and every generated skill showed as "UNTESTED" regardless
     of actual track record. `success_rate` happened to work since that
     key name matched by coincidence, which is likely why this went
     unnoticed. _load_metrics now maps uses/successes correctly.
  5. `_fmt_core_skill`/`_fmt_generated_skill` now read `.priority` and
     `.description` defensively via getattr, matching how `_rank()`
     already treats them as possibly-missing (`getattr(s, "priority",
     0.5)`) instead of accessing them directly and risking an
     AttributeError on a skill object that lacks one.

CORRECTION NOTE: an earlier pass at this file rewrote the "OUTPUT FORMAT"
/ "Tool syntax" sections, believing agent.py's native `tool_calls`
handling meant the LLM used structured function-calling. That was wrong
— confirmed against agent/llm.py, whose `chat()` parses the model's raw
text as JSON (`{"answer": "..."}` / `{"tool": "...", "args": {...}}`)
and only then reshapes it into the `{"content", "tool_calls"}` dict
agent.py consumes. The JSON-in-text protocol below is correct and
matches llm.py's `_extract_json` exactly; it has been restored verbatim.

PLUGIN TOOL VISIBILITY FIX: `plugin_tool` didn't appear anywhere in
AVAILABLE TOOLS or the Tool syntax examples, and there was no way for a
currently-loaded plugin (auto-created from a repeated command, or
explicitly created by the model) to ever be listed for the model to see
— `list_plugin_tools()` in agent.py had zero call sites. Added
`plugin_tool` to the documented tool list, and a new `plugin_tools`
param to `build_system_prompt()` that renders a `## AVAILABLE PLUGIN
TOOLS` block from whatever's currently loaded, via
`{{PLUGIN_TOOLS_BLOCK}}`.

BELIEF STATE COMPLETION: `assumptions`/`unknowns` were described here as
part of the belief state, but there was no JSON key the model could
actually use to report them, `agent/llm.py`'s `chat()` never read any
such key, and `agent.py` never displayed them even if populated —
ReflAct's belief-state mechanism was running at half capacity. Added the
actual keys to the JSON examples here, matching the corresponding fix in
`agent/llm.py` and `agent.py`.
"""
import json
import logging
import re
from typing import Optional, List

from session.memory import get_relevant_kv_facts, get_skill_metric

logger = logging.getLogger(__name__)

STOPWORDS = {
    "about", "after", "all", "also", "and", "any", "are", "around", "before",
    "best", "between", "but", "can", "check", "create", "does", "during", "each",
    "for", "from", "give", "have", "how", "into", "its", "just", "make", "need",
    "next", "not", "only", "onto", "over", "plan", "that", "the", "their", "then",
    "there", "this", "through", "time", "to", "use", "using", "very", "what",
    "when", "where", "which", "with", "would", "your", "you"
}

MAIN_SYSTEM_PROMPT = """\
You are PENZER — a self-evolving autonomous agent with full system access.
You learn from every task. Skills compound. Memory persists.

════════════════════════════════════════════════════════
OUTPUT FORMAT — single JSON object only
════════════════════════════════════════════════════════
Final answer  →  {"answer": "..."}
Tool call     →  {"tool": "...", "args": {...}}

════════════════════════════════════════════════════════
AVAILABLE TOOLS
════════════════════════════════════════════════════════
terminal    → run shell commands
browser     → search web, fetch pages, scrape
file_editor → read / write / edit / list / delete files
memory      → store / retrieve / list / delete key-value facts
planning    → create and follow multi-step plans
plugin_tool → manually create a new reusable tool when you expect a
              specific workflow to repeat across this task or future ones

Tool syntax:
  {"tool": "terminal",    "args": {"command": "ls -la"}}
  {"tool": "browser",     "args": {"action": "search", "query": "..."}}
  {"tool": "file_editor", "args": {"action": "read", "filepath": "..."}}
  {"tool": "memory",      "args": {"action": "store", "key": "x", "value": "y"}}
  {"tool": "memory",      "args": {"action": "get", "key": "x"}}
  {"tool": "memory",      "args": {"action": "list"}}
  {"tool": "memory",      "args": {"action": "delete", "key": "x"}}
  {"tool": "planning",    "args": {"action": "create", "goal": "...", "steps": [...]}}
  {"tool": "plugin_tool", "args": {"action": "create", "name": "snake_case_name",
                                    "description": "...", "code": "def snake_case_name(**kwargs): ..."}}

Once a plugin tool is created, call it directly BY NAME like any other
tool — {"tool": "your_plugin_name", "args": {...}} — do not route back
through plugin_tool to use it. The agent also auto-creates a plugin on
its own when it notices you've run the exact same terminal command
twice; you don't need to do that yourself, but you can still hand-write
one for anything more structured than a shell command.

{{PLUGIN_TOOLS_BLOCK}}

Note: the "memory" tool is a simple key-value store (built-in, not MCP).
Use it to persist facts the user explicitly shares (preferences, project paths,
env details) — separate from your own episodic/semantic memory which updates
automatically after every task.

════════════════════════════════════════════════════════
BELIEF STATE — read before every action
════════════════════════════════════════════════════════
You maintain an explicit belief state at all times:
  - goal_progress : not_started | in_progress | blocked | complete
  - verified_facts: things confirmed true by tool results
  - assumptions   : things you're assuming (not confirmed)
  - unknowns      : things still to find out

Your BELIEF injection each turn shows what's currently tracked. To
update assumptions/unknowns, include them directly in your JSON output:
  {"tool": "...", "args": {...}, "assumptions": ["..."], "unknowns": ["..."]}
  {"answer": "...", "assumptions": ["..."], "unknowns": ["..."]}
Both are optional and get replaced each turn with whatever you provide —
they reflect your CURRENT understanding, not a running log. Omit them
entirely if nothing's changed.

Before each action:
  "Given my belief state and goal, what is the next step?"
  "Does the last result contradict what I believed?"
  "Am I closer to the goal or further away?"

If BLOCKED:
  → Do not repeat the same action
  → Change approach entirely

════════════════════════════════════════════════════════
INJECTED CONTEXT — read at the start of every task
════════════════════════════════════════════════════════
Below the skills block you may see these sections — they are real data
retrieved from memory for THIS specific task, not generic advice:

  ## Memory / ## Relevant Memory  — episodic events + semantic patterns
  ## Recalled Insights            — cross-task rules (ExpeL) that generalize
  ## Similar Past Runs            — past episodes with similar goals
  ## Past Experience              — post-mortems: what worked/failed last time

Use these BEFORE acting. If "Past Experience" says a tool failed last time
for this kind of task, do not repeat that exact failure — try the alternative
noted in "next_time". If a "Stored Facts" section is present and it contains a
relevant fact, use it before asking the user again.

════════════════════════════════════════════════════════
SKILL PROTOCOL — MANDATORY before any tool call
════════════════════════════════════════════════════════
STEP 1: Check YOUR SKILLS below
  Core skill matches?      → follow agent_behavior exactly
  Generated skill matches? → reuse it, don't reinvent
  Multiple skills match?   → follow MULTI-SKILL PLAN shown in [ReflAct]
  Nothing matches?         → proceed, generate skill after if 3+ tools used

STEP 2: Execute following the skill steps in order

STEP 3: Record outcome — success improves skill priority over time

════════════════════════════════════════════════════════
MULTI-SKILL EXECUTION
════════════════════════════════════════════════════════
When 2+ skills match, a SKILL PLAN is built and shown in each [ReflAct]:
  SKILL PLAN [done/total steps]
    [skill_name] step N: instruction

Rules:
  1. Follow plan in order — do not skip steps
  2. Steps using DIFFERENT tools → can run in parallel
     Steps using SAME tool       → run sequentially
  3. After each tool result: mark step done, move to next
  4. All steps done → synthesize results, give final answer

Tool routing — result feeds next step:
  memory      → feeds planning / reasoning
  browser     → feeds file_editor / terminal (save the data)
  terminal    → feeds file_editor (process output)
  file_editor → feeds terminal / browser (use the file)

If a step fails:
  → Try fallback tool once
  → Skip non-critical step, note failure
  → Never abandon full plan because one step failed

════════════════════════════════════════════════════════
DECISION PROCESS
════════════════════════════════════════════════════════
1. BELIEF STATE  — what do I know? what am I assuming?
2. SKILL PLAN?   — merged plan active? follow step by step
3. SINGLE SKILL? — follow its agent_behavior in order
4. NO SKILLS?    — reason about best tool sequence
5. KNOW ANSWER?  → {"answer": "..."}
6. ONE TOOL?     → call it
7. COMPLEX TASK? → follow subtask plan shown in [Executor]
8. AFTER TOOL:
   ✓ done?       → update belief, mark step done, answer or continue
   ✓ continue?   → update belief, call next tool in plan
   ✗ failed?     → update belief (blocked), try fallback
   ✗ stuck 3x?   → rethink entirely

{{SKILLS_BLOCK}}

════════════════════════════════════════════════════════
OUTPUT STYLE — actions not dumps
════════════════════════════════════════════════════════
Show:  Running: ls -la | Reading: config.py | Search: "python docs"
Never: dump full file contents, long stdout, raw HTML

════════════════════════════════════════════════════════
GENERATING NEW SKILLS — trajectory-informed
════════════════════════════════════════════════════════
Trigger: solved a novel task with 3+ tool calls

Steps:
  1. List agent/skills/generated/ — similar skill exists? update it
  2. Get date: terminal → date +%Y-%m-%d
  3. Write .skill.md with:
     - name        : snake_case descriptive name
     - description : one verb phrase
     - keywords    : words user would type
     - mcp_tools   : tools actually used
     - priority    : 0.7 for new
     - agent_behavior : exact winning tool sequence, step by step
     - failure_modes  : what failed and why, what to avoid

Quality checklist:
  ✓ agent_behavior = the exact tool sequence that worked
  ✓ failure_modes  = concrete warnings from this run
  ✓ No duplicates — check generated/ first
  ✓ Keywords = what a user would actually type

════════════════════════════════════════════════════════
SELF-EVOLUTION — after every complex task
════════════════════════════════════════════════════════
  Used a skill?      → metrics tracked automatically per tool call
  Invented approach? → generate skill via instructions above
  Skill >80% success → priority bumps over time
  Skill <40% success → flagged for review

Answer user first. Generate/update skills silently after.

════════════════════════════════════════════════════════
SAFETY
════════════════════════════════════════════════════════
Dangerous commands (rm -rf · mkfs · shutdown · iptables -F · chmod 000)
  → warn user, wait for explicit confirmation

Installs (pip · apt · npm · curl|bash · wget)
  → ask first: "I need [X] — ok to install?"

Never expose passwords, API keys, or private data
Never access files outside working directory
"""


def _load_metrics(skill_name: str) -> dict:
    """
    Load real metrics from storage for a skill, normalized to the
    {"success_count", "failure_count", "success_rate"} shape this module
    uses for display. `get_skill_metric()` itself returns
    {"uses", "successes", "success_rate"} — mapped here rather than
    changing memory.py's return shape, since agent.py's own
    `_tool_confidence` already consumes `success_rate` from that same
    dict directly and has no need for the other two.
    """
    try:
        m = get_skill_metric(skill_name)
        uses      = m.get("uses", 0)
        successes = m.get("successes", 0)
        return {
            "success_count": successes,
            "failure_count": max(0, uses - successes),
            "success_rate":  m.get("success_rate", 0.0),
        }
    except Exception:
        return {"success_count": 0, "failure_count": 0, "success_rate": 0.0}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {
        token for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def _skill_token_set(skill) -> set[str]:
    tokens = set()
    for field in [skill.name, getattr(skill, "description", ""), *(skill.keywords or [])]:
        tokens.update(_tokenize(field))
    return tokens


def _fmt_core_skill(skill) -> str:
    tools    = ", ".join(skill.mcp_tools or []) or "none"
    behavior = (skill.agent_behavior or "").strip()
    keywords = ", ".join(skill.keywords[:4]) if skill.keywords else "none"
    priority = getattr(skill, "priority", 0.5)
    version  = getattr(skill, "version", "1.0")

    rate = getattr(skill, "success_rate", None)
    if rate is not None:
        if rate > 0.85:   badge = " ✅ PROVEN"
        elif rate > 0.75: badge = " 🟢 RELIABLE"
        elif rate < 0.50 and rate > 0: badge = " ⚠️  UNSTABLE"
        elif rate == 0:   badge = ""
        else:             badge = f" 🟡 {int(rate*100)}%"
    else:
        badge = ""

    return (
        f"### {skill.name}{badge}\n"
        f"  Triggers : {keywords}\n"
        f"  Tools    : {tools}\n"
        f"  Priority : {priority}  v{version}\n"
        f"{behavior}\n"
    )


def _fmt_generated_skill(skill) -> str:
    lines       = [l.strip() for l in (skill.agent_behavior or "").splitlines() if l.strip()]
    step1       = lines[0] if lines else "(no steps)"
    description = getattr(skill, "description", "")
    priority    = getattr(skill, "priority", 0.5)
    version     = getattr(skill, "version", "1.0")
    success     = getattr(skill, "success_count", 0)
    failure     = getattr(skill, "failure_count", 0)
    total       = success + failure
    rate        = (success / total * 100) if total > 0 else 0

    if total >= 10 and rate > 80:   status = "🔥 VERY HOT"
    elif total >= 5 and rate > 75:  status = "🟠 HOT"
    elif total >= 1 and rate >= 50: status = "🟡 WARMING"
    elif total == 0:                status = "❄️ UNTESTED"
    else:                           status = "🔵 COOL"

    # Show failure_modes if present
    failure_modes = getattr(skill, "failure_modes", "") or ""
    failure_line  = f"  Avoid  : {failure_modes[:100]}\n" if failure_modes else ""

    return (
        f"- **{skill.name}** {status}\n"
        f"  {description}\n"
        f"  {success}✓ {failure}✗ ({int(rate)}%) | "
        f"Priority: {priority} v{version}\n"
        f"  Step 1: {step1}\n"
        f"{failure_line}"
    )


def _enrich(skills: List) -> None:
    """Load real metrics from storage into each skill object."""
    for skill in skills:
        m = _load_metrics(skill.name)
        skill.success_count = m.get("success_count", 0)
        skill.failure_count = m.get("failure_count", 0)
        skill.success_rate  = m.get("success_rate", 0.0)


def _rank(skills: List, goal: str) -> List:
    """Rank by task-language overlap, keyword match, and proven success rate."""
    goal_tokens = _tokenize(goal)
    if not goal_tokens:
        return sorted(
            skills,
            key=lambda s: (
                getattr(s, "priority", 0.5),
                getattr(s, "success_rate", 0.0),
            ),
            reverse=True,
        )

    def score(skill) -> float:
        base = float(getattr(skill, "priority", 0.5))
        skill_tokens = _skill_token_set(skill)
        overlap = goal_tokens & skill_tokens

        if overlap:
            base += min(0.7, len(overlap) * 0.16)

        keyword_hits = 0
        for kw in skill.keywords or []:
            if _tokenize(kw) & goal_tokens:
                keyword_hits += 1
        if keyword_hits:
            base += min(0.4, keyword_hits * 0.12)

        if any(token in goal_tokens for token in _tokenize(skill.name)):
            base += 0.08

        base += getattr(skill, "success_rate", 0.0) * 0.15
        return min(1.0, base)

    return sorted(skills, key=lambda s: score(s), reverse=True)


def _fmt_plugin_tools_block(plugin_tools: Optional[dict]) -> str:
    """
    Render currently-loaded plugin tools so the model actually knows they
    exist and are callable by name. Previously `list_plugin_tools()` had
    zero call sites anywhere in agent.py — a plugin could be created (auto
    or explicit) and the model would never learn it exists unless it
    happened to re-derive the exact same name itself.
    """
    if not plugin_tools:
        return ""
    lines = ["## AVAILABLE PLUGIN TOOLS", "Created earlier — call these directly by name:"]
    for name, description in sorted(plugin_tools.items()):
        lines.append(f"  {name} → {description}")
    return "\n".join(lines) + "\n"


def _format_kv_context(goal: str, memory_context: str = "") -> str:
    facts = get_relevant_kv_facts(goal, n=3)
    if not facts:
        return memory_context
    lines = ["## Stored Facts"]
    for fact in facts:
        lines.append(f"- {fact['key']}: {fact['value']}")
    if memory_context:
        return f"{memory_context}\n\n" + "\n".join(lines)
    return "\n".join(lines)


def build_system_prompt(
    core_skills: Optional[List]      = None,
    generated_skills: Optional[List] = None,
    memory_context: str              = "",
    extra: str                       = "",
    goal: str                        = "",
    plugin_tools: Optional[dict]     = None,
) -> str:
    skills_lines: List[str] = []

    if core_skills:
        _enrich(core_skills)
        ranked = _rank(core_skills, goal)
        skills_lines += [
            "## CORE SKILLS",
            f"{len(ranked)} skills — check before any tool.",
            "✅ PROVEN = battle-tested. Use by default.",
            "",
        ]
        if ranked:
            best = ranked[0]
            skills_lines.append(f"Best fit for this task: {best.name} — {getattr(best, 'description', '')}")
            skills_lines.append("")
        for skill in ranked[:12]:
            skills_lines.append(_fmt_core_skill(skill))

    if generated_skills:
        _enrich(generated_skills)
        ranked = _rank(generated_skills, goal)
        skills_lines += [
            "## LEARNED PATTERNS",
            "Skills generated from past runs. Capture exact winning sequences.",
            "🔥 HOT = proven multiple times. Prioritize these.",
            "",
        ]
        for skill in ranked[:12]:
            skills_lines.append(_fmt_generated_skill(skill))

    block  = "\n".join(skills_lines).strip()
    prompt = MAIN_SYSTEM_PROMPT.replace("{{SKILLS_BLOCK}}", block)
    prompt = prompt.replace("{{PLUGIN_TOOLS_BLOCK}}", _fmt_plugin_tools_block(plugin_tools))

    if memory_context:
        prompt += f"\n\n{_format_kv_context(goal, memory_context)}"
    elif goal:
        prompt += f"\n\n{_format_kv_context(goal)}"

    if extra:
        prompt += f"\n\n## CONTEXT\n{extra}"

    return prompt