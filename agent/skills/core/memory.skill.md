---
skill_id: core.memory
name: Memory Manager
description: Store facts, solutions, and patterns. Retrieve them to avoid repeating work.
keywords: [memory, store, remember, retrieve, fact, pattern, recall, save, forget, context, history]
mcp_tools: [memory]
agent_behavior: |
  STORE WHEN:
  - You solved a problem successfully → store the solution
  - User shares facts about themselves or their system → store immediately
  - A command worked well → store the exact command
  - A pattern is likely to repeat → store the pattern

  HOW TO STORE:
  {"tool": "memory", "args": {"action": "store", "key": "short_key", "value": "concise fact or solution"}}

  FORMAT:
  - Concise: "Fixed X by doing Y with command Z"
  - Include context: problem + solution + tool used
  - Key should be descriptive: "wifi_scan_cmd", "user_os", "project_path"

  RETRIEVE WHEN:
  - Starting a familiar task → check memory first
  - User references something from before → retrieve it
  - Need context about user's environment → retrieve it

  HOW TO RETRIEVE:
  {"tool": "memory", "args": {"action": "get", "key": "short_key"}}

  LIST ALL MEMORIES:
  {"tool": "memory", "args": {"action": "list"}}

  FORGET WHEN:
  - User explicitly asks to forget something
  - Information is outdated or wrong

  HOW TO FORGET:
  {"tool": "memory", "args": {"action": "delete", "key": "short_key"}}

  PRIORITY ORDER:
  1. Always check memory BEFORE running a command you may have run before
  2. Always store AFTER solving something non-trivial
  3. Never ask user for info you could retrieve from memory
priority: 0.95
core: true
version: "2.1"
---
# Memory Manager
Check memory before acting. Store after solving. Never repeat work.