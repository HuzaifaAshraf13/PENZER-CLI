---
skill_id: core.subagent
name: subagent
description: delegate a focused subtask to a child Penzer agent
keywords:
  - subagent
  - sub agent
  - spawn agent
  - delegate task
  - break down task
mcp_tools:
  - subagent
agent_behavior: |
  1. Identify a subgoal that can be handled independently.
  2. Use the subagent tool with a concise goal.
  3. Merge the child result into the main task.
priority: 0.95
core: true
version: 1.0
---
# Subagent
Delegate a focused subtask to a child Penzer agent when the main task is broad or can be decomposed.
