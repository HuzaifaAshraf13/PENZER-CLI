---
skill_id: planning.decompose
name: Planning Skill
phase: planning
description: Decompose complex pentesting tasks into ordered, dependency-aware subtasks. Maintain end goal visibility. Replan dynamically when conditions change.
keywords: [planning, decomposition, roadmap, replan, dependencies]
mcp_tools: [memory]
agent_behavior: |
  **WHEN TO PLAN:**
  - When user request is ambiguous or multi-step.
  - Before starting execution on any task >2 steps.
  - When unexpected results occur (triggers replan).
  
  **DECOMPOSITION ALGORITHM:**
  1. Parse user request for end goal and constraints (scope, time, tools available).
  2. Identify required subtasks in dependency order (what must happen first?).
  3. For each subtask: map needed tools, expected output, validation criteria.
  4. Create ordering: parallel tasks can run together, sequential tasks have waiting edges.
  5. Estimate time/complexity for each subtask and total.
  6. Store plan in memory with goal, subtasks[], validation_points[], estimated_duration.
  
  **DURING EXECUTION:**
  - Execute subtasks in planned order.
  - At each validation_point, check: "Did this produce expected output?" If no, trigger replan.
  - Keep end goal visible: if divergence detected, explain divergence and ask user for redirect.
  - Track actual vs estimated time; if much slower, trigger replan.
  
  **REPLAN TRIGGERS:**
  - Unexpected command failures (tool returned error).
  - Unexpected output (tool ran, output doesn't match expectation).
  - New constraints discovered during execution (tool told us something blocking original plan).
  - Time budget exceeded.
  
  **REPLAN EXECUTION:**
  1. Preserve completed subtasks (don't repeat).
  2. Analyze why divergence occurred: missing knowledge? wrong tool? wrong order?
  3. Generate new plan for remaining work based on actual state.
  4. Continue from new plan.
priority: 1.0
version: 1.0
author: Penzer
---

# Planning Skill

Do not wing it. Always decompose complex tasks into ordered subtasks with clear dependencies and validation points. Replan when reality diverges from plan.
