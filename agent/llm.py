"""
Minimal LLM interface - supports local servers and any cloud API
"""
import os
import json
import time
import requests
from pathlib import Path
from typing import Union
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent


def _detect_provider(url: str) -> str:
    """Detect provider from URL."""
    url = url.lower()
    if "googleapis" in url or "generativelanguage" in url:
        return "gemini"
    if "openai.com" in url:
        return "openai"
    if "anthropic.com" in url:
        return "anthropic"
    if "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url:
        return "local"
    # Default to OpenAI-compatible (works for vLLM, Ollama, Together, Groq, etc)
    return "openai_compatible"


class LLMModel:
    """Universal LLM model — handles any provider."""

    def __init__(self, api_key: str, url: str):
        self.api_key = api_key
        self.url = url.rstrip("/")
        self.provider = _detect_provider(url)
        self.model_name = self.provider

    def create_chat_completion(self, messages: list, max_tokens: int = 2048,
                               temperature: float = 0.7) -> str:

        if self.provider == "gemini":
            return self._call_gemini(messages, max_tokens, temperature)
        elif self.provider == "anthropic":
            return self._call_anthropic(messages, max_tokens, temperature)
        else:
            # OpenAI-compatible: works for OpenAI, vLLM, Ollama, Groq, Together, local
            return self._call_openai_compatible(messages, max_tokens, temperature)

    def _call_openai_compatible(self, messages: list, max_tokens: int, temperature: float) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        base = self.url if "/v1" in self.url else f"{self.url}/v1"
        response = requests.post(
            f"{base}/chat/completions",
            headers=headers,
            json={"model": os.getenv("MODEL_NAME", "local-model"),
                  "messages": messages,
                  "max_tokens": max_tokens,
                  "temperature": temperature},
            timeout=60
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _call_gemini(self, messages: list, max_tokens: int, temperature: float) -> str:
        prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
        url = self.url if "?" in self.url else f"{self.url}?key={self.api_key}"
        response = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens,
                                       "temperature": temperature}},
            timeout=30
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]

    def _call_anthropic(self, messages: list, max_tokens: int, temperature: float) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        msgs = [m for m in messages if m["role"] != "system"]
        response = requests.post(
            f"{self.url}/v1/messages",
            headers={"x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json={"model": os.getenv("MODEL_NAME", "claude-3-5-haiku-20241022"),
                  "max_tokens": max_tokens,
                  "system": system,
                  "messages": msgs,
                  "temperature": temperature},
            timeout=60
        )
        response.raise_for_status()
        return response.json()["content"][0]["text"]


class LLM:
    def __init__(self):
        self.model = self._init_model()
        self.model_name = getattr(self.model, 'model_name', 'unknown')
        self._plain_text_failures = 0

    def _init_model(self) -> LLMModel:
        load_dotenv(str(PROJECT_ROOT / ".env"), override=False)
        local_url = os.getenv("LOCAL_SERVER_URL", "").strip().strip('"\'')
        api_key = os.getenv("API_KEY", "").strip().strip('"\'')
        api_url = os.getenv("URL", "").strip().strip('"\'')

        if local_url:
            print("[LLM] Auto-detected: Local server available")
            return LLMModel("", local_url)
        elif api_key and api_url:
            provider = _detect_provider(api_url)
            print(f"[LLM] Auto-detected: {provider} API")
            return LLMModel(api_key, api_url)
        else:
            raise FileNotFoundError("No LOCAL_SERVER_URL or API credentials in .env")

    def _extract_json(self, text: str) -> dict | None:
        for fence in ["```json", "```"]:
            if fence in text:
                start = text.find(fence) + len(fence)
                start = text.find("\n", start) + 1
                end = text.find("```", start)
                if end > start:
                    text = text[start:end].strip()
                    break
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        if start != -1:
            for end in range(len(text), start, -1):
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    continue
        # Salvage key fields
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
Never explain yourself in plain text. Just output the JSON immediately. One JSON object. Nothing else.
"""
        enforced_system = system + format_instruction
        prompt_messages = [{"role": "system", "content": enforced_system}] + messages

        try:
            raw = self._call_with_backoff(prompt_messages)
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                return {"content": "Rate limit reached — please wait a moment and try again.", "tool_calls": []}
            return {"content": "LLM request failed — check your API credentials.", "tool_calls": []}
        except Exception:
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