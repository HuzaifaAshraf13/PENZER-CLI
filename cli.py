#!/usr/bin/env python3
"""
PENZER-CLI: Local Cognitive Security Shell
Modern terminal-based autonomous pentesting agent with rich UI
"""

import threading
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich.syntax import Syntax

# Initialize logging first
from logger import get_logger
logger = get_logger("cli")

from agent.agent import Agent
from agent.server import start_server
from agent.core import cleanup_reme
from config import Colors

# Rich console for beautiful terminal output
console = Console(force_terminal=True)


def display_banner():
    """Display stylish banner with ASCII art and info."""
    banner = r"""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║          [red]╔═╗╔═╗╔╗╔╔═╗╔═╗╦═╗[/red]                          ║
║          [red]╠═╝║╣ ║║║╔═╝║╣ ╠╦╝[/red]                          ║
║          [red]╩  ╚═╝╝╚╝╚═╝╚═╝╩╚═[/red]                          ║
║                                                           ║
║              Autonomous Pentesting Agent                 ║
║         Local Cognitive Security Shell v1.0              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """
    console.print(banner, justify="center")


async def boot_sequence():
    """Animated boot sequence with spinners and status updates."""
    console.print("\n", justify="center")
    console.print(Panel(
        "[bold cyan]PENZER INITIALIZATION SEQUENCE[/bold cyan]",
        border_style="cyan",
        expand=False
    ), justify="center")
    
    boot_steps = [
        ("Initializing quantum buffer", "cyan"),
        ("Weaving neural filaments", "blue"),
        ("Scanning entropy matrices", "yellow"),
        ("Calibrating reasoning engines", "red"),
        ("Establishing secure handshake", "green"),
        ("Ready for operations", "magenta"),
    ]
    
    for step, color in boot_steps:
        with console.status(f"[{color}]{step}[/{color}]", spinner="dots"):
            await asyncio.sleep(0.2)
        console.print(f"[{color}]✓[/{color}] {step}", justify="center")
    
    console.print("\n[bold green]System online. The grid hums with potential.[/bold green]\n", justify="center")


def display_help():
    """Display help information in a nice table."""
    table = Table(
        title="[bold cyan]AVAILABLE COMMANDS[/bold cyan]",
        show_header=True,
        header_style=HEADER_COLOR,
        border_style="cyan",
    )
    table.add_column("Command", style="cyan", width=20)
    table.add_column("Description", style="white")
    
    commands = [
        ("exit / quit", "Shutdown the agent gracefully"),
        ("help", "Display this help information"),
        ("clear", "Clear the terminal screen"),
        ("<any text>", "Send request to autonomous agent"),
    ]
    
    for cmd, desc in commands:
        table.add_row(cmd, desc)
    
    console.print(table)
    console.print()


async def main():
    """Main CLI loop with modern terminal UI."""
    try:
        # Display splash screen
        display_banner()
        await boot_sequence()
        
        # Start MCP server in background
        console.print("[bold cyan]Starting Model Context Protocol server...[/bold cyan]")
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        await asyncio.sleep(1)
        console.print("[green]✓ MCP server started[/green]\n")
        
        # Initialize agent
        console.print("[bold cyan]Loading agent systems...[/bold cyan]")
        with console.status("[cyan]Initializing agent...[/cyan]", spinner="dots"):
            agent = await Agent().async_init()
        console.print("[green]✓ Agent ready[/green]\n")
        
        console.print(
            Panel(
                "[bold]Ready to execute pentesting operations[/bold]\n"
                "Type 'help' for commands or speak your intent.",
                border_style="green",
                style="green"
            )
        )
        
        # Main interaction loop
        while True:
            try:
                user_input = console.input("[bold cyan]user[/bold cyan]> ").strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                console.print("\n[yellow]⚠ Interrupted by user[/yellow]")
                continue
            
            # Handle special commands
            if not user_input:
                continue
            
            if user_input.lower() in ["exit", "quit"]:
                console.print("\n[bold yellow]Shutting down...[/bold yellow]")
                break
            
            if user_input.lower() == "help":
                display_help()
                continue
            
            if user_input.lower() == "clear":
                console.clear()
                continue
            
            # Process through agent with spinner
            console.print()
            with console.status(
                "[bold cyan]Processing request through ReAct framework...[/bold cyan]",
                spinner="dots",
                spinner_style="cyan"
            ):
                response = await agent.execute_user_request(user_input)
            
            # Display result
            console.print()
            if response.get("status") == "error":
                console.print(Panel(
                    f"[red]{response.get('response')}[/red]",
                    title="[red][bold]ERROR[/bold][/red]",
                    border_style="red",
                    expand=False
                ))
            else:
                console.print(Panel(
                    f"[green]{response.get('response', 'Success')}[/green]",
                    title="[green][bold]RESULT[/bold][/green]",
                    border_style="green",
                    expand=False
                ))
            console.print()
    
    except Exception as e:
        console.print(Panel(
            f"[red]Fatal error: {str(e)}[/red]",
            title="[red][bold]SYSTEM ERROR[/bold][/red]",
            border_style="red"
        ))
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        console.print("\n[bold yellow]Cleaning up resources...[/bold yellow]")
        await cleanup_reme()
        console.print("[green]✓ Shutdown complete[/green]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Fatal: {e}[/red]")
        sys.exit(1)
