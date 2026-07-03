---
skill_id: core.memory
name: Memory Manager
description: Retrieve or store durable user facts and follow-up context with the built-in memory tool; use it for things like IPs, preferences, paths, and prior answers.
keywords: [memory, store, remember, retrieve, fact, save, forget, recall, key, value, preference, ip, address, public, project, path, config, env, name, email, phone]
mcp_tools: [memory]
agent_behavior: |
  WHAT THIS TOOL IS
    A structured key-value store for facts the user explicitly wants kept.
    It is separate from automatic episodic and semantic memory, which the agent
    updates itself and persists in .penzer/memory/ using split JSON files.

  ACTIONS (exact syntax — only these four)
    get    → {"tool": "memory", "args": {"action": "get", "key": "..."}}
    store  → {"tool": "memory", "args": {"action": "store", "key": "...", "value": "..."}}
    list   → {"tool": "memory", "args": {"action": "list"}}
    delete → {"tool": "memory", "args": {"action": "delete", "key": "..."}}

  BEFORE ACTING — CHECK MEMORY FIRST
    When: user references something prior · needs env context · repeats a familiar task
          · asks for a fact like "my IP" or "my name" · asks to remember something
    How: memory · get · <key>  (or memory · list if key unknown)
    Hit  → reuse it and avoid redoing work
    Miss → proceed normally

  AFTER SOLVING — STORE IF EXPLICITLY USEFUL
    When: the user says "remember this" · a durable fact will be needed again
          (path, config, preference, command, credential reference) · not for
          routine task outcomes, which go to automatic episodic/semantic memory
    How: memory · store · <key> · <value>
    Key   : descriptive snake_case — project_path · user_os · wifi_scan_cmd
    Value : concise and durable — not a full essay

  FORGET — DELETE WHEN STALE
    When: user explicitly asks · info is wrong or outdated · replaced by better data
    How: memory · delete · <key>

  HARD RULES
    - Never ask the user for information you can retrieve from memory
    - Never repeat work you have a stored solution for
    - Do NOT use this tool to log routine task outcomes; that happens automatically
      via episodic and semantic memory after each run
    - Check memory before any task that references "last time" / "remember" / "as before"
    - Treat this as the explicit, user-owned memory channel for durable facts
priority: 0.96
core: true
version: "5.0"
---
# Memory Manager
Use the memory tool for explicit, durable facts that the user wants to keep.
Automatic episodic and semantic memory runs separately and is stored under
.penzer/memory. Use get / store / list / delete only when appropriate, and avoid
using this tool for routine task logging.