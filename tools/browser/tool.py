"""Lightweight Selenium browser harness for the Penzer agent.

WebDriver owns navigation and DOM interaction. Browser events are collected
through WebDriver's logging surface when available, with CDP used only for
Chromium-specific event and storage helpers. Neither protocol is exposed to
the agent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tools.executor import confirm_action
from tools.standards import error, success, warning

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSION_ROOT = PROJECT_ROOT / "data" / "browser_sessions"
DOWNLOAD_ROOT = PROJECT_ROOT / "data" / "browser_downloads"
MAX_OBSERVE_ELEMENTS = 80
MAX_TEXT = 500
SAFE_SCHEMES = {"http", "https"}
DEFAULT_DEBUGGER_ADDRESS = "127.0.0.1:9222"
CHROME_BINARY = os.getenv("PENZER_CHROME_BINARY", "google-chrome")
CHROME_USER_DATA_DIR = os.getenv(
    "PENZER_CHROME_USER_DATA_DIR", str(Path.home() / ".config" / "google-chrome-penzer")
)
CHROME_PROFILE_DIRECTORY = os.getenv("PENZER_CHROME_PROFILE", "Default")


@dataclass
class BrowserState:
    element_ids: dict[str, str] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    bidi_available: bool = False
    cdp_available: bool = False
    attached: bool = False
    login_confirmed: bool = False
    original_handle: str | None = None
    background_handle: str | None = None


_drivers: dict[str, webdriver.Chrome] = {}
_states: dict[str, BrowserState] = {}
_lock = threading.RLock()


def _session_dir(session_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "default"
    path = SESSION_ROOT / safe_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_dir(session_id: str) -> Path:
    path = DOWNLOAD_ROOT / re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state(session_id: str) -> BrowserState:
    return _states.setdefault(session_id, BrowserState())


def _safe_url(url: str, allowed_hosts: list[str] | None = None) -> str | None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in SAFE_SCHEMES or not parsed.netloc:
        return None
    if allowed_hosts:
        hostname = (parsed.hostname or "").lower()
        permitted = {str(host).lower().lstrip(".") for host in allowed_hosts}
        if hostname not in permitted and not any(hostname.endswith("." + host) for host in permitted):
            return None
    return parsed.geturl()


def _load_persisted_state(driver: webdriver.Chrome, session_id: str) -> None:
    state_file = _session_dir(session_id) / "state.json"
    if not state_file.exists():
        return
    try:
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        url = _safe_url(saved.get("url"))
        if url:
            driver.get(url)
        for cookie in saved.get("cookies", []):
            try:
                driver.add_cookie(cookie)
            except WebDriverException:
                continue
        if saved.get("storage"):
            driver.execute_script(
                "for (const [key, value] of Object.entries(arguments[0])) "
                "localStorage.setItem(key, value);",
                saved["storage"],
            )
    except Exception:
        logger.debug("Could not restore browser session %s", session_id, exc_info=True)


def _persist_state(driver: webdriver.Chrome, session_id: str) -> None:
    try:
        storage = driver.execute_script("return Object.fromEntries(Object.entries(localStorage));")
        payload = {
            "url": driver.current_url if _safe_url(driver.current_url) else "",
            "cookies": driver.get_cookies(),
            "storage": storage if isinstance(storage, dict) else {},
        }
        (_session_dir(session_id) / "state.json").write_text(
            json.dumps(payload, ensure_ascii=True), encoding="utf-8"
        )
    except Exception:
        logger.debug("Could not persist browser session %s", session_id, exc_info=True)


def _prepare_background_target(driver: webdriver.Chrome, state: BrowserState) -> None:
    """Keep automation out of the user's active tab and window focus."""
    state.original_handle = driver.current_window_handle
    target = driver.execute_cdp_cmd(
        "Target.createTarget", {"url": "about:blank", "background": True}
    )
    handle = target.get("targetId") if isinstance(target, dict) else None
    if not handle:
        raise WebDriverException("Chrome did not return a background target")
    driver.switch_to.window(handle)
    state.background_handle = handle


def _debugger_available(address: str) -> bool:
    host, separator, port = address.rpartition(":")
    if not separator or not host:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except (OSError, ValueError):
        return False


def _chrome_process_running(data_dir: Path | None = None) -> bool:
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeDecodeError):
            continue
        if CHROME_BINARY in command and "--type=" not in command and (
            data_dir is None or f"--user-data-dir={data_dir}" in command
        ):
            return True
    return False


def _harden_profile_permissions() -> None:
    target = Path(CHROME_USER_DATA_DIR)
    if not target.exists():
        return
    try:
        target.chmod(0o700)
        for path in target.rglob("*"):
            if path.is_symlink():
                continue
            path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError as exc:
        raise WebDriverException(
            f"Could not secure Chrome profile permissions: {exc}"
        ) from exc


def _prepare_profile() -> None:
    target = Path(CHROME_USER_DATA_DIR)
    target.mkdir(parents=True, exist_ok=True)
    _harden_profile_permissions()


def _start_profile_chrome(address: str) -> bool:
    if _chrome_process_running(Path(CHROME_USER_DATA_DIR)):
        return False
    host, _, port = address.rpartition(":")
    if not host or not port:
        return False
    try:
        _prepare_profile()
        subprocess.Popen(
            [
                CHROME_BINARY,
                "--headless=new",
                f"--remote-debugging-address={host}",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={CHROME_USER_DATA_DIR}",
                f"--profile-directory={CHROME_PROFILE_DIRECTORY}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _debugger_available(address):
            return True
        time.sleep(0.1)
    return False


def _get_driver(session_id: str = "default", attach_existing: bool = True,
                debugger_address: str | None = None) -> webdriver.Chrome:
    with _lock:
        if session_id in _drivers:
            try:
                _ = _drivers[session_id].current_url
                return _drivers[session_id]
            except WebDriverException:
                _drivers.pop(session_id, None)

        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        address = (
            debugger_address
            or os.getenv("PENZER_BROWSER_DEBUGGER_ADDRESS", "").strip()
            or DEFAULT_DEBUGGER_ADDRESS
        )
        if not _debugger_available(address):
            _start_profile_chrome(address)
        if not _debugger_available(address):
            raise WebDriverException(
                f"Chrome remote debugging is unavailable at {address}; "
                "Penzer could not start its background profile"
            )
        if address:
            options.add_experimental_option("debuggerAddress", address)
        options.add_experimental_option("prefs", {
            "download.default_directory": str(_download_dir(session_id)),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        })
        options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
        driver = webdriver.Chrome(options=options)
        state = _state(session_id)
        state.attached = bool(address)
        state.bidi_available = callable(getattr(driver, "bidi_connection", None))
        state.cdp_available = callable(getattr(driver, "execute_cdp_cmd", None))
        _drivers[session_id] = driver
        _prepare_background_target(driver, state)
        return driver


def _drain_events(driver: webdriver.Chrome, state: BrowserState) -> list[dict]:
    events: list[dict] = []
    try:
        for item in driver.get_log("browser"):
            level = str(item.get("level", "INFO")).lower()
            events.append({
                "type": "js_error" if level in {"severe", "error"} else "console",
                "level": level,
                "message": str(item.get("message", ""))[:MAX_TEXT],
                "timestamp": item.get("timestamp"),
            })
    except Exception:
        pass
    try:
        for item in driver.get_log("performance"):
            message = json.loads(item["message"]).get("message", {})
            method = message.get("method", "")
            if method.startswith("Network."):
                params = message.get("params", {})
                events.append({
                    "type": "network",
                    "event": method.removeprefix("Network."),
                    "request_id": params.get("requestId"),
                    "url": (params.get("request", {}) or {}).get("url") or
                           (params.get("response", {}) or {}).get("url"),
                    "status": (params.get("response", {}) or {}).get("status"),
                })
    except Exception:
        pass
    state.events.extend(events)
    state.events = state.events[-100:]
    return events


def _wait_ready(driver: webdriver.Chrome, timeout: int) -> None:
    WebDriverWait(driver, max(1, min(int(timeout), 60))).until(
        lambda item: item.execute_script("return document.readyState") in {"interactive", "complete"}
    )


def _element(driver: webdriver.Chrome, state: BrowserState, element_id: str):
    selector = state.element_ids.get(element_id)
    if not selector:
        raise ValueError(f"Unknown element_id '{element_id}'. Call observe first.")
    try:
        return driver.find_element(By.CSS_SELECTOR, selector)
    except NoSuchElementException as exc:
        raise ValueError(f"Element '{element_id}' is no longer present. Call observe again.") from exc


def _observe(driver: webdriver.Chrome, state: BrowserState) -> dict:
    script = """
    const nodes = [...document.querySelectorAll(
      'a,button,input,textarea,select,[role],h1,h2,h3,[contenteditable="true"]'
    )].slice(0, arguments[0]);
    return nodes.map((node) => {
      let id = node.getAttribute('data-penzer-id');
      if (!id) { id = 'e' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); node.setAttribute('data-penzer-id', id); }
      return {id, tag: node.tagName.toLowerCase(), role: node.getAttribute('role') || '',
        name: node.getAttribute('aria-label') || node.getAttribute('name') || node.innerText?.trim().slice(0, 120) || '',
        text: (node.innerText || node.value || '').trim().slice(0, 180),
        href: node.href || '', type: node.getAttribute('type') || '', disabled: !!node.disabled};
    });
    """
    nodes = driver.execute_script(script, MAX_OBSERVE_ELEMENTS) or []
    state.element_ids = {
        item["id"]: f'[data-penzer-id="{item["id"]}"]'
        for item in nodes if item.get("id")
    }
    return {
        "url": driver.current_url,
        "title": driver.title[:MAX_TEXT],
        "ready_state": driver.execute_script("return document.readyState"),
        "elements": nodes,
        "element_count": len(nodes),
    }


def _tabs(driver: webdriver.Chrome) -> list[dict]:
    current = driver.current_window_handle
    tabs = []
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        tabs.append({"handle": handle, "url": driver.current_url, "title": driver.title[:MAX_TEXT]})
    driver.switch_to.window(current)
    return tabs


def _wait_for_login(session_id: str, timeout: int | None = None) -> dict:
    """Pause the agent until the user confirms manual login is complete."""
    state = _state(session_id)
    if state.login_confirmed:
        return success(data={"session_id": session_id, "login_confirmed": True})
    approved = confirm_action(
        "browser login ready",
        "Log into the attached Chrome session. Type 'y' here only after login is complete.",
        timeout=timeout,
    )
    if not approved:
        return error("Login was not confirmed; browser work is paused.")
    state.login_confirmed = True
    return success(data={"session_id": session_id, "login_confirmed": True})


def _browser_impl(action: str, query: str = None, url: str = None,
                  selector: str = None, text: str = None, timeout: int = 10,
                  session_id: str = "default", element_id: str = None,
                  amount: int = 600, direction: str = "down", index: int = None,
                  key: str = None, value: str = None, allowed_hosts: list[str] = None,
                  subaction: str = "list", cookie: dict = None,
                  attach_existing: bool = True, debugger_address: str = None) -> dict:
    attach_existing = attach_existing or action.lower() == "attach"
    try:
        driver = _get_driver(
            session_id,
            attach_existing=attach_existing,
            debugger_address=debugger_address,
        )
    except WebDriverException as exc:
        return error(f"Could not attach to Chrome at the debugging endpoint: {exc}")
    if driver is None:
        return error("Could not attach to an existing Chrome session.")
    state = _state(session_id)
    action = (action or "observe").lower()
    try:
        if action == "attach":
            login = _wait_for_login(session_id, timeout=None)
            if login.get("status") != "success":
                return login
            return success(data={"action": "attach", "session_id": session_id,
                                 "url": driver.current_url, "title": driver.title[:MAX_TEXT],
                                 "login_confirmed": True})

        if action in {"open", "navigate"}:
            target = _safe_url(url or query, allowed_hosts)
            if not target:
                return error("Only http(s) URLs are allowed, and the host must be permitted.")
            driver.get(target)
            _wait_ready(driver, timeout)
            state.element_ids = {}
            return success(data={"action": "open", "url": driver.current_url, "title": driver.title[:MAX_TEXT]})

        if action == "search":
            if not query:
                return error("search requires 'query'")
            driver.get(
                "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
            )
            _wait_ready(driver, timeout)
            return success(data={"action": action, "query": query, "url": driver.current_url, "title": driver.title[:MAX_TEXT]})

        if action == "observe":
            events = _drain_events(driver, state)
            data = _observe(driver, state)
            data["events"] = events
            return success(data=data)

        if action == "click":
            target = _element(driver, state, element_id) if element_id else driver.find_element(By.CSS_SELECTOR, selector or "")
            WebDriverWait(driver, timeout).until(lambda item: target.is_displayed() and target.is_enabled())
            target.click()
            _wait_ready(driver, timeout)
            return success(data={"action": action, "element_id": element_id, "url": driver.current_url})

        if action == "type":
            if not text:
                return error("type requires 'text'")
            target = _element(driver, state, element_id) if element_id else driver.find_element(By.CSS_SELECTOR, selector or "input")
            target.clear()
            target.send_keys(text)
            return success(data={"action": action, "element_id": element_id, "text_length": len(text)})

        if action == "scroll":
            distance = amount if direction == "down" else -amount
            if element_id:
                target = _element(driver, state, element_id)
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", target
                )
            else:
                driver.execute_script("window.scrollBy(0, arguments[0]);", distance)
            return success(data={"action": action, "element_id": element_id, "direction": direction, "amount": amount})

        if action in {"back", "forward", "refresh"}:
            getattr(driver, action)()
            _wait_ready(driver, timeout)
            state.element_ids = {}
            return success(data={"action": action, "url": driver.current_url, "title": driver.title[:MAX_TEXT]})

        if action in {"tabs", "tab"}:
            if subaction == "close":
                if len(driver.window_handles) <= 1:
                    return warning(data={"tabs": _tabs(driver)}, message="Cannot close the last tab")
                driver.close()
                driver.switch_to.window(driver.window_handles[-1])
            elif subaction == "switch":
                handles = driver.window_handles
                if index is None or index < 0 or index >= len(handles):
                    return error("tab switch requires a valid index")
                driver.switch_to.window(handles[index])
            return success(data={"tabs": _tabs(driver), "current": driver.current_window_handle})

        if action == "cookies":
            if subaction == "add":
                if not cookie:
                    return error("cookies add requires 'cookie'")
                driver.add_cookie(cookie)
            elif subaction == "delete_all":
                driver.delete_all_cookies()
            return success(data={"cookies": driver.get_cookies()})

        if action == "storage":
            if subaction == "set":
                if key is None or value is None:
                    return error("storage set requires 'key' and 'value'")
                driver.execute_script("localStorage.setItem(arguments[0], arguments[1]);", key, value)
            elif subaction == "delete":
                driver.execute_script("localStorage.removeItem(arguments[0]);", key)
            elif subaction == "clear":
                driver.execute_script("localStorage.clear();")
            stored = driver.execute_script("return Object.fromEntries(Object.entries(localStorage));")
            return success(data={"storage": stored})

        if action == "downloads":
            files = sorted(item.name for item in _download_dir(session_id).iterdir() if item.is_file())
            return success(data={"directory": str(_download_dir(session_id)), "files": files})

        if action in {"events", "network"}:
            events = _drain_events(driver, state)
            if action == "network":
                events = [item for item in events if item.get("type") == "network"]
            return success(data={"events": events, "backend": "bidi" if state.bidi_available else "cdp"})

        if action == "eval":
            if not text:
                return error("eval requires 'text'")
            return success(data={"result": driver.execute_script(text)})

        if action in {"get", "get_content", "content"}:
            return success(data={"content": driver.find_element(By.TAG_NAME, "body").text[:5000], "url": driver.current_url})

        return warning(data={}, message="Unknown action. Use observe, open, search, click, type, scroll, tabs, back, forward, refresh, cookies, storage, downloads, events, network, eval, or get_content.")
    except Exception as exc:
        logger.debug("Browser action failed", exc_info=True)
        return error(f"Browser error: {exc}")
    finally:
        try:
            _drain_events(driver, state)
        except Exception:
            pass


def browser_direct(action: str, query: str = None, url: str = None, selector: str = None,
                   text: str = None, timeout: int = 10, session_id: str = "default",
                   element_id: str = None, amount: int = 600, direction: str = "down",
                   index: int = None, key: str = None, value: str = None,
                   allowed_hosts: list[str] = None, subaction: str = "list",
                   cookie: dict = None, attach_existing: bool = True,
                   debugger_address: str = None) -> dict:
    return _browser_impl(action, query, url, selector, text, timeout, session_id,
                         element_id, amount, direction, index, key, value,
                         allowed_hosts, subaction, cookie, attach_existing, debugger_address)


def browser(**kwargs) -> dict:
    """Core browser harness entry point; protocol details stay internal."""
    return browser_direct(**kwargs)


def browser_info_direct(session_id: str = "default") -> dict:
    try:
        driver = _get_driver(session_id)
        if driver is None:
            return error(
                f"Chrome remote debugging is unavailable at {DEFAULT_DEBUGGER_ADDRESS}."
            )
        state = _state(session_id)
        return success(data={
            "session_id": session_id,
            "url": driver.current_url,
            "title": driver.title[:MAX_TEXT],
            "tabs": _tabs(driver),
            "download_directory": str(_download_dir(session_id)),
            "event_backend": "bidi" if state.bidi_available else "cdp",
            "attached": state.attached,
            "login_confirmed": state.login_confirmed,
        })
    except WebDriverException:
        return error(
            f"Cannot attach to Chrome at {DEFAULT_DEBUGGER_ADDRESS}; "
            "Chrome may already be open without remote debugging. "
            "Close it once, then rerun Penzer so it can start the real profile "
            "in background mode."
        )
    except Exception as exc:
        return error(f"Failed to get browser info: {exc}")


def browser_info(session_id: str = "default") -> dict:
    return browser_info_direct(session_id=session_id)


def browser_close_direct(session_id: str = "default") -> dict:
    with _lock:
        driver = _drivers.get(session_id)
        if not driver:
            return warning(data={}, message=f"Session {session_id} not found")
        try:
            state = _state(session_id)
            if state.background_handle in driver.window_handles:
                driver.switch_to.window(state.background_handle)
                driver.close()
            _drivers.pop(session_id, None)
            _states.pop(session_id, None)
            return success(data={"closed": session_id})
        except Exception as exc:
            return error(f"Failed to close browser: {exc}")


def browser_close(session_id: str = "default") -> dict:
    return browser_close_direct(session_id=session_id)