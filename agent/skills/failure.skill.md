---
skill_id: failure.logging
name: Failure Skill
phase: failure
description: Log every failure with full context. Detect recurring patterns. Convert successful retries into new skills.
keywords: [failure, logging, diagnostics, patterns, recovery]
mcp_tools: [memory]
agent_behavior: |
  **ON FAILURE:**
  1. Capture context:
     - command_attempted, expected_outcome, actual_output
     - environment: os, shell, pwd, user, installed_tools
     - timestamp, task_type, domain, skills_used_before_failure
  2. Store in memory: failure_log entry.
  3. Categorize: network? permissions? tool_missing? logic? timeout?
  
  **PATTERN DETECTION:**
  - Retrieve prior failures for same task_type + domain.
  - Detect recurring: "Same command failed 3x with same error."
  - Surface patterns.
  
  **RETRY STRATEGY:**
  1. If first failure, retry with corrected approach.
  2. If retry succeeds, extract delta: "Changed [X] to [Y], now works."
  3. Create/update skill from successful retry.
priority: 1.0
version: 1.0
author: Penzer
---

# Failure Skill

Log every failure. Detect patterns. Convert successful retries into skills.