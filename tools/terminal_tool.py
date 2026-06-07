"""
Terminal Tool — routes all execution through unified executor.
"""

import os
from agent.core import mcp
from tools.executor import execute, get_change_log, reset, set_exec_budget
from tools.standards import success, error, warning

_cwd = os.getcwd()


@mcp.tool()
def terminal(command: str, timeout: int = 60, workdir: str = None, force: bool = False) -> dict:
    """Execute a bash command."""
    global _cwd
    if command.strip().startswith("cd "):
        target = os.path.normpath(os.path.join(_cwd, os.path.expanduser(command.strip()[3:].strip())))
        if not os.path.isdir(target):
            return error(f"No such directory: {target}")
        _cwd = target
        return success(data={"cwd": _cwd})
    if timeout == 0:
        import subprocess
        proc = subprocess.Popen(["bash", "-c", command], cwd=workdir or _cwd,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        return success(data={"pid": proc.pid, "mode": "detached"})
    return execute(command, mode="bash", timeout=timeout, workdir=workdir, force=force)


@mcp.tool()
def run_python(code: str, timeout: int = 60, use_venv: bool = True) -> dict:
    """Execute Python code inline."""
    return execute(code, mode="python", timeout=timeout)


@mcp.tool()
def run_bash(script: str, timeout: int = 60) -> dict:
    """Execute a multi-line bash script inline."""
    return execute(script, mode="bash", timeout=timeout)


@mcp.tool()
def terminal_get_change_log() -> dict:
    """Return all commands executed this session."""
    return success(data={"changes": get_change_log(), "count": len(get_change_log())})