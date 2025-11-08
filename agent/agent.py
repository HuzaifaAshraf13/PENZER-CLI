# agent/agent.py
import os
from fastmcp import Client
from google import genai
from dotenv import load_dotenv

class Agent:
    def __init__(self):
        load_dotenv()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")

        # LLM client
        self.client = genai.Client(api_key=self.gemini_api_key)

        # Correct MCP client initialization
        self.mcp_client = Client("http://127.0.0.1:8000/mcp")


    def run_tool(self, tool_name: str, args: dict):
        """Call a tool on MCP server."""
        return self.mcp_client.call_tool(tool_name, args)

    def process_input(self, user_input: str):
        """Use LLM for reasoning and optionally call tools."""
        print(f"User: {user_input}")

        if user_input.startswith("echo "):
            msg = user_input[5:]
            result = self.run_tool("echo", {"message": msg})
            print(f"Tool output: {result}")
        elif user_input.startswith("add "):
            parts = user_input[4:].split()
            if len(parts) == 2:
                result = self.run_tool("add", {"a": int(parts[0]), "b": int(parts[1])})
                print(f"Tool output: {result}")
        else:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[user_input]
            )
            print(f"Agent: {response.text}")
