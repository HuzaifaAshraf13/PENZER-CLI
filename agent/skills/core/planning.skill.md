---
skill_id: core.planning
name: Task Planner
description: Break down complex tasks into clear executable steps before acting
keywords: [plan, complex, steps, strategy, approach, breakdown, multi-step, organize, how to, figure out]
mcp_tools: [memory]
agent_behavior: |
  TRIGGER — USE THIS SKILL WHEN:
  - Task has more than 2 steps
  - Task is ambiguous or unclear
  - Task involves multiple tools
  - You are unsure where to start
  - Previous attempts failed

  PLANNING PROCESS:
  1. GOAL: State the end goal in one clear sentence
  2. STEPS: Break into 3-6 concrete steps — each step must:
     - Have exactly ONE action
     - Name the exact tool to use
     - Define what success looks like
  3. RISKS: Identify what could go wrong at each step
  4. FALLBACK: Define an alternative if a step fails

  PLAN FORMAT (output this before doing anything):
  {"answer": "PLAN:\nGoal: [one sentence]\nStep 1: [action] using [tool] → success = [condition]\nStep 2: [action] using [tool] → success = [condition]\n..."}

  EXECUTION RULES:
  - Execute steps in order — never skip
  - Verify each step worked before the next
  - If a step fails: stop, diagnose, replan from that point
  - Never execute more than one step per tool call

  AFTER COMPLETION:
  - Save successful plan to memory: what worked, what didn't
  - Write it as a generated skill if it's a pattern likely to repeat

  EXAMPLES OF GOOD PLANS:
  Task: "scan network usage"
  Goal: Identify which processes are using the most network bandwidth
  Step 1: Check active connections using ss -tp → success = list of processes
  Step 2: Cross-reference PIDs with ps aux → success = process names matched
  Step 3: Summarize top 5 by connection count → success = clear answer to user

  Task: "find and fix a bug in my code"
  Goal: Locate and resolve the error causing the failure
  Step 1: Read the file using file_editor → success = code visible
  Step 2: Identify the bug using run_python → success = error reproduced
  Step 3: Fix the line using file_editor replace → success = no error
  Step 4: Verify fix using run_python → success = clean output

priority: 0.9
core: true
version: "2.1"
---
# Task Planner
Always plan before acting on complex tasks. One step at a time. Verify before continuing.