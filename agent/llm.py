"""
Universal LLM interface – supports local servers and any cloud API.
Handles JSON and XML tool calls, retries, multiple providers, and robust error handling.
"""
import os
import json
import re
import asyncio
import logging
import httpx
from pathlib import Path
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
RETRY_DELAYS = [0, 2, 4, 8, 16]
# HTTP status codes that we retry with backoff (rate limiting + transient server errors)
# 401/403 are NOT retried – they indicate authentication issues.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _detect_provider(url: str) -> str:
    """Determine the API provider from the URL."""
    url = url.lower()
    if "googleapis" in url or "generativelanguage" in url:
        return "gemini"
    if "openai.com" in url:
        return "openai"
    if "anthropic.com" in url:
        return "anthropic"
    if "openrouter.ai" in url:
        return "openrouter"   # uses OpenAI-compatible API
    if "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url:
        return "local"
    return "openai_compatible"


class LLMModel:
    """Universal async LLM model – handles any provider."""

    def __init__(self, api_key: str, url: str):
        self.api_key = api_key
        self.url = url.rstrip("/")
        self.provider = _detect_provider(url)
        self.model_name = os.getenv("MODEL_NAME", self.provider)
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
        self, messages: List[Dict[str, str]], max_tokens: int = 2048, temperature: float = 0.7
    ) -> str:
        if self.provider == "gemini":
            return await self._call_gemini(messages, max_tokens, temperature)
        elif self.provider == "anthropic":
            return await self._call_anthropic(messages, max_tokens, temperature)
        else:
            # OpenAI, OpenRouter, local, and any OpenAI-compatible
            return await self._call_openai_compatible(messages, max_tokens, temperature)

    async def _call_openai_compatible(
        self, messages: List[Dict[str, str]], max_tokens: int, temperature: float
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # If the URL already ends with /v1, don't add another one
        base = self.url if "/v1" in self.url else f"{self.url}/v1"
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # For OpenRouter, we may want to include extra headers, but it works with the standard format.
        r = await self.client.post(
            f"{base}/chat/completions",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def _call_gemini(
        self, messages: List[Dict[str, str]], max_tokens: int, temperature: float
    ) -> str:
        # Gemini uses a different structure; we flatten all messages into a single text prompt
        # with role labels. This is not perfect but works for instruction-tuned models.
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
        self, messages: List[Dict[str, str]], max_tokens: int, temperature: float
    ) -> str:
        # Anthropic separates system prompt from conversation
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
                "model": self.model_name,
                "max_tokens": max_tokens,
                "system": system,
                "messages": msgs,
                "temperature": temperature,
            },
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]


class LLM:
    """Main interface for the application – loads config, parses responses, handles retries."""

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
            raise FileNotFoundError(
                "No LOCAL_SERVER_URL or API credentials (API_KEY + URL) found in .env"
            )

    # ---------- JSON extraction ----------
    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract the first JSON object from text (supports markdown fences)."""
        # Remove markdown code fences
        for fence in ("```json", "```"):
            if fence in text:
                start = text.find(fence) + len(fence)
                start = text.find("\n", start) + 1
                end = text.find("```", start)
                if end > start:
                    text = text[start:end].strip()
                    break
        text = text.strip()
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try to find a JSON object anywhere in the text
        start = text.find("{")
        if start == -1:
            return None
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            return None

    # ---------- XML tool‑call extraction ----------
    def _parse_xml_tool_calls(self, text: str) -> tuple[List[Dict[str, Any]], str]:
        """
        Extract tool calls from XML‑style tags:
          <tool_call> <function=NAME> <parameter=KEY> VALUE </tool_call>
        Returns (tool_calls_list, cleaned_text_without_blocks)
        """
        tool_calls = []
        pattern = re.compile(r'<tool_call>(.*?)</tool_call>', re.DOTALL)
        matches = list(pattern.finditer(text))
        if not matches:
            return [], text
        # Remove all <tool_call> blocks from the original text
        cleaned = pattern.sub('', text).strip()
        for idx, match in enumerate(matches):
            inner = match.group(1).strip()
            # Extract function name: <function=NAME> or <function>NAME</function>
            func_match = re.search(r'<function[=>]\s*(\w+)', inner)
            if not func_match:
                continue
            func_name = func_match.group(1)
            # Extract parameters: <parameter=KEY> VALUE
            args = {}
            param_matches = re.finditer(
                r'<parameter=(\w+)>\s*(.*?)\s*(?=<|$)', inner, re.DOTALL
            )
            for pm in param_matches:
                key = pm.group(1)
                value = pm.group(2).strip()
                args[key] = value
            # If no parameter tags, use the inner text after the function tag as "command"
            if not args:
                inner_without_func = re.sub(r'<function[=>]\s*\w+\s*>', '', inner).strip()
                if inner_without_func:
                    args = {"command": inner_without_func}
            if func_name:
                tool_calls.append({
                    "id": f"tool_call_{idx + 1}",
                    "name": func_name,
                    "arguments": args
                })
        return tool_calls, cleaned

    # ---------- Retry logic ----------
    async def _call_with_backoff(self, messages: List[Dict[str, str]]) -> str:
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

    # ---------- Main chat entry point ----------
    async def chat(self, system: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Send a chat request and return a dict with:
          - content (str): the answer text
          - tool_calls (list): each with id, name, arguments
          - assumptions (list): optional belief-state fields
          - unknowns (list): optional belief-state fields
        """
        prompt = [{"role": "system", "content": system}] + messages
        try:
            raw = await self._call_with_backoff(prompt)
            self.call_count += 1
            self.token_estimate += sum(
                len(str(m.get("content", "")).split())
                for m in prompt
            )
        except httpx.HTTPStatusError as e:
            # User‑friendly error messages for common HTTP errors
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

        # --- Structured parsing (JSON first, then XML) ---
        data = self._extract_json(raw)
        if data:
            # JSON parsing succeeded
            answer = data.get("answer") or data.get("thought", "")
            tool_name = str(data.get("tool", "")).strip()
            tool_args = data.get("args", {})

            def _as_list(v) -> list:
                if not v:
                    return []
                return v if isinstance(v, list) else [str(v)]

            assumptions = _as_list(data.get("assumptions"))
            unknowns = _as_list(data.get("unknowns"))

            # Multiple independent tool calls (new format)
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

            # Single tool call (old format)
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

            # Final answer (no tool call) — but only if the model actually
            # gave us an answer/thought field. If none of the known keys
            # ("answer", "thought", "tool", "tools") were present, this
            # JSON blob doesn't match any schema we taught the model —
            # most commonly the model echoing back the history's
            # "tools" key (used internally by agent.py's assistant-turn
            # log entries) under a slightly different name, or some other
            # malformed structure. Returning the raw JSON as "content"
            # here would let it silently pass through _loop() in agent.py
            # as a bogus final answer (and later get flagged by cli.py's
            # clean_response() as "still executing", which is exactly
            # backwards — the run has actually stalled, not progressed).
            # Fall through to the "no recognized schema" handling below,
            # which the caller (agent.py's _llm_with_retry / _loop) treats
            # as an empty/non-actionable turn and prompts the model to
            # continue, rather than ending the run.
            if answer:
                return {
                    "content": answer,
                    "tool_calls": [],
                    "assumptions": assumptions,
                    "unknowns": unknowns,
                }

        # --- XML tool calls (fallback) ---
        tool_calls, cleaned = self._parse_xml_tool_calls(raw)
        if tool_calls:
            return {
                "content": cleaned or "",
                "tool_calls": tool_calls,
                "assumptions": [],
                "unknowns": [],
            }

        # --- No structured tool call found ---
        # If `raw` parsed as JSON (data is not None) but matched none of
        # the schemas above (no answer/thought/tool/tools, and no XML
        # tool_call tags either), don't hand the raw JSON string back as
        # if it were a genuine final answer — that lets a malformed or
        # echoed response silently masquerade as real content downstream.
        # Treat it as an empty response instead, so agent.py's _loop()
        # nudges the model to continue rather than ending the run on
        # garbage. Only genuinely unstructured plain-text output falls
        # through to the raw-text return.
        if data is not None:
            logger.warning("LLM returned JSON with no recognized schema: %s", raw[:200])
            return {"content": "", "tool_calls": [], "assumptions": [], "unknowns": []}
        return {"content": raw.strip(), "tool_calls": [], "assumptions": [], "unknowns": []}


def get_model_choice() -> LLM:
    """Factory function to create an LLM instance (for compatibility)."""
    return LLM()