#!/usr/bin/env python3
"""
PENZER-CLI: Autonomous Terminal Agent
"""
import threading
import asyncio
import sys
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt

from logger import get_logger
logger = get_logger("cli")

from agent.agent import PenzerAgent
from agent.server import start_server
from agent.core import cleanup_reme
from config import Colors

console = Console(force_terminal=True, width=100)


def display_banner():
    console.print("""
    [red bold]╔════════════════════════════════════════╗[/red bold]
    [red bold]║[/red bold]         [white bold]PENZER[/white bold] [red bold]terminal Agent[/red bold]              [red bold]║[/red bold]
    [red bold]║[/red bold]         [white]Autonomous assistant[/white]           [red bold]║[/red bold]
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

            # Live status that updates as agent works
            with console.status("", spinner="dots") as status:
                def on_status(msg: str):
                    status.update(f"[red bold]{msg}[/red bold]")

                agent.on_status = on_status
                response = await agent.run(user_input)

            console.print()
            console.print("[white bold]" + "─" * 100 + "[/white bold]")
            console.print(f"[white]{response or 'No response'}[/white]")
            console.print("[white bold]" + "─" * 100 + "[/white bold]")
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