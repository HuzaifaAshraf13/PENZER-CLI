---
skill_id: benchmark.selftest
name: Benchmark Skill
phase: benchmark
description: Periodically self-test skills for speed, accuracy, reliability. Flag degradation.
keywords: [benchmark, performance, regression, testing, metrics]
mcp_tools: [memory, terminal]
agent_behavior: |
  **BENCHMARK DESIGN:**
  - For each skill, design representative test case.
  - Store: inputs, expected_output, timeout_sec, domain.
  - Example: terminal.skill → "Run 'whoami', verify output non-empty."
  
  **BENCHMARK EXECUTION (every 100 tasks):**
  1. For each skill: execute test case.
  2. Measure: duration_ms, success/failure, output correctness.
  3. Store: skill_id, timestamp, duration_ms, success, output.
  
  **PERFORMANCE METRICS:**
  - Per skill: avg_duration, min/max, success_rate on benchmarks.
  - Compare to prior: "Prior=50ms, now=120ms. Degradation?"
  - Flag if >20% drop: "possible_degradation".
  
  **DEGRADATION RESPONSE:**
  1. Log in memory and audit.
  2. Trigger versioning.skill review.
  3. If skill change caused it: suggest rollback.
  4. If environment change: note for context.
  
  **RELIABILITY:**
  - Run test 5x in a row.
  - Calculate variance. High variance = unreliable.
  - Flag unreliable for investigation.
priority: 1.0
version: 1.0
author: Penzer
---

# Benchmark Skill

Self-test all skills periodically. Measure speed, accuracy, reliability. Flag degradation.