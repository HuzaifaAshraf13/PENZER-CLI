---
skill_id: communication.notify
name: Communication Skill
phase: communication
description: Provide pre-action explanations, real-time progress, post-action summaries. Adapt verbosity to user preference.
keywords: [communication, progress, updates, verbosity, transparency]
mcp_tools: [memory]
agent_behavior: |
  **PRE-ACTION:**
  1. Explain what: "I will run [command]."
  2. Why: "Needed to [purpose]."
  3. Expected: "Result is [outcome]."
  4. Impact: "[impact]" or "Read-only; no changes."
  
  **REAL-TIME PROGRESS (tasks >10 seconds):**
  - Every 10-30 seconds: "[step] of [total] complete."
  
  **POST-ACTION:**
  1. What was done: [action].
  2. Outcome: [success/partial/failure].
  3. Key findings: [finding1], [finding2].
  4. Next steps: [action] or "Complete."
  5. Duration: [time].
  
  **ADAPT TO USER PREFERENCE:**
  - Query memory: user_verbosity_preference.
  - Terse: One-line summaries.
  - Normal: Pre/post explanations.
  - Verbose: All + reasoning + alternatives.
priority: 1.0
version: 1.0
author: Penzer
---

# Communication Skill

Explain before, update during, summarize after. Adapt to user preference.