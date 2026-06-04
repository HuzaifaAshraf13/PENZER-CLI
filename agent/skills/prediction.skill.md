---
skill_id: prediction.next
name: Prediction Skill
phase: prediction
description: Predict user's next likely action. Prepare proactively. Track accuracy.
keywords: [prediction, proactive, suggestions, anticipation]
mcp_tools: [memory]
agent_behavior: |
  **PREDICTION ALGORITHM:**
  1. Analyze current task and context: what just happened, what's the goal?
  2. Query memory: prior similar tasks, user's next_action_after history.
  3. Build candidates: [cand1, cand2, cand3] with confidence scores.
  4. Choose top with highest confidence.
  
  **PREDICTION CONFIDENCE:**
  - >70%: "Next, you might want to [pred]. Prepare?"
  - 40-70%: "Possible steps: [opt1], [opt2], [opt3]. Which?"
  - <40%: "What next?"
  
  **PREPARATION:**
  - If accepted: pre-execute setup (memory queries, tool readiness).
  - Execution faster because prep is done.
  
  **ACCURACY TRACKING:**
  - Track: was_correct (user did [predicted] or not?).
  - Calculate: accuracy = correct / total.
  - If >75%, increase confidence. If <50%, decrease.
  - Store: accuracy_by_task_type.
priority: 1.0
version: 1.0
author: Penzer
---

# Prediction Skill

Anticipate next action. Suggest proactively. Track accuracy and improve.