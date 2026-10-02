"""PENZER Browser Tool - Chrome DevTools Protocol via cdpify"""

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import time
from typing import Optional, Any, Dict, List
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlparse

import requests

try:
    from cdpify import Client
except ImportError as exc:
    raise ImportError("cdpify is required: pip install cdpify") from exc


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_sessions: Dict[str, "CDPSession"] = {}
_chrome_pids: Dict[str, int] = {}
_event_loop: Optional[asyncio.AbstractEventLoop] = None


@dataclass
class CDPSession:
    session_id: str
    client: Optional[Any] = None
    root_client: Optional[Client] = None
    target_id: str = ""
    port: int = 0
    process: Optional[subprocess.Popen] = None
    element_refs: Dict[str, Dict] = field(default_factory=dict)
    last_error: str = ""
    action_count: int = 0
    url: str = ""


def success(data: Any = None, message: str = "") -> Dict:
    return {
        "status": "success",
        "message": message or "OK",
        "data": data if data is not None else {},
    }


def error(message: str, data: Any = None) -> Dict:
    return {
        "status": "error",
        "message": message,
        "data": data if data is not None else {},
    }


def warning(message: str, data: Any = None) -> Dict:
    return {
        "status": "warning",
        "message": message,
        "data": data if data is not None else {},
    }


def _find_free_port(start: int = 9222, end: int = 9500) -> int:
    import socket

    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue

    raise RuntimeError(f"No free ports in range {start}-{end}")


def _candidate_chrome_binaries() -> List[str]:
    preferred = os.getenv("PENZER_CHROME_BINARY")
    if preferred:
        return [preferred]
    return [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "microsoft-edge-stable",
        "brave-browser",
    ]


def _verify_chrome_binary(binary: str) -> bool:
    """Verify Chrome binary exists and is functional."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.error("Chrome verification failed for %s: %s", binary, exc)
        return False


def _spawn_chrome(port: int) -> subprocess.Popen:
    for binary in _candidate_chrome_binaries():
        if _verify_chrome_binary(binary):
            break
    else:
        raise RuntimeError("Chrome binary not found or broken in common locations")

    try:
        return subprocess.Popen(
            [
                binary,
                "--headless=new",
                f"--remote-debugging-port={port}",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Chrome not found: {binary}") from exc


def _get_debugger_url(port: int, timeout: int = 15) -> str:
    start = time.time()

    while time.time() - start < timeout:
        try:
            response = requests.get(
                f"http://127.0.0.1:{port}/json/version",
                timeout=2,
            )
            response.raise_for_status()
            return response.json()["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.5)

    raise RuntimeError(f"Chrome not responding on port {port} after {timeout}s")


def _kill_chrome(pid: int, process: Optional[subprocess.Popen] = None):
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), 15)
        else:
            os.kill(pid, 15)
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.debug("Chrome cleanup failed: %s", exc)
    if process is not None:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait()


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create persistent event loop."""
    global _event_loop
    
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)
    
    return _event_loop


async def _navigate_to_url(client: Client, url: str) -> bool:
    try:
        await client.execute("Page.enable")
        await client.execute("Page.navigate", {"url": url})
        await client.execute("Runtime.enable")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = await client.execute(
                "Runtime.evaluate",
                {"expression": "document.readyState", "returnByValue": True},
            )
            if state.get("result", {}).get("value") == "complete":
                break
            await asyncio.sleep(0.2)
        return True
    except Exception as exc:
        logger.error("Navigate failed: %s", exc)
        return False


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_government_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return (
        host.endswith(".gov")
        or ".gov." in host
        or host.endswith(".mil")
        or ".mil." in host
    )


async def _get_page_metadata(client: Client) -> Dict[str, str]:
    try:
        result = await client.execute(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify({url: location.href, title: document.title, description: document.querySelector('meta[name=\"description\"]')?.content || document.querySelector('meta[property=\"og:description\"]')?.content || '', canonical: document.querySelector('link[rel=\"canonical\"]')?.href || ''})",
                "returnByValue": True,
            },
        )
        value = result.get("result", {}).get("value", "{}")
        metadata = json.loads(value) if isinstance(value, str) else {}
        if isinstance(metadata, dict):
            return {
                "url": str(metadata.get("url") or ""),
                "title": str(metadata.get("title") or ""),
                "description": str(metadata.get("description") or ""),
                "canonical": str(metadata.get("canonical") or ""),
            }
    except Exception as exc:
        logger.debug("Page metadata unavailable: %s", exc)
    return {"url": "", "title": "", "description": "", "canonical": ""}


async def _get_page_summary(client: Client) -> Dict[str, Any]:
    try:
        result = await client.execute(
            "Runtime.evaluate",
            {
                "expression": """
                (() => {
                    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const headingText = () => Array.from(document.querySelectorAll('h1, h2, h3, h4')).map(el => clean(el.innerText)).filter(Boolean).slice(0, 8);
                    const text = clean(document.body ? document.body.innerText : '');
                    const title = clean(document.title || '');
                    const description = clean(
                        document.querySelector('meta[name="description"]')?.content ||
                        document.querySelector('meta[property="og:description"]')?.content ||
                        ''
                    );
                    const summary = text ? text.slice(0, 500) : description || title;
                    return {
                        url: location.href,
                        title,
                        description,
                        headings: headingText(),
                        text,
                        summary: clean(summary)
                    };
                })()
                """,
                "returnByValue": True,
            },
        )
        value = result.get("result", {}).get("value", "{}")
        summary = json.loads(value) if isinstance(value, str) else value
        if isinstance(summary, dict):
            return {
                "url": str(summary.get("url") or ""),
                "title": str(summary.get("title") or ""),
                "description": str(summary.get("description") or ""),
                "headings": [str(item) for item in (summary.get("headings") or []) if str(item)],
                "text": str(summary.get("text") or ""),
                "summary": str(summary.get("summary") or ""),
            }
    except Exception as exc:
        logger.debug("Page summary unavailable: %s", exc)
    return {"url": "", "title": "", "description": "", "headings": [], "text": "", "summary": ""}


def _page_block_reason(page: Dict[str, Any]) -> str:
    title = str(page.get("title") or "").lower()
    text = str(page.get("text") or "").lower()
    description = str(page.get("description") or "").lower()
    page_text = f"{title} {description} {text}"
    challenge_markers = (
        "just a moment",
        "attention required",
        "security check",
        "security challenge",
        "verify you are human",
        "checking your browser",
        "captcha",
        "cloudflare",
    )
    if any(marker in page_text for marker in challenge_markers):
        return "The site presented an anti-bot or security challenge; page content is unavailable."
    return ""


async def _get_snapshot_elements(
    client: Client,
    session: CDPSession,
) -> List[Dict]:
    try:
        await client.execute("Runtime.enable")

        query = """
        Array.from(
            document.querySelectorAll(
                'button, a, input, textarea, select, [role="button"], [onclick]'
            )
        )
        .filter(el => el.offsetParent !== null)
        .map((el, i) => {
            const ref = '@e' + (i + 1);
            el.setAttribute('data-penzer-ref', ref);
            return {
                ref,
                tag: el.tagName.toLowerCase(),
                text: (
                    el.innerText?.trim() ||
                    el.value ||
                    el.placeholder ||
                    el.getAttribute('aria-label') ||
                    el.title ||
                    ''
                ),
                type: el.type || '',
                role: el.getAttribute('role') || ''
            };
        })
        """

        result = await client.execute(
            "Runtime.evaluate", {"expression": query, "returnByValue": True}
        )

        elements = result.get("result", {}).get("value") or []

        session.element_refs.clear()

        for element in elements:
            session.element_refs[element["ref"]] = element

        return elements

    except Exception as exc:
        logger.error("Snapshot failed: %s", exc)
        return []


async def _click_element(
    client: Client,
    ref: str,
    session: CDPSession,
) -> bool:
    try:
        element = session.element_refs.get(ref)

        if not element:
            return False

        import json

        selector = json.dumps(f'[data-penzer-ref="{element["ref"]}"]')
        query = f"""(() => {{
            const el = document.querySelector({selector});
            if (!el || !el.isConnected || el.offsetParent === null) return false;
            el.click();
            return true;
        }})()"""

        result = await client.execute(
            "Runtime.evaluate", {"expression": query, "returnByValue": True}
        )

        await asyncio.sleep(0.5)

        return bool(result.get("result", {}).get("value"))

    except Exception as exc:
        logger.error("Click failed: %s", exc)
        return False


async def _type_into_element(
    client: Client,
    ref: str,
    text: str,
    session: CDPSession,
) -> bool:
    try:
        element = session.element_refs.get(ref)

        if not element:
            return False

        import json

        selector = json.dumps(f'[data-penzer-ref="{element["ref"]}"]')
        value = json.dumps(text)

        query = f"""
        (() => {{
            const input = document.querySelector({selector});
            if (!input || !input.isConnected || input.offsetParent === null) return false;
            if (!(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement)) return false;
            input.focus();
            const prototype = input instanceof HTMLTextAreaElement
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, {value});
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """

        result = await client.execute(
            "Runtime.evaluate", {"expression": query, "returnByValue": True}
        )

        await asyncio.sleep(0.2)

        return bool(result.get("result", {}).get("value"))

    except Exception as exc:
        logger.error("Type failed: %s", exc)
        return False


async def _get_page_content(client: Client) -> str:
    try:
        result = await client.execute(
            "Runtime.evaluate",
            {
                "expression": "document.body ? document.body.innerText : ''",
                "returnByValue": True,
            },
        )

        return result.get("result", {}).get("value", "")[:5000]

    except Exception as exc:
        logger.error("Content fetch failed: %s", exc)
        return ""


async def _screenshot_page(client: Client) -> str:
    try:
        result = await client.execute("Page.captureScreenshot")
        return result.get("data", "")

    except Exception as exc:
        logger.error("Screenshot failed: %s", exc)
        return ""


async def _get_session_async(session_id: str) -> CDPSession:
    if session_id in _sessions:
        return _sessions[session_id]

    port = _find_free_port()

    process = _spawn_chrome(port)
    root_client = None
    try:
        time.sleep(3)
        debugger_url = _get_debugger_url(port, timeout=15)
        root_client = Client(debugger_url)
        await root_client.connect()
        target = await root_client.execute(
            "Target.createTarget", {"url": "about:blank"}
        )
        target_id = target["targetId"]
        attached = await root_client.execute(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        client = root_client.session(attached["sessionId"])
    except BaseException:
        if root_client is not None:
            try:
                await root_client.disconnect()
            except Exception:
                logger.debug("CDP disconnect failed during browser startup cleanup", exc_info=True)
        _kill_chrome(process.pid, process)
        raise

    session = CDPSession(
        session_id=session_id,
        client=client,
        root_client=root_client,
        target_id=target_id,
        port=port,
        process=process,
    )

    _sessions[session_id] = session
    _chrome_pids[session_id] = process.pid

    logger.info(
        "Created browser session %s on port %s",
        session_id,
        port,
    )

    return session


def _get_session(session_id: str) -> CDPSession:
    if session_id in _sessions:
        return _sessions[session_id]

    loop = _get_event_loop()

    return loop.run_until_complete(
        _get_session_async(session_id)
    )


async def _browser_impl(
    action: str,
    session_id: str,
    **kwargs,
) -> Dict:
    session = _sessions.get(session_id)
    if session is None:
        session = await _get_session_async(session_id)

    if not session.client:
        return error("Session not connected")

    session.action_count += 1

    try:

        if action == "search":
            query = str(kwargs.get("query", "")).strip()
            if not query:
                return error("Search query required")
            url = f"https://www.bing.com/search?{urlencode({'format': 'rss', 'q': query})}"
            if not await _navigate_to_url(session.client, url):
                return error(f"Search navigation failed for: {query}")
            session.url = url
            session.element_refs.clear()
            content = await _get_page_content(session.client)
            result_data = await session.client.execute(
                "Runtime.evaluate",
                {
                    "expression": """JSON.stringify(Array.from(document.querySelectorAll('item')).map(item => {
                        const value = name => item.querySelector(name)?.textContent?.trim() || '';
                        return {title: value('title'), url: value('link'), snippet: value('description'), date: value('pubDate')};
                    }).filter(item => item.title && item.url))""",
                    "returnByValue": True,
                },
            )
            raw_results = result_data.get("result", {}).get("value", "[]")
            try:
                search_results = json.loads(raw_results)
            except (json.JSONDecodeError, TypeError):
                search_results = []
            for item in search_results:
                if isinstance(item, dict):
                    item["government_domain"] = _is_government_domain(item.get("url", ""))
                    candidate_text = " ".join(
                        str(item.get(key, "")) for key in ("title", "url", "snippet")
                    ).lower()
                    query_terms = {
                        token for token in re.findall(r"[a-z0-9]+", query.lower())
                        if len(token) > 2 and token not in {"the", "and", "for", "official", "latest", "current"}
                    }
                    item["query_matches"] = sorted(
                        term for term in query_terms
                        if re.search(rf"\b{re.escape(term)}\b", candidate_text)
                        or any(term in token for token in re.findall(r"[a-z0-9]+", candidate_text) if len(token) > len(term))
                    )
                    item["relevance"] = len(item["query_matches"])
            search_results.sort(
                key=lambda item: (
                    int(item.get("relevance", 0)),
                    bool(item.get("government_domain")),
                ),
                reverse=True,
            )
            government_domain_candidates = [
                item for item in search_results
                if isinstance(item, dict) and item.get("government_domain")
            ]
            relevant_government_results = [
                item for item in government_domain_candidates
                if item.get("relevance", 0) >= 2
            ]
            page = await _get_page_metadata(session.client)
            return success(
                {
                    "query": query,
                    "url": page["url"] or url,
                    "title": page["title"],
                    "government_domain_candidates": government_domain_candidates,
                    "relevant_government_results": relevant_government_results,
                    "results": search_results,
                    "content": content,
                },
                f"Searched the web for {query}",
            )

        elif action == "open":

            url = kwargs.get("url") or kwargs.get("query", "")

            if not url:
                return error("URL required")
            if not _is_http_url(url):
                return error("Only complete HTTP or HTTPS URLs can be opened")

            ok = await _navigate_to_url(
                session.client,
                url,
            )

            if ok:
                page = await _get_page_metadata(session.client)
                summary = await _get_page_summary(session.client)
                final_url = summary.get("url") or page["url"] or url
                block_reason = _page_block_reason(summary)
                session.url = final_url
                session.element_refs.clear()

                return success(
                    {
                        "url": final_url,
                        "title": page["title"] or summary.get("title") or "",
                        "description": page["description"] or summary.get("description") or "",
                        "summary": summary.get("summary") or "",
                        "headings": summary.get("headings") or [],
                        "text": summary.get("text") or "",
                        "blocked": bool(block_reason),
                        "block_reason": block_reason,
                    },
                    f"Opened {final_url}",
                )

            return error(
                f"Failed to navigate to {url}"
            )


        elif action == "snapshot":

            elements = await _get_snapshot_elements(
                session.client,
                session,
            )

            return success(
                {
                    "url": session.url,
                    "elements": elements,
                    "element_count": len(elements),
                },
                f"Found {len(elements)} elements",
            )


        elif action == "click":

            ref = kwargs.get("ref", "")

            if not ref:
                return error("ref required")

            ok = await _click_element(
                session.client,
                ref,
                session,
            )

            if ok:
                return success(
                    {"ref": ref},
                    f"Clicked {ref}",
                )

            return error(
                f"Click failed: {ref}"
            )


        elif action == "type":

            ref = kwargs.get("ref", "")
            text = kwargs.get("text", "")

            if not ref or not text:
                return error(
                    "ref and text required"
                )

            ok = await _type_into_element(
                session.client,
                ref,
                text,
                session,
            )

            if ok:
                return success(
                    {"ref": ref},
                    f"Typed {len(text)} chars",
                )

            return error(
                f"Type failed: {ref}"
            )


        elif action == "screenshot":

            image = await _screenshot_page(
                session.client
            )

            return success(
                {"image": image},
                "Screenshot taken",
            )


        elif action == "content":

            summary = await _get_page_summary(session.client)
            content = summary.get("text") or await _get_page_content(session.client)
            page = await _get_page_metadata(session.client)
            block_reason = _page_block_reason(summary)
            if page["url"]:
                session.url = page["url"]

            return success(
                {
                    "url": page["url"] or session.url,
                    "title": page["title"] or summary.get("title") or "",
                    "description": page["description"] or summary.get("description") or "",
                    "content": content,
                    "text": content,
                    "summary": summary.get("summary") or content[:500],
                    "headings": summary.get("headings") or [],
                    "blocked": bool(block_reason),
                    "block_reason": block_reason,
                },
                f"Extracted {len(content)} chars",
            )


        elif action == "eval":

            js = kwargs.get("text", "")

            if not js:
                return error(
                    "text (JavaScript) required"
                )

            result = await session.client.execute(
                "Runtime.evaluate", {"expression": js, "returnByValue": True}
            )

            return success(
                {
                    "result": result
                    .get("result", {})
                    .get("value")
                },
                "Executed",
            )


        else:
            return error(
                f"Unknown action: {action}"
            )

    except Exception as exc:

        session.last_error = str(exc)

        logger.error(
            "Action failed: %s",
            exc,
        )

        return error(
            f"Action failed: {exc}"
        )


def browser(
    action: str,
    session_id: str = "default",
    **kwargs,
) -> Dict:
    """Main browser interface."""

    loop = _get_event_loop()

    return loop.run_until_complete(
        _browser_impl(
            action,
            session_id,
            **kwargs,
        )
    )


def browser_close(
    session_id: str = "default",
) -> Dict:
    """Close browser session."""

    if session_id not in _sessions:
        return warning(
            f"Session not found: {session_id}"
        )

    session = _sessions[session_id]

    try:

        if session.root_client:

            loop = _get_event_loop()

            loop.run_until_complete(
                session.root_client.disconnect()
            )

    except Exception as exc:
        logger.debug(
            "CDP close failed: %s",
            exc,
        )

    pid = _chrome_pids.get(session_id)

    if pid:
        _kill_chrome(pid, session.process)

    _sessions.pop(session_id, None)
    _chrome_pids.pop(session_id, None)

    return success(
        {"session_id": session_id},
        "Session closed",
    )


def browser_abort_direct(session_id: str = "default") -> Dict:
    """Stop a timed-out owned Chrome session without touching a live event loop."""
    session = _sessions.pop(session_id, None)
    pid = _chrome_pids.pop(session_id, None)
    process = session.process if session else None
    if process is None or process.poll() is not None:
        return warning(f"No running owned browser session: {session_id}")

    try:
        _kill_chrome(process.pid, process)
    except Exception as exc:
        logger.warning("Browser abort failed for %s: %s", session_id, exc)
        return error(f"Browser abort failed: {exc}")

    return success({"session_id": session_id, "pid": pid}, "Timed-out browser stopped")


def browser_close_all() -> Dict:
    """Close all browser sessions."""

    sessions = list(_sessions.keys())

    for session_id in sessions:
        browser_close(session_id)

    return success(
        {"closed": len(sessions)},
        f"Closed {len(sessions)} sessions",
    )


def browser_list() -> Dict:
    """List active sessions."""

    sessions = []

    for session_id, session in _sessions.items():

        sessions.append(
            {
                "id": session_id,
                "port": session.port,
                "url": session.url,
                "actions": session.action_count,
                "elements": len(session.element_refs),
            }
        )

    return success(
        {"sessions": sessions}
    )


def browser_info(
    session_id: str = "default",
) -> Dict:
    """Get session information."""

    if session_id not in _sessions:
        return error(
            f"Session not found: {session_id}"
        )

    session = _sessions[session_id]

    return success(
        {
            "session_id": session_id,
            "port": session.port,
            "url": session.url,
            "action_count": session.action_count,
            "last_error": session.last_error,
            "elements": len(session.element_refs),
        }
    )


# ------------------------------------------------------------------
# Compatibility API used by Penzer execution.py
# ------------------------------------------------------------------

def browser_direct(
    action: str,
    session_id: str = "default",
    **kwargs,
) -> Dict:
    """Direct browser interface for Penzer execution layer."""

    return browser(
        action=action,
        session_id=session_id,
        **kwargs,
    )


def browser_info_direct(
    session_id: str = "default",
) -> Dict:
    """Direct browser-info interface."""

    return browser_info(
        session_id=session_id
    )


def browser_close_direct(
    session_id: str = "default",
) -> Dict:
    """Direct browser-close interface."""

    return browser_close(
        session_id=session_id
    )