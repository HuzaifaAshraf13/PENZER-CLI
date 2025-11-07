# Takes user commands and starts sessions
import argparse

def display_logo():
    """Displays the PENZER ASCII art logo."""
    logo = """
    ██████╗ ███████╗███╗   ██╗███████╗███████╗██████╗ 
    ██╔══██╗██╔════╝████╗  ██║██╔════╝██╔════╝██╔══██╗
    ██████╔╝█████╗  ██╔██╗ ██║███████╗█████╗  ██████╔╝
    ██╔═══╝ ██╔══╝  ██║╚██╗██║╚════██║██╔══╝  ██╔══██╗
    ██║     ███████╗██║ ╚████║███████║███████╗██║  ██║
    ╚═╝     ╚══════╝╚═╝  ╚═══╝╚══════╝╚══════╝╚═╝  ╚═╝
    """
    print(logo)

def main():
    """Main function to run the CLI."""
    parser = argparse.ArgumentParser(description="Penzer-CLI: An intelligent assistant.")
    args = parser.parse_args()

    display_logo()
    print("Welcome to Penzer-CLI! Type 'exit' or 'quit' to end the session.")

    from session.session import Session
    from agent.agent import Agent

    session = Session(None) # Session will now manage continuous input
    agent = Agent()

    while True:
        user_input = input("You: ")
        if user_input.lower() in ['exit', 'quit']:
            print("Exiting Penzer-CLI. Goodbye!")
            break
        agent.run(session, user_input)

if __name__ == "__main__":
    main()
