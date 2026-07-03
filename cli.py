#!/usr/bin/env python3
"""PENZER-CLI: Autonomous Terminal Agent"""
import threading
import asyncio
import sys
import re
import json
import logging
import subprocess
import os
from pathlib import Path
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from logger import get_logger
from version import get_version, check_for_update, perform_update
from tools.executor import format_execution_state
from config import DEFAULT_PROFILE, PROFILE_OPTIONS, get_profile_settings

logger = get_logger("cli")

for _log in [
    "agent.agent", "penzer.core", "penzer.server",
    "agent.skills.search", "agent.skills.loader",
    "agent.skills.base", "session.memory", "httpx",
]:
    logging.getLogger(_log).setLevel(logging.WARNING)

from agent.agent import PenzerAgent
from agent.server import start_server

console = Console(force_terminal=True, width=100)


def compose_summary_lines(matched: list[str] | None = None, trace: list | None = None, calls_used: int = 0, tokens_used: int = 0) -> list[str]:
    lines = []
    if matched:
        lines.append("Skills: " + " · ".join(matched))
    if trace:
        tools_used = " · ".join(dict.fromkeys(trace))
        lines.append("Tools: " + tools_used)
    if calls_used or tokens_used:
        lines.append(f"LLM: {calls_used} calls · ~{tokens_used} tokens")
    return lines


class LiveStatusView:
    def __init__(self) -> None:
        self.current = "Starting…"
        self.events: list[str] = []

    def update(self, message: str) -> None:
        if not message:
            return
        message = str(message).strip()
        if message == self.current and self.events and self.events[-1] == message:
            return
        self.current = message
        self.events.append(message)
        self.events = self.events[-6:]

    def render(self) -> str:
        lines = [f"● {self.current}"]
        recent = [event for event in self.events[-3:] if event != self.current]
        if recent:
            lines.append("  ↳ " + " → ".join(recent))
        state = format_execution_state()
        if state and state != "No execution state yet.":
            first_state = state.splitlines()[0]
            lines.append(f"  ↳ {first_state}")
        return "\n".join(lines)


def clean_response(text: str) -> str:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if data.get("tool_calls") or data.get("tool"):
                return "Executing task..."
            return data.get("answer") or data.get("thought") or data.get("content") or text
    except (json.JSONDecodeError, ValueError):
        pass

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
        "[cyan]You're talking to something that plans[/cyan]\n"
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


async def main():
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
                if console.input("Resume this task? [y/N]: ").strip().lower() not in {"y", "yes"}:
                    console.print("[dim]Resume cancelled.[/dim]")
                    continue
                response = await agent.resume_last_task()
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
            with console.status("[cyan]Working…[/cyan]", spinner="dots") as status:
                def _on_status(msg: str) -> None:
                    status_view.update(msg)
                    status.update(f"[cyan]{status_view.current}[/cyan]")

                agent.on_status = _on_status
                response = await agent.run(user_input)

            from tools.executor import format_execution_state
            state_summary = format_execution_state()
            if state_summary and state_summary != "No execution state yet.":
                console.print(f"[dim]{state_summary}[/dim]")

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


def main_entrypoint() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Fatal: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main_entrypoint()