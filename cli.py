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
    parser.add_argument("task", type=str, nargs='?', default="No task provided", help="The task for Penzer to perform.")

    args = parser.parse_args()

    display_logo()
    print(f"Executing task: {args.task}")
    # Placeholder for session and agent logic
    # from session.session import Session
    # from agent.agent import Agent
    #
    # session = Session(args.task)
    # agent = Agent()
    # agent.run(session)

if __name__ == "__main__":
    main()
