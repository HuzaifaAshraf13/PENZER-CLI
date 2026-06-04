---
skill_id: terminal.executor
name: Terminal Skill
phase: terminal
description: Execute terminal commands safely. Explain before execution. Detect dangerous commands. Track changes. Validate results.
keywords: [terminal, shell, execute, safe, commands]
mcp_tools: [terminal, memory]
agent_behavior: |
  **BEFORE ANY COMMAND:**
  1. Explain what it does and why.
  2. State expected outcome.
  3. Detect dangerous patterns:
     - rm -rf on critical paths, chmod 000, dd, mkfs, systemctl stop, reboot, iptables -F
     - If dangerous: flag "⚠️ DANGEROUS" and require "confirm" from user.
  4. Execute if safe.
  
  **EXECUTION:**
  1. Run command via terminal tool.
  2. Capture stdout, stderr, exit_code.
  3. Store in memory: command, output, exit_code, timestamp, environment.
  
  **VALIDATION:**
  1. Check exit_code.
  2. Compare actual output to expected.
  3. If mismatch: analyze and retry or fallback.
  4. If fails: trigger failure.skill to log context.
  
  **CHANGE TRACKING:**
  - Before state-changing commands, snapshot system.
  - After execution, capture new state.
  - Create change_log: timestamp, command, old_state, new_state, reverse_command.
priority: 1.0
version: 1.0
author: Penzer
---

# Terminal Skill

Explain before executing. Detect dangerous commands. Track changes. Validate results.