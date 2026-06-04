---
skill_id: skills.management
name: Skills System
phase: skills
description: CRUD operations for skills. Semantic search before tasks. Synthesize and update skills after success.
keywords: [skill, crud, create, retrieve, update, delete, semantic, synthesis]
mcp_tools: [memory]
agent_behavior: |
  **SEMANTIC SEARCH (before every task):**
  1. Parse request: domain, task_type, goal.
  2. Search skills: keyword + description matching.
  3. Return top-3 with relevance scores.
  4. Use top-3 as guidance.
  
  **SKILL CRUD:**
  - create_skill(id, name, phase, desc, keywords, behavior): new skill, store.
  - get_skill(id): retrieve from memory.
  - list_skills(phase/domain): list matching.
  - update_skill(id, updates): new version, keep prior.
  - delete_skill(id): mark deprecated (never delete).
  
  **SYNTHESIS (after success):**
  1. Analyze: which skills used, order, what worked.
  2. Extract pattern into behavior: "When [condition], do [actions]."
  3. Create/update skill: synthesis_skill_id=auto.
  4. Confidence = success_rate / attempts.
  5. >0.8: auto-add. 0.5-0.8: propose user.
  
  **METRICS:**
  - Per skill: usage_count, success_count, failure_count, avg_duration.
  - success_rate = success / (success + failure).
  - last_used, domains_used_in.
priority: 1.0
version: 1.0
author: Penzer
---

# Skills System

Core skill management. Semantic search before tasks. Synthesize after success.