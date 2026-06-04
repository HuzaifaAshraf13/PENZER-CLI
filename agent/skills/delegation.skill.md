---
skill_id: delegation.spawn
name: Delegation Skill
phase: delegation
description: Spawn sub-agents for parallelizable tasks. Coordinate results. Handle sub-agent failures gracefully.
keywords: [delegation, sub-agent, parallel, coordination]
mcp_tools: [memory]
agent_behavior: |
  **WHEN TO DELEGATE:**
  - Multiple independent parallelizable subtasks (e.g., scan 100 hosts = 10 x 10).
  - Self-contained subtask with clear success/failure criteria.
  - Parallel execution saves significant time.
  
  **SUB-AGENT CONTRACT:**
  1. Define input: "Task: [desc]. Input: [data]. Output: [schema]."
  2. Define timeout: "Complete within [N] seconds or timeout."
  3. Failure handling: "Log error, continue (don't block parent)."
  4. Communication: "Report status every [interval] or on completion."
  
  **SPAWNING:**
  1. Partition into [N] sub-tasks with contract.
  2. Spawn N sub-agents in parallel (up to 4-8 concurrent).
  3. Store sub-agent_ids and wait for completion or timeout.
  
  **RESULT COORDINATION:**
  1. Collect [result_1, ..., result_N].
  2. Filter: successes=[], failures=[].
  3. Merge successes (concat lists, aggregate counts).
  4. Log failures for audit.
  5. If >50%, partial_success. If >90%, success.
priority: 1.0
version: 1.0
author: Penzer
---

# Delegation Skill

Parallelize independent tasks. Coordinate results. Handle failures gracefully.