"""
Terminal Tool — single unified tool for all execution.
Handles bash commands, Python code, multi-line scripts, and background processes.
"""

import os
import subprocess
from agent.core import mcp
from tools.executor import execute, get_change_log
from tools.standards import success, error, warning

_cwd = os.getcwd()


@mcp.tool()
def terminal(
    command: str = None,
    code: str = None,
    script: str = None,
    mode: str = "bash",
    timeout: int = 60,
    workdir: str = None,
    force: bool = False,
    background: bool = False,
) -> dict:
    """
    Unified execution tool. Use for any terminal, bash, or Python execution.

    Args:
        command: Single bash command (e.g. "ls -la", "df -h")
        code: Python code to run inline (sets mode="python" automatically)
        script: Multi-line bash script to run
        mode: "bash" or "python" (auto-detected if code is provided)
        timeout: Seconds before kill (default 60). Use 0 for background.
        workdir: Working directory override
        force: Bypass dangerous command check
        background: Run detached in background, returns pid

    Examples:
        terminal(command="ls -la")
        terminal(code="import os; print(os.getcwd())")
        terminal(script="#!/bin/bash\\napt update\\napt install -y curl")
        terminal(command="python3 app.py", background=True)
    """
    global _cwd

    # Auto-detect what was passed
    if code is not None:
        payload = code
        mode = "python"
    elif script is not None:
        payload = script
        mode = "bash"
    elif command is not None:
        payload = command
        mode = "bash"
    else:
        return error("Provide command, code, or script")

    if not payload.strip():
        return error("Empty input provided")

    # Handle cd — persist working directory
    if mode == "bash" and payload.strip().startswith("cd "):
        target = os.path.normpath(os.path.join(_cwd, os.path.expanduser(payload.strip()[3:].strip())))
        if not os.path.isdir(target):
            return error(f"No such directory: {target}")
        _cwd = target
        return success(data={"cwd": _cwd})

    # Background process
    if background or timeout == 0:
        try:
            proc = subprocess.Popen(
                ["bash", "-c", payload],
                cwd=workdir or _cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return success(data={"pid": proc.pid, "mode": "background"})
        except Exception as e:
            return error(str(e))

    return execute(payload, mode=mode, timeout=timeout, workdir=workdir or _cwd, force=force)


@mcp.tool()
def terminal_get_change_log() -> dict:
    """Return all commands executed this session for audit/rollback."""
    changes = get_change_log()
    return success(data={"changes": changes, "count": len(changes)})