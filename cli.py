#!/usr/bin/env python3
"""PENZER-CLI: Autonomous Terminal Agent"""
import threading
import asyncio
import re
import json
import logging
import subprocess
import os
import signal
from pathlib import Path
from typing import Callable
from rich.markdown import Markdown
from rich.panel import Panel
from logger import get_logger, console as console
from version import get_version, check_for_update, perform_update
from tools.executor import format_execution_state, kill_all_running, set_live_hooks
from config import PROFILE_OPTIONS, get_profile_settings
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
    """Generate formatted status lines with rich formatting."""
    lines = []
    if matched:
        skill_text = "[bold cyan]Skills:[/] " + "[dim]·[/] ".join(f"[green]{s}[/]" for s in matched)
        lines.append(skill_text)
    if trace:
        tools_text = "[bold cyan]Tools:[/] " + "[dim]·[/] ".join(f"[yellow]{t}[/]" for t in dict.fromkeys(trace))
        lines.append(tools_text)
    if calls_used or tokens_used:
        llm_text = f"[bold cyan]LLM:[/] [magenta]{calls_used}[/] calls [dim]·[/] ~[magenta]{tokens_used}[/] tokens"
        lines.append(llm_text)
    return lines
class LiveStatusView:
    def __init__(self) -> None:
        self.current = "[dim]Starting…[/]"
        self.events: list[str] = []
        self._lock = threading.Lock()
        self.current_skill = ""

    def update_skill(self, skill_name: str) -> None:
        with self._lock:
            self.current_skill = f"[bold green]Using:[/] [cyan]{skill_name}[/]"

    def update(self, message: str) -> None:
        if not message:
            return
        message = str(message).strip()
        with self._lock:
            if message == self.current and self.events and self.events[-1] == message:
                return
            self.current = message
            self.events.append(message)
            self.events = self.events[-6:]
    def render(self) -> str:
        with self._lock:
            lines = []
            if self.current_skill:
                lines.append(self.current_skill)
            lines.append(f"[bold]●[/] {self.current}")
            recent = [event for event in self.events[-3:] if event != self.current]
            if recent:
                lines.append("  [dim]↳[/] " + " [dim]→[/] ".join(recent))
            state = format_execution_state()
            if state and state != "No execution state yet.":
                first_state = state.splitlines()[0]
                lines.append(f"  [dim]↳[/] {first_state}")
            return "\n".join(lines)
def clean_response(text: str) -> str:
    """Clean and parse CLI response text with robust error handling."""
    if not text:
        return ""

    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if data.get("tool_calls") or data.get("tool"):
                return "Executing task..."
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
        "• [cyan]update[/cyan]    Check for updates",
        "• [cyan]state[/cyan]     Show current execution state",
        "• [cyan]memory[/cyan]    Show saved facts and memory state",
        "• [cyan]checkpoints[/cyan] Show saved checkpoints",
        "• [cyan]resume[/cyan]    Resume last interrupted task",
        "• [cyan]profile[/cyan]   Show or switch the current CLI profile",
        "• [cyan]benchmark[/cyan]  Show a lightweight quality summary",
        "• [cyan]exit[/cyan]      Leave Penzer",
    ])
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
def _env_path() -> Path:
    return Path(".env")
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
def _handle_apikey_command(user_input: str) -> None:
    tokens = user_input.split()
    if len(tokens) == 1 or tokens[1] in ("help", "show"):
        env = _read_env()
        local_url = env.get("LOCAL_SERVER_URL", "(none)")
        api_key = _mask_key(env.get("API_KEY", ""))
        api_url = env.get("URL", "(none)")
        console.print("[white]API credentials in .env:[/white]")
        console.print(f"  LOCAL_SERVER_URL={local_url}")
        console.print(f"  API_KEY={api_key}")
        console.print(f"  URL={api_url}")
        return
    if len(tokens) >= 3 and tokens[1] == "local":
        local_url = tokens[2]
        _write_env({"LOCAL_SERVER_URL": local_url})
        console.print(f"[green]LOCAL_SERVER_URL set to {local_url}[/green]")
        return
    if len(tokens) >= 4 and tokens[1] in ("set", "update"):
        api_key = tokens[2]
        api_url = tokens[3]
        _write_env({"API_KEY": api_key, "URL": api_url})
        console.print("[green]API_KEY and URL updated in .env[/green]")
        return
    console.print("[yellow]Usage:[/yellow]")
    console.print("  apikey show")
    console.print("  apikey set <API_KEY> <URL>")
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


async def main():
    global _current_task
    try:
        display_banner()
        logging.getLogger("penzer.server").setLevel(logging.CRITICAL)
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        await asyncio.sleep(0.5)
        with console.status("[red bold]Loading agent...[/red bold]", spinner="dots"):
            agent = await PenzerAgent().async_init()
        console.print("[bold green]✓ Ready[/bold green]")
        console.print("[dim]Type a task or run [cyan]help[/cyan] for commands.[/dim]\n")
        maybe_notify_update()
        while True:
            try:
                user_input = console.input("[bold cyan]▸ [/bold cyan]").strip()
            except (EOFError, KeyboardInterrupt):
                agent.clear_session()
                console.print("\n[dim]Session cleared. Memory retained.[/dim]")
                break
            if not user_input:
                continue
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
            if user_input.lower() == "update":
                try:
                    result = perform_update()
                    console.print("[green]" + result.get("message", "Update complete") + "[/green]")
                except Exception as exc:
                    console.print(f"[red]Update failed: {exc}[/red]")
                continue
            if user_input.lower() == "state":
                console.print(Panel(format_execution_state(), title="Execution State", border_style="yellow"))
                continue
            if user_input.lower() == "memory":
                from session.memory import kv_list, load_history
                console.print(Panel(
                    "\n".join([
                        "[bold]Stored facts[/bold]",
                        kv_list(),
                        "",
                        f"[dim]Recent history entries: {len(load_history())}[/dim]",
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
            if user_input.lower() == "benchmark":
                from session.memory import load_history
                history = load_history()
                settings = get_profile_settings()
                summary = {
                    "history_entries": len(history),
                    "profile": settings["name"],
                    "memory_entries": len(history),
                    "approval_required": settings["approval_required"],
                }
                console.print(Panel(
                    f"History entries: {summary['history_entries']}\n"
                    f"Profile: {summary['profile']}\n"
                    f"Approval required: {summary['approval_required']}\n"
                    f"Memory-backed context: enabled",
                    title="Benchmark Summary",
                    border_style="cyan",
                ))
                continue
            console.print()
            calls_before  = getattr(agent.llm, "call_count", 0)
            tokens_before = getattr(agent.llm, "token_estimate", 0)
            status_view = LiveStatusView()
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
                with console.status("[cyan]Working…[/cyan]", spinner="dots") as status:
                    def _on_status(msg: str) -> None:
                        status_view.update(msg)
                        status.update(f"[cyan]{status_view.current}[/cyan]")
                    agent.on_status = _on_status
                    set_live_hooks(status.stop, status.start)
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
                set_live_hooks(None, None)
                console.show_cursor(True)
                console.file.flush()
            calls_used  = getattr(agent.llm, "call_count", 0) - calls_before
            tokens_used = getattr(agent.llm, "token_estimate", 0) - tokens_before
            response = clean_response(response or "No response.")
            if response and response.strip():
                console.print()
                console.print(Markdown(response))
            matched = getattr(agent, "_matched_skills", [])
            trace = getattr(agent, "_trace", [])
            summary_lines = compose_summary_lines(
                matched=matched,
                trace=[t["tool"] for t in trace],
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
    try:
        asyncio.run(main())
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