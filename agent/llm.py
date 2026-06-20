"""
Minimal LLM interface - supports local servers and any cloud API
"""
import os
import json
import asyncio
import httpx
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
RETRY_DELAYS = [0, 2, 4, 8, 16]


def _detect_provider(url: str) -> str:
    url = url.lower()
    if "googleapis" in url or "generativelanguage" in url:
        return "gemini"
    if "openai.com" in url:
        return "openai"
    if "anthropic.com" in url:
        return "anthropic"
    if "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url:
        return "local"
    return "openai_compatible"


class LLMModel:
    """Universal async LLM model — handles any provider."""

    def __init__(self, api_key: str, url: str):
        self.api_key = api_key
        self.url = url.rstrip("/")
        self.provider = _detect_provider(url)
        self.model_name = self.provider
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def create_chat_completion(
        self, messages: list, max_tokens: int = 2048, temperature: float = 0.7
    ) -> str:
        if self.provider == "gemini":
            return await self._call_gemini(messages, max_tokens, temperature)
        elif self.provider == "anthropic":
            return await self._call_anthropic(messages, max_tokens, temperature)
        else:
            return await self._call_openai_compatible(messages, max_tokens, temperature)

    async def _call_openai_compatible(
        self, messages: list, max_tokens: int, temperature: float
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        base = self.url if "/v1" in self.url else f"{self.url}/v1"
        r = await self.client.post(
            f"{base}/chat/completions",
            headers=headers,
            json={
                "model": os.getenv("MODEL_NAME", "local-model"),
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def _call_gemini(
        self, messages: list, max_tokens: int, temperature: float
    ) -> str:
        prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
        url = self.url if "?" in self.url else f"{self.url}?key={self.api_key}"
        r = await self.client.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            },
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    async def _call_anthropic(
        self, messages: list, max_tokens: int, temperature: float
    ) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        msgs = [m for m in messages if m["role"] != "system"]
        r = await self.client.post(
            f"{self.url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("MODEL_NAME", "claude-3-5-haiku-20241022"),
                "max_tokens": max_tokens,
                "system": system,
                "messages": msgs,
                "temperature": temperature,
            },
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]


class LLM:
    def __init__(self):
        self.model = self._init_model()
        self.model_name = getattr(self.model, "model_name", "unknown")
        self.call_count = 0
        self.token_estimate = 0

    def _init_model(self) -> LLMModel:
        load_dotenv(str(PROJECT_ROOT / ".env"), override=False)
        local_url = os.getenv("LOCAL_SERVER_URL", "").strip().strip("\"'")
        api_key   = os.getenv("API_KEY", "").strip().strip("\"'")
        api_url   = os.getenv("URL", "").strip().strip("\"'")

        if local_url:
            print("[LLM] Auto-detected: Local server")
            return LLMModel("", local_url)
        elif api_key and api_url:
            provider = _detect_provider(api_url)
            print(f"[LLM] Auto-detected: {provider} API")
            return LLMModel(api_key, api_url)
        else:
            raise FileNotFoundError("No LOCAL_SERVER_URL or API credentials in .env")

    def _extract_json(self, text: str) -> Optional[dict]:
        for fence in ("```json", "```"):
            if fence in text:
                start = text.find(fence) + len(fence)
                start = text.find("\n", start) + 1
                end = text.find("```", start)
                if end > start:
                    text = text[start:end].strip()
                    break

        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        if start == -1:
            return None
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            return None

    async def _call_with_backoff(self, messages: list) -> str:
        last_error = None
        for attempt, wait in enumerate(RETRY_DELAYS):
            if wait:
                await asyncio.sleep(wait)
            try:
                return await self.model.create_chat_completion(messages)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < len(RETRY_DELAYS) - 1:
                    last_error = e
                    continue
                raise
            except Exception as e:
                last_error = e
                if attempt == len(RETRY_DELAYS) - 1:
                    raise
        raise RuntimeError(f"LLM failed after retries: {last_error}")

    async def chat(self, system: str, messages: list) -> dict:
        prompt = [{"role": "system", "content": system}] + messages

        try:
            raw = await self._call_with_backoff(prompt)
            self.call_count += 1
            self.token_estimate += sum(
                len(str(m.get("content", "")).split())
                for m in prompt
            )
        except httpx.HTTPStatusError as e:
            # User-friendly error messages
            if e.response.status_code == 429:
                return {"content": "⏳ Rate limit reached. Please wait and try again.", "tool_calls": []}
            if e.response.status_code == 401:
                return {"content": "🔐 Authentication failed. Check your API key.", "tool_calls": []}
            if e.response.status_code == 403:
                return {"content": "🔑 Access denied. Invalid credentials.", "tool_calls": []}
            if e.response.status_code == 500:
                return {"content": "⚠️ LLM server error. Try again in a moment.", "tool_calls": []}
            if e.response.status_code == 503:
                return {"content": "🔌 LLM service unavailable. Server is down.", "tool_calls": []}
            if e.response.status_code == 504:
                return {"content": "⏱️ LLM took too long. Please try again.", "tool_calls": []}
            return {"content": f"❌ HTTP {e.response.status_code}. Try again.", "tool_calls": []}
        except httpx.TimeoutException:
            return {"content": "⏱️ Request timed out. LLM is slow.", "tool_calls": []}
        except httpx.ConnectError:
            return {"content": "🌐 Connection failed. Check your internet.", "tool_calls": []}
        except Exception as e:
            error_msg = str(e)[:50]
            return {"content": f"❌ Error: {error_msg}", "tool_calls": []}

        data = self._extract_json(raw)
        if not data:
            return {"content": raw.strip(), "tool_calls": []}

        # Support both new format (answer) and old format (thought)
        answer    = data.get("answer") or data.get("thought", "")
        tool_name = str(data.get("tool", "")).strip()
        tool_args = data.get("args", {})

        # If tool is specified, return tool call
        if tool_name:
            return {
                "content": answer,
                "tool_calls": [{
                    "id": "tool_call_1",
                    "name": tool_name,
                    "arguments": tool_args if isinstance(tool_args, dict) else {},
                }],
            }

        # Otherwise, return as final answer
        return {"content": answer or raw.strip(), "tool_calls": []}


def get_model_choice() -> LLM:
    return LLM()