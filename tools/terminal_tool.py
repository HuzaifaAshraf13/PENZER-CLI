"""
Terminal Tool — single unified tool for all execution.
Handles bash commands, Python code, multi-line scripts, and background processes.
"""

import os
import asyncio
import subprocess
import time
import uuid
from pathlib import Path

from agent.core import mcp
from tools.executor import execute, get_change_log
from tools.standards import success, error, warning
from config import get_profile_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_JOB_ROOT = PROJECT_ROOT / "logs" / "jobs"
_JOB_ROOT.mkdir(parents=True, exist_ok=True)

_cwd = os.getcwd()
_SESSION_CWDS: dict[str, str] = {}
_JOB_REGISTRY: dict[str, dict] = {}


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


def _read_job_tail(log_path: Path, max_chars: int = 4000) -> str:
    try:
        if not log_path.exists():
            return ""
        content = log_path.read_text(encoding="utf-8", errors="replace")
        return content[-max_chars:] if len(content) > max_chars else content
    except Exception:
        return ""


def _start_background_job(payload: str, effective_workdir: str, mode: str, workflow: str, session_id: str | None) -> dict:
    job_id = uuid.uuid4().hex[:12]
    log_path = _JOB_ROOT / f"{job_id}.log"
    try:
        with log_path.open("w", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                ["bash", "-c", payload],
                cwd=effective_workdir,
                stdout=fh,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        record = {
            "job_id": job_id,
            "workflow": workflow,
            "session_id": session_id,
            "cwd": effective_workdir,
            "mode": mode,
            "command": payload[:400],
            "pid": proc.pid,
            "status": "running",
            "started_at": time.time(),
            "log_path": str(log_path),
            "proc": proc,
        }
        _JOB_REGISTRY[job_id] = record
        return success(data={
            "job_id": job_id,
            "pid": proc.pid,
            "status": "running",
            "workflow": workflow,
            "cwd": effective_workdir,
            "session_id": session_id,
            "log_path": str(log_path),
        })
    except Exception as exc:
        return error(f"Could not start background job: {exc}")


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
        result = _start_background_job(payload, effective_workdir, mode, workflow, session_id)
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


@mcp.tool()
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
    if not job_id:
        return error("Provide a job_id")

    record = _JOB_REGISTRY.get(job_id)
    if record is None:
        log_path = _JOB_ROOT / f"{job_id}.log"
        if log_path.exists():
            return success(data={
                "job_id": job_id,
                "status": "finished",
                "workflow": "unknown",
                "log_path": str(log_path),
                "output_tail": _read_job_tail(log_path),
            })
        return error(f"Unknown job_id: {job_id}")

    proc = record.get("proc")
    if proc is not None:
        rc = proc.poll()
        status = "running" if rc is None else ("success" if rc == 0 else "failed")
        record["status"] = status
        if rc is not None:
            record["returncode"] = rc
    else:
        status = record.get("status", "finished")

    log_path = Path(record.get("log_path", _JOB_ROOT / f"{job_id}.log"))
    output_tail = _read_job_tail(log_path)
    return success(data={
        "job_id": job_id,
        "status": status,
        "workflow": record.get("workflow", "general"),
        "session_id": record.get("session_id"),
        "pid": record.get("pid"),
        "cwd": record.get("cwd"),
        "command": record.get("command"),
        "returncode": record.get("returncode"),
        "output_tail": output_tail,
    })


def terminal_kill_direct(job_id: str) -> dict:
    """Terminate a background terminal job by id."""
    if not job_id:
        return error("Provide a job_id")

    record = _JOB_REGISTRY.get(job_id)
    if record is None:
        return error(f"Unknown job_id: {job_id}")

    proc = record.get("proc")
    if proc is None:
        return success(data={"job_id": job_id, "killed": False, "status": "not_running"})

    try:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), 15)
            except Exception:
                proc.terminate()
        record["status"] = "killed"
        record["killed"] = True
        return success(data={
            "job_id": job_id,
            "killed": True,
            "status": "killed",
            "pid": proc.pid,
            "workflow": record.get("workflow", "general"),
        })
    except Exception as exc:
        return error(f"Could not kill job {job_id}: {exc}")


@mcp.tool()
def terminal_check_job(job_id: str) -> dict:
    return terminal_check_job_direct(job_id)


@mcp.tool()
def terminal_kill(job_id: str) -> dict:
    return terminal_kill_direct(job_id)


@mcp.tool()
def terminal_get_change_log() -> dict:
    """Return all commands executed this session for audit/rollback."""
    changes = get_change_log()
    return success(data={"changes": changes, "count": len(changes)})