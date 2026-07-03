"""
Terminal Tool — single unified tool for all execution.
Handles bash commands, Python code, multi-line scripts, and background processes.
"""

import os
import subprocess
from agent.core import mcp
from tools.executor import execute, get_change_log
from tools.standards import success, error, warning
from config import get_profile_settings

_cwd = os.getcwd()
_SESSION_CWDS: dict[str, str] = {}


def _resolve_workdir(session_id: str | None, workdir: str | None) -> str:
    if workdir:
        target = os.path.abspath(os.path.expanduser(workdir))
        if session_id:
            _SESSION_CWDS[session_id] = target
        return target

    if session_id and session_id in _SESSION_CWDS:
        return _SESSION_CWDS[session_id]

    return _cwd


def _set_session_workdir(session_id: str | None, target: str) -> None:
    if session_id:
        _SESSION_CWDS[session_id] = target
    else:
        global _cwd
        _cwd = target


def _terminal_impl(
    command: str = None,
    code: str = None,
    script: str = None,
    mode: str = "bash",
    timeout: int = 60,
    workdir: str = None,
    force: bool = False,
    background: bool = False,
    session_id: str = None,
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
        session_id: Reuse a working directory and context across related calls

    Examples:
        terminal(command="ls -la")
        terminal(code="from pathlib import Path; Path('demo.txt').write_text('hello')")
        terminal(script="#!/bin/bash\\nmkdir -p build\\necho done")
        terminal(command="python3 app.py", background=True, session_id="app")
    """
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

    effective_workdir = _resolve_workdir(session_id, workdir)

    # Handle cd — persist working directory per session
    if mode == "bash" and payload.strip().startswith("cd "):
        target = os.path.normpath(os.path.join(effective_workdir, os.path.expanduser(payload.strip()[3:].strip())))
        if not os.path.isdir(target):
            return error(f"No such directory: {target}")
        _set_session_workdir(session_id, target)
        return success(data={"cwd": target, "session_id": session_id})

    # Background process
    if background or timeout == 0:
        try:
            proc = subprocess.Popen(
                ["bash", "-c", payload],
                cwd=effective_workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return success(data={"pid": proc.pid, "mode": "background", "cwd": effective_workdir, "session_id": session_id})
        except Exception as e:
            return error(str(e))

    return execute(
        payload,
        mode=mode,
        timeout=timeout,
        workdir=effective_workdir,
        force=force,
        approval_required=get_profile_settings().get("approval_required", True),
        confirmation_reason="This action may be destructive or sensitive.",
    )


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
    session_id: str = None,
) -> dict:
    return _terminal_impl(
        command=command,
        code=code,
        script=script,
        mode=mode,
        timeout=timeout,
        workdir=workdir,
        force=force,
        background=background,
        session_id=session_id,
    )


@mcp.tool()
def terminal_get_change_log() -> dict:
    """Return all commands executed this session for audit/rollback."""
    changes = get_change_log()
    return success(data={"changes": changes, "count": len(changes)})