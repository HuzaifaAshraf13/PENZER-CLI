---
skill_id: reasoning.reflect
name: Reasoning Skill
phase: reasoning
description: Surface assumptions, edge cases, risks. Consider alternatives. Reason before every major decision. Reflect after completion.
keywords: [reasoning, reflection, assumptions, alternatives, risks]
mcp_tools: [memory]
agent_behavior: |
  **BEFORE ACTING:**
  1. State the goal: "I am trying to [goal] because [reason]."
  2. List assumptions: "I assume [X], [Y], [Z]..."
  3. Identify edge cases and risks.
  4. Generate alternatives: "I could [alt1], [alt2], [alt3]."
  5. Choose with reasoning: "I choose [alt1] because [reasoning]."
  
  **DURING EXECUTION:**
  - Notice divergence from assumptions. Flag immediately.
  - Stop and reconsider if assumptions invalidated.
  
  **AFTER COMPLETION:**
  1. What worked? What failed? Why?
  2. Which assumptions held? Which were wrong?
  3. Store lessons in memory.
priority: 1.0
version: 1.0
author: Penzer
---

# Reasoning Skill

Think out loud. State assumptions and risks BEFORE acting.