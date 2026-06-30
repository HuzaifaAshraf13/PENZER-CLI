---
skill_id: core.memory
name: Memory Manager
description: Store and retrieve simple facts via the built-in key-value memory tool. Separate from your automatic episodic/semantic memory.
keywords: [memory, store, remember, retrieve, fact, save, forget, recall, key, value]
mcp_tools: [memory]
agent_behavior: |
  WHAT THIS TOOL IS
    A simple key-value store, NOT your automatic episodic/semantic memory.
    Episodic and semantic memory update automatically after every task —
    you never call a tool for that. This "memory" tool is only for facts
    the user explicitly wants saved under a specific key, on demand.

  ACTIONS (exact syntax — only these four)
    get    → {"tool": "memory", "args": {"action": "get", "key": "..."}}
    store  → {"tool": "memory", "args": {"action": "store", "key": "...", "value": "..."}}
    list   → {"tool": "memory", "args": {"action": "list"}}
    delete → {"tool": "memory", "args": {"action": "delete", "key": "..."}}

  BEFORE ACTING — CHECK MEMORY FIRST
    When: user references something prior · need env context · familiar task
    How: memory · get · <key>  (or memory · list if key unknown)
    Hit  → use it, skip re-doing the work
    Miss → proceed normally

  AFTER SOLVING — STORE IF EXPLICITLY USEFUL
    When: user says "remember this" · a fact they'll need again (path, config,
          credential reference, preference) · NOT for general task outcomes
          (those go to episodic memory automatically)
    How: memory · store · <key> · <value>
    Key   : descriptive snake_case — wifi_scan_cmd · user_os · project_path
    Value : the actual fact, concise — not a full sentence essay

  FORGET — DELETE WHEN STALE
    When: user explicitly asks · info is wrong or outdated · replaced by better data
    How: memory · delete · <key>

  HARD RULES
    - Never ask the user for info you could retrieve from memory
    - Never repeat work you have a stored solution for
    - Do NOT use this tool to log task outcomes — that happens automatically
      via episodic/semantic memory after every run
    - Check memory before any task that references "last time" / "remember" / "as before"
priority: 0.95
core: true
version: "4.0"
---
# Memory Manager
Simple key-value store for explicit facts. get / store / list / delete — exact actions only.
Your episodic and semantic memory already runs automatically; this tool is just for
on-demand facts the user wants saved under a name.