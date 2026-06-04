---
skill_id: pattern.detect
name: Pattern Skill
phase: pattern
description: Detect behavioral patterns in user requests and prior executions. Adapt defaults and workflows.
keywords: [pattern, personalization, behavior, adaptation, learning]
mcp_tools: [memory]
agent_behavior: |
  **PATTERN DETECTION:**
  1. After every task: record task_type, domain, context, approach, outcome.
  2. Periodically (every 20 tasks): analyze for patterns.
  3. Detect: "In [domain], user always does [approach]", "User prefers [style]", "Frequent tasks: [task]".
  
  **ADAPTATION:**
  1. Based on patterns, propose adapted defaults: "You usually do [approach]; default to it?"
  2. Adapt communication.skill verbosity based on user patterns.
  3. Pre-suggest next likely task.
  
  **USER PREFERENCE LEARNING:**
  - Detect: confirmation_preference, verbosity, tool_preference.
  - Store: user_preferences = {pref: value}.
  - Propose: "I notice [style]. Adopt [adapted_style]?"
  
  **WORKFLOW ADAPTATION:**
  - If user frequently does [A, B, C] in sequence: create macro.
  - Suggest: "You usually do [A]->[B]->[C]. Create macro?"
priority: 1.0
version: 1.0
author: Penzer
---

# Pattern Skill

Detect user patterns. Adapt defaults. Learn preferences. Suggest macros.