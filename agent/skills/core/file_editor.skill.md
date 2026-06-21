---
skill_id: core.file_editor
name: File Editor
description: Read, write, edit, append, delete, list, and create files and directories
keywords: [file, read, write, edit, create, delete, append, replace, list, directory, folder, path, content]
mcp_tools: [file_editor]
agent_behavior: |

  ACTION REFERENCE
    read whole file    → file_editor · read   · filepath
    read line range    → file_editor · read   · filepath · line_start · line_end
    overwrite/create   → file_editor · write  · filepath · content
    add to end         → file_editor · append · filepath · content
    change a string    → file_editor · replace· filepath · find · replace
    create empty file  → file_editor · create · filepath
    remove a file      → file_editor · delete · filepath
    list a directory   → file_editor · list   · filepath

  SAFE EDIT SEQUENCE
    list dir → read file → replace or write → read again to verify
    Use replace for targeted edits · use write only for full rewrites

  RULES
    - Always read before editing — never write blind
    - Always check returned status — error means it failed, never assume success
    - Use list first if unsure whether a file or directory exists
    - write and create auto-create parent dirs — no need to mkdir first
    - replace fails silently if find string is absent — confirm it's in the read output first

priority: 0.95
core: true
version: "3.0"
---
# File Editor
list → read → edit → verify. Never write blind. Always check status.