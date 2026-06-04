---
skill_id: memory.persistence
name: Memory Skill
phase: memory
description: Persist context across sessions, tag memories, retrieve relevant memories, and forget outdated entries. Foundation for all other skills.
keywords: [memory, persistence, retrieval, tagging, context]
mcp_tools: [memory]
agent_behavior: |
  **RETRIEVE PHASE (before every task):**
  1. Extract key signals from user request: domain, task_type, time_window, scope.
  2. Search memory: prior attempts on same task, similar patterns, known failures, lessons learned.
  3. If relevant memories found, inject into reasoning phase as context.
  4. If contradictions found, flag and ask user to validate.
  
  **STORE PHASE (after completing tasks):**
  1. Identify key findings: what worked, what failed, unexpected behaviors, edge cases.
  2. Tag each: domain, task_type, outcome (success/partial/failure), timestamp, confidence_score.
  3. Attach metadata: tools_used, alternatives_considered, performance_metrics.
  4. Store summaries in short-term memory for LLM context; extended artifacts in long-term.
  5. Link related memories bidirectionally to form knowledge graph.
  
  **FORGET PHASE (when memory grows stale):**
  1. On contradictions: ask user to validate "Memory says X, reality shows Y. Which is correct?"
  2. Mark outdated memories as archived (not deleted) with expiry_reason and timestamp.
  3. If same memory retrieved >5 times with no value added, flag for review.
  
  **MEMORY SYNTAX:**
  - `memory(action="store", workspace_id="<task_id>", data={findings})`
  - `memory(action="retrieve", workspace_id="<task_id>", query="search_term")`
  - `memory(action="search", workspace_id="<task_id>", query="search_term")`
priority: 1.0
version: 1.0
author: Penzer
---

# Memory Skill

Foundation for learning across sessions. Always retrieve before tasks, store after. Never hallucinate past experience — always check memory first.
