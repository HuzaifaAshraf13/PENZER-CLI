#!/usr/bin/env python3
"""
PENZER-CLI: Autonomous Terminal Agent
"""
import threading
import asyncio
import sys
import re
import json
import logging
from rich.console import Console
from rich.markdown import Markdown

from logger import get_logger
logger = get_logger("cli")

for _log in [
    "agent.agent", "penzer.core", "penzer.server",
    "agent.skills.search", "agent.skills.loader",
    "agent.skills.base", "httpx"
]:
    logging.getLogger(_log).setLevel(logging.WARNING)

from agent.agent import PenzerAgent
from agent.server import start_server

console = Console(force_terminal=True, width=100)


def clean_response(text: str) -> str:
    """Extract only the answer, never intermediate thoughts."""
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if data.get("tool_calls") or data.get("tool"):
                return "Executing task..."
            return data.get("answer") or data.get("thought") or data.get("content") or text
    except (json.JSONDecodeError, ValueError):
        pass

    # Error messages with emoji — return as-is
    if text and text[0] in "⏳🔑🔐⚠️🔌⏱️🌐❌":
        return text

    text = re.sub(r'^(thought|answer)\s*:\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'```[\w]*\n?', '', text).strip()

    def strip_json_blocks(s: str) -> str:
        result    = []
        depth     = 0
        in_string = False
        escape    = False
        for ch in s:
            if escape:
                escape = False
                if depth > 0:
                    continue
            elif ch == '\\' and in_string:
                escape = True
                if depth > 0:
                    continue
            elif ch == '"' and not escape:
                in_string = not in_string
                if depth > 0:
                    continue
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


def display_banner():
    console.print("""
    [red bold]╔════════════════════════════════════════╗[/red bold]
    [red bold]║[/red bold]         [white bold]PENZER[/white bold] [red bold]Terminal Agent[/red bold]              [red bold]║[/red bold]
    [red bold]║[/red bold]         [white]Autonomous Assistant[/white]           [red bold]║[/red bold]
    [red bold]╚════════════════════════════════════════╝[/red bold]
    """)


def display_help():
    console.print("""
[red bold]COMMANDS:[/red bold]
  [white]help[/white]  - Show this help
  [white]clear[/white] - Clear screen
  [white]exit[/white]  - Exit Penzer
    """)


async def main():
    try:
        display_banner()

        logging.getLogger("penzer.server").setLevel(logging.CRITICAL)
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        await asyncio.sleep(0.5)

        with console.status("[red bold]Loading agent...[/red bold]", spinner="dots"):
            agent = await PenzerAgent().async_init()

        console.print("[white on red]  READY  [/white on red]\n")

        while True:
            try:
                user_input = console.input("[red bold]>>> [/red bold]").strip()
            except (EOFError, KeyboardInterrupt):
                agent.clear_session()
                console.print("\n[dim]Session cleared. Memory retained.[/dim]")
                break

            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                agent.clear_session()
                console.print("[dim]Session cleared. Memory retained.[/dim]")
                break
            if user_input.lower() == "help":
                display_help()
                continue
            if user_input.lower() == "clear":
                console.clear()
                continue

            console.print()

            calls_before  = agent.llm.call_count
            tokens_before = agent.llm.token_estimate

            with console.status("", spinner="dots") as status:
                agent.on_status = lambda msg: status.update(f"[dim]{msg}[/dim]")
                response = await agent.run(user_input)

            calls_used  = agent.llm.call_count  - calls_before
            tokens_used = agent.llm.token_estimate - tokens_before

            response = clean_response(response or "No response.")
            console.print()
            console.print(Markdown(response))

            # Show matched skills
            if agent._last_matched_skills:
                skill_names = " · ".join(agent._last_matched_skills)
                console.print(f"[dim]  ⭐ {skill_names}[/dim]")

            # Show tools used
            if agent._trace:
                tools_used = " · ".join(dict.fromkeys(t["tool"] for t in agent._trace))
                console.print(f"[dim]  🔧 {tools_used}[/dim]")

            console.print(f"[dim]  {calls_used} LLM calls · ~{tokens_used} tokens[/dim]")
            console.print()

    except Exception as e:
        console.print(f"\n[red bold]ERROR: {str(e)}[/red bold]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Fatal: {e}[/red]")
        sys.exit(1)