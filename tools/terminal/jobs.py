"""Background terminal process lifecycle and job queries."""

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from tools.sandbox import build_sandbox_command
from tools.standards import error, success


_JOB_ROOT = Path(__file__).resolve().parent.parent / "logs" / "jobs"
_JOB_ROOT.mkdir(parents=True, exist_ok=True)
_JOB_REGISTRY: dict[str, dict] = {}
_JOB_LOCK = threading.RLock()


def _read_job_tail(log_path: Path, max_chars: int = 4000) -> str:
    try:
        if not log_path.exists():
            return ""
        content = log_path.read_text(encoding="utf-8", errors="replace")
        return content[-max_chars:] if len(content) > max_chars else content
    except Exception:
        return ""


def _refresh_status(record: dict) -> str:
    if record.get("killed"):
        return "killed"
    proc = record.get("proc")
    if proc is None:
        return record.get("status", "finished")
    returncode = proc.poll()
    if returncode is None:
        status = "running"
    else:
        status = "success" if returncode == 0 else "failed"
        record["returncode"] = returncode
    record["status"] = status
    return status


def start_job(payload: str, effective_workdir: str, mode: str, workflow: str, session_id: str | None) -> dict:
    job_id = uuid.uuid4().hex[:12]
    log_path = _JOB_ROOT / f"{job_id}.log"
    try:
        with log_path.open("w", encoding="utf-8") as output:
            command = ["bash", "-c", payload]
            if mode == "python":
                command = [sys.executable, "-c", payload]
            proc = subprocess.Popen(
                build_sandbox_command(command, effective_workdir),
                cwd=effective_workdir,
                stdout=output,
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
        with _JOB_LOCK:
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


def check_job(job_id: str) -> dict:
    if not job_id:
        return error("Provide a job_id")

    with _JOB_LOCK:
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

    status = _refresh_status(record)
    log_path = Path(record.get("log_path", _JOB_ROOT / f"{job_id}.log"))
    return success(data={
        "job_id": job_id,
        "status": status,
        "workflow": record.get("workflow", "general"),
        "session_id": record.get("session_id"),
        "pid": record.get("pid"),
        "cwd": record.get("cwd"),
        "command": record.get("command"),
        "returncode": record.get("returncode"),
        "output_tail": _read_job_tail(log_path),
    })


def list_jobs(status: str | None = None, session_id: str | None = None) -> dict:
    """List known background jobs, optionally filtered by status or session."""
    status_filter = status.lower().strip() if status else None
    with _JOB_LOCK:
        records = list(_JOB_REGISTRY.values())

    jobs = []
    known_ids = set()
    for record in records:
        job_status = _refresh_status(record)
        job_id = record["job_id"]
        known_ids.add(job_id)
        if status_filter and job_status != status_filter:
            continue
        if session_id is not None and record.get("session_id") != session_id:
            continue
        jobs.append({
            "job_id": job_id,
            "status": job_status,
            "workflow": record.get("workflow", "general"),
            "session_id": record.get("session_id"),
            "pid": record.get("pid"),
            "cwd": record.get("cwd"),
            "command": record.get("command"),
            "returncode": record.get("returncode"),
            "started_at": record.get("started_at"),
        })

    for log_path in _JOB_ROOT.glob("*.log"):
        job_id = log_path.stem
        if job_id in known_ids or (status_filter and status_filter != "finished"):
            continue
        jobs.append({
            "job_id": job_id,
            "status": "finished",
            "workflow": "unknown",
            "session_id": None,
            "log_path": str(log_path),
            "started_at": log_path.stat().st_mtime,
        })

    jobs.sort(key=lambda job: job.get("started_at") or 0, reverse=True)
    if session_id is not None:
        jobs = [job for job in jobs if job.get("session_id") == session_id]
    return success(data={"jobs": jobs, "count": len(jobs)})


def kill_job(job_id: str) -> dict:
    if not job_id:
        return error("Provide a job_id")

    with _JOB_LOCK:
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