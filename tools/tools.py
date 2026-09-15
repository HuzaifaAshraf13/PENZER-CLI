# tools/tools.py
"""
Central tool imports and re-exports.
Individual tools are in separate files:
  - terminal_tool.py: terminal()
  - browser_tool.py: browser()
  - ui_tool.py: ui()
  - file_editor_tool.py: file_editor()
"""

# Import tool entry points. Browser and file-editor wrappers register with MCP;
# terminal execution is exposed directly by the agent.
from tools.terminal_tool import terminal, terminal_check_job, terminal_kill
from tools.browser_tool import browser
from tools.file_editor_tool import file_editor, file_editor_direct

__all__ = [
    "terminal",
    "terminal_check_job",
    "terminal_kill",
    "browser",
    "ui",
    "file_editor",
    "file_editor_direct",
]