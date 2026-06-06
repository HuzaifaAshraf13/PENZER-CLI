"""
Terminal Tool: Execute bash/python with safety features.
"""

import os
import subprocess
import logging

from agent.core import mcp
from tools.standards import success, error, warning

logger = logging.getLogger(__name__)

_cwd = os.getcwd()
_change_log = []

DANGEROUS_PATTERNS = [
    "rm -rf", "rm -f", "mkfs", "dd if=", ":(){:|:&};:",
    "chmod -R 777", "> /dev/sda", "shutdown", "reboot",
    "halt", "poweroff", "iptables -F", "ufw disable",
]


def _is_dangerous(command: str) -> tuple[bool, str]:
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in command.lower():
            return True, pattern
    return False, ""


def _run(cmd: list, timeout: int) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_cwd)
    data = {"stdout": proc.stdout[:50_000], "stderr": proc.stderr[:10_000], "exit_code": proc.returncode, "cwd": _cwd}
    return success(data=data) if proc.returncode == 0 else warning(data=data, message=f"Exit code {proc.returncode}")


@mcp.tool()
def terminal(command: str, timeout: int = 60, workdir: str = None, force: bool = False) -> dict:
    """Execute a bash command. Set force=True to bypass safety check."""
    global _cwd, _change_log

    if not command or not command.strip():
        return error("No command provided")

    if not force:
        dangerous, pattern = _is_dangerous(command)
        if dangerous:
            return warning(data={"command": command}, message=f"Dangerous: '{pattern}'. Set force=True to run.")

    if command.strip().startswith("cd "):
        target = os.path.normpath(os.path.join(_cwd, os.path.expanduser(command.strip()[3:].strip())))
        if not os.path.isdir(target):
            return error(f"No such directory: {target}")
        _cwd = target
        return success(data={"cwd": _cwd})

    if timeout == 0:
        proc = subprocess.Popen(["bash", "-c", command], cwd=workdir or _cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return success(data={"pid": proc.pid, "mode": "detached"})

    try:
        _change_log.append({"type": "command", "command": command})
        return _run(["bash", "-c", command], min(max(1, timeout), 3600))
    except subprocess.TimeoutExpired:
        return error(f"Timed out after {timeout}s")
    except Exception as e:
        return error(str(e))


@mcp.tool()
def run_python(code: str, timeout: int = 60, use_venv: bool = True) -> dict:
    """Execute Python code inline. Auto-detects venv."""
    python_bin = "python3"
    if use_venv:
        for venv in ["env", "venv", ".venv", ".env"]:
            p = os.path.join(_cwd, venv, "bin", "python")
            if os.path.isfile(p):
                python_bin = p
                break
    try:
        _change_log.append({"type": "python", "code": code[:100]})
        return _run([python_bin, "-c", code], timeout)
    except subprocess.TimeoutExpired:
        return error(f"Timed out after {timeout}s")
    except Exception as e:
        return error(str(e))


@mcp.tool()
def run_bash(script: str, timeout: int = 60) -> dict:
    """Execute a multi-line bash script inline."""
    dangerous, pattern = _is_dangerous(script)
    if dangerous:
        return warning(data={}, message=f"Dangerous: '{pattern}'. Use terminal(force=True) instead.")
    try:
        _change_log.append({"type": "bash_script", "script": script[:100]})
        return _run(["bash", "-c", script], timeout)
    except subprocess.TimeoutExpired:
        return error(f"Timed out after {timeout}s")
    except Exception as e:
        return error(str(e))


@mcp.tool()
def terminal_get_change_log() -> dict:
    """Return all commands executed this session."""
    return success(data={"changes": _change_log, "count": len(_change_log)})