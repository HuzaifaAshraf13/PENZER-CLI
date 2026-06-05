# tools/terminal_tool.py
"""
Terminal Tool: Execute bash commands with safety features.
"""

import os
import subprocess
import logging

from agent.core import mcp
from tools.standards import success, error, warning

logger = logging.getLogger(__name__)

_cwd = os.getcwd()


@mcp.tool()
def terminal(command: str, timeout: int = 60, workdir: str = None) -> dict:
    """
    Run any bash command. Returns stdout, stderr, exit code.
    Maintains working directory across calls.

    Args:
        command: Any bash command — pipes, redirects, chains, background (&), anything.
        timeout: Seconds before kill (default 60, max 3600). Use 0 for fire-and-forget.
        workdir: Optional one-off directory override for this command only.
    """
    global _cwd

    if not command or not command.strip():
        return error("No command provided")

    # Handle cd — persist for future calls
    if command.strip().startswith("cd "):
        target = os.path.expanduser(command.strip()[3:].strip())
        target = target if os.path.isabs(target) else os.path.join(_cwd, target)
        target = os.path.normpath(target)
        if not os.path.isdir(target):
            return error(f"No such directory: {target}")
        _cwd = target
        return success(data={"cwd": _cwd})

    # Fire-and-forget (timeout=0) — detach, return pid
    if timeout == 0:
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                cwd=workdir or _cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return success(data={"pid": proc.pid, "mode": "detached"})
        except Exception as e:
            return error(str(e))

    timeout = max(1, min(timeout, 3600))
    cwd = workdir or _cwd
    logger.info("terminal[%s]: %s", cwd, command[:200])

    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        # Update cwd if command changed it
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
            "stdout": proc.stdout[:50_000],
            "stderr": proc.stderr[:10_000],
            "exit_code": proc.returncode,
            "cwd": _cwd,
        }
        return success(data=data) if proc.returncode == 0 \
            else warning(data=data, message=f"Exit code {proc.returncode}")

    except subprocess.TimeoutExpired:
        return error(f"Timed out after {timeout}s — use timeout=0 for long-running processes")
    except Exception as e:
        return error(str(e))
