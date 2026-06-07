"""
tools/executor.py
Unified safe executor — single entry point for all code/command execution.
All terminal, bash, and python calls route through here.
"""

import os
import subprocess
import logging
import resource
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# LIMITS
# ─────────────────────────────────────────
DEFAULT_TIMEOUT   = 60       # seconds
MAX_TIMEOUT       = 3600     # 1 hour hard cap
MAX_OUTPUT        = 50_000   # chars
MAX_MEMORY_MB     = 512      # MB RAM limit per process

DANGEROUS_PATTERNS = [
    "rm -rf", "rm -f", "mkfs", "dd if=",
    ":(){:|:&};:", "chmod -R 777", "> /dev/sda",
    "shutdown", "reboot", "halt", "poweroff",
    "iptables -F", "ufw disable",
]

_cwd = os.getcwd()
_change_log: list = []
_exec_count: int = 0
_exec_budget: int = 100  # max executions per session


# ─────────────────────────────────────────
# SAFETY
# ─────────────────────────────────────────

def is_dangerous(command: str) -> tuple[bool, str]:
    for p in DANGEROUS_PATTERNS:
        if p.lower() in command.lower():
            return True, p
    return False, ""


def _set_limits():
    """Apply resource limits to subprocess."""
    try:
        mem = MAX_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except Exception:
        pass


# ─────────────────────────────────────────
# CORE EXECUTOR
# ─────────────────────────────────────────

def execute(
    command: str,
    mode: str = "bash",          # "bash" | "python"
    timeout: int = DEFAULT_TIMEOUT,
    workdir: Optional[str] = None,
    force: bool = False,
    venv_path: Optional[str] = None,
) -> dict:
    """
    Unified executor for bash commands and Python code.

    Args:
        command: Bash command or Python code to run
        mode: "bash" or "python"
        timeout: Seconds before kill (capped at MAX_TIMEOUT)
        workdir: Working directory override
        force: Bypass dangerous command check
        venv_path: Path to Python binary (for venv support)

    Returns:
        dict with stdout, stderr, exit_code, cwd, mode
    """
    global _cwd, _change_log, _exec_count

    if not command or not command.strip():
        return _error("No command provided")

    # Budget check
    if _exec_count >= _exec_budget:
        return _error(f"Execution budget exhausted ({_exec_budget} calls). Reset session to continue.")

    # Safety check
    if not force:
        dangerous, pattern = is_dangerous(command)
        if dangerous:
            return _warn(f"Dangerous pattern '{pattern}' detected. Set force=True to run.")

    # Resolve working directory
    cwd = workdir or _cwd
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    # Build command
    if mode == "python":
        python_bin = venv_path or _find_venv_python(cwd) or "python3"
        cmd = [python_bin, "-c", command]
    else:
        cmd = ["bash", "-c", command]

    logger.info(f"executor[{mode}][{cwd}]: {command[:150]}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            preexec_fn=_set_limits,
        )

        _exec_count += 1
        _change_log.append({
            "mode": mode,
            "command": command[:200],
            "exit_code": proc.returncode,
            "cwd": cwd,
        })

        # Update cwd if cd was used
        if mode == "bash" and "cd " in command:
            try:
                result = subprocess.run(
                    ["bash", "-c", f"{command} > /dev/null 2>&1; pwd"],
                    capture_output=True, text=True, cwd=cwd, timeout=5,
                )
                new_cwd = result.stdout.strip().splitlines()[-1]
                if new_cwd and os.path.isdir(new_cwd):
                    _cwd = new_cwd
            except Exception:
                pass

        data = {
            "stdout":    proc.stdout[:MAX_OUTPUT],
            "stderr":    proc.stderr[:10_000],
            "exit_code": proc.returncode,
            "cwd":       _cwd,
            "mode":      mode,
            "exec_count": _exec_count,
        }

        return _success(data) if proc.returncode == 0 else _warn_data(data, f"Exit code {proc.returncode}")

    except subprocess.TimeoutExpired:
        return _error(f"Timed out after {timeout}s")
    except Exception as e:
        return _error(str(e))


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _find_venv_python(cwd: str) -> Optional[str]:
    for d in ["env", "venv", ".venv", ".env"]:
        p = os.path.join(cwd, d, "bin", "python")
        if os.path.isfile(p):
            return p
    return None


def get_change_log() -> list:
    return _change_log


def get_exec_count() -> int:
    return _exec_count


def set_exec_budget(budget: int) -> None:
    global _exec_budget
    _exec_budget = budget


def reset() -> None:
    global _change_log, _exec_count
    _change_log = []
    _exec_count = 0


def _success(data: dict) -> dict:
    return {"status": "success", "data": data}

def _warn(msg: str) -> dict:
    return {"status": "warning", "data": {}, "message": msg}

def _warn_data(data: dict, msg: str) -> dict:
    return {"status": "warning", "data": data, "message": msg}

def _error(msg: str) -> dict:
    return {"status": "error", "data": {}, "message": msg}