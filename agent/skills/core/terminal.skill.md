---
skill_id: core.terminal
name: Terminal Executor
description: Run bash commands, scripts, and Python code safely and efficiently
keywords: [terminal, bash, shell, command, execute, run, script, python]
mcp_tools: [terminal, run_bash, run_python]
agent_behavior: |
  TOOL SELECTION:
  - Single command → terminal
  - Multi-line script → run_bash
  - Python code → run_python

  BEFORE EXECUTION:
  - Flag dangerous commands: rm -rf, dd, mkfs, shutdown, iptables -F, chmod 000
  - Never install packages without user permission
  - Always use built-in tools first

  BUILT-IN TOOLS CHEATSHEET (use these, never install alternatives):
  - Network usage per app:  ss -tp | grep ESTAB
  - Active connections:     netstat -tp 2>/dev/null || ss -tp
  - Top memory usage:       ps aux --sort=-%mem | head -20
  - Top CPU usage:          ps aux --sort=-%cpu | head -20
  - Disk usage:             df -h && du -sh /* 2>/dev/null | sort -rh | head -10
  - Open files/ports:       lsof -i -n -P | head -20
  - Running processes:      ps aux | grep -v grep
  - Network interfaces:     cat /proc/net/dev
  - Free memory:            free -h
  - System info:            uname -a && uptime

  AFTER EXECUTION:
  - Check exit_code — non-zero = failure
  - Read stderr, diagnose, retry with different approach
  - Never repeat the exact same failed command

  ON REPEATED FAILURE:
  - Try a completely different built-in command
  - After 2 failures: reflect and change strategy entirely
priority: 1.0
core: true
version: "2.1"
---
# Terminal Executor
Use built-in tools. Never install. Check exit_code. Never repeat failures.