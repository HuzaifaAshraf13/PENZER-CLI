---
skill_id: audit.immutable_log
name: Audit Skill
phase: audit
description: Maintain immutable, searchable log of all actions and outcomes. Generate audit reports.
keywords: [audit, logs, immutable, accountability, review]
mcp_tools: [memory]
agent_behavior: |
  **AUDIT LOG CONTENTS:**
  - Every action: timestamp, action_type, inputs, outputs, outcome, domain.
  - Every decision: considered, chosen, alternatives, reasoning.
  - Every change: old_state, new_state, reverse available?.
  - Every failure: context, reason, impact, recovered?.
  
  **IMMUTABILITY:**
  - Append-only (never modify).
  - On correction: new entry referencing prior.
  - Timestamp + sequence on each entry.
  
  **SEARCHABLE:**
  - Index: timestamp, action_type, domain, outcome, skill_used.
  - Queries: "Show [domain] actions", "Show failures in [window]", "Show action [N]".
  
  **AUDIT REPORTS (every 50 actions):**
  - Success rate by domain.
  - Failure patterns.
  - Trust score accuracy.
  - Self-model accuracy.
priority: 1.0
version: 1.0
author: Penzer
---

# Audit Skill

Every action logged. Immutable record for trust and self-assessment training.