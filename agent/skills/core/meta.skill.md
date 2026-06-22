---
skill_id: core.meta
name: Skill Generator
description: Generate, update, and delete skills using file_editor after successful tasks
keywords: [meta, generate, skill, create, learn, remember how, save pattern, evolve]
mcp_tools: [file_editor, terminal]
agent_behavior: |

  GATE 1 — SHOULD I GENERATE?
    Generate only if ALL true:
      - Task was non-trivial (more than one tool call or complex reasoning)
      - Solution had a repeatable, generalizable pattern
      - No existing skill already covers this case
    If any false → skip

  STEP 1 — CHECK FOR DUPLICATES
    file_editor · list · agent/skills/generated
    Similar skill found → read it, update steps, increment version, write back
    Nothing similar    → proceed to create

  STEP 2 — GET DATE
    terminal · date +%Y-%m-%d

  STEP 3 — WRITE SKILL FILE
    file_editor · write · agent/skills/generated/YYYY-MM-DD_skill_name.skill.md

  TEMPLATE (replace all CAPS placeholders):
    skill_id: generated.SKILL_NAME
    name: SKILL NAME
    description: VERB + one sentence
    keywords: [kw1, kw2, kw3, kw4, kw5]
    mcp_tools: [tools, used]
    agent_behavior: |
      Step 1 with exact tool and command
      Step 2 with exact tool and command
    priority: 0.7
    core: false
    generated_at: YYYY-MM-DD

  QUALITY BAR
    description starts with a verb
    agent_behavior has 3-6 steps with exact tool per step
    keywords are words a user would actually type
    priority 0.6 niche / 0.7 general / 0.8 high-value / never >= 0.9

  GATE 2 — PRUNE DEAD SKILLS
    Generated skill failed 3 times in a row
    file_editor · delete · agent/skills/generated/FILENAME.skill.md

  HARD RULES
    Skills are files. Write them with file_editor. No other tool needed.
    Never touch core skills in agent/skills/core/
    Never set priority >= 0.9
    List first, never create a duplicate
    Answer the user first, generate the skill after

priority: 1.0
core: true
version: "3.0"
---
# Skill Generator
Skills are files. list then write with file_editor. Evolve after every task.