"""
tools/executor.py
Unified safe executor — single entry point for all code/command execution.
All terminal, bash, and python calls route through here.
"""
import os
import re
import sys
import signal
import subprocess
import logging
import resource
import threading
import queue
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field, asdict
from config import get_profile_settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# LIVE-DISPLAY PAUSE HOOKS
# ─────────────────────────────────────────
# confirm_action() below calls input() to ask for approval. Since
# execute() is always invoked via asyncio.to_thread from the terminal
# tool, that input() call runs on a worker thread — while cli.py's
# Rich console.status() spinner keeps repainting the terminal from its
# own internal refresh thread. Without pausing it, the approval prompt
# is written but immediately overwritten by the next spinner frame, so
# it's genuinely invisible: the user can't see what they're being asked
# to approve, and Penzer appears to hang. cli.py registers status.stop
# / status.start here (Rich's own documented pattern for prompting
# during a Live display) for the duration of each turn.
_pause_live = None
_resume_live = None

# Serializes every confirm_action() call — dangerous, sensitive, AND
# privileged confirmations all go through this same lock. Without it,
# two confirmations firing concurrently (e.g. _run_parallel running two
# batched sudo calls at once — see the module note on PRIVILEGE_PATTERN
# and _run_speculative in execution_manager.py) would both call input()
# on the same real stdin at the same time: two prompts interleaved on
# screen, keystrokes meant for one prompt landing in the other. This
# makes concurrent confirmations queue instead of collide — the second
# caller's prompt simply waits its turn.
_confirm_lock = threading.RLock()


def set_live_hooks(pause, resume) -> None:
    global _pause_live, _resume_live
    _pause_live = pause
    _resume_live = resume


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

SENSITIVE_PATTERNS = [
    "pip install", "pip3 install", "python -m pip install",
    "apt install", "apt-get install", "yum install", "dnf install",
    "brew install", "npm install", "curl -fsSL", "curl | bash",
    "wget -qO-", "wget ", "git clone", "ssh ", "scp ", "rsync ",
    "http://", "https://",
]

# ─────────────────────────────────────────
# PRIVILEGE ESCALATION (sudo / su / pkexec / doas)
# ─────────────────────────────────────────
# Separate from DANGEROUS_PATTERNS/SENSITIVE_PATTERNS on purpose: those
# two run through the normal piped Popen below once approved. Privilege
# escalation runs through a DIFFERENT path (_run_privileged_interactive)
# that never pipes stdin/stdout/stderr — it inherits the real controlling
# terminal directly. That matters because sudo (and su/pkexec/doas) read
# and write their password prompt via /dev/tty, not via the process's
# stdin/stdout fds, whenever a real controlling terminal is available.
# By leaving those fds un-redirected instead of using subprocess.PIPE:
#   - the password prompt appears on the user's own screen, and their
#     keystrokes go straight to the kernel tty driver with echo off
#   - this Python process — and therefore every place that logs command
#     output (_change_log, agent.history, step/episodic memory) — never
#     reads, buffers, or sees the password at any point
#   - the command's real stdout/stderr also go straight to the screen
#     instead of being captured, which is intentional here: a piped
#     stderr is exactly the channel that would otherwise let the
#     password prompt (or anything else sensitive) get logged
#
# So: detect it, ask the user to confirm they even want to proceed, and
# if approved run it via direct terminal inheritance rather than through
# the normal piped path. `force=True` does NOT bypass the confirmation
# (force only bypasses the dangerous/sensitive *content* checks below);
# this isn't a content filter, it's a different execution mode.
PRIVILEGE_PATTERN = re.compile(r'(?:^|[;&|\n]\s*)(sudo|su|pkexec|doas)\b', re.IGNORECASE)

# Patterns that pipe or type a password straight into the command string
# itself (e.g. `echo mypassword | sudo -S ...`, `sudo --stdin`). These
# get a harder, unconditional refusal regardless of the terminal-
# inheritance path above — because the plaintext password is already
# sitting in `command` by the time we see it, as PLAIN TEXT in a string
# that gets logged to _change_log/history/memory. Terminal inheritance
# only protects a password the user types interactively at a real
# prompt; it does nothing for one already embedded in the command.
SUDO_PASSWORD_LEAK_PATTERN = re.compile(
    r'(sudo\s+(-S\b|--stdin\b)|\bsudo\b.*<<<|(echo|printf)\s+\S+\s*\|\s*sudo\b)',
    re.IGNORECASE,
)

# How long to wait for a privileged command to finish, including the
# time it takes a human to notice the password prompt and type it. Much
# longer than DEFAULT_TIMEOUT (60s) — that's sized for non-interactive
# commands, not "human has to go find their password."
SUDO_INTERACTIVE_TIMEOUT = 300


def requires_privilege_escalation(command: str) -> tuple[bool, str]:
    m = PRIVILEGE_PATTERN.search(command)
    if m:
        return True, m.group(1).lower()
    return False, ""


def has_sudo_password_leak_risk(command: str) -> bool:
    return bool(SUDO_PASSWORD_LEAK_PATTERN.search(command))


_cwd = os.getcwd()
_change_log: list = []
_exec_count: int = 0
_exec_budget: int = 100  # max executions per session
_execution_state: dict = {}

# ─────────────────────────────────────────
# RUNNING PROCESS REGISTRY
# ─────────────────────────────────────────
# Every Popen launched by execute() is registered here for the duration
# of its run. This is what makes Ctrl+C actually able to kill a running
# nmap scan (or anything else) instead of just abandoning it in the
# background — asyncio.to_thread cannot cancel the thread it's running
# in, so cancellation has to reach the OS process directly, from outside
# the blocked thread. cli.py calls kill_all_running() from its
# KeyboardInterrupt handler to do exactly that.
_running_lock = threading.Lock()
_running_procs: dict[int, subprocess.Popen] = {}
_proc_id_counter = 0


def _register_proc(proc: subprocess.Popen) -> int:
    global _proc_id_counter
    with _running_lock:
        _proc_id_counter += 1
        pid_key = _proc_id_counter
        _running_procs[pid_key] = proc
    return pid_key


def _unregister_proc(pid_key: int) -> None:
    with _running_lock:
        _running_procs.pop(pid_key, None)


def kill_all_running() -> int:
    """
    Forcibly terminate every command currently in flight. Kills the whole
    process group (not just the direct child) so tools that spawn their
    own subprocesses — nmap included — don't leave orphans behind.
    Returns the number of processes killed.
    """
    with _running_lock:
        procs = list(_running_procs.items())
    killed = 0
    for pid_key, proc in procs:
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                killed += 1
        except Exception as e:
            logger.debug("kill_all_running: failed to kill pid %s: %s", pid_key, e)
        finally:
            _unregister_proc(pid_key)
    return killed


def has_running() -> bool:
    with _running_lock:
        return len(_running_procs) > 0


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


CONFIRM_TIMEOUT_DEFAULT = 120  # seconds — how long we wait for a yes/no
# before treating "no answer" as "not approved". Matches the AI-agent
# governance standard that approval requests must not be valid/pending
# indefinitely — previously confirm_action's input() call had no bound
# at all, so a human walking away mid-prompt left the worker thread
# (and, transitively, anything waiting on it) blocked forever.


def _read_line_with_timeout(prompt: str, timeout: Optional[float]):
    """
    Reads one line of input without blocking the caller indefinitely.
    Runs the actual input() call on a background thread and waits on it
    through a queue with a timeout — if nobody answers in time, this
    returns None and the CALLER stops waiting.

    Note this does NOT kill the background thread on timeout — Python
    threads blocked in input() can't be forcibly cancelled, so it keeps
    running and will silently consume whatever the user eventually
    types later. That's an accepted, pre-existing characteristic of any
    thread-based approach to bounding a blocking call, not something
    this introduces; what changes is that the caller's decision is no
    longer held hostage to it — a timeout is treated as "not answered"
    and the run moves on instead of hanging.
    """
    if timeout is None:
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None
    q: "queue.Queue" = queue.Queue(maxsize=1)

    def _reader():
        try:
            q.put(input(prompt))
        except Exception:
            q.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None
    except KeyboardInterrupt:
        return None


def _log_approval_decision(command: str, category: str, decision: str, reason: str = "") -> None:
    """
    Records the approval DECISION itself — approved, denied, or
    no_response — into the audit trail, not just successful executions.
    Before this, declining a command (or a prompt timing out) left no
    trace anywhere; only successful runs ever got a _change_log entry.
    This is what "audit trail" means in AI-agent governance guidance
    (capture what was proposed and what was decided, even when the
    answer was no) — see the module-level research note above
    PRIVILEGE_PATTERN for more context. No authenticated multi-user
    "approver identity" exists in this local single-user CLI context;
    what we do have — timestamp, category, command, outcome — is what
    gets recorded.
    """
    global _change_log
    _change_log.append({
        "type": "approval_decision",
        "category": category,       # "privileged" | "dangerous" | "sensitive"
        "command": command[:200],
        "decision": decision,       # "approved" | "denied" | "no_response"
        "reason": (reason or "")[:200],
        "timestamp": datetime.now().isoformat(),
    })


def confirm_action(
    command: str,
    reason: str = "",
    resume_after: bool = True,
    timeout: Optional[float] = None,
) -> bool:
    """Prompt the user for explicit approval before running a risky command.

    timeout: if given, stop waiting after this many seconds and treat
    the lack of an answer as "not approved" (fail closed) rather than
    blocking forever — see _read_line_with_timeout.

    resume_after=False: leaves the live display paused after answering,
    instead of resuming it in the finally block below. Used by the
    privilege-escalation path in execute(), where the real sudo password
    prompt happens immediately after this yes/no — resuming the Rich
    spinner right here would start repainting the terminal at exactly
    the moment sudo tries to print its own prompt to it, visually
    swallowing it. The caller is then responsible for calling
    _resume_live() itself once the whole interactive sequence is done
    (see _run_privileged_interactive's caller in execute()).
    """
    prompt = "Approve this command? [y/N]: "
    if reason:
        prompt = f"{reason}\n{prompt}"
    if timeout is not None:
        prompt = f"{prompt} (auto-declines in {int(timeout)}s if no response) "
    response = None
    with _confirm_lock:
        if _pause_live is not None:
            try:
                _pause_live()
            except Exception:
                pass
        try:
            response = _read_line_with_timeout(prompt, timeout)
        finally:
            if resume_after and _resume_live is not None:
                try:
                    _resume_live()
                except Exception:
                    pass
    if response is None:
        return False
    return response.strip().lower() in {"y", "yes"}


def is_sensitive(command: str) -> bool:
    for p in SENSITIVE_PATTERNS:
        if p.lower() in command.lower():
            return True
    return False


def _set_limits():
    """Apply resource limits to subprocess. Also puts the child in its
    own process group (via start_new_session in Popen) so a kill can
    target the whole group, not just this one PID."""
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
    approval_required: Optional[bool] = None,
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
        force: Bypass dangerous command check. Does NOT bypass the
            privilege-escalation check below — see PRIVILEGE_PATTERN
            docstring for why that one is architectural, not a content
            filter `force` is meant to override.
        venv_path: Path to Python binary (for venv support)

    Returns:
        dict with stdout, stderr, exit_code, cwd, mode
    """
    global _cwd, _change_log, _exec_count

    if approval_required is None:
        approval_required = get_profile_settings().get("approval_required", True)

    if not command or not command.strip():
        return _error("No command provided")

    # Budget check
    if _exec_count >= _exec_budget:
        return _error(f"Execution budget exhausted ({_exec_budget} calls). Reset session to continue.")

    # ── Privilege escalation check — ALWAYS enforced, first, independent
    # of `force` and `approval_required`. See module-level comment above
    # PRIVILEGE_PATTERN for the full reasoning.
    escalates, priv_tool = requires_privilege_escalation(command)
    if escalates:
        if has_sudo_password_leak_risk(command):
            # Plaintext password is already sitting in `command` — refuse
            # unconditionally, no prompt, no echo of the command back.
            return _error(
                f"Refusing this command: it pipes a password into {priv_tool} "
                "non-interactively, which would leak the password into logs "
                "and memory. If you need to run this, do it directly in your "
                "own terminal, not through Penzer."
            )
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            # No real controlling terminal attached to this process (e.g.
            # headless, under a supervisor, no pty). Promising "your
            # terminal will prompt you for a password" would be a lie in
            # this case, and spawning the process anyway would either
            # have sudo fail cryptically or silently inherit whatever
            # non-terminal stdin/stdout Penzer itself happens to have.
            # Refuse cleanly instead of pretending this can work.
            return _warn(
                f"Can't run {priv_tool} commands here — no interactive "
                "terminal is attached to this session, so there's nowhere "
                "safe for a password prompt to go. Run this yourself in "
                f"your own terminal:\n\n  {command}"
            )
        # A command can be BOTH privileged AND match a dangerous/sensitive
        # pattern (e.g. `sudo rm -rf /important`). The privilege check
        # runs first and gates the whole thing either way, but the
        # approval prompt below only mentioned "needs sudo" — silently
        # dropping the fact that it's ALSO a known-dangerous pattern.
        # That under-informs the person approving it. Surface both here.
        extra_risk = []
        _dangerous, _pattern = is_dangerous(command)
        if _dangerous:
            extra_risk.append(f"⚠ ALSO matches a known dangerous pattern: '{_pattern}'")
        if is_sensitive(command):
            extra_risk.append("⚠ ALSO matches a sensitive/install-related pattern")
        extra_risk_text = ("\n" + "\n".join(extra_risk) + "\n") if extra_risk else ""

        # Held for the WHOLE sequence (confirm -> real password prompt ->
        # command finishing), not just the confirm_action() call — see
        # _confirm_lock's module comment. RLock so confirm_action()'s own
        # internal `with _confirm_lock:` (same thread) doesn't deadlock
        # against this outer one.
        with _confirm_lock:
            update_execution_state(
                needs_confirmation=True,
                confirmation_reason=f"Requires {priv_tool} (elevated privileges).",
            )
            approved = confirm_action(
                command,
                f"This needs {priv_tool} / elevated privileges: {command}\n"
                f"{extra_risk_text}"
                f"If you approve, your terminal will prompt you for your "
                f"password directly — Penzer will not see, read, or store it.",
                resume_after=False,
                timeout=CONFIRM_TIMEOUT_DEFAULT,
            )
            audit_reason = f"requires {priv_tool}" + (
                f"; {'; '.join(extra_risk)}" if extra_risk else ""
            )
            if not approved:
                if _resume_live is not None:
                    try:
                        _resume_live()
                    except Exception:
                        pass
                _log_approval_decision(command, "privileged", "denied", audit_reason)
                return _warn(f"{priv_tool} command not approved. Execution cancelled by user.")
            _log_approval_decision(command, "privileged", "approved", audit_reason)
            try:
                return _run_privileged_interactive(
                    command, cwd=workdir or _cwd, timeout=max(timeout, SUDO_INTERACTIVE_TIMEOUT)
                )
            finally:
                # Resume only now — after the real password prompt and the
                # command itself have both finished — not right after the
                # yes/no like confirm_action's default behavior would.
                if _resume_live is not None:
                    try:
                        _resume_live()
                    except Exception:
                        pass

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
                if confirm_action(command, reason, timeout=CONFIRM_TIMEOUT_DEFAULT):
                    force = True
                    _log_approval_decision(command, "dangerous", "approved", reason)
                else:
                    _log_approval_decision(command, "dangerous", "denied", reason)
                    return _warn(f"{reason} Execution cancelled by user.")
            else:
                return _warn(f"{reason} Set force=True to run.")

        sensitive = is_sensitive(command)
        if sensitive and approval_required:
            reason = "Sensitive or install-related command detected."
            update_execution_state(
                needs_confirmation=True,
                confirmation_reason=reason,
            )
            if confirm_action(command, reason, timeout=CONFIRM_TIMEOUT_DEFAULT):
                force = True
                _log_approval_decision(command, "sensitive", "approved", reason)
            else:
                _log_approval_decision(command, "sensitive", "denied", reason)
                return _warn(f"{reason} Execution cancelled by user.")

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

    proc = None
    pid_key = None
    try:
        # Popen (not subprocess.run) so the process object exists and is
        # registered *before* we block waiting on it — this is what lets
        # kill_all_running() reach it from another thread while
        # communicate() is still blocking here. start_new_session=True
        # puts it in its own process group so nmap (or anything it
        # spawns) dies with it, not just the immediate child.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            preexec_fn=_set_limits,
            start_new_session=True,
        )
        pid_key = _register_proc(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_proc_group(proc)
            proc.wait(timeout=5)
            return _error(f"Timed out after {timeout}s")

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
            "stdout":    stdout[:MAX_OUTPUT],
            "stderr":    stderr[:10_000],
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
    except Exception as e:
        if proc is not None:
            _kill_proc_group(proc)
        return _error(str(e))
    finally:
        if pid_key is not None:
            _unregister_proc(pid_key)


def _run_privileged_interactive(command: str, cwd: str, timeout: int) -> dict:
    """
    Runs an approved privilege-escalation command (sudo/su/pkexec/doas)
    with stdin/stdout/stderr all left un-redirected — i.e. inherited
    directly from the real controlling terminal Penzer itself is running
    in — instead of the subprocess.PIPE capture every other command in
    this executor uses.

    Why this is safe: sudo (and su/pkexec/doas) read and write their
    password prompt via /dev/tty directly whenever a real controlling
    terminal is available, not via the process's stdin/stdout file
    descriptors. By leaving those fds un-redirected:
      - the prompt appears on the user's own screen, and their keystrokes
        go straight to the kernel tty driver (echo disabled by the tty
        itself, same as any normal password prompt)
      - this Python process never reads, buffers, stores, or forwards
        those keystrokes anywhere — not to a variable, not to a log, not
        to _change_log/history/memory. There is no code path here for
        the password to travel through, because there's no pipe for it
        to travel through.

    Trade-off, and why it's intentional: because stdout/stderr aren't
    captured, the command's real output goes straight to the screen
    instead of coming back to the agent as text — we only learn the
    exit code. That's the same property that keeps the password safe:
    a piped stderr is exactly the channel that would otherwise let the
    password prompt (or anything else the command prints) get logged.
    """
    global _exec_count, _change_log
    proc = None
    pid_key = None
    try:
        proc = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd,
            stdin=None, stdout=None, stderr=None,  # inherit the real terminal, no capture
            start_new_session=True,
        )
        pid_key = _register_proc(proc)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_proc_group(proc)
            proc.wait(timeout=5)
            return _error(f"Timed out after {timeout}s waiting for the privileged command to finish.")

        _exec_count += 1
        _change_log.append({
            "mode": "bash",
            "command": command[:200],
            "exit_code": proc.returncode,
            "cwd": cwd,
            "privileged": True,
        })
        data = {
            "stdout": "(privileged command — output printed directly to your terminal, not captured)",
            "stderr": "",
            "exit_code": proc.returncode,
            "cwd": cwd,
            "mode": "bash",
            "exec_count": _exec_count,
        }
        update_execution_state(needs_confirmation=False, confirmation_reason="")
        return _success(data) if proc.returncode == 0 else _warn_data(data, f"Exit code {proc.returncode}")
    except Exception as e:
        if proc is not None:
            _kill_proc_group(proc)
        return _error(str(e))
    finally:
        if pid_key is not None:
            _unregister_proc(pid_key)


def _kill_proc_group(proc: subprocess.Popen) -> None:
    """Best-effort kill of the whole process group, falling back to a
    plain kill() if the group is already gone or we lack permission."""
    try:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


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