# cli.py
import threading
import time
import sys
from agent.agent import Agent
from agent.server import start_server

# --- Utility: typing effect ---
def type_effect(text, delay=0.02, color="", newline=True):
    if color:
        text = f"{color}{text}\033[0m"
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    if newline:
        print()

# --- Display logo ---
def display_logo():
    logo = r"""
\033[91m
╔═╗╔═╗╔╗╔╔═╗╔═╗╦═╗
╠═╝║╣ ║║║╔═╝║╣ ╠╦╝
╩  ╚═╝╝╚╝╚═╝╚═╝╩╚═

       Penzer-CLI // Local Cognitive Shell
    -------------------------------------------------
\033[0m
    """
    print(logo)

# --- Boot sequence ---
def boot_sequence():
    steps = [
        "Stabilizing quantum buffer...",
        "Splicing neural filaments...",
        "Checking entropy drift...",
        "Igniting reasoning core...",
        "Handshake: accepted"
    ]
    type_effect("[ Boot Sequence Initiated ]", 0.02, color="\033[91m")
    for step in steps:
        type_effect(f" • {step}", 0.02, color="\033[91m")
        time.sleep(0.05)
    type_effect("\nPenzer is online. The grid hums. Commands await.\n", 0.02, color="\033[91m")

# --- Main CLI ---
import asyncio
async def main():
    display_logo()
    boot_sequence()
    # start MCP server thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    agent = await Agent().async_init()
    type_effect("Speak your intent.\n", 0.02, color="\033[91m")

    while True:
        user_input = input("\033[97mUser> \033[0m")
        if user_input.lower() in ["exit", "quit"]:
            type_effect("\n[ Shutting Down ] Penzer slips back into the dark mesh.", 0.02, color="\033[91m")
            break
        await agent.process_input(user_input)

if __name__ == "__main__":
    asyncio.run(main())
