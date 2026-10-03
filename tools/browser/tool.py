"""PENZER Browser Tool - Chrome DevTools Protocol via cdpify"""

import asyncio
import atexit
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Any, Dict, List
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlparse
from xml.etree import ElementTree

import requests

try:
    from cdpify import Client
    from cdpify.events import RawCDPEvent
except ImportError as exc:
    raise ImportError("cdpify is required: pip install cdpify") from exc


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_sessions: Dict[str, "CDPSession"] = {}
_chrome_pids: Dict[str, int] = {}
_starting_processes: Dict[str, subprocess.Popen] = {}
_session_lock = threading.RLock()
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
    target_clients: Dict[str, Any] = field(default_factory=dict)
    target_sessions: Dict[str, str] = field(default_factory=dict)
    event_tasks: List[asyncio.Task] = field(default_factory=list)
    network_events: List[Dict[str, Any]] = field(default_factory=list)
    next_ref: int = 1
    console_messages: List[Dict[str, str]] = field(default_factory=list)
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


def _browser_profile_dir(session_id: str) -> Path:
    profile_root = Path(os.getenv(
        "PENZER_BROWSER_PROFILE_ROOT",
        Path(__file__).resolve().parents[2] / "data" / "browser_profiles",
    ))
    profile_name = quote(session_id, safe="._-") or "default"
    if profile_name in {".", ".."}:
        profile_name = f"_{profile_name.replace('.', 'dot')}"
    profile_dir = (profile_root / profile_name).resolve()
    personal_roots = (
        Path.home() / ".config" / "google-chrome",
        Path.home() / ".config" / "chromium",
        Path.home() / ".config" / "microsoft-edge",
        Path.home() / ".config" / "BraveSoftware" / "Brave-Browser",
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
        Path.home() / "Library" / "Application Support" / "Chromium",
        Path.home() / "Library" / "Application Support" / "Microsoft Edge",
        Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser",
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Google" / "Chrome" / "User Data",
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Microsoft" / "Edge" / "User Data",
    )
    if any(profile_dir == root.resolve() or root.resolve() in profile_dir.parents for root in personal_roots):
        raise ValueError("Browser profile root must not be inside a personal Chrome-family profile")
    return profile_dir


def _spawn_chrome(session_id: str) -> subprocess.Popen:
    for binary in _candidate_chrome_binaries():
        if _verify_chrome_binary(binary):
            break
    else:
        raise RuntimeError("Chrome binary not found or broken in common locations")

    profile_dir = _browser_profile_dir(session_id)
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    chrome_args = [
        binary,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    if os.getenv("PENZER_BROWSER_HEADLESS", "1").lower() not in {"0", "false", "no"}:
        chrome_args.append("--headless=new")

    try:
        return subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Chrome not found: {binary}") from exc


async def _get_debugger_url(profile_dir: str, timeout: int = 15) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    port_file = Path(profile_dir) / "DevToolsActivePort"

    while time.monotonic() < deadline:
        try:
            lines = (await asyncio.to_thread(port_file.read_text, encoding="ascii")).splitlines()
            port = int(lines[0])
            response = await asyncio.to_thread(
                requests.get, f"http://127.0.0.1:{port}/json/version", timeout=2,
            )
            response.raise_for_status()
            return port, response.json()["webSocketDebuggerUrl"]
        except (OSError, ValueError, IndexError, KeyError, requests.RequestException):
            await asyncio.sleep(0.1)

    raise RuntimeError(f"Chrome did not publish a debugger endpoint in {timeout}s")


def _kill_chrome(pid: int, process: Optional[subprocess.Popen] = None):
    if process is not None and process.poll() is not None:
        return

    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.debug("Chrome graceful cleanup failed: %s", exc)
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

    if process is not None:
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass

    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        elif process is not None:
            process.kill()
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.warning("Chrome force cleanup failed for %s: %s", pid, exc)
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
    finally:
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.error("Chrome process %s did not exit after SIGKILL", pid)


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create persistent event loop."""
    global _event_loop
    
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)
    
    return _event_loop


async def _navigate_to_url(
    client: Client,
    url: str,
    wait_until: str = "networkidle",
    timeout: float = 15,
) -> bool:
    try:
        await client.execute("Page.enable")
        await client.execute("Runtime.enable")
        navigation = await client.execute("Page.navigate", {"url": url})
        if navigation.get("errorText"):
            raise RuntimeError(navigation["errorText"])
        wait_until = wait_until.lower()
        if wait_until not in {"commit", "domcontentloaded", "load", "networkidle"}:
            raise ValueError("wait_until must be commit, domcontentloaded, load, or networkidle")
        if wait_until == "commit":
            return True
        deadline = time.monotonic() + max(0.1, min(timeout, 60))
        previous_count = -1
        quiet_samples = 0
        while time.monotonic() < deadline:
            state = await client.execute(
                "Runtime.evaluate",
                {
                    "expression": "({state: document.readyState, resources: performance.getEntriesByType('resource').length})",
                    "returnByValue": True,
                },
            )
            page_state = state.get("result", {}).get("value") or {}
            resource_count = page_state.get("resources", -1)
            quiet_samples = quiet_samples + 1 if resource_count == previous_count else 0
            previous_count = resource_count
            ready = page_state.get("state") == "complete" if wait_until == "load" else page_state.get("state") in {"interactive", "complete"}
            quiet = wait_until != "networkidle" or quiet_samples >= 2
            if ready and quiet:
                break
            await asyncio.sleep(0.25)
        return True
    except Exception as exc:
        logger.error("Navigate failed: %s", exc)
        return False


async def _wait_for_page(client: Client, selector: str = "", text: str = "", timeout: float = 10) -> bool:
    deadline = time.monotonic() + max(0.1, min(timeout, 60))
    while time.monotonic() < deadline:
        expression = "({state: document.readyState, selector: false, text: false})"
        if selector or text:
            expression = """(() => ({
                state: document.readyState,
                selector: %s,
                text: %s
            }))()""" % (
                f"!!document.querySelector({json.dumps(selector)})" if selector else "true",
                f"(document.body?.innerText || '').includes({json.dumps(text)})" if text else "true",
            )
        result = await client.execute(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        state = result.get("result", {}).get("value") or {}
        if state.get("state") in {"interactive", "complete"} and state.get("selector") and state.get("text"):
            return True
        await asyncio.sleep(0.2)
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
                "expression": "JSON.stringify({url: location.href, title: document.title, description: document.querySelector('meta[name=\"description\"]')?.content || document.querySelector('meta[property=\"og:description\"]')?.content || '', canonical: document.querySelector('link[rel=\"canonical\"]')?.href || '', language: document.documentElement.lang || '', author: document.querySelector('meta[name=\"author\"]')?.content || '', published: document.querySelector('meta[property=\"article:published_time\"]')?.content || document.querySelector('time[datetime]')?.dateTime || ''})",
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
                "language": str(metadata.get("language") or ""),
                "author": str(metadata.get("author") or ""),
                "published": str(metadata.get("published") or ""),
            }
    except Exception as exc:
        logger.debug("Page metadata unavailable: %s", exc)
    return {
        "url": "", "title": "", "description": "", "canonical": "",
        "language": "", "author": "", "published": "",
    }


async def _get_page_summary(client: Client) -> Dict[str, Any]:
    try:
        result = await client.execute(
            "Runtime.evaluate",
            {
                "expression": """
                (() => {
                    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const headingText = () => Array.from(document.querySelectorAll('h1, h2, h3, h4')).map(el => clean(el.innerText)).filter(Boolean).slice(0, 8);
                    const text = clean(document.body ? document.body.innerText : '').slice(0, 10000);
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
) -> Dict[str, Any]:
    try:
        await client.execute("Runtime.enable")

        query = f"""
        (() => {{
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            const controls = Array.from(document.querySelectorAll(
                'a[href], button, input, textarea, select, [role], [onclick], [contenteditable="true"]'
            )).filter(el => el.getClientRects().length || el.type === 'file');
            let next = {session.next_ref};
            const elements = controls.slice(0, 100).map(el => {{
                let ref = el.getAttribute('data-penzer-ref');
                if (!ref) {{ ref = '@e' + next++; el.setAttribute('data-penzer-ref', ref); }}
                const role = el.getAttribute('role') || {{
                    A: 'link', BUTTON: 'button', INPUT: el.type === 'checkbox' ? 'checkbox' : 'textbox',
                    TEXTAREA: 'textbox', SELECT: 'combobox'
                }}[el.tagName] || (el.isContentEditable ? 'textbox' : '');
                return {{
                    ref, role,
                    name: clean(el.getAttribute('aria-label') || el.getAttribute('alt') || el.innerText || el.value || el.placeholder || el.title).slice(0, 160),
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    value: el.type === 'password' ? '[redacted]' : (el.value || '').slice(0, 120),
                    checked: 'checked' in el ? el.checked : undefined,
                    disabled: !!el.disabled,
                    required: !!el.required,
                    href: el.href || undefined
                }};
            }});
            const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
                .filter(el => el.getClientRects().length).slice(0, 12)
                .map(el => ({{level: Number(el.tagName[1]), text: clean(el.innerText)}}));
            const landmarks = Array.from(document.querySelectorAll('main,nav,header,footer,aside,[role="main"],[role="navigation"],[role="search"]'))
                .filter(el => el.getClientRects().length).slice(0, 10)
                .map(el => ({{role: el.getAttribute('role') || el.tagName.toLowerCase(), name: clean(el.getAttribute('aria-label') || el.innerText).slice(0, 100)}}));
            return {{title: document.title, url: location.href, headings, landmarks, elements, nextRef: next}};
        }})()
        """

        result = await client.execute(
            "Runtime.evaluate", {"expression": query, "returnByValue": True}
        )

        snapshot = result.get("result", {}).get("value") or {}
        if isinstance(snapshot, list):
            snapshot = {"elements": snapshot}
        elements = snapshot.get("elements") or []

        session.element_refs.clear()

        for element in elements:
            session.element_refs[element["ref"]] = element
        session.next_ref = int(snapshot.get("nextRef", session.next_ref))

        return {
            "url": str(snapshot.get("url") or session.url),
            "title": str(snapshot.get("title") or ""),
            "headings": snapshot.get("headings") or [],
            "landmarks": snapshot.get("landmarks") or [],
            "elements": elements,
        }

    except Exception as exc:
        logger.error("Snapshot failed: %s", exc)
        return {"url": session.url, "title": "", "headings": [], "landmarks": [], "elements": []}


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
            el.scrollIntoView({{block: 'center', inline: 'center'}});
            const rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
        }})()"""

        result = await client.execute(
            "Runtime.evaluate", {"expression": query, "returnByValue": True}
        )
        point = result.get("result", {}).get("value")
        if not isinstance(point, dict):
            return False

        await client.execute("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": point["x"], "y": point["y"],
        })
        await client.execute("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": point["x"], "y": point["y"],
            "button": "left", "clickCount": 1,
        })
        await client.execute("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": point["x"], "y": point["y"],
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(0.5)

        return True

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

        query = f"""
        (() => {{
            const input = document.querySelector({selector});
            if (!input || !input.isConnected || input.offsetParent === null) return false;
            if (!(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement || input.isContentEditable)) return false;
            input.focus();
            if (input.isContentEditable) {{
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(input);
                selection.removeAllRanges();
                selection.addRange(range);
            }} else {{
                input.select();
            }}
            return true;
        }})()
        """

        result = await client.execute(
            "Runtime.evaluate", {"expression": query, "returnByValue": True}
        )

        if not result.get("result", {}).get("value"):
            return False

        await client.execute("Input.insertText", {"text": text})
        await asyncio.sleep(0.2)

        return True

    except Exception as exc:
        logger.error("Type failed: %s", exc)
        return False


async def _element_point(client: Client, ref: str, session: CDPSession) -> Optional[Dict[str, float]]:
    element = session.element_refs.get(ref)
    if not element:
        return None
    selector = json.dumps(f'[data-penzer-ref="{ref}"]')
    result = await client.execute("Runtime.evaluate", {
        "expression": f"""(() => {{
            const el = document.querySelector({selector});
            if (!el || !el.isConnected || !el.getClientRects().length) return null;
            el.scrollIntoView({{block: 'center', inline: 'center'}});
            const rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
        }})()""",
        "returnByValue": True,
    })
    point = result.get("result", {}).get("value")
    return point if isinstance(point, dict) else None


async def _hover_element(client: Client, ref: str, session: CDPSession) -> bool:
    point = await _element_point(client, ref, session)
    if point is None:
        return False
    await client.execute("Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": point["x"], "y": point["y"],
    })
    return True


async def _scroll_page(client: Client, direction: str, amount: int, ref: str = "", session: Optional[CDPSession] = None) -> bool:
    if ref and session is not None:
        point = await _element_point(client, ref, session)
        if point is None:
            return False
        x, y = point["x"], point["y"]
    else:
        viewport = await client.execute("Runtime.evaluate", {
            "expression": "({x: innerWidth / 2, y: innerHeight / 2})",
            "returnByValue": True,
        })
        point = viewport.get("result", {}).get("value") or {"x": 640, "y": 480}
        x, y = point["x"], point["y"]
    delta = max(-3000, min(3000, amount)) * (1 if direction.lower() in {"down", "right"} else -1)
    await client.execute("Input.dispatchMouseEvent", {
        "type": "mouseWheel", "x": x, "y": y,
        "deltaX": delta if direction.lower() in {"left", "right"} else 0,
        "deltaY": delta if direction.lower() in {"up", "down"} else 0,
    })
    return True


async def _find_elements(client: Client, session: CDPSession, text: str, limit: int = 20) -> List[Dict[str, Any]]:
    needle = text.strip().lower()
    if not needle:
        return []
    snapshot = await _get_snapshot_elements(client, session)
    matches = [
        element for element in snapshot["elements"]
        if needle in str(element.get("name") or "").lower()
    ]
    remaining = max(0, min(limit, 50) - len(matches))
    if remaining:
        expression = f"""(() => {{
            const needle = {json.dumps(needle)};
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            let next = {session.next_ref};
            const matches = Array.from(document.querySelectorAll('h1,h2,h3,p,li,td,th,summary,label'))
                .filter(el => el.getClientRects().length && clean(el.innerText).toLowerCase().includes(needle))
                .slice(0, {remaining}).map(el => {{
                    let ref = el.getAttribute('data-penzer-ref');
                    if (!ref) {{ ref = '@e' + next++; el.setAttribute('data-penzer-ref', ref); }}
                    return {{ref, role: el.getAttribute('role') || el.tagName.toLowerCase(), name: clean(el.innerText).slice(0, 160), tag: el.tagName.toLowerCase()}};
                }});
            return {{matches, nextRef: next}};
        }})()"""
        result = await client.execute("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
        })
        found = result.get("result", {}).get("value") or {}
        text_matches = found.get("matches", []) if isinstance(found, dict) else []
        session.next_ref = int(found.get("nextRef", session.next_ref)) if isinstance(found, dict) else session.next_ref
        for element in text_matches:
            session.element_refs[element["ref"]] = element
        matches.extend(text_matches)
    return matches[:max(1, min(limit, 50))]


async def _select_option(client: Client, ref: str, value: str, session: CDPSession) -> bool:
    element = session.element_refs.get(ref)
    if not element or element.get("tag") != "select":
        return False
    selector = json.dumps(f'[data-penzer-ref="{ref}"]')
    wanted = json.dumps(value)
    result = await client.execute("Runtime.evaluate", {
        "expression": f"""(() => {{
            const select = document.querySelector({selector});
            if (!select || !select.isConnected) return false;
            const option = Array.from(select.options).find(item => item.value === {wanted} || item.label === {wanted});
            if (!option) return false;
            select.value = option.value;
            select.dispatchEvent(new Event('input', {{bubbles: true}}));
            select.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true;
        }})()""",
        "returnByValue": True,
    })
    return bool(result.get("result", {}).get("value"))


async def _upload_file(client: Client, ref: str, filepath: str, session: CDPSession) -> bool:
    if ref not in session.element_refs or session.element_refs[ref].get("tag") != "input":
        return False
    path = Path(filepath).expanduser().resolve(strict=True)
    if not path.is_file():
        return False
    document = await client.execute("DOM.getDocument", {"depth": 0})
    root_id = document.get("root", {}).get("nodeId")
    if not root_id:
        return False
    found = await client.execute("DOM.querySelector", {
        "nodeId": root_id,
        "selector": f"input[type=file][data-penzer-ref={json.dumps(ref)}]",
    })
    node_id = found.get("nodeId")
    if not node_id:
        return False
    await client.execute("DOM.setFileInputFiles", {
        "nodeId": node_id,
        "files": [str(path)],
    })
    return True


async def _refresh_tabs(session: CDPSession) -> List[Dict[str, str]]:
    if session.root_client is None:
        return []
    result = await session.root_client.execute("Target.getTargets")
    targets = [target for target in result.get("targetInfos", []) if target.get("type") == "page"]
    live_ids = {target["targetId"] for target in targets}
    for target_id in list(session.target_clients):
        if target_id not in live_ids:
            session.target_clients.pop(target_id, None)
            session.target_sessions.pop(target_id, None)
    for target in targets:
        target_id = target["targetId"]
        if target_id not in session.target_clients:
            attached = await session.root_client.execute(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session.target_sessions[target_id] = attached["sessionId"]
            session.target_clients[target_id] = session.root_client.session(attached["sessionId"])
            await session.target_clients[target_id].execute("Page.enable")
            await session.target_clients[target_id].execute("Runtime.enable")
            await session.target_clients[target_id].execute("Network.enable")
    if session.target_id not in live_ids and targets:
        session.target_id = targets[0]["targetId"]
        session.client = session.target_clients[session.target_id]
        session.element_refs.clear()
    return [{
        "id": target["targetId"],
        "url": target.get("url", ""),
        "title": target.get("title", ""),
        "active": target["targetId"] == session.target_id,
    } for target in targets]


async def _capture_events(root_client: Client, session: CDPSession, event_name: str) -> None:
    try:
        async for received in root_client.listen_all(event_name, RawCDPEvent):
            event = received.value
            params = event.params
            session_id = received.session_id
            target_id = next((
                target_id for target_id, value in session.target_sessions.items()
                if value == session_id
            ), "")
            if event_name == "Runtime.consoleAPICalled":
                args = params.get("args", [])
                message = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in args)
                session.console_messages.append({
                    "type": str(params.get("type", "log")),
                    "text": message[:500],
                    "url": str((params.get("stackTrace", {}).get("callFrames") or [{}])[0].get("url", "")),
                })
                del session.console_messages[:-50]
            else:
                session.network_events.append({
                    "event": event_name.rsplit(".", 1)[-1],
                    "target": target_id,
                    "url": str(params.get("request", {}).get("url") or params.get("response", {}).get("url") or "")[:500],
                    "method": str(params.get("request", {}).get("method", "")),
                    "status": params.get("response", {}).get("status"),
                    "error": str(params.get("errorText", "")),
                })
                del session.network_events[:-100]
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("CDP event stream ended: %s", event_name, exc_info=True)


async def _search_web(query: str) -> Dict[str, Any]:
    query = query.strip()
    if not query:
        return error("Search query required")
    url = f"https://www.bing.com/search?{urlencode({'format': 'rss', 'q': query})}"
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=15)
        response.raise_for_status()
        rss = ElementTree.fromstring(response.content)
    except (requests.RequestException, ElementTree.ParseError) as exc:
        return error(f"Search request failed: {exc}")

    results = []
    for result in rss.iter():
        if result.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        fields = {
            child.tag.rsplit("}", 1)[-1].lower(): (child.text or "").strip()
            for child in result
        }
        if fields.get("title") and fields.get("link"):
            results.append({
                "title": fields["title"],
                "url": fields["link"],
                "snippet": fields.get("description", ""),
                "date": fields.get("pubdate", ""),
            })

    query_terms = {
        token for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) > 2 and token not in {"the", "and", "for", "official", "latest", "current"}
    }
    for item in results:
        item["government_domain"] = _is_government_domain(item["url"])
        candidate_text = " ".join(str(item.get(key, "")) for key in ("title", "url", "snippet")).lower()
        item["query_matches"] = sorted(
            term for term in query_terms
            if re.search(rf"\b{re.escape(term)}\b", candidate_text)
            or any(term in token for token in re.findall(r"[a-z0-9]+", candidate_text) if len(token) > len(term))
        )
        item["relevance"] = len(item["query_matches"])
    results.sort(
        key=lambda item: (int(item.get("relevance", 0)), bool(item.get("government_domain"))),
        reverse=True,
    )
    government = [item for item in results if item.get("government_domain")]
    return success({
        "query": query,
        "url": url,
        "title": "Bing search results",
        "government_domain_candidates": government,
        "relevant_government_results": [item for item in government if item.get("relevance", 0) >= 2],
        "results": results,
        "content": "\n".join(item["snippet"] for item in results if item["snippet"]),
    }, f"Searched the web for {query}")


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
    with _session_lock:
        cached = _sessions.get(session_id)
    if cached is not None:
        if cached.process is None or cached.process.poll() is None:
            return cached
        with _session_lock:
            if _sessions.get(session_id) is cached:
                _sessions.pop(session_id, None)
                _chrome_pids.pop(session_id, None)
        for task in cached.event_tasks:
            task.cancel()
        if cached.root_client is not None:
            try:
                await cached.root_client.disconnect()
            except Exception:
                logger.debug("CDP disconnect failed for dead session", exc_info=True)

    profile_dir = _browser_profile_dir(session_id)
    process = _spawn_chrome(session_id)
    with _session_lock:
        _starting_processes[session_id] = process
        _chrome_pids[session_id] = process.pid
    root_client = None
    try:
        port, debugger_url = await _get_debugger_url(str(profile_dir), timeout=15)
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
        await client.execute("Page.enable")
        await client.execute("Runtime.enable")
        await client.execute("Network.enable")
    except BaseException:
        try:
            if root_client is not None:
                await asyncio.wait_for(asyncio.shield(root_client.disconnect()), timeout=3)
        except BaseException:
            logger.debug("CDP disconnect failed during browser startup cleanup", exc_info=True)
        finally:
            _kill_chrome(process.pid, process)
            with _session_lock:
                if _starting_processes.get(session_id) is process:
                    _starting_processes.pop(session_id, None)
                    _chrome_pids.pop(session_id, None)
        raise

    session = CDPSession(
        session_id=session_id,
        client=client,
        root_client=root_client,
        target_id=target_id,
        port=port,
        process=process,
        target_clients={target_id: client},
        target_sessions={target_id: attached["sessionId"]},
    )
    for event_name in (
        "Runtime.consoleAPICalled",
        "Network.responseReceived",
        "Network.loadingFailed",
    ):
        session.event_tasks.append(asyncio.create_task(
            _capture_events(root_client, session, event_name)
        ))

    with _session_lock:
        if _starting_processes.get(session_id) is not process:
            aborted = True
        else:
            aborted = False
            _sessions[session_id] = session
            _chrome_pids[session_id] = process.pid
            _starting_processes.pop(session_id, None)
    if aborted:
        for task in session.event_tasks:
            task.cancel()
        await asyncio.gather(*session.event_tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(root_client.disconnect(), timeout=3)
        except Exception:
            logger.debug("CDP disconnect failed after startup abort", exc_info=True)
        _kill_chrome(process.pid, process)
        raise RuntimeError("Browser startup was aborted")

    logger.info(
        "Created browser session %s on port %s",
        session_id,
        port,
    )

    return session


async def _browser_impl(
    action: str,
    session_id: str,
    **kwargs,
) -> Dict:
    if action == "search":
        return await _search_web(str(kwargs.get("query", "")))
    if action == "sessions":
        return browser_list()

    session = await _get_session_async(session_id)

    if not session.client:
        return error("Session not connected")

    session.action_count += 1

    try:
        await _refresh_tabs(session)

        if action == "open":

            url = kwargs.get("url") or kwargs.get("query", "")

            if not url:
                return error("URL required")
            if not _is_http_url(url):
                return error("Only complete HTTP or HTTPS URLs can be opened")

            ok = await _navigate_to_url(
                session.client,
                url,
                wait_until=str(kwargs.get("wait_until", "networkidle")),
                timeout=float(kwargs.get("timeout", 15)),
            )
            if ok and (kwargs.get("selector") or kwargs.get("text")):
                ok = await _wait_for_page(
                    session.client,
                    selector=str(kwargs.get("selector") or ""),
                    text=str(kwargs.get("text") or ""),
                    timeout=float(kwargs.get("timeout", 15)),
                )

            if ok:
                page = await _get_page_metadata(session.client)
                summary = await _get_page_summary(session.client)
                final_url = summary.get("url") or page["url"] or url
                block_reason = _page_block_reason(summary)
                session.url = final_url
                session.element_refs.clear()
                tabs = await _refresh_tabs(session)

                return success(
                    {
                        "url": final_url,
                        "title": page["title"] or summary.get("title") or "",
                        "description": page["description"] or summary.get("description") or "",
                        "canonical": page.get("canonical", ""),
                        "summary": summary.get("summary") or "",
                        "headings": summary.get("headings") or [],
                        "text": summary.get("text") or "",
                        "blocked": bool(block_reason),
                        "block_reason": block_reason,
                        "tabs": tabs,
                    },
                    f"Opened {final_url}",
                )

            return error(
                f"Failed to navigate to {url}"
            )


        elif action == "snapshot":

            snapshot = await _get_snapshot_elements(
                session.client,
                session,
            )

            return success(
                {**snapshot, "element_count": len(snapshot["elements"])},
                f"Snapshot: {len(snapshot['elements'])} controls",
            )


        elif action == "find":
            text = str(kwargs.get("text", kwargs.get("query", "")))
            matches = await _find_elements(
                session.client, session, text, int(kwargs.get("limit", 20))
            )
            return success({"matches": matches}, f"Found {len(matches)} matching elements")


        elif action == "scroll":
            direction = str(kwargs.get("direction", "down"))
            amount = int(kwargs.get("amount", 600))
            ok = await _scroll_page(
                session.client, direction, amount,
                str(kwargs.get("ref", "")), session,
            )
            return success({"direction": direction, "amount": amount}, "Scrolled") if ok else error("Scroll target not found")


        elif action == "hover":
            ref = str(kwargs.get("ref", ""))
            if not ref:
                return error("ref required")
            return success({"ref": ref}, f"Hovered {ref}") if await _hover_element(session.client, ref, session) else error(f"Hover failed: {ref}")


        elif action == "select":
            ref = str(kwargs.get("ref", ""))
            value = str(kwargs.get("value", kwargs.get("text", "")))
            if not ref or not value:
                return error("ref and value required")
            return success({"ref": ref, "value": value}, "Selected option") if await _select_option(session.client, ref, value, session) else error(f"Select failed: {ref}")


        elif action == "wait":
            ready = await _wait_for_page(
                session.client,
                selector=str(kwargs.get("selector", "")),
                text=str(kwargs.get("text", "")),
                timeout=float(kwargs.get("timeout", 10)),
            )
            return success({"ready": ready, "url": session.url}, "Page condition met" if ready else "Wait timed out")


        elif action == "tabs":
            operation = str(kwargs.get("operation", "list")).lower()
            tabs = await _refresh_tabs(session)
            if operation == "list":
                return success({"tabs": tabs, "active": session.target_id}, f"{len(tabs)} tabs")
            if operation == "new":
                url = str(kwargs.get("url", "about:blank"))
                if url != "about:blank" and not _is_http_url(url):
                    return error("New tab URL must be HTTP(S) or about:blank")
                created = await session.root_client.execute("Target.createTarget", {"url": url})
                target_id = created.get("targetId")
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    tabs = await _refresh_tabs(session)
                    if target_id in session.target_clients:
                        break
                    await asyncio.sleep(0.05)
                if target_id in session.target_clients:
                    session.target_id = target_id
                    session.client = session.target_clients[target_id]
                    session.element_refs.clear()
                    tabs = await _refresh_tabs(session)
                return success({"tabs": tabs, "active": target_id}, "Tab created")
            target_id = str(kwargs.get("target_id", kwargs.get("tab_id", "")))
            if operation == "switch":
                if target_id not in session.target_clients:
                    return error("Unknown tab; use tabs operation=list to inspect tabs")
                session.target_id = target_id
                session.client = session.target_clients[target_id]
                session.element_refs.clear()
                session.url = next((tab["url"] for tab in tabs if tab["id"] == target_id), "")
                return success({"active": target_id, "tabs": tabs}, "Tab selected")
            if operation == "close":
                if target_id not in session.target_clients:
                    return error("Unknown tab")
                if len(tabs) <= 1:
                    return error("Cannot close the last browser tab")
                await session.root_client.execute("Target.closeTarget", {"targetId": target_id})
                tabs = await _refresh_tabs(session)
                session.url = next((tab["url"] for tab in tabs if tab["id"] == session.target_id), "")
                return success({"tabs": tabs, "active": session.target_id}, "Tab closed")
            return error("tabs operation must be list, new, switch, or close")


        elif action == "metadata":
            metadata = await _get_page_metadata(session.client)
            session.url = metadata.get("url") or session.url
            return success(metadata, "Page metadata")


        elif action == "upload":
            ref = str(kwargs.get("ref", ""))
            filepath = str(kwargs.get("filepath", ""))
            if not ref or not filepath:
                return error("ref and filepath required")
            if await _upload_file(session.client, ref, filepath, session):
                return success({"ref": ref, "filename": Path(filepath).name}, "File selected for upload")
            return error("Upload failed; use a file input ref and an existing local file")


        elif action == "downloads":
            profile_dir = _browser_profile_dir(session_id)
            download_dir = profile_dir / "downloads"
            operation = str(kwargs.get("operation", "list")).lower()
            if operation == "enable":
                download_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                await session.root_client.execute("Browser.setDownloadBehavior", {
                    "behavior": "allow", "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                })
                return success({"directory": str(download_dir)}, "Downloads enabled")
            if operation != "list":
                return error("downloads operation must be list or enable")
            files = []
            if download_dir.is_dir():
                for path in sorted(download_dir.iterdir(), key=lambda item: item.name)[-50:]:
                    if path.is_file():
                        stat = path.stat()
                        files.append({"name": path.name, "size": stat.st_size})
            return success({"directory": str(download_dir), "files": files}, f"{len(files)} downloads")


        elif action == "diagnostics":
            result = await session.client.execute("Runtime.evaluate", {
                "expression": "JSON.stringify(performance.getEntriesByType('resource').slice(-30).map(r => ({url:r.name, type:r.initiatorType, duration:Math.round(r.duration), size:r.transferSize || 0})))",
                "returnByValue": True,
            })
            raw = result.get("result", {}).get("value", "[]")
            try:
                resources = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                resources = []
            return success({
                "url": session.url,
                "console": session.console_messages[-20:],
                "network_events": session.network_events[-30:],
                "resources": resources,
                "last_error": session.last_error,
            }, "Browser diagnostics")


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
                tabs = await _refresh_tabs(session)
                return success(
                    {"ref": ref, "tabs": tabs},
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


        elif action == "press":

            key = str(kwargs.get("key", "")).strip()
            if not key:
                return error("key required")

            await session.client.execute(
                "Input.dispatchKeyEvent", {"type": "keyDown", "key": key}
            )
            await session.client.execute(
                "Input.dispatchKeyEvent", {"type": "keyUp", "key": key}
            )
            return success({"key": key}, f"Pressed {key}")


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

    with _session_lock:
        session = _sessions.get(session_id)
        starting = session_id in _starting_processes
        pid = _chrome_pids.get(session_id)
    if session is None and not starting:
        return warning(
            f"Session not found: {session_id}"
        )
    if session is None:
        return browser_abort_direct(session_id)
    loop = _get_event_loop()
    try:
        loop.run_until_complete(_close_session_async(session))
    except Exception as exc:
        logger.debug("Browser close failed: %s", exc)
    finally:
        if pid or session.process is not None:
            _kill_chrome(pid or session.process.pid, session.process)
        with _session_lock:
            if _sessions.get(session_id) is session:
                _sessions.pop(session_id, None)
                _chrome_pids.pop(session_id, None)

    return success(
        {"session_id": session_id},
        "Session closed",
    )


async def _close_session_async(session: CDPSession) -> None:
    for task in session.event_tasks:
        task.cancel()
    if session.event_tasks:
        await asyncio.gather(*session.event_tasks, return_exceptions=True)
    if session.root_client is not None:
        await asyncio.wait_for(session.root_client.disconnect(), timeout=3)


def _cleanup_owned_chrome() -> None:
    with _session_lock:
        sessions = list(_sessions.items())
        starting = list(_starting_processes.items())
        pids = dict(_chrome_pids)
    for session_id, session in sessions:
        pid = pids.get(session_id)
        process = session.process
        if pid or process is not None:
            try:
                _kill_chrome(pid or process.pid, process)
            except Exception:
                logger.exception("Failed to clean up owned browser session %s", session_id)
    for session_id, process in starting:
        try:
            _kill_chrome(process.pid, process)
        except Exception:
            logger.exception("Failed to clean up starting browser session %s", session_id)
    with _session_lock:
        _sessions.clear()
        _chrome_pids.clear()
        _starting_processes.clear()


atexit.register(_cleanup_owned_chrome)


def browser_abort_direct(session_id: str = "default") -> Dict:
    """Stop a timed-out owned Chrome session without touching a live event loop."""
    with _session_lock:
        session = _sessions.pop(session_id, None)
        process = session.process if session else _starting_processes.pop(session_id, None)
        pid = _chrome_pids.pop(session_id, None)
    if session is not None and _event_loop is not None and not _event_loop.is_closed():
        for task in session.event_tasks:
            _event_loop.call_soon_threadsafe(task.cancel)
    if process is None and pid is None:
        return warning(f"No running owned browser session: {session_id}")
    if process is not None and process.poll() is not None:
        return warning(f"No running owned browser session: {session_id}")

    try:
        _kill_chrome(pid or process.pid, process)
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

    with _session_lock:
        sessions = [
            {
                "id": session_id,
                "port": session.port,
                "url": session.url,
                "actions": session.action_count,
                "elements": len(session.element_refs),
            }
            for session_id, session in _sessions.items()
        ]

    return success(
        {"sessions": sessions}
    )


def browser_info(
    session_id: str = "default",
) -> Dict:
    """Get session information."""

    with _session_lock:
        session = _sessions.get(session_id)
    if session is None:
        return error(f"Session not found: {session_id}")

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