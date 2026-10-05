"""
Terminal Tool — single unified tool for all execution.
Handles bash commands, Python code, multi-line scripts, and background processes.
"""

import os
import asyncio
import shlex

from tools.executor import approve_background_command, execute, get_change_log
from tools.standards import success, error, warning
from config import get_profile_settings
from . import jobs, session


async def _terminal_impl(
    command: str = None,
    code: str = None,
    script: str = None,
    mode: str = "bash",
    timeout: int = 60,
    workdir: str = None,
    force: bool = False,
    background: bool = False,
    session_id: str = None,
    workflow: str = "general",
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

    workflow = (workflow or "general").strip() or "general"
    effective_workdir = session.resolve_workdir(session_id, workdir)

    # Handle cd — persist working directory per session
    leading_cd = None
    if mode == "bash" and payload.strip().startswith("cd "):
        command_text = payload.strip()
        if any(operator in command_text[3:] for operator in ("&&", ";", "||", "|", "\n")):
            try:
                leading_cd = shlex.split(command_text.split("&&", 1)[0].strip(), posix=True)[1]
            except (IndexError, ValueError):
                leading_cd = None
        else:
            leading_cd = command_text[3:].strip()
    if leading_cd is not None:
        target = os.path.normpath(os.path.join(effective_workdir, os.path.expanduser(leading_cd)))
        if not os.path.isdir(target):
            return error(f"No such directory: {target}")
        if "&&" not in payload and ";" not in payload and "||" not in payload and "|" not in payload and "\n" not in payload:
            session.set_workdir(session_id, target)
            return success(data={"cwd": target, "session_id": session_id})

    # Background process
    if background or timeout == 0:
        approved, reason = await asyncio.to_thread(
            approve_background_command,
            payload,
            get_profile_settings().get("approval_required", True),
        )
        if not approved:
            return warning(message=reason)
        result = jobs.start_job(payload, effective_workdir, mode, workflow, session_id)
        if result.get("status") == "success":
            result["data"]["mode"] = "background"
            result["data"]["workflow"] = workflow
        return result

    # FIX: execute() is a blocking sandbox call. Running it directly inside
    # this async tool handler stalls the whole agent event loop for the
    # entire command duration — the CLI stops rendering output (though
    # stdin keeps buffering keystrokes, which is why input felt "accepted"
    # but nothing appeared on screen, e.g. during long nmap scans).
    # asyncio.to_thread offloads it to a worker thread so the event loop
    # — and therefore your CLI's render/input loop — stays responsive.
    try:
        result = await asyncio.to_thread(
            execute,
            payload,
            mode=mode,
            timeout=timeout,
            workdir=effective_workdir,
            force=force,
            approval_required=get_profile_settings().get("approval_required", True),
            confirmation_reason="This action may be destructive or sensitive.",
        )
        if isinstance(result.get("data"), dict):
            result["data"]["workflow"] = workflow
        if result.get("status") == "success" and leading_cd is not None:
            session.set_workdir(session_id, target)
            if isinstance(result.get("data"), dict):
                result["data"]["cwd"] = target
        return result
    except Exception as e:
        return error(str(e))


async def terminal_direct(
    command: str = None,
    code: str = None,
    script: str = None,
    mode: str = "bash",
    timeout: int = 60,
    workdir: str = None,
    force: bool = False,
    background: bool = False,
    session_id: str = None,
    workflow: str = "general",
) -> dict:
    return await _terminal_impl(
        command=command,
        code=code,
        script=script,
        mode=mode,
        timeout=timeout,
        workdir=workdir,
        force=force,
        background=background,
        session_id=session_id,
        workflow=workflow,
    )


async def terminal(
    command: str = None,
    code: str = None,
    script: str = None,
    mode: str = "bash",
    timeout: int = 60,
    workdir: str = None,
    force: bool = False,
    background: bool = False,
    session_id: str = None,
    workflow: str = "general",
) -> dict:
    return await terminal_direct(
        command=command,
        code=code,
        script=script,
        mode=mode,
        timeout=timeout,
        workdir=workdir,
        force=force,
        background=background,
        session_id=session_id,
        workflow=workflow,
    )


def terminal_check_job_direct(job_id: str) -> dict:
    """Return the status and recent output for a background terminal job."""
    return jobs.check_job(job_id)


def terminal_list_jobs_direct(status: str = None, session_id: str = None) -> dict:
    """List background jobs with optional status and session filters."""
    return jobs.list_jobs(status=status, session_id=session_id)


def terminal_kill_direct(job_id: str) -> dict:
    """Terminate a background terminal job by id."""
    return jobs.kill_job(job_id)


def terminal_check_job(job_id: str) -> dict:
    return terminal_check_job_direct(job_id)


def terminal_list_jobs(status: str = None, session_id: str = None) -> dict:
    return terminal_list_jobs_direct(status=status, session_id=session_id)


def terminal_kill(job_id: str) -> dict:
    return terminal_kill_direct(job_id)


def terminal_get_change_log() -> dict:
    """Return all commands executed this session for audit/rollback."""
    changes = get_change_log()
    return success(data={"changes": changes, "count": len(changes)})