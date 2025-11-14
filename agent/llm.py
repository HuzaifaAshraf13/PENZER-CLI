# agent/llm.py
import os
from dotenv import load_dotenv
from google import genai
from typing import Optional

class LLM:
    def __init__(self):
        load_dotenv()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")
        self.client = genai.Client(api_key=self.gemini_api_key)

    # FIX: Add system_instruction as a keyword argument.
    def generate_content(self, prompt: str, system_instruction: Optional[str] = None):
        """
        Generates content from the Gemini model, including a system instruction 
        for role-setting/tool use context.
        """
        
        # --- Configure the model with the system instruction (if provided) ---
        config = {}
        if system_instruction:
            # FIX: Use the 'system_instruction' key within the configuration dictionary
            config['system_instruction'] = system_instruction
        
        # Check if the config dictionary is empty before passing it
        config_param = config if config else None

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
            config=config_param # Pass the configuration
        )
        return response.text