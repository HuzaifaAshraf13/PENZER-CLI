---
skill_id: file_editor_skill
name: File Operations & Editing Skill
phase: post_exploitation
description: Read, write, edit, delete, and manage files with syntax awareness and diff generation
keywords:
  - file
  - read
  - write
  - edit
  - delete
  - create
  - append
  - replace
  - diff
  - content
mcp_tools:
  - file_editor
agent_behavior: |
  When user needs file operations:
  1. Read files to inspect configuration, code, or data
  2. Write new files for payloads, scripts, or reports
  3. Edit files by finding and replacing specific content
  4. Delete sensitive files during post-exploitation cleanup
  5. Compare files to identify changes or similarities
  6. Store file paths and important content in memory
---

## WHEN TO USE

Use this skill when you need to:
- **Read configuration files** to understand system setup and find misconfigurations
- **Write exploit payloads** or malicious scripts to disk
- **Modify existing files** for privilege escalation or persistence
- **Create new files** for data exfiltration, backdoors, or logging
- **Delete files** to cover tracks or remove evidence during post-exploitation
- **Search and replace** across multiple files systematically
- **Compare files** to identify changes, diffs, or configuration differences
- **Work with code** in any language with appropriate awareness

## ALGORITHM/PROCEDURE

### File Read Pattern
1. Identify target file path
2. Call `file_editor(action="read", filepath=path)`
3. Optionally specify line ranges: `line_start=10, line_end=50`
4. Receive file content (first 10KB)
5. Analyze content for secrets, configs, or vulnerabilities
6. Store important findings in memory

### File Write Pattern
1. Prepare content to write (payload, script, report)
2. Determine target filepath
3. Call `file_editor(action="write", filepath=path, content=data)`
4. Receive confirmation and bytes written
5. Verify write with subsequent read if needed

### File Append Pattern
1. Identify existing file to append to
2. Prepare content (new lines, log entries, backdoor code)
3. Call `file_editor(action="append", filepath=path, content=data)`
4. File created automatically if doesn't exist
5. Useful for log manipulation or persistence

### Find and Replace Pattern
1. Identify file and target text
2. Prepare replacement text
3. Call `file_editor(action="replace", filepath=path, find=old, replace=new)`
4. Receive count of replacements made
5. Store changes in memory for tracking

### Directory Listing Pattern
1. Specify directory path
2. Call `file_editor(action="list", filepath=dir_path)`
3. Receive up to 50 entries (files and directories)
4. Use to enumerate filesystem, find targets, identify structure

## INTEGRATION

**With Other Skills:**
- **exploitation.skill.md**: Write exploit payloads and scripts to target systems
- **post_exploitation.skill.md**: Modify files for persistence, clean up logs, exfiltrate data
- **enumeration.skill.md**: Read config files and system information
- **reporting.skill.md**: Read generated reports and evidence files
- **terminal.skill.md**: Complement terminal commands with file inspection

**MCP Tool Interaction:**
- Calls `file_editor` tool with action and filepath parameters
- Receives JSON responses with content (first 10KB), size info, and operation status
- Return codes: success (200), warning (206), error (400+)

**With Memory Tool:**
- Store important file paths: `/etc/passwd`, `/config/app.conf`
- Store extracted secrets and credentials
- Track files modified for post-exploitation reporting

## LLM OPTIMIZATION

**Prompt Injection:**
```
File operations require precision and care:
1. Always read before writing to avoid data loss
2. Use line_start/line_end for large files (context efficiency)
3. For code files, maintain proper indentation and syntax
4. Store sensitive file paths in memory, not logs
5. Use find/replace carefully with exact string matching
```

**Context Window:**
- Content: Limited to first 10KB per read (token efficient)
- Large files: Read in sections or store path in memory for reference
- Multiple files: Batch operations or reference via memory tool

**Example Reasoning:**
```
User: "Find admin credentials in config and save them"
Semantic activation: Yes (keywords: read, file, write, credentials)
Skill reasoning: Read config file → extract credentials → write to secure file → store path in memory
Tool chain: file_editor(read) → parse response → file_editor(write) → memory(store) → report
```

## SPECIAL FEATURES

### Syntax Awareness
- Automatically maintains proper indentation when editing code files
- Preserves formatting for configuration files (YAML, JSON, INI)
- Handles different line endings (CRLF vs LF)

### Safe Operations
- Prevents accidental overwrites (check file_exists if critical)
- Creates parent directories automatically
- Reports bytes written/modified for verification
- Line range reading prevents context overflow
