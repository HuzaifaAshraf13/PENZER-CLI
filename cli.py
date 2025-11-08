# cli.py
import threading
from agent.agent import Agent
from agent.server import start_server

def display_logo():
    logo = """
╔═╗╔═╗╔╗╔╔═╗╔═╗╦═╗
╠═╝║╣ ║║║╔═╝║╣ ╠╦╝
╩  ╚═╝╝╚╝╚═╝╚═╝╩╚═                          
    """
    print(logo)

def main():
    display_logo()
    print("Welcome to Penzer-CLI! Type 'exit' or 'quit' to end the session.\n")

    # Start MCP server in background thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    print("MCP server started in background.\n")

    agent = Agent()

    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            print("Exiting Penzer-CLI. Goodbye!")
            break
        agent.process_input(user_input)

if __name__ == "__main__":
    main()
