---
skill_id: core.terminal
name: Terminal Executor
description: Run bash commands, scripts, and Python code safely and efficiently
keywords: [terminal, bash, shell, command, execute, run, script, python]
mcp_tools: [terminal, run_bash, run_python]
agent_behavior: |

  STEP 1 — PICK THE RIGHT TOOL
    Single bash command     → terminal(command=...)
    Multi-line bash script  → terminal(script=...)
    Inline Python code      → terminal(code=...)
    Reuse a working context → terminal(..., session_id="name")

  STEP 2 — SAFETY CHECK (before anything else)
    If command contains: rm -rf · dd · mkfs · shutdown · iptables -F · chmod 000
    → Warn the user, explain the risk, wait for explicit confirmation

  STEP 3 — INSTALL CHECK
    Does the task need pip · apt · npm · curl|bash · wget · any external package?
      → First check BUILT-IN CHEATSHEET below — use a built-in if one covers it
      → If no built-in exists, STOP and ask:
           "I need [tool] to do this. Should I install it? (yes / no)"
      → If user says yes  → install, then proceed
      → If user says no   → tell user what can't be done without it, stop there
    Never silently install. Never install "just to try something".

  STEP 4 — RUN AND CHECK
    After execution:
      exit_code = 0   → success, report output cleanly
      exit_code ≠ 0   → read stderr, diagnose the actual error, then retry differently
    Use inline Python to create files, write scripts, inspect directories, and generate content.
    Use bash scripts for shell pipelines, file operations, and environment setup.

  STEP 5 — FAILURE HANDLING
    Retry 1: try a different built-in or approach
    Retry 2: change strategy entirely, explain why
    Rule: never run the exact same failed command again

  BUILT-IN CHEATSHEET (always prefer these — no install needed):
    Network usage per app:   ss -tp | grep ESTAB
    Active connections:      netstat -tp 2>/dev/null || ss -tp
    Top memory processes:    ps aux --sort=-%mem | head -20
    Top CPU processes:       ps aux --sort=-%cpu | head -20
    Disk usage:              df -h && du -sh /* 2>/dev/null | sort -rh | head -10
    Open files/ports:        lsof -i -n -P | head -20
    Running processes:       ps aux | grep -v grep
    Network interfaces:      cat /proc/net/dev
    Free memory:             free -h
    System info:             uname -a && uptime

priority: 1.0
core: true
version: "3.0"
---
# Terminal Executor
Pick tool → safety check → ask before installing → run → handle failures.