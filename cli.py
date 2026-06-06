#!/usr/bin/env python3
"""
PENZER-CLI: Local Cognitive Security Shell
Minimalist terminal-based autonomous pentesting agent - Red & White theme
"""

import threading
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt

# Initialize logging first
from logger import get_logger
logger = get_logger("cli")

from agent.agent import PenzerAgent
from agent.server import start_server
from agent.core import cleanup_reme
from config import Colors

# Rich console - clean, minimal style
console = Console(force_terminal=True, width=100)


def display_banner():
    """Display clean red and white banner."""
    banner = """
    [red bold]╔════════════════════════════════════════╗[/red bold]
    [red bold]║[/red bold]         [white bold]PENZER[/white bold] [red bold]terminal Agent[/red bold]              [red bold]║[/red bold]
    [red bold]║[/red bold]         [white]Autonomous assitant [/white]           [red bold]║[/red bold]
    [red bold]╚════════════════════════════════════════╝[/red bold]
    """
    console.print(banner)


async def boot_sequence():
    """Quick initialization."""
    console.print()
    with console.status("[red bold]Initializing...[/red bold]", spinner="dots"):
        await asyncio.sleep(0.5)


def display_help():
    """Display available commands."""
    help_text = """
[red bold]COMMANDS:[/red bold]
  [white]help[/white]  - Show this help
  [white]clear[/white] - Clear screen
  [white]quit[/white]  - Exit PENZER
  [white]text[/white]  - Send to agent
    """
    console.print(help_text)


async def main():
    """Main CLI loop with clean red and white theme."""
    try:
        # Display splash screen
        display_banner()
        await boot_sequence()
        
        # Start MCP server in background
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        await asyncio.sleep(0.5)
        
        # Initialize agent
        with console.status("[red bold]Loading agent...[/red bold]", spinner="dots"):
            agent = await PenzerAgent().async_init()
        
        console.print("[white on red]  READY  [/white on red]\n")
        
        # Main interaction loop
        while True:
            try:
                user_input = console.input("[red bold]>>>[/red bold] [white]").strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                console.print()
                continue
            
            # Handle special commands
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
            
            # Process through agent
            console.print()
            with console.status("[red bold]Processing...[/red bold]", spinner="dots"):
                response = await agent.run(user_input)
            
            console.print()
            
            if not response:
                console.print("[yellow]No response[/yellow]")
                console.print()
                continue
            
            # Clean output - just display the response
            console.print("[white bold]" + "─" * 100 + "[/white bold]")
            console.print(f"[white]{response}[/white]")
            console.print("[white bold]" + "─" * 100 + "[/white bold]")
            console.print()
    
    except Exception as e:
        console.print(f"\n[red bold]ERROR: {str(e)}[/red bold]")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
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
