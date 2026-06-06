"""
Minimal LLM interface - supports local servers and cloud APIs
"""
import os
import json
import requests
from pathlib import Path
from typing import Optional, Union
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent


class LocalServerModel:
    """Local AI server (llama.cpp, ollama, vLLM, etc)"""

    def __init__(self, url: str):
        self.url = url.rstrip('/')
        self.model_name = "local-server"

    def create_chat_completion(self, messages: list, max_tokens: int = 2048,
                               temperature: float = 0.5, top_p: float = 0.9) -> str:
        response = requests.post(
            f"{self.url}/v1/chat/completions",
            json={"model": "local-model", "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p},
            timeout=60
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


class APIModel:
    """Cloud AI API (Google Gemini, OpenAI, etc)"""

    def __init__(self, api_key: str, url: str):
        self.api_key = api_key
        self.url = url
        self.model_name = "api"

    def create_chat_completion(self, messages: list, max_tokens: int = 2048,
                               temperature: float = 0.7, top_p: float = 0.95) -> str:
        prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
        response = requests.post(
            self.url if "?" in self.url else f"{self.url}?key={self.api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens,
                                       "temperature": temperature, "topP": top_p}},
            timeout=30
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]


class LLM:
    """Main LLM wrapper"""

    def __init__(self):
        self.model = self._init_model()
        self.model_name = getattr(self.model, 'model_name', 'unknown')

    def _init_model(self) -> Union[LocalServerModel, APIModel]:
        load_dotenv(str(PROJECT_ROOT / ".env"), override=False)
        local_url = os.getenv("LOCAL_SERVER_URL", "").strip().strip('"\'')
        api_key = os.getenv("API_KEY", "").strip().strip('"\'')
        api_url = os.getenv("URL", "").strip().strip('"\'')

        if local_url:
            print("[LLM] Auto-detected: Local server available")
            return LocalServerModel(local_url)
        elif api_key and api_url:
            print("[LLM] Auto-detected: API credentials available")
            return APIModel(api_key, api_url)
        else:
            raise FileNotFoundError("No LOCAL_SERVER_URL or API credentials in .env")

    def _extract_json(self, text: str) -> dict | None:
        """Extract JSON from LLM output — handles code blocks and raw JSON."""
        # Strip code blocks
        for fence in ["```json", "```"]:
            if fence in text:
                start = text.find(fence) + len(fence)
                start = text.find("\n", start) + 1
                end = text.find("```", start)
                if end > start:
                    text = text[start:end].strip()
                    break

        # Try full parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Try finding JSON object anywhere in text
        for start in [text.find("{"), 0]:
            if start == -1:
                continue
            for end in range(len(text), start, -1):
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    continue

        return None

    async def chat(self, system: str, messages: list) -> dict:
        """
        Chat interface for agent.
        Expects LLM to respond with JSON:
        {"thought": "...", "tool": "tool_name", "args": {...}}
        or just text for final answers.
        """
        # Force JSON tool-call format in system prompt
        enforced_system = system + """

RESPONSE FORMAT — always respond with valid JSON only:
If you need to use a tool:
{"thought": "why you are doing this", "tool": "tool_name", "args": {"key": "value"}}

If you have a final answer (no tool needed):
{"thought": "your answer here"}

Available tools: terminal, run_python, run_bash, browser, file_editor, memory
NEVER respond with plain text. ALWAYS respond with JSON.
"""

        prompt_messages = [{"role": "system", "content": enforced_system}] + messages

        try:
            raw = self.model.create_chat_completion(prompt_messages)
        except Exception as e:
            return {"content": f"LLM error: {str(e)}", "tool_calls": []}

        data = self._extract_json(raw)

        if not data:
            # LLM returned plain text — treat as final answer
            return {"content": raw.strip(), "tool_calls": []}

        thought = data.get("thought", "")
        tool_name = data.get("tool", "").strip()
        tool_args = data.get("args", {})

        if tool_name:
            return {
                "content": thought,
                "tool_calls": [{
                    "id": "tool_call_1",
                    "name": tool_name,
                    "arguments": tool_args if isinstance(tool_args, dict) else {}
                }]
            }

        return {"content": thought or raw.strip(), "tool_calls": []}


def get_model_choice() -> LLM:
    return LLM()