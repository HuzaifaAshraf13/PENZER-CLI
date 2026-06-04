---
skill_id: trust.dynamic
name: Trust Skill
phase: trust
description: Maintain dynamic trust scores per action domain. Enforce validation levels based on trust.
keywords: [trust, confidence, validation, accountability]
mcp_tools: [memory]
agent_behavior: |
  **TRUST SCORE STRUCTURE:**
  - trust_by_domain: {domain: score_0_to_1}
  - New domains: score=0.5 (neutral).
  
  **TRUST UPDATE:**
  - Success: score = (score * count + 1) / (count + 1). Max +0.05.
  - Failure: score = (score * count - 0.3) / (count + 1). Max -0.1.
  - Clamp to [0, 1].
  
  **VALIDATION LEVELS:**
  - High trust (>0.7): Execute, explain after.
  - Medium (0.3-0.7): Explain before, validate after, confirm state-changes.
  - Low (<0.3): Require explicit confirmation before execution.
  
  **USER COMMANDS:**
  - "Show trust scores" → display trust_by_domain.
  - "Set trust [domain] [score]" → override.
  - "Reset trust [domain]" → reset to 0.5.
priority: 1.0
version: 1.0
author: Penzer
---

# Trust Skill

Earn trust through success. High-trust actions execute fast.