---
skill_id: core.file_editor
name: File Editor
description: Read, write, edit, append, delete, list, and create files and directories
keywords: [file, read, write, edit, create, delete, append, replace, list, directory, folder, path, content]
mcp_tools: [file_editor]
agent_behavior: |
  ACTION REFERENCE:
  Read file:      {"tool": "file_editor", "args": {"action": "read", "filepath": "path/to/file"}}
  Write file:     {"tool": "file_editor", "args": {"action": "write", "filepath": "path/to/file", "content": "..."}}
  Append:         {"tool": "file_editor", "args": {"action": "append", "filepath": "path/to/file", "content": "..."}}
  Find/replace:   {"tool": "file_editor", "args": {"action": "replace", "filepath": "path/to/file", "find": "old", "replace": "new"}}
  Delete file:    {"tool": "file_editor", "args": {"action": "delete", "filepath": "path/to/file"}}
  Create empty:   {"tool": "file_editor", "args": {"action": "create", "filepath": "path/to/file"}}
  List dir:       {"tool": "file_editor", "args": {"action": "list", "filepath": "path/to/dir"}}
  Read range:     {"tool": "file_editor", "args": {"action": "read", "filepath": "path", "line_start": 1, "line_end": 50}}

  RULES:
  - Always read a file before editing it
  - Use replace for small edits, write for full rewrites
  - Use list before reading to confirm file exists
  - Check returned status — error means it failed
  - Create parent dirs automatically via write/create
priority: 0.95
core: true
version: "2.0"
---
# File Editor
Read, write, edit, and manage files. Always read before editing.