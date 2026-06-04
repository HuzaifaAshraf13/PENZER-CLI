---
skill_id: rollback.changes
name: Rollback Skill
phase: rollback
description: Maintain change logs. Undo changes cleanly in reverse order on failure or user request. Verify state after rollback.
keywords: [rollback, undo, changes, recovery]
mcp_tools: [memory, terminal]
agent_behavior: |
  **CHANGE LOG STRUCTURE:**
  - timestamp, command_executed, old_state, new_state, reverse_command, tool_used.
  
  **WHEN TO ROLLBACK:**
  - User requests: "Undo the last N changes."
  - On terminal.skill failure.
  - On unexpected state detected.
  
  **ROLLBACK ALGORITHM:**
  1. Retrieve change_log from memory (reverse timestamp order).
  2. Build rollback_plan: [reverse_cmd_1, ..., reverse_cmd_N].
  3. Confirm: "Rollback will execute: [plan]. Proceed?"
  4. Execute rollback_plan in order.
  5. Verify each reverse_cmd succeeded.
  6. If fails: stop, alert user.
  
  **STATE VERIFICATION:**
  1. After rollback, inspect environment (pwd, user, permissions).
  2. Compare to pre-task snapshot.
  3. Log discrepancy in memory if not matching.
priority: 1.0
version: 1.0
author: Penzer
---

# Rollback Skill

Track every change. Undo in reverse order. Verify state.