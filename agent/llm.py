"""
Minimal LLM interface - supports local servers and cloud APIs
"""
import os
import json
import time
import requests
from pathlib import Path
from typing import Optional, Union
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
_cache: dict = {}


class LocalServerModel:
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
    def __init__(self):
        self.model = self._init_model()
        self.model_name = getattr(self.model, 'model_name', 'unknown')
        self._plain_text_failures = 0

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
        """Robust JSON extractor — handles code blocks, partial JSON, buried objects."""
        # Strip code fences
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

        # Find first { and try progressively shorter substrings
        start = text.find("{")
        if start != -1:
            for end in range(len(text), start, -1):
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    continue

        # Last resort — salvage key fields with string search
        result = {}
        for key in ["thought", "tool", "args"]:
            marker = f'"{key}"'
            idx = text.find(marker)
            if idx != -1:
                val_start = text.find(":", idx) + 1
                val = text[val_start:val_start + 200].strip().strip(",")
                try:
                    result[key] = json.loads(val)
                except Exception:
                    result[key] = val.strip('"')
        return result if result else None

    def _call_with_backoff(self, messages: list) -> str:
        """Call LLM with exponential backoff on rate limit."""
        for attempt, wait in enumerate([0, 2, 4, 8, 16]):
            if wait:
                time.sleep(wait)
            try:
                return self.model.create_chat_completion(messages)
            except requests.HTTPError as e:
                if e.response.status_code == 429 and attempt < 4:
                    continue
                raise requests.HTTPError(f"HTTP {e.response.status_code}", response=e.response)
        raise RuntimeError("LLM failed after retries")

    async def chat(self, system: str, messages: list) -> dict:
        # Switch to simpler prompt if Gemini keeps returning plain text
        if self._plain_text_failures >= 3:
            format_instruction = '\nRespond with JSON only: {"thought": "...", "tool": "tool_name", "args": {}}'
        else:
            format_instruction = """

RESPONSE FORMAT — always respond with valid JSON only:
If you need to use a tool:
{"thought": "why you are doing this", "tool": "tool_name", "args": {"key": "value"}}

If you have a final answer (no tool needed):
{"thought": "your answer here"}

Available tools: terminal, run_python, run_bash, browser, file_editor, memory
Never explain yourself in plain text. Never say "I will search" or "Let me look that up."
Just output the JSON immediately. One JSON object. Nothing else. NEVER respond with plain text.
"""

        enforced_system = system + format_instruction
        prompt_messages = [{"role": "system", "content": enforced_system}] + messages

        try:
            raw = self._call_with_backoff(prompt_messages)
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                return {"content": "LLM rate limit reached — please wait a moment and try again.", "tool_calls": []}
            return {"content": "LLM request failed — check your API credentials.", "tool_calls": []}
        except Exception as e:
            return {"content": "LLM did not respond — please try again.", "tool_calls": []}

        data = self._extract_json(raw)

        if not data:
            self._plain_text_failures += 1
            return {"content": raw.strip(), "tool_calls": []}

        self._plain_text_failures = 0
        thought = data.get("thought", "")
        tool_name = str(data.get("tool", "")).strip()
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