# agent/llm.py
import os
from dotenv import load_dotenv
from google import genai

class LLM:
    def __init__(self):
        load_dotenv()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")
        self.client = genai.Client(api_key=self.gemini_api_key)

    def generate_content(self, prompt: str):
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt]
        )
        return response.text
