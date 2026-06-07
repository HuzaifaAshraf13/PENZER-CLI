#!/usr/bin/env python3
"""
PENZER-CLI: Autonomous Terminal Agent
"""
import threading
import asyncio
import sys
import re
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

import logging
from logger import get_logger
logger = get_logger("cli")
for _log in ["agent.agent", "penzer.core", "penzer.server", "agent.skills.search"]:
    logging.getLogger(_log).setLevel(logging.WARNING)

from agent.agent import PenzerAgent
from agent.server import start_server
from agent.core import cleanup_reme

console = Console(force_terminal=True, width=100)


def clean_response(text: str) -> str:
    """Strip JSON artifacts and internal thought leakage."""
    # Remove "thought:" prefix
    text = re.sub(r'^thought\s*:\s*', '', text, flags=re.IGNORECASE).strip()
    # Remove JSON blobs
    text = re.sub(r'\{[^{}]{0,500}\}', '', text).strip()
    # Remove markdown code fences
    text = re.sub(r'```[\w]*\n?', '', text).strip()
    return text


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
                console.print()
                continue

            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                break
            if user_input.lower() == "help":
                display_help()
                continue
            if user_input.lower() == "clear":
                console.clear()
                continue

            console.print()

            with console.status("", spinner="dots") as status:
                def on_status(msg: str):
                    status.update(f"[dim]{msg}[/dim]")

                agent.on_status = on_status
                response = await agent.run(user_input)

            # Clean and render response
            response = clean_response(response or "No response")
            console.print()
            console.print(Markdown(response))
            calls = agent.llm.call_count
            tokens = agent.llm.token_estimate
            console.print(f"[dim]  {calls} LLM calls · ~{tokens} tokens[/dim]")
            console.print()

    except Exception as e:
        console.print(f"\n[red bold]ERROR: {str(e)}[/red bold]")
        import traceback
        traceback.print_exc()
    finally:
        await cleanup_reme()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Fatal: {e}[/red]")
        sys.exit(1)