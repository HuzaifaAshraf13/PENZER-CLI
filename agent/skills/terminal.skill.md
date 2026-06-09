---
skill_id: terminal.executor
name: Terminal Skill
phase: terminal
description: Execute terminal commands safely. Explain before execution. Detect dangerous commands. Track changes. Validate results.
keywords: [terminal, shell, execute, bash, command, run, script]
mcp_tools: [terminal, run_bash, run_python, memory]
agent_behavior: |
  BEFORE EVERY COMMAND:
  - State what the command does and why in your thought
  - Dangerous patterns (rm -rf, dd, mkfs, chmod 000, reboot, iptables -F, shutdown):
    flag in thought and use force=True only if user confirmed
  
  EXECUTION:
  - Use terminal for single commands
  - Use run_bash for multi-line scripts
  - Use run_python for Python code
  - Always check exit_code in result — non-zero means failure
  
  VALIDATION:
  - If exit_code != 0: read stderr, diagnose, retry with fix or try different approach
  - If output is empty when output was expected: command may have silently failed — verify
  - Never assume success without checking exit_code
  
  ON FAILURE:
  - Do not repeat the exact same command
  - Change approach: different flags, different tool, different method
  - After 2 failures on same task: reflect and recover
priority: 1.0
version: 1.1
author: Penzer
---
# Terminal Skill
Execute commands safely. Always check exit_code. Never repeat a failed command unchanged.