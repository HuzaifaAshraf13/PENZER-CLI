---
skill_id: core.memory
name: Memory Manager
description: Store facts, solutions, and patterns. Retrieve them to avoid repeating work.
keywords: [memory, store, remember, retrieve, fact, pattern, recall, save, forget, context, history]
mcp_tools: [memory]
agent_behavior: |

  BEFORE ACTING — CHECK MEMORY FIRST
    When: familiar task · user references something prior · need env context
    How:
      retrieve one key  → memory · get · <key>
      scan everything   → memory · list
    Hit  → use it, skip re-doing the work
    Miss → proceed normally

  AFTER SOLVING — STORE IF NON-TRIVIAL
    When: solved a real problem · command worked well · user shared an env fact
    Skip: trivial one-liners, obvious answers unlikely to repeat
    How:
      memory · store · <key> · <value>
    Key   : descriptive snake_case — wifi_scan_cmd · user_os · project_path
    Value : "Fixed X by doing Y using Z" — always include problem + solution + tool

  FORGET — DELETE WHEN STALE
    When: user explicitly asks · info is wrong or outdated · replaced by better data
    How:
      memory · delete · <key>

  HARD RULES
    - Never ask the user for info you could retrieve from memory
    - Never repeat work you have a stored solution for
    - Check memory before every familiar task — not just when you feel like it

priority: 0.95
core: true
version: "3.0"
---
# Memory Manager
Check before acting. Store after solving. Delete when stale. Never repeat work.