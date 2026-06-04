---
skill_id: scheduling.timer
name: Scheduling Skill
phase: scheduling
description: Schedule tasks for future execution. Manage scheduled jobs. Wake to execute on schedule. Persist.
keywords: [scheduling, cron, timer, persistence, automation]
mcp_tools: [memory, terminal]
agent_behavior: |
  **SCHEDULE STRUCTURE:**
  - schedule_id, task_description, trigger_time/trigger_interval.
  - trigger_type: "once" | "periodic" | "cron".
  - created_timestamp, next_execution_time, last_execution_time.
  - status: "scheduled" | "running" | "completed" | "failed".
  
  **SCHEDULE COMMANDS:**
  - "Schedule task [desc] at [time]" → once.
  - "Schedule task [desc] every [interval]" → periodic.
  - "Schedule task [desc] cron [expr]" → cron.
  - "List schedules" → show all with status.
  - "Cancel schedule [id]" → remove.
  
  **PERSISTENCE & EXECUTION:**
  - Store in memory (survives restart).
  - Check every minute for overdue jobs.
  - Execute job: capture outcome, update next_execution_time.
  - Log in audit.
  
  **FAILURE HANDLING:**
  - If fails: log in failure.skill.
  - Retry on next scheduled time.
  - If repeated failures: alert user, ask disable.
priority: 1.0
version: 1.0
author: Penzer
---

# Scheduling Skill

Schedule tasks for future execution. Persistent schedules survive restarts.