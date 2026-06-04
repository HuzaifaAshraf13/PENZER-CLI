---
skill_id: versioning.history
name: Versioning Skill
phase: versioning
description: Track version history for skills. Monitor performance per version. Support rollbacks. Provide diffs.
keywords: [versioning, rollback, diff, history, performance]
mcp_tools: [memory]
agent_behavior: |
  **SKILL VERSION STRUCTURE:**
  - skill_id, version, created_timestamp, created_by.
  - agent_behavior: [full instruction text].
  - performance_metrics: {success_rate, avg_duration, failure_count, domains_used}.
  - prior_version_id: [reference].
  
  **VERSION CREATION:**
  - On skill create/update: store as new version.
  - Compute diff: what changed from prior version.
  
  **PERFORMANCE TRACKING:**
  - After execution: update success_count, failure_count, avg_duration.
  - Calculate: success_rate = success_count / (success_count + failure_count).
  - Track by domain: success_rate_by_domain.
  
  **AUTO-ROLLBACK ON DEGRADATION:**
  - If new success_rate falls >15% below prior, flag for review.
  - If approved, auto-rollback to prior version.
  - Log in audit.
  
  **DIFF VIEW:**
  - "Show diff v[old] vs v[new]" → highlights changes.
priority: 1.0
version: 1.0
author: Penzer
---

# Versioning Skill

Every skill change is a version. Track performance. Auto-rollback if degraded.