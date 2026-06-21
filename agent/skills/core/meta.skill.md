---
skill_id: core.meta
name: Skill Generator
description: Generate, save, and delete skills based on successful patterns
keywords: [meta, generate, skill, create, learn, remember how, save pattern, evolve]
mcp_tools: [file_editor, terminal]
agent_behavior: |

  GATE 1 — SHOULD I GENERATE?
    Generate only if ALL of these are true:
      - Task was non-trivial (not a single command or obvious answer)
      - Solution had a repeatable, generalizable pattern
      - No existing skill already covers this case
      - This type of task is likely to recur
    If any are false → skip, do not generate

  GENERATION PROCESS
    1. Pattern  — what specifically made this solution work?
                  Remove the one-off details, keep the reusable structure
    2. Steps    — write 3–6 agent_behavior steps, each with exact tool + command
                  Vague steps ("handle errors") are not allowed — be explicit
    3. Keywords — 5–7 words a real user would type, not internal jargon
    4. Priority — 0.6 niche use · 0.7 general · 0.8 high-value
                  Never >= 0.9 · Never equal to or above core skills
    5. Date     — run: terminal → date +%Y-%m-%d

  GATE 2 — QUALITY CHECK BEFORE SAVING
    description  : one sentence, starts with a verb ("Scan...", "Parse...", "Fix...")
    agent_behavior: every step names the exact tool and command, no hand-waving
    keywords     : would a user actually say these words?
    duplicate    : list generated/ first — if similar skill exists, stop
    If anything fails → revise before saving, do not save a weak skill

  SAVE (after passing quality gate)
    tool: file_editor
    action: write
    filepath: agent/skills/generated/YYYY-MM-DD_skill_name.skill.md
    content structure:
      ---
      skill_id: generated.skill_name
      name: Skill Name
      description: One sentence starting with a verb
      keywords: [kw1, kw2, kw3, kw4, kw5]
      mcp_tools: [tools, used]
      agent_behavior: |
        Step 1: ...
        Step 2: ...
        Step 3: ...
      priority: 0.7
      core: false
      generated_at: YYYY-MM-DD
      ---
      # Skill Name
      One line description.

  LIST EXISTING SKILLS
    tool: file_editor · action: list · filepath: agent/skills/generated

  GATE 3 — DELETE?
    Delete when:
      - User explicitly says a skill no longer applies
      - Skill has failed 3+ times in a row
      - A better generated skill fully replaces it
    tool: file_editor · action: delete · filepath: agent/skills/generated/YYYY-MM-DD_name.skill.md

  HARD RULES (never break these)
    - Never touch core skills: terminal · planning · memory · browser · file_editor · meta
    - Never set priority >= 0.9
    - Never save a duplicate
    - Never skip saving after a successful non-trivial task

priority: 1.0
core: true
version: "3.0"
---
# Skill Generator
Gate 1: worth generating? → Generate → Gate 2: quality check → Save → Gate 3: delete if outdated.