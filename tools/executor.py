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
from dataclasses import dataclass, field, asdict

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
_execution_state: dict = {}


@dataclass
class ExecutionState:
    goal: str = ""
    current_step: str = ""
    completed_steps: list[str] = field(default_factory=list)
    blocked_steps: list[str] = field(default_factory=list)
    next_action: str = ""
    needs_confirmation: bool = False
    confirmation_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────
# SAFETY
# ─────────────────────────────────────────

def is_dangerous(command: str) -> tuple[bool, str]:
    for p in DANGEROUS_PATTERNS:
        if p.lower() in command.lower():
            return True, p
    return False, ""


def confirm_action(command: str, reason: str = "") -> bool:
    """Prompt the user for explicit approval before running a risky command."""
    prompt = "This action may be destructive or sensitive. Approve execution? [y/N]: "
    if reason:
        prompt = f"{reason}\n{prompt}"
    try:
        response = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return response in {"y", "yes"}


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
    approval_required: bool = False,
    confirmation_reason: str = "",
    state: Optional[dict] = None,
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
            reason = f"Dangerous pattern '{pattern}' detected."
            if approval_required:
                update_execution_state(
                    needs_confirmation=True,
                    confirmation_reason=reason,
                )
                if confirm_action(command, reason):
                    force = True
                else:
                    return _warn(f"{reason} Execution cancelled by user.")
            else:
                return _warn(f"{reason} Set force=True to run.")

    # Resolve working directory
    cwd = workdir or _cwd
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    # Build command
    if mode == "python":
        python_bin = venv_path or _find_venv_python(cwd) or "python3"
        cmd = [python_bin, "-c", command]
    else:
        cmd = ["bash", "-c", command]

    if state:
        set_execution_state(state)

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

        update_execution_state(
            needs_confirmation=False,
            confirmation_reason="",
        )

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
    global _change_log, _exec_count, _execution_state
    _change_log = []
    _exec_count = 0
    _execution_state = {}


def set_execution_state(state: dict) -> None:
    global _execution_state
    _execution_state = state


def get_execution_state() -> dict:
    return _execution_state


def format_execution_state() -> str:
    state = get_execution_state().get("state", {}) if isinstance(get_execution_state(), dict) else {}
    goal = state.get("goal", "") or ""
    current_step = state.get("current_step", "") or ""
    completed = state.get("completed_steps", []) or []
    blocked = state.get("blocked_steps", []) or []
    next_action = state.get("next_action", "") or ""
    lines = []
    if goal:
        lines.append(f"Goal: {goal}")
    if current_step:
        lines.append(f"Current step: {current_step}")
    if completed:
        lines.append("Completed steps: " + " · ".join(completed[-3:]))
    if blocked:
        lines.append("Blocked steps: " + " · ".join(blocked[-3:]))
    if next_action:
        lines.append(f"Next action: {next_action}")
    if not lines:
        return "No execution state yet."
    return "\n".join(lines)


def update_execution_state(**updates) -> dict:
    global _execution_state
    state = _execution_state.setdefault("state", ExecutionState().to_dict())
    if not isinstance(state, dict):
        state = {}
    state.update(updates)
    _execution_state["state"] = state
    return _execution_state


def _success(data: dict) -> dict:
    return {"status": "success", "data": data}

def _warn(msg: str) -> dict:
    return {"status": "warning", "data": {}, "message": msg}

def _warn_data(data: dict, msg: str) -> dict:
    return {"status": "warning", "data": data, "message": msg}

def _error(msg: str) -> dict:
    return {"status": "error", "data": {}, "message": msg}