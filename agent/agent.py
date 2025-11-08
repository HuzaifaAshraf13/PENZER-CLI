# agent/agent.py
import os
from dotenv import load_dotenv
from fastmcp import Client
from google import genai

class Agent:
    def __init__(self):
        # Load Gemini API key
        load_dotenv()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")

        # Initialize Gemini LLM client
        self.client = genai.Client(api_key=self.gemini_api_key)

        # Initialize MCP client (connects to local MCP server)
        self.mcp_client = Client("http://127.0.0.1:8000/mcp")

    def run_tool(self, tool_name: str, args: dict):
        """Call MCP tool and return result."""
        return self.mcp_client.call_tool(tool_name, args)

    def process_input(self, user_input: str):
        """Process user input and call tools or LLM as needed."""
        if user_input.startswith("echo "):
            msg = user_input[5:]
            result = self.run_tool("echo", {"message": msg})
            print(f"Agent: {result}")
        elif user_input.startswith("add "):
            parts = user_input[4:].split()
            if len(parts) == 2:
                try:
                    a = int(parts[0])
                    b = int(parts[1])
                    result = self.run_tool("add", {"a": a, "b": b})
                    print(f"Agent: {result}")
                except ValueError:
                    print("Agent: Invalid numbers provided for add command.")
            else:
                print("Agent: Please provide exactly two numbers for add.")
        else:
            # Fallback to LLM for free-form input
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[user_input]
            )
            print(f"Agent: {response.text}")
