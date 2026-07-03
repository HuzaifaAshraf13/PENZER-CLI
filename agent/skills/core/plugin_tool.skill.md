---
skill_id: core.plugin_tool
name: Plugin Tool Creator
description: Reuse or create Python plugin tools for repetitive tasks and persist them under tools/plugins.
keywords: [plugin, tool, create, python, generate, extend, automate, custom tool, reuse]
mcp_tools: [terminal, file_editor]
agent_behavior: |
  WHEN TO USE
    Use this skill when a task would benefit from a reusable helper that is
    more specific than a one-off bash or Python command, especially if the
    same workflow may be repeated later.

  STEP 1 — CHECK EXISTING TOOLS FIRST
    Before creating anything, inspect the available plugin tools and registry.
    If a suitable plugin already exists, reuse it instead of creating a duplicate.

  STEP 2 — DEFINE THE TOOL ONLY IF NEEDED
    If no suitable plugin exists, choose a clear function name and a short
    description. The implementation should be self-contained Python that accepts
    keyword arguments and returns a value or string.

  STEP 3 — CREATE THE PLUGIN ONLY WHEN REQUIRED
    If no matching plugin is available, call the built-in plugin creation handler
    with the exact pattern:
    {"tool": "plugin_tool", "args": {"action": "create", "name": "tool_name", "description": "...", "code": "..."}}
    This writes the module to tools/plugins and registers it for later reuse.

  STEP 4 — VERIFY THE RESULT
    Confirm the plugin is available and can be invoked by name.

  HARD RULES
    - Prefer reuse over creation when a plugin already matches the task
    - Prefer a small, focused tool over a large one
    - Keep the code deterministic and safe
    - Save the tool in tools/plugins so it persists across runs
    - Always use the plugin_tool handler instead of trying to hand-write files directly
priority: 0.93
core: true
version: "2.0"
---
# Plugin Tool Creator
Reuse an existing plugin when possible. If a repeated workflow truly needs a
new helper, create one with the plugin_tool handler and persist it under
tools/plugins for later use.
