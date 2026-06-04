---
skill_id: hypothesis.test
name: Hypothesis Skill
phase: hypothesis
description: For ambiguous problems, form explicit testable hypotheses. Design minimal tests. Record outcomes. Never repeat disproved hypotheses.
keywords: [hypothesis, experiment, testing, validation]
mcp_tools: [memory, terminal]
agent_behavior: |
  **WHEN TO USE:**
  - When task outcome is unclear or ambiguous.
  - Before making expensive/destructive decisions.
  
  **HYPOTHESIS FORMATION:**
  1. State the ambiguity: "It's unclear whether [X]."
  2. List possible states: "State could be [state1], [state2], [state3]."
  3. For each state, form testable hypothesis: "If [state], then [observable]."
  4. Design minimal test command to distinguish states.
  
  **HYPOTHESIS TESTING:**
  1. Execute minimal test command(s).
  2. Record exact output.
  3. Eliminate hypotheses where output contradicts.
  4. If multiple remain, design additional tests.
  
  **RECORD IN MEMORY:**
  - Store: hypothesis_tested, test_command, outcome, confidence, timestamp.
  - Never test same hypothesis twice in same session.
priority: 1.0
version: 1.0
author: Penzer
---

# Hypothesis Skill

Never assume. Test ambiguities with minimal commands. Record results.