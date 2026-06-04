---
skill_id: meta.audit
name: Meta Skill
phase: meta
description: Periodically audit entire capability set. Identify gaps. Autonomously propose or build new skills.
keywords: [meta, audit, evolve, self-improvement, gap-filling]
mcp_tools: [memory]
agent_behavior: |
  **META-AUDIT (every 50-100 tasks):**
  1. List skills: [id, success_rate, domains_used].
  2. Identify gaps: "Low success in [domain1], [domain2]."
  3. Identify missing: "Asked to do [X], no skill for it."
  4. Prioritize: frequency_requested, impact_on_success_rate.
  
  **NEW SKILL PROPOSAL:**
  1. For each gap: propose skill_id, name, phase, description.
  2. Describe how it fills gap, expected improvement.
  3. Present: "I need [skill] for [domain]. Approve?"
  4. If approved: build.
  
  **SKILL BUILDING:**
  1. Analyze prior attempts in gap domain.
  2. Extract successful pattern into agent_behavior.
  3. Create skill v1.0.
  4. Test on similar prior tasks.
  5. Store in memory via skills.skill.md.
  
  **SKILL CONSOLIDATION:**
  - If overlapping skills: consolidate into unified skill.
  - Deprecate old in versioning.
priority: 1.0
version: 1.0
author: Penzer
---

# Meta Skill

Audit capabilities. Identify gaps. Build new skills. Consolidate duplicates.