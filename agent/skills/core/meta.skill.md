---
skill_id: core.meta
name: Skill Generator
description: Generate new skills from successful patterns. Delete outdated ones. Evolve over time.
keywords: [meta, generate, skill, create, learn, new skill, pattern, evolve, save, remember how]
mcp_tools: [file_editor, memory]
agent_behavior: |
  GENERATE A SKILL WHEN:
  - You just solved a non-trivial task successfully
  - The solution had a repeatable pattern
  - No existing skill covered this exact case
  - This type of task is likely to come up again

  DO NOT GENERATE A SKILL WHEN:
  - Task was trivial (single command, obvious answer)
  - Task was too specific to ever repeat
  - A similar generated skill already exists

  GENERATION PROCESS:
  1. Identify the pattern — what made this solution work?
  2. Generalize the steps — remove specifics, keep the structure
  3. Pick 5-7 keywords — what would a user say to trigger this?
  4. Set priority 0.6-0.85 — never equal to or above core skills
  5. Get today's date: {"tool": "terminal", "args": {"command": "date +%Y-%m-%d"}}
  6. Write the skill file immediately

  EXACT TOOL CALL TO SAVE:
  {"tool": "file_editor", "args": {"action": "write", "filepath": "agent/skills/generated/YYYY-MM-DD_skill_name.skill.md", "content": "---\nskill_id: generated.skill_name\nname: Skill Name\ndescription: One line when to use this\nkeywords: [kw1, kw2, kw3, kw4, kw5]\nmcp_tools: [tools, used]\nagent_behavior: |\n  Step 1: ...\n  Step 2: ...\n  Step 3: ...\npriority: 0.7\ncore: false\ngenerated_at: YYYY-MM-DD\n---\n# Skill Name\nOne line description."}}

  DELETE A SKILL WHEN:
  - User says a skill no longer applies
  - Skill has failed 3+ times in a row
  - A better generated skill replaces it
  - {"tool": "file_editor", "args": {"action": "delete", "filepath": "agent/skills/generated/YYYY-MM-DD_name.skill.md"}}

  LIST EXISTING GENERATED SKILLS:
  {"tool": "file_editor", "args": {"action": "list", "filepath": "agent/skills/generated"}}

  QUALITY BAR FOR A GOOD SKILL:
  - description: one sentence, starts with a verb ("Scan...", "Parse...", "Fix...")
  - agent_behavior: 3-6 steps, each with exact tool and command
  - keywords: words a user would actually say, not technical jargon
  - priority: 0.7 for general, 0.8 for high-value, 0.6 for niche

  NEVER:
  - Modify core skills (terminal, planning, memory, browser, file_editor, meta)
  - Set priority >= 0.9
  - Generate duplicate skills
  - Skip saving after a successful non-trivial task
priority: 1.0
core: true
version: "2.1"
---
# Skill Generator
Learn from every success. Save the pattern. Delete what's outdated. Get smarter over time.