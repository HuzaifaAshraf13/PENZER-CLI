"""
Minimal LLM interface - supports local servers and any cloud API

Fixes (this pass):
  1. `_call_with_backoff` only retried on HTTP 429. Transient server-side
     errors (500, 502, 503, 504) raised immediately on the first attempt
     instead of getting the same backoff-and-retry treatment — despite
     `chat()` having dedicated friendly messages for exactly those codes
     ("LLM server error. Try again in a moment.", "LLM service
     unavailable. Server is down.") which implied they were expected to
     be retried. Confirmed with a mocked transport: a 500-then-200
     sequence returned the friendly error message after a single
     attempt instead of succeeding on retry. `RETRYABLE_STATUS` now
     covers 429 plus the common transient 5xx codes; 401/403 (auth
     failures, where retrying the same credentials is pointless) are
     correctly left out and still fail immediately, unchanged from
     before.
  Everything else in this file — the JSON-in-text tool-call/answer
  parsing in `_extract_json`/`chat()`, markdown-fenced JSON extraction,
  provider dispatch, and the 429 retry path — was verified working
  against a mocked HTTP transport and left as-is.

Known limitation, not changed here: `_call_gemini` flattens the entire
message list (including the system prompt and all prior turns) into one
text blob sent as a single `parts` entry, rather than using Gemini's
structured `contents` array with proper user/model roles and a separate
`system_instruction` field. It works — instruction-tuned models can
generally follow "ROLE: content" formatted text — but it doesn't use the
provider's actual multi-turn API and loses the role distinction Gemini's
own API would preserve. Flagging rather than rewriting since I can't
verify a rewrite against a live Gemini endpoint from here (no network
access to googleapis.com in this environment) — happy to do it if you
can confirm the exact response shape you're seeing, or test it yourself
against the real API first.
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

# 429 (rate limited) and the common transient 5xx server errors all
# benefit from backoff-and-retry. 401/403 (auth failures) deliberately
# are NOT here — retrying with the same bad credentials just wastes the
# whole retry budget for a request that can never succeed.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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
                if e.response.status_code in RETRYABLE_STATUS and attempt < len(RETRY_DELAYS) - 1:
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
            return {"content": raw.strip(), "tool_calls": [], "assumptions": [], "unknowns": []}

        # Support both new format (answer) and old format (thought)
        answer    = data.get("answer") or data.get("thought", "")
        tool_name = str(data.get("tool", "")).strip()
        tool_args = data.get("args", {})

        # Belief-state fields (ReflAct): optional — the model isn't
        # required to include these, but if it does, they're captured
        # here rather than silently dropped. Previously not read at all,
        # so even a model that dutifully reasoned about its assumptions
        # and unknowns in valid JSON had that discarded before agent.py
        # ever saw it.
        def _as_list(v) -> list:
            if not v:
                return []
            return v if isinstance(v, list) else [str(v)]

        assumptions = _as_list(data.get("assumptions"))
        unknowns    = _as_list(data.get("unknowns"))

        # Multiple INDEPENDENT calls in one turn — e.g. `which ss`,
        # `which netstat`, `which lsof` are unrelated checks with no
        # data dependency between them. Previously this parser could
        # only ever extract a single {"tool", "args"} pair, so
        # agent.py's own parallel/race execution machinery
        # (_run_parallel, _run_race) — built to handle exactly this
        # case — could never actually receive more than one call per
        # turn and was effectively dead code. Optional and additive:
        # a model that never uses "tools" behaves exactly as before.
        tools_array = data.get("tools")
        if isinstance(tools_array, list) and tools_array:
            tool_calls = []
            for idx, t in enumerate(tools_array):
                if not isinstance(t, dict):
                    continue
                tname = str(t.get("tool", "")).strip()
                targs = t.get("args", {})
                if tname:
                    tool_calls.append({
                        "id": f"tool_call_{idx + 1}",
                        "name": tname,
                        "arguments": targs if isinstance(targs, dict) else {},
                    })
            if tool_calls:
                return {
                    "content": answer,
                    "tool_calls": tool_calls,
                    "assumptions": assumptions,
                    "unknowns": unknowns,
                }

        # If tool is specified, return tool call
        if tool_name:
            return {
                "content": answer,
                "tool_calls": [{
                    "id": "tool_call_1",
                    "name": tool_name,
                    "arguments": tool_args if isinstance(tool_args, dict) else {},
                }],
                "assumptions": assumptions,
                "unknowns": unknowns,
            }

        # Otherwise, return as final answer
        return {"content": answer or raw.strip(), "tool_calls": [], "assumptions": assumptions, "unknowns": unknowns}


def get_model_choice() -> LLM:
    return LLM()