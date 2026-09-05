#!/usr/bin/env python3
"""PENZER-CLI: Autonomous Terminal Agent"""
import threading
import asyncio
import re
import json
import logging
import subprocess
import os
import sys
import signal
import warnings
import argparse
import contextlib
import io
from pathlib import Path

try:
    from authlib.deprecate import AuthlibDeprecationWarning
except Exception:
    AuthlibDeprecationWarning = DeprecationWarning

warnings.filterwarnings("ignore", category=AuthlibDeprecationWarning)
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*", category=Warning)
warnings.filterwarnings("ignore", category=Warning, module="requests")
from typing import Any, Callable
from rich.markdown import Markdown
from rich.panel import Panel
from logger import get_logger, console as console
from version import get_version, check_for_update, perform_update
from tools.executor import format_execution_state, kill_all_running, set_live_hooks
from config import PROFILE_OPTIONS, get_profile_settings, validate_config
from agent.activity_timeline import ActivityTimeline, set_activity_timeline, get_activity_timeline
from ui.terminal import InteractiveTerminal, normalize_command

warnings.filterwarnings("ignore", message=".*authlib.*deprecated.*", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*", category=Warning)
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*", category=DeprecationWarning)

logger = get_logger("cli")

from agent.agent import PenzerAgent
from agent.server import start_server

for _log in [
    "agent.agent", "agent.penzermodule", "penzer.core", "penzer.server",
    "agent.skills.search", "agent.skills.loader",
    "agent.skills.base", "session.memory", "httpx",
    "tools.executor", "tools.plugins",
]:
    logging.getLogger(_log).setLevel(logging.WARNING)


def compose_summary_lines(matched: list[str] | None = None, trace: list | None = None, calls_used: int = 0, tokens_used: int = 0) -> list[str]:
    """Generate compact execution metadata without dumping tool output."""
    lines: list[str] = []
    if trace:
        executed: list[str] = []
        for entry in trace:
            if isinstance(entry, dict):
                name = str(entry.get("tool") or "tool")
                args = entry.get("args") or {}
                command = args.get("command") if isinstance(args, dict) else None
                label = f"{name}: {command}" if command else name
            else:
                label = str(entry)
            executed.append(label if len(label) <= 80 else label[:77] + "...")
        lines.append("[dim]Executed:[/] " + " · ".join(executed))
    if tokens_used:
        lines.append(f"[dim]LLM:[/] ~{tokens_used} tokens")
    return lines


class LiveStatusView:
    def __init__(self) -> None:
        self.current = "Starting…"
        self.events: list[str] = []
        self._lock = threading.Lock()
        self.current_skill = ""
        self.timeline = ActivityTimeline()
        set_activity_timeline(self.timeline)

    def _clean(self, text: str) -> str:
        text = str(text or "").strip()
        text = re.sub(r"\[(?:/?[A-Za-z0-9_]+|/?)\]", "", text)
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
        text = " ".join(text.split())
        if not text:
            return "Working…"

        m = re.search(r"([A-Za-z0-9_\- ]+?)\s+(done|failed|warning|running|success)\b", text, flags=re.I)
        if m:
            return f"{m.group(1).strip()} {m.group(2).lower()}"

        if re.search(r"Sensitive or install-related command detected\.|Approve this command\?|Command:\s*", text, flags=re.I):
            return "Approving command…"

        if len(text) > 90:
            text = text[:87] + "..."
        return text

    def update_skill(self, skill_name: str) -> None:
        with self._lock:
            self.current_skill = self._clean(skill_name)

    def update(self, message: str) -> None:
        if not message:
            return
        message = self._clean(message)
        with self._lock:
            if message == self.current and self.events and self.events[-1] == message:
                return
            self.current = message
            self.events.append(message)
            self.events = self.events[-6:]

    def render(self) -> str:
        with self._lock:
            current = self.current
            if self.current_skill:
                return f"{self.current_skill} · {current}"
            return f"● {current}"

    def status_line(self) -> str:
        return self.render()

    def _render_activity_bubble(self) -> str:
        total_events = len(self.timeline.events)
        active_events = sum(1 for event in self.timeline.events if event.get("status") == "running")
        completed_events = sum(1 for event in self.timeline.events if event.get("status") != "running")
        last_running = next((event for event in reversed(self.timeline.events) if event.get("status") == "running"), None)
        event = last_running or (self.timeline.events[-1] if self.timeline.events else None)
        if not event:
            return "[dim]No activity yet.[/]"
        status = event.get("status", "running")
        label = self._activity_label(event)
        progress_suffix = f"  [dim]({completed_events}/{total_events} completed, {active_events} running)[/]" if total_events > 1 else ""
        if status == "running":
            bubble = f"[bold blue]●[/] [bold]{label}[/bold]{progress_suffix}"
            bubble += "  [dim](type 'activity' to expand)[/]"
            return bubble
        if status == "success":
            return f"[bold green]✓ Completed[/bold green] in {self._compute_total_duration()}s  [dim]({completed_events}/{total_events} completed)[/dim]  [dim](type 'activity' to reopen)[/]"
        if status == "failed":
            return f"[bold red]✗ Failed[/bold red] — {label}  [dim]({total_events} events total)[/dim]  [dim](type 'activity' to inspect)[/]"
        return f"[bold yellow]⚠[/bold yellow] {label}  [dim]({total_events} events total)[/dim]  [dim](type 'activity' to inspect)[/]"

    def _short_label(self, text: str, max_chars: int = 40) -> str:
        clean = " ".join(str(text or "").split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max_chars - 1].rstrip() + "…"

    def _activity_label(self, event: dict[str, Any]) -> str:
        event_type = event.get("event_type", "activity")
        details = event.get("details") or {}
        title = event.get("title") or event.get("message") or "Working"
        message = str(event.get("message") or "")
        if event_type == "progress":
            if message:
                return self._short_label(message, 60)
            return "Progress update…"
        if event_type == "terminal":
            if message:
                return f"Running command: {self._short_label(message, 50)}"
            return "Running command…"
        if event_type == "file_operation":
            operation = str(details.get("operation") or event.get("title") or "file").lower()
            path = details.get("path") or details.get("filepath") or details.get("file")
            filename = Path(path).name if path else None
            if operation in {"read", "list"}:
                return f"Reading {filename or 'files'}…"
            if operation in {"write", "replace", "append", "create", "delete"}:
                if filename:
                    return f"Updating {filename}…"
                return "Updating files…"
            return "Working with files…"
        if event_type == "search":
            query = details.get("query") or message
            if query:
                return f"Searching for {self._short_label(query, 40)}"
            return "Searching…"
        if event_type == "memory":
            return "Recalling memory…"
        if event_type == "plugin":
            return "Managing plugins…"
        if event_type == "skill":
            return "Matching skills…"
        if event_type == "thinking":
            return "Planning task…"
        if event_type == "tool":
            tool_name = details.get("tool") or event.get("title")
            if tool_name:
                return f"Executing {self._short_label(tool_name, 40)}…"
            return "Running skill…"
        if event_type == "summary":
            return "Finalizing results…"
        return title

    def _compute_total_duration(self) -> str:
        total = 0.0
        for event in self.timeline.events:
            duration = event.get("details", {}).get("duration_sec")
            if isinstance(duration, (int, float)):
                total += float(duration)
        return f"{total:.1f}"


def clean_response(text: str) -> str:
    """Clean and parse CLI response text with robust error handling.

    By the time a response reaches here, agent.run()/resume_last_task()
    has already returned — the run loop has fully finished (successfully,
    or by giving up/erroring out with an explanatory message). There is
    no legitimate case where the *final* result string still contains an
    unresolved tool call: agent.py's _loop() only exits with a final
    string once the model gave a real answer (_handle_empty_calls) or the
    run was stopped for a specific, already-explained reason (iteration
    limit, timeout, resource limit, etc). So if this text still parses as
    JSON containing a tool/tools/tool_calls key, that's not "the task is
    still executing" — it's evidence something upstream (most likely
    llm.py's response parser) failed to fully resolve a model response,
    and the raw JSON leaked through as the "final answer" by mistake.
    We still want a readable message here rather than dumping raw JSON at
    the user, but we log it so the actual cause (an llm.py/agent.py
    schema mismatch) doesn't go unnoticed.
    """
    if not text:
        return ""
    text = text.strip()
    text = text.replace("\\n", "\n")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # Accept "tool"/"tools" (llm.py's recognized schema) as well
            # as "tool_calls" (agent.py's history-entry key) — any of
            # these leaking through as a "final" response means the run
            # ended on an unresolved/garbled model turn rather than a
            # genuine answer, not that the task is actively in progress.
            if data.get("tool_calls") or data.get("tools") or data.get("tool"):
                logger.warning(
                    "clean_response received a final result still containing "
                    "an unresolved tool-call payload — this indicates the run "
                    "ended on a malformed LLM response rather than a real "
                    "final answer: %s", text[:200],
                )
                return "The task ended before producing a final answer (a malformed model response was returned). Please try rephrasing or running again."
            return data.get("answer") or data.get("thought") or data.get("content") or text
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(f"Failed to parse response JSON: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error parsing response: {e}")
    # Fallback to basic cleaning if JSON parsing fails
    if text and text[0] in "⏳🔑🔐⚠️🔌⏱️🌐❌":
        return text
    text = re.sub(r'^(thought|answer)\s*:\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'```[\w]*\n?', '', text).strip()

    def strip_json_blocks(s: str) -> str:
        result, depth, in_string, escape = [], 0, False, False
        for ch in s:
            if escape:
                escape = False
                if depth > 0: continue
            elif ch == '\\' and in_string:
                escape = True
                if depth > 0: continue
            elif ch == '"' and not escape:
                in_string = not in_string
                if depth > 0: continue
            elif ch == '{' and not in_string:
                depth += 1
                continue
            elif ch == '}' and not in_string:
                depth = max(0, depth - 1)
                continue
            if depth == 0:
                result.append(ch)
        return ''.join(result).strip()

    text = strip_json_blocks(text)
    return text.strip() or "Done."


def build_help_text() -> str:
    return "\n".join([
        "[bold]Commands[/bold]",
        "• [cyan]help[/cyan]      Show this help",
        "• [cyan]clear[/cyan]     Clear the terminal",
        "• [cyan]plugins[/cyan]   List available plugin tools",
        "• [cyan]apikey[/cyan]    Manage API credentials",
        "• [cyan]doctor[/cyan]    Show startup health diagnostics",
        "• [cyan]update[/cyan]    Check for updates",
        "• [cyan]state[/cyan]     Show current execution state",
        "• [cyan]plan[/cyan]      Show the current execution plan",
        "• [cyan]memory[/cyan]    Show saved facts and memory state",
        "• [cyan]checkpoints[/cyan] Show saved checkpoints",
        "• [cyan]activity[/cyan]  Show the execution activity drawer",
        "• [cyan]resume[/cyan]    Resume last interrupted task",
        "• [cyan]profile[/cyan]   Show or switch the current CLI profile",
        "• [cyan]benchmark[/cyan]  Show a lightweight quality summary",
        "• [cyan]dashboard[/cyan] Show a runtime overview of memory, plugins, and health",
        "• [cyan]exit[/cyan]      Leave Penzer",
    ])


def build_session_summary(*, history_entries: int, memory_entries: int, checkpoint_count: int, profile_name: str, last_goal: str | None = None) -> str:
    """Build a compact CLI summary for the current session state."""
    lines = [
        f"Profile: {profile_name}",
        f"History entries: {history_entries}",
        f"Stored facts: {memory_entries}",
        f"Checkpoints: {checkpoint_count}",
    ]
    if last_goal:
        lines.append(f"Last goal: {last_goal}")
    return "\n".join(lines)


def build_dashboard_text(*, profile_name: str, history_entries: int, memory_entries: int, checkpoint_count: int, plugin_count: int, health_status: str, last_goal: str | None = None) -> str:
    """Build a richer CLI dashboard overview for the current runtime state."""
    lines = [
        "[bold]Dashboard[/bold]",
        f"Profile: {profile_name}",
        f"Health: {health_status}",
        f"History entries: {history_entries}",
        f"Stored facts: {memory_entries}",
        f"Checkpoints: {checkpoint_count}",
        f"Plugins: {plugin_count}",
    ]
    if last_goal:
        lines.append(f"Last goal: {last_goal}")
    return "\n".join(lines)


PENZER_LOGO = r"""
 ____   _____   _   _   _____   _____   ____
|  _ \ | ____| | \ | | |__  / | ____| |  _ \
| |_) ||  _|   |  \| |   / /  |  _|   | |_) |
|  __/ | |___  | |\  |  / /_  | |___  |  _ <
|_|    |_____| |_| \_| /____| |_____| |_| \_\
""".strip("\n")


def display_banner():
    console.print(f"[bold red]{PENZER_LOGO}")
    console.print(Panel(
        "[bold white]Autonomous Terminal Agent[/bold white]\n"
        "[cyan]Autonomy comes up with constraints[/cyan]\n"
        f"[dim]v{get_version()} · type 'help' to get started[/dim]",
        border_style="red",
        padding=(0, 1),
    ))


def display_help():
    console.print(Panel(build_help_text(), title="Help", border_style="cyan", padding=(0, 1)))


def maybe_notify_update() -> None:
    try:
        result = check_for_update()
        if result.get("update_available"):
            console.print()
            console.print("[yellow]Update available:[/yellow] " + result.get("message", ""))
            console.print("[dim]Run update to install the latest version.[/dim]")
            console.print()
    except Exception:
        pass


PROJECT_ROOT = Path(__file__).resolve().parent


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _read_env() -> dict[str, str]:
    env = {}
    path = _env_path()
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _write_env(updates: dict[str, str]) -> None:
    path = _env_path()
    existing_lines = []
    existing_keys = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#"):
                existing_lines.append(line)
                continue
            if "=" not in line:
                existing_lines.append(line)
                continue
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in updates:
                existing_lines.append(f'{key}="{updates[key]}"')
                existing_keys.add(key)
            else:
                existing_lines.append(line)
    for key, value in updates.items():
        if key not in existing_keys:
            existing_lines.append(f'{key}="{value}"')
    path.write_text("\n".join(existing_lines).strip() + "\n", encoding="utf-8")


def _mask_key(key: str) -> str:
    if not key:
        return "(none)"
    return key if len(key) <= 8 else f"{key[:4]}...{key[-4:]}"


def _has_llm_config() -> bool:
    env = _read_env()
    local_server = (env.get("LOCAL_SERVER_URL") or env.get("LLM_LOCAL_SERVER_URL") or "").strip()
    api_key = (env.get("LLM_API_KEY") or env.get("API_KEY") or "").strip()
    api_url = (env.get("LLM_API_URL") or env.get("URL") or "").strip()
    return bool(local_server or (api_key and api_url))


def prompt_for_llm_credentials() -> dict[str, str]:
    while True:
        console.print("\n[bold yellow]LLM configuration required.[/bold yellow]")
        console.print("Choose how you want to configure Penzer:")
        console.print("  [cyan]1[/cyan] Use a local server URL")
        console.print("  [cyan]2[/cyan] Enter API key and API URL")
        console.print("  [cyan]3[/cyan] Exit")

        try:
            choice = console.input("[bold cyan]Select option [1-3]: [/bold cyan]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("LLM configuration is required to start Penzer.")

        if choice in {"1", "local", "server"}:
            while True:
                try:
                    local_url = console.input("Enter LOCAL_SERVER_URL: ").strip()
                except (EOFError, KeyboardInterrupt):
                    raise SystemExit("LLM configuration is required to start Penzer.")
                if local_url:
                    _write_env({"LOCAL_SERVER_URL": local_url, "LLM_LOCAL_SERVER_URL": local_url})
                    console.print(f"[green]Saved LOCAL_SERVER_URL={local_url}[/green]")
                    return {"LOCAL_SERVER_URL": local_url}
                console.print("[red]LOCAL_SERVER_URL cannot be empty.[/red]")

        if choice in {"2", "api", "apikey"}:
            while True:
                try:
                    api_key = console.input("Enter LLM_API_KEY: ").strip()
                    api_url = console.input("Enter LLM_API_URL (for example https://api.openai.com/v1): ").strip()
                except (EOFError, KeyboardInterrupt):
                    raise SystemExit("LLM configuration is required to start Penzer.")
                if api_key and api_url:
                    _write_env({
                        "LLM_API_KEY": api_key,
                        "API_KEY": api_key,
                        "LLM_API_URL": api_url,
                        "URL": api_url,
                    })
                    console.print("[green]Saved API credentials to .env[/green]")
                    return {"LLM_API_KEY": api_key, "LLM_API_URL": api_url}
                console.print("[red]Both LLM_API_KEY and LLM_API_URL are required.[/red]")

        if choice in {"3", "exit", "quit", "q"}:
            raise SystemExit("LLM configuration is required to start Penzer.")

        console.print("[red]Please choose 1, 2, or 3.[/red]")


def _handle_apikey_command(user_input: str) -> None:
    tokens = user_input.split()
    if len(tokens) == 1 or tokens[1] in ("help", "show"):
        env = _read_env()
        local_url = env.get("LOCAL_SERVER_URL") or env.get("LLM_LOCAL_SERVER_URL", "(none)")
        api_key = env.get("LLM_API_KEY") or env.get("API_KEY", "")
        api_url = env.get("LLM_API_URL") or env.get("URL", "(none)")
        console.print("[white]API credentials in .env:[/white]")
        console.print(f"  LOCAL_SERVER_URL={local_url}")
        console.print(f"  LLM_API_KEY={_mask_key(api_key)}")
        console.print(f"  LLM_API_URL={api_url}")
        return
    if len(tokens) >= 3 and tokens[1] == "local":
        local_url = tokens[2]
        _write_env({"LOCAL_SERVER_URL": local_url, "LLM_LOCAL_SERVER_URL": local_url})
        console.print(f"[green]LOCAL_SERVER_URL set to {local_url}[/green]")
        return
    if len(tokens) >= 4 and tokens[1] in ("set", "update"):
        api_key = tokens[2]
        api_url = tokens[3]
        _write_env({
            "LLM_API_KEY": api_key,
            "API_KEY": api_key,
            "LLM_API_URL": api_url,
            "URL": api_url,
        })
        console.print("[green]LLM_API_KEY and LLM_API_URL updated in .env[/green]")
        return
    console.print("[yellow]Usage:[/yellow]")
    console.print("  apikey show")
    console.print("  apikey set <LLM_API_KEY> <LLM_API_URL>")
    console.print("  apikey local <LOCAL_SERVER_URL>")
    console.print("[dim]Example: apikey set mykey https://api.openai.com/v1[/dim]")


class ShutdownHandler:
    """Handles graceful shutdown of CLI components."""

    def __init__(self):
        self._should_exit = threading.Event()
        self._cleanup_handlers: list[Callable] = []

    def register_cleanup(self, handler: Callable):
        """Register a cleanup function to be called on shutdown."""
        self._cleanup_handlers.append(handler)

    def shutdown(self, signum=None, frame=None):
        """Initiate graceful shutdown."""
        if self._should_exit.is_set():
            return
        logger.info("Initiating graceful shutdown...")
        self._should_exit.set()
        # Run cleanup handlers in reverse order
        for handler in reversed(self._cleanup_handlers):
            try:
                handler()
            except Exception as e:
                logger.error(f"Cleanup handler failed: {e}")

    def should_exit(self) -> bool:
        """Check if shutdown was requested."""
        return self._should_exit.is_set()


def safe_subprocess_run(*args, **kwargs) -> subprocess.CompletedProcess:
    """Wrapper for subprocess.run with enhanced error handling."""
    try:
        return subprocess.run(*args, **kwargs, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e.cmd}")
        logger.debug(f"Error output: {e.stderr}")
        raise
    except Exception as e:
        logger.error(f"Unexpected subprocess error: {e}")
        raise


def run_doctor() -> dict:
    """Return a structured health report for startup-sensitive subsystems."""
    from pathlib import Path

    config_errors = validate_config()
    memory_ok = True
    memory_message = "memory store available"
    try:
        import session.memory_storage as storage
        storage._load()
    except Exception as exc:
        memory_ok = False
        memory_message = f"memory store unavailable: {exc}"

    plugin_ok = True
    plugin_message = "plugin directory readable"
    try:
        from tools.plugins import discover_plugins
        list(discover_plugins())
    except Exception as exc:
        plugin_ok = False
        plugin_message = f"plugin discovery failed: {exc}"

    skills_ok = True
    skills_message = "skills parse cleanly"
    try:
        from agent.skills.loader import load_all_skills
        load_all_skills()
    except Exception as exc:
        skills_ok = False
        skills_message = f"skill loading failed: {exc}"

    mcp_ok = True
    mcp_message = "MCP registry reachable"
    try:
        from agent.core import get_mcp_status
        get_mcp_status()
    except Exception as exc:
        mcp_ok = False
        mcp_message = f"MCP status unavailable: {exc}"

    checks = {
        "config": {
            "ok": not config_errors,
            "message": "; ".join(config_errors) if config_errors else "config validated",
        },
        "memory": {"ok": memory_ok, "message": memory_message},
        "plugins": {"ok": plugin_ok, "message": plugin_message},
        "skills": {"ok": skills_ok, "message": skills_message},
        "mcp": {"ok": mcp_ok, "message": mcp_message},
    }
    ok = all(check["ok"] for check in checks.values())
    return {
        "ok": ok,
        "checks": checks,
    }


# ─────────────────────────────────────────
# INTERRUPT HANDLING
# ─────────────────────────────────────────
# A real Ctrl+C (SIGINT) needs to reach two different places depending on
# what Penzer is doing at that instant:
#
#   1. A tool call is running (e.g. agent.run() -> terminal -> nmap via
#      asyncio.to_thread): the blocking subprocess is running in a worker
#      thread that asyncio cannot cancel on its own, so the signal has to
#      reach tools/executor.py's process registry directly and kill it,
#      then cancel the asyncio task waiting on it.
#
#   2. Penzer is idle at the "▸ " prompt: console.input() is a plain
#      blocking call, already handled by the existing
#      `except (EOFError, KeyboardInterrupt)` below — but only if a
#      KeyboardInterrupt actually gets raised. Installing a custom
#      signal.signal(SIGINT, ...) handler replaces Python's default
#      handler, and per PEP 475 an interrupted blocking call is
#      automatically retried unless the handler itself raises — so this
#      handler must explicitly re-raise KeyboardInterrupt for the idle
#      case, or Ctrl+C at the prompt would silently do nothing.
#
# One more wrinkle: when case 2's raise happens while asyncio's own
# scheduler is between coroutines (not inside any particular await), the
# exception can't be caught by any try/except written inside main() —
# only by wrapping the asyncio.run(main()) call itself, in
# main_entrypoint() below. Both catches are needed; neither alone is
# sufficient.
_current_task: "asyncio.Task | None" = None


def _sigint_handler(signum, frame):
    killed = kill_all_running()
    if killed:
        logger.info("SIGINT: killed %d running process(es)", killed)
    if _current_task is not None and not _current_task.done():
        _current_task.cancel()
    else:
        raise KeyboardInterrupt()


signal.signal(signal.SIGINT, _sigint_handler)


def _render_activity_event(terminal_ui: InteractiveTerminal, event: dict[str, Any]) -> None:
    """Render low-frequency lifecycle events while a task is executing."""
    rendered = terminal_ui.format_event(event)
    if not rendered:
        return
    terminal_ui.set_status(terminal_ui.event_status(event))
    try:
        sys.stdout.write("\r\033[2K\r")
        sys.stdout.write(rendered)
        sys.stdout.flush()
    except Exception:
        logger.debug("Unable to render activity event", exc_info=True)


async def run_noninteractive(task: str, json_mode: bool = False) -> int:
    """Run one task without the prompt UI for scripts and CI."""
    if not _has_llm_config():
        message = "LLM configuration is missing. Configure .env before using non-interactive mode."
        if json_mode:
            print(json.dumps({"event": "error", "message": message}))
        else:
            console.print(f"[red]{message}[/red]")
        return 2

    timeline = ActivityTimeline()
    set_activity_timeline(timeline)

    def emit(event: dict) -> None:
        if json_mode:
            print(json.dumps(event, ensure_ascii=True), flush=True)
        else:
            title = event.get("title") or event.get("event_type", "activity")
            status = event.get("status", "running")
            print(f"[{status}] {title}", flush=True)

    timeline.set_stream_handler(emit)
    logging.getLogger("penzer.server").setLevel(logging.CRITICAL)
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    await asyncio.sleep(0.5)
    # LLM initialization has a legacy informational print; keep JSON stdout pure.
    with contextlib.redirect_stdout(io.StringIO()) if json_mode else contextlib.nullcontext():
        agent = await PenzerAgent().async_init()
    try:
        response = await agent.run(task)
    except asyncio.CancelledError:
        message = "Task cancelled."
        if json_mode:
            print(json.dumps({"event": "cancelled", "message": message}))
        else:
            print(message)
        return 130
    cleaned = clean_response(response or "No response.")
    failed = bool(getattr(agent, "_failed", False))
    result = {"event": "final_result", "result": cleaned, "status": "failed" if failed else "success"}
    if json_mode:
        print(json.dumps(result, ensure_ascii=True), flush=True)
    else:
        print(cleaned)
    return 1 if failed else 0


async def main(task: str | None = None, json_mode: bool = False):
    global _current_task
    try:
        if task:
            return await run_noninteractive(task, json_mode=json_mode)
        terminal_ui = InteractiveTerminal(cwd=PROJECT_ROOT)
        display_banner()
        if not _has_llm_config():
            prompt_for_llm_credentials()
        logging.getLogger("penzer.server").setLevel(logging.CRITICAL)
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        await asyncio.sleep(0.5)
        console.print("[red bold]Loading agent...[/red bold]")
        agent = await PenzerAgent().async_init()
        console.print("[bold green]✓ Ready[/bold green]")
        console.print("[dim]Type a task or run [cyan]help[/cyan] for commands.[/dim]\n")
        maybe_notify_update()
        while True:
            try:
                user_input = (await terminal_ui.get_input()).strip()
            except (EOFError, KeyboardInterrupt):
                agent.clear_session()
                console.print("\n[dim]Session cleared. Memory retained.[/dim]")
                break
            if not user_input:
                continue
            user_input = normalize_command(user_input)
            if user_input.lower() in ("exit", "quit"):
                agent.clear_session()
                console.print("[dim]Session cleared. Memory retained.[/dim]")
                break
            if user_input.lower() == "help":
                display_help()
                continue
            if user_input.lower() == "clear":
                console.clear()
                continue
            if user_input.lower() == "plugins":
                from tools.plugins import list_plugin_metadata
                plugin_names = agent.list_plugin_tools()
                metadata = list_plugin_metadata()
                if plugin_names:
                    console.print("[white]Available plugin tools:[/white]")
                    for name in plugin_names:
                        console.print(f"  - {name}")
                else:
                    console.print("[dim]No plugin tools available yet.[/dim]")
                if metadata:
                    console.print()
                    console.print("[cyan]Plugin modules:[/cyan]")
                    for entry in metadata:
                        console.print(f"  - {entry['name']}: {', '.join(entry['functions']) or 'no functions'}")
                continue
            if user_input.lower().startswith("apikey"):
                _handle_apikey_command(user_input)
                continue
            if user_input.lower() == "doctor":
                report = run_doctor()
                status = "healthy" if report.get("ok") else "needs attention"
                panel_lines = [f"Status: {status}"]
                for name, entry in report.get("checks", {}).items():
                    panel_lines.append(f"- {name}: {'ok' if entry.get('ok') else 'issue'} — {entry.get('message', '')}")
                console.print(Panel("\n".join(panel_lines), title="Doctor", border_style="cyan"))
                continue
            if user_input.lower() == "update":
                try:
                    result = perform_update()
                    style = "green" if result.get("success") else "red"
                    console.print(f"[{style}]{result.get('message', 'Update finished')}[/{style}]")
                except Exception as exc:
                    console.print(f"[red]Update failed: {exc}[/red]")
                continue
            if user_input.lower() == "state":
                console.print(Panel(format_execution_state(), title="Execution State", border_style="yellow"))
                continue
            if user_input.lower() == "plan":
                plan = getattr(agent, "get_plan", lambda: [])()
                console.print(Panel(
                    InteractiveTerminal.format_plan(plan) if plan else "No plan created yet.",
                    title="Execution Plan", border_style="cyan",
                ))
                continue
            if user_input.lower() in ("activity", "drawer"):
                timeline = get_activity_timeline()
                if timeline and timeline.events:
                    console.print(Panel(timeline.render_drawer(), title="Execution Activity", border_style="cyan"))
                else:
                    console.print(Panel("No activity recorded yet.", title="Execution Activity", border_style="cyan"))
                continue
            if user_input.lower() == "memory":
                from session.memory import kv_list, load_history
                history = load_history()
                stored_facts = kv_list()
                summary = build_session_summary(
                    history_entries=len(history),
                    memory_entries=max(1, len([line for line in str(stored_facts).splitlines() if line.strip()])),
                    checkpoint_count=len(load_checkpoints()) if "load_checkpoints" in globals() else 0,
                    profile_name=get_profile_settings()["name"],
                    last_goal=(history[-1].get("goal") if history else None),
                )
                console.print(Panel(
                    "\n".join([
                        "[bold]Stored facts[/bold]",
                        stored_facts or "(none)",
                        "",
                        summary,
                    ]),
                    title="Memory",
                    border_style="magenta",
                ))
                continue
            if user_input.lower() == "checkpoints":
                from session.memory import load_checkpoints
                checkpoints = load_checkpoints()
                if checkpoints:
                    for idx, cp in enumerate(checkpoints, 1):
                        console.print(f"[cyan]{idx}.[/cyan] {cp.get('goal','')} — {cp.get('belief','')} @ {cp.get('timestamp','')}")
                else:
                    console.print("[dim]No checkpoints saved yet.[/dim]")
                continue
            if user_input.lower() == "resume":
                from session.memory import load_last_run
                snapshot = load_last_run()
                if not snapshot:
                    console.print("[dim]No interrupted task to resume.[/dim]")
                    continue
                console.print(Panel(
                    f"Last interrupted goal: [bold]{snapshot.get('goal','')}[/bold]\n"
                    f"Current step: [bold]{snapshot.get('resume_state', {}).get('current_step','')}[/bold]\n"
                    f"Blocked: [bold]{', '.join(snapshot.get('resume_state', {}).get('blocked_steps', [])) or 'none'}[/bold]",
                    title="Resume Preview",
                    border_style="green",
                ))
                try:
                    proceed = console.input("Resume this task? [y/N]: ").strip().lower() in {"y", "yes"}
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Resume cancelled.[/dim]")
                    continue
                if not proceed:
                    console.print("[dim]Resume cancelled.[/dim]")
                    continue
                try:
                    _current_task = asyncio.ensure_future(agent.resume_last_task())
                    response = await _current_task
                except asyncio.CancelledError:
                    console.show_cursor(True)
                    console.file.flush()
                    console.print("\n[yellow]Interrupted.[/yellow]")
                    agent.clear_session()
                    console.print("[dim]Session cleared. Memory retained.[/dim]")
                    return
                except Exception as e:
                    console.show_cursor(True)
                    console.file.flush()
                    console.print(f"\n[red]Error: {e}[/red]")
                    console.print("[dim]You can try another command.[/dim]")
                    continue
                finally:
                    _current_task = None
                console.print()
                console.print(Markdown(clean_response(response or "No response.")))
                continue
            if user_input.lower().startswith("profile"):
                parts = user_input.split()
                if len(parts) > 1:
                    profile = parts[1].lower()
                    if profile in PROFILE_OPTIONS:
                        os.environ["PENZER_PROFILE"] = profile
                        console.print(f"[green]Profile set to {profile}[/green]")
                    else:
                        console.print(f"[yellow]Unknown profile. Choose from: {', '.join(PROFILE_OPTIONS)}[/yellow]")
                else:
                    current_profile = get_profile_settings()["name"]
                    console.print(f"[cyan]Current profile:[/cyan] {current_profile}")
                    for name, description in PROFILE_OPTIONS.items():
                        console.print(f"  - {name}: {description}")
                continue
            if user_input.lower() == "dashboard":
                from session.memory import load_history, load_checkpoints
                from tools.plugins import list_plugin_metadata
                history = load_history()
                settings = get_profile_settings()
                doctor_report = run_doctor()
                plugin_count = len(list_plugin_metadata())
                dashboard = build_dashboard_text(
                    profile_name=settings["name"],
                    history_entries=len(history),
                    memory_entries=len(history),
                    checkpoint_count=len(load_checkpoints()),
                    plugin_count=plugin_count,
                    health_status="healthy" if doctor_report.get("ok") else "needs attention",
                    last_goal=(history[-1].get("goal") if history else None),
                )
                console.print(Panel(dashboard, title="Dashboard", border_style="magenta"))
                continue

            if user_input.lower() == "benchmark":
                from session.memory import load_history, load_checkpoints
                history = load_history()
                settings = get_profile_settings()
                summary = build_session_summary(
                    history_entries=len(history),
                    memory_entries=len(history),
                    checkpoint_count=len(load_checkpoints()),
                    profile_name=settings["name"],
                    last_goal=(history[-1].get("goal") if history else None),
                )
                console.print(Panel(
                    "\n".join([
                        summary,
                        f"Approval required: {settings['approval_required']}",
                        "Memory-backed context: enabled",
                    ]),
                    title="Benchmark Summary",
                    border_style="cyan",
                ))
                continue
            calls_before  = getattr(agent.llm, "call_count", 0)
            tokens_before = getattr(agent.llm, "token_estimate", 0)
            status_view = LiveStatusView()
            terminal_ui.set_status("RUNNING")
            status_view.timeline.set_stream_handler(
                lambda event: _render_activity_event(terminal_ui, event)
            )
            # FIX 1: console.status() hides the cursor and takes over
            # terminal rendering while active. On long-running tool calls
            # (e.g. an nmap network scan run via asyncio.to_thread), the
            # cursor/render state was not reliably restored once the
            # block exited — the next console.input() prompt still
            # accepted keystrokes underneath, but nothing visibly drew on
            # screen. The try/finally guarantees the cursor is shown and
            # the stream flushed regardless of how the block exits.
            #
            # FIX 2: `_current_task` (module-level, see _sigint_handler
            # above) must point at this specific task while it runs, so a
            # real Ctrl+C can find and cancel it — and must be cleared
            # from the `finally` so a Ctrl+C arriving after this turn
            # finishes (the gap before the next prompt) takes the idle
            # path instead of trying to cancel a task that's already done.
            try:
                # Real agent CLIs keep the user informed with one short status line
                # that updates in place. No spinner, no noisy timeline, no internal
                # logger spam.
                last_rendered = ""

                def _on_status(msg: str) -> None:
                    nonlocal last_rendered
                    if not msg:
                        return
                    status_view.update(msg)
                    rendered = status_view.status_line()
                    if rendered == last_rendered:
                        return
                    last_rendered = rendered
                    try:
                        sys.stdout.write("\r\033[2K")
                        sys.stdout.write(rendered)
                        sys.stdout.write("\r")
                        sys.stdout.flush()
                    except Exception:
                        pass

                agent.on_status = _on_status
                noisy_streams: set[str] = set()

                def _on_output(label: str, line: str) -> None:
                    text = line.strip()
                    if not text:
                        return
                    looks_like_html = label == "stdout" and bool(
                        "<" in text
                        or ">" in text
                        or "&quot" in text.lower()
                        or "ifconfig.me" in text.lower()
                        or "need a robust api" in text.lower()
                        or "open sans" in text.lower()
                    )
                    if looks_like_html:
                        if label not in noisy_streams:
                            noisy_streams.add(label)
                            _on_status("Terminal stdout: receiving response…")
                        return
                    _on_status(f"Terminal {label}: {text[:100]}")

                set_live_hooks(None, None, _on_output)
                _current_task = asyncio.ensure_future(agent.run(user_input))
                response = await _current_task
            except asyncio.CancelledError:
                # Reached when _sigint_handler cancelled _current_task
                # above. The handler already called kill_all_running()
                # before cancelling, so the subprocess is already dead —
                # this branch is just cleanup and messaging.
                console.show_cursor(True)
                console.file.flush()
                console.print("\n[yellow]Interrupted.[/yellow]")
                agent.clear_session()
                console.print("[dim]Session cleared. Memory retained.[/dim]")
                return
            except Exception as e:
                # Without this, any error from agent.run() — a flaky LLM
                # call, a tool bug, a malformed response — was uncaught
                # here and fell through to the outer `except Exception`
                # around the whole while loop, which ends main() entirely.
                # One bad command would silently end the whole session
                # instead of just failing that one turn.
                console.show_cursor(True)
                console.file.flush()
                console.print(f"\n[red]Error: {e}[/red]")
                console.print("[dim]You can try another command.[/dim]")
                continue
            finally:
                _current_task = None
                set_live_hooks(None, None, None)
                terminal_ui.set_status("IDLE")
                # Clear the progress line before printing the final answer.
                try:
                    sys.stdout.write("\r\033[2K\r")
                    sys.stdout.flush()
                except Exception:
                    pass
                console.show_cursor(True)
                console.file.flush()
            calls_used  = getattr(agent.llm, "call_count", 0) - calls_before
            tokens_used = getattr(agent.llm, "token_estimate", 0) - tokens_before
            response = clean_response(response or "No response.")
            if response and response.strip():
                # Clears any transient working indicator before printing the final output.
                try:
                    sys.stdout.write("\r\033[2K\r")
                    sys.stdout.flush()
                except Exception:
                    pass
                console.print()
                console.print(Markdown(response))
            matched = getattr(agent, "_matched_skills", [])
            trace = getattr(agent, "_trace", [])
            summary_lines = compose_summary_lines(
                matched=matched,
                trace=trace,
                calls_used=calls_used,
                tokens_used=tokens_used,
            )
            if summary_lines:
                console.print()
                for line in summary_lines:
                    console.print(f"[dim]{line}[/dim]")
            console.print()
    except Exception as e:
        console.print(f"\n[red bold]ERROR: {str(e)}[/red bold]")
        import traceback
        traceback.print_exc()


def main_entrypoint():
    parser = argparse.ArgumentParser(description="PENZER autonomous terminal agent")
    parser.add_argument("--json", action="store_true", dest="json_mode", help="emit structured JSON events")
    parser.add_argument("task", nargs="?", help="run one task without the interactive prompt")
    args = parser.parse_args()
    try:
        exit_code = asyncio.run(main(task=args.task, json_mode=args.json_mode))
        if exit_code is not None:
            return exit_code
    except KeyboardInterrupt:
        # Reached when Ctrl+C fires while idle (no task running for
        # _sigint_handler to cancel, so it re-raised KeyboardInterrupt
        # directly). That raise happens inside asyncio's own scheduler,
        # not inside any coroutine's stack, so no try/except inside
        # main() can ever catch it — only wrapping asyncio.run() itself,
        # here, can. Without this, Penzer would exit via an unhandled
        # traceback instead of a clean message.
        console.print("\n[dim]Interrupted. Goodbye.[/dim]")


if __name__ == "__main__":
    main_entrypoint()