"""PENZER Browser Tool - Chrome DevTools Protocol via cdpify"""

import asyncio
import logging
import os
import subprocess
import time
from typing import Optional, Any, Dict, List
from dataclasses import dataclass, field

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
    client: Optional[Client] = None
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
        logger.error("Chrome verification failed: %s", exc)
        return False


def _spawn_chrome(port: int) -> subprocess.Popen:
    binary = os.getenv("PENZER_CHROME_BINARY", "google-chrome")

    if not _verify_chrome_binary(binary):
        raise RuntimeError(f"Chrome binary not found or broken: {binary}")

    try:
        return subprocess.Popen(
            [
                binary,
                "--headless=new",
                f"--remote-debugging-port={port}",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
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


def _kill_chrome(pid: int):
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), 15)
        else:
            os.kill(pid, 15)
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.debug("Chrome cleanup failed: %s", exc)


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create persistent event loop."""
    global _event_loop
    
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)
    
    return _event_loop


async def _navigate_to_url(client: Client, url: str) -> bool:
    try:
        await client.Page.enable()
        await client.Page.navigate(url=url)
        return True
    except Exception as exc:
        logger.error("Navigate failed: %s", exc)
        return False


async def _get_snapshot_elements(
    client: Client,
    session: CDPSession,
) -> List[Dict]:
    try:
        await client.Runtime.enable()

        query = """
        Array.from(
            document.querySelectorAll(
                'button, a, input, textarea, select, [role="button"], [onclick]'
            )
        )
        .filter(el => el.offsetParent !== null)
        .map((el, i) => ({
            ref: '@e' + (i + 1),
            tag: el.tagName.toLowerCase(),
            text: (
                el.textContent.trim() ||
                el.value ||
                el.placeholder ||
                el.title ||
                ''
            ),
            type: el.type || '',
            role: el.getAttribute('role') || ''
        }))
        """

        result = await client.Runtime.evaluate(expression=query)

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

        tag = json.dumps(element.get("tag", ""))
        text = json.dumps((element.get("text") or "")[:50])

        query = f"""
        (() => {{
            const tag = {tag};
            const text = {text};

            const el = Array.from(document.querySelectorAll(tag))
                .find(e =>
                    (e.textContent || '').includes(text) ||
                    e.value === text
                );

            if (!el) return false;

            el.click();
            return true;
        }})()
        """

        result = await client.Runtime.evaluate(expression=query)

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

        target = json.dumps(element.get("text") or "")
        value = json.dumps(text)

        query = f"""
        (() => {{
            const target = {target};

            const input = Array.from(
                document.querySelectorAll('input, textarea')
            ).find(el =>
                el.value === target ||
                el.placeholder === target ||
                el.getAttribute('aria-label') === target
            );

            if (!input) return false;

            input.focus();
            input.select();
            input.value = {value};

            input.dispatchEvent(
                new Event('input', {{ bubbles: true }})
            );

            input.dispatchEvent(
                new Event('change', {{ bubbles: true }})
            );

            return true;
        }})()
        """

        result = await client.Runtime.evaluate(expression=query)

        await asyncio.sleep(0.2)

        return bool(result.get("result", {}).get("value"))

    except Exception as exc:
        logger.error("Type failed: %s", exc)
        return False


async def _get_page_content(client: Client) -> str:
    try:
        result = await client.Runtime.evaluate(
            expression="document.body ? document.body.innerText : ''"
        )

        return result.get("result", {}).get("value", "")[:5000]

    except Exception as exc:
        logger.error("Content fetch failed: %s", exc)
        return ""


async def _screenshot_page(client: Client) -> str:
    try:
        result = await client.Page.captureScreenshot()
        return result.get("data", "")

    except Exception as exc:
        logger.error("Screenshot failed: %s", exc)
        return ""


async def _get_session_async(session_id: str) -> CDPSession:
    if session_id in _sessions:
        return _sessions[session_id]

    port = _find_free_port()

    process = _spawn_chrome(port)

    # Increased sleep — Chrome needs time to start up
    time.sleep(3)

    debugger_url = _get_debugger_url(port, timeout=15)

    client = Client(debugger_url)

    await client.connect()

    session = CDPSession(
        session_id=session_id,
        client=client,
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
    session = _get_session(session_id)

    if not session.client:
        return error("Session not connected")

    session.action_count += 1

    try:

        if action == "open":

            url = kwargs.get("url", "")

            if not url:
                return error("URL required")

            ok = await _navigate_to_url(
                session.client,
                url,
            )

            if ok:
                session.url = url

                return success(
                    {"url": url},
                    f"Opened {url}",
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

            content = await _get_page_content(
                session.client
            )

            return success(
                {"content": content},
                f"Extracted {len(content)} chars",
            )


        elif action == "eval":

            js = kwargs.get("text", "")

            if not js:
                return error(
                    "text (JavaScript) required"
                )

            result = await session.client.Runtime.evaluate(
                expression=js
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

        if session.client:

            loop = _get_event_loop()

            loop.run_until_complete(
                session.client.close()
            )

    except Exception as exc:
        logger.debug(
            "CDP close failed: %s",
            exc,
        )

    pid = _chrome_pids.get(session_id)

    if pid:
        _kill_chrome(pid)

    _sessions.pop(session_id, None)
    _chrome_pids.pop(session_id, None)

    return success(
        {"session_id": session_id},
        "Session closed",
    )


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