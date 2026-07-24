---
skill_id: core.planning
name: Task Planner
description: Break complex tasks into verified executable steps before acting
keywords: [plan, complex, steps, strategy, approach, breakdown, multi-step, organize, how to, figure out]
mcp_tools: [memory]
agent_behavior: |

  TRIGGER — PLAN BEFORE ACTING WHEN:
    - Task needs 3+ steps
    - Goal is ambiguous or unclear
    - Task spans multiple tools
    - You are unsure where to start
    - A previous attempt failed
    Single obvious action → skip planning, just act

  STEP 1 — FORM THE PLAN
    Output this before touching any tool:

    Goal     : [one sentence — what does "done" look like?]
    Step 1   : [one action] using [exact tool] → success = [condition]
    Step 2   : [one action] using [exact tool] → success = [condition]
    ...
    Risks    : [what could break at each step?]
    Fallback : [if step N fails, do what instead?]

    Rules for steps:
      - One action per step, no batching
      - Name the exact tool — not "check it" but "run ss -tp via terminal"
      - Success condition must be testable, not vague ("output is clean" not "works")
      - 3 steps minimum · 6 steps maximum — split into phases if larger

  STEP 2 — EXECUTE
    - Run steps in order, never skip ahead
    - After each step: verify the success condition before moving on
    - One tool call per step — never run two steps in one call
    - Step fails → STOP · diagnose the actual error · replan from that step
    - Do not retry the same failed action blindly

  STEP 3 — AFTER COMPLETION
    - Save to memory: what worked, what was tricky, which step needed replanning
    - If the plan was a repeatable pattern → generate the skill yourself,
      following core.meta's GATE 1 / STEP 1-3 / TEMPLATE directly with
      file_editor. There is no delegation between skills — skills are
      static text matched into context by keyword overlap, not callable
      agents you can hand a task to. Consulting core.meta here means
      following its documented steps in this same turn, not invoking it
      as if it were a tool.

  EXAMPLE PLAN
    Task: find and fix a bug in my code
    Goal: locate and resolve the error causing the test failure
    Step 1: read the file via file_editor → success = code visible in context
    Step 2: reproduce the error via run_python → success = error message captured
    Step 3: fix the line via file_editor replace → success = diff looks correct
    Step 4: verify via run_python → success = clean output, no error

priority: 0.9
core: true
version: "3.1"
---
# Task Planner
Plan before acting. One step, one tool call, verify before continuing. Replan
on failure. Generate resulting skills directly with file_editor — no
skill-to-skill handoff mechanism exists.