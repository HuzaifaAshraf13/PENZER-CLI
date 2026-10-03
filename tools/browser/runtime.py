"""Chrome process and DevTools endpoint lifecycle helpers."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote

import requests


logger = logging.getLogger(__name__)


def candidate_chrome_binaries(environ: Mapping[str, str]) -> list[str]:
    preferred = environ.get("PENZER_CHROME_BINARY")
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


def verify_chrome_binary(binary: str, subprocess_module: Any = subprocess) -> bool:
    try:
        result = subprocess_module.run([binary, "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception as exc:
        logger.error("Chrome verification failed for %s: %s", binary, exc)
        return False


def browser_profile_dir(session_id: str, environ: Mapping[str, str]) -> Path:
    profile_root = Path(environ.get(
        "PENZER_BROWSER_PROFILE_ROOT",
        Path(__file__).resolve().parents[2] / "data" / "browser_profiles",
    ))
    profile_name = quote(session_id, safe="._-") or "default"
    if profile_name in {".", ".."}:
        profile_name = f"_{profile_name.replace('.', 'dot')}"
    profile_dir = (profile_root / profile_name).resolve()
    local_app_data = environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    personal_roots = (
        Path.home() / ".config" / "google-chrome",
        Path.home() / ".config" / "chromium",
        Path.home() / ".config" / "microsoft-edge",
        Path.home() / ".config" / "BraveSoftware" / "Brave-Browser",
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
        Path.home() / "Library" / "Application Support" / "Chromium",
        Path.home() / "Library" / "Application Support" / "Microsoft Edge",
        Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser",
        Path(local_app_data) / "Google" / "Chrome" / "User Data",
        Path(local_app_data) / "Microsoft" / "Edge" / "User Data",
    )
    if any(profile_dir == root.resolve() or root.resolve() in profile_dir.parents for root in personal_roots):
        raise ValueError("Browser profile root must not be inside a personal Chrome-family profile")
    return profile_dir


def spawn_chrome(
    session_id: str,
    *,
    environ: Mapping[str, str],
    candidate_binaries: Callable[[], list[str]],
    verify_binary: Callable[[str], bool],
    profile_dir: Callable[[str], Path],
    subprocess_module: Any = subprocess,
    os_module: Any = os,
) -> Any:
    for binary in candidate_binaries():
        if verify_binary(binary):
            break
    else:
        raise RuntimeError("Chrome binary not found or broken in common locations")

    session_profile = profile_dir(session_id)
    session_profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    chrome_args = [
        binary,
        "--remote-debugging-port=0",
        f"--user-data-dir={session_profile}",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    if environ.get("PENZER_BROWSER_HEADLESS", "1").lower() not in {"0", "false", "no"}:
        chrome_args.append("--headless=new")

    try:
        return subprocess_module.Popen(
            chrome_args,
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            preexec_fn=os_module.setsid if hasattr(os_module, "setsid") else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Chrome not found: {binary}") from exc


async def get_debugger_url(
    profile_dir: str,
    timeout: int = 15,
    requests_module: Any = requests,
) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    port_file = Path(profile_dir) / "DevToolsActivePort"
    while time.monotonic() < deadline:
        try:
            lines = (await asyncio.to_thread(port_file.read_text, encoding="ascii")).splitlines()
            port = int(lines[0])
            response = await asyncio.to_thread(
                requests_module.get, f"http://127.0.0.1:{port}/json/version", timeout=2,
            )
            response.raise_for_status()
            return port, response.json()["webSocketDebuggerUrl"]
        except (OSError, ValueError, IndexError, KeyError, requests_module.RequestException):
            await asyncio.sleep(0.1)
    raise RuntimeError(f"Chrome did not publish a debugger endpoint in {timeout}s")


def kill_chrome(
    pid: int,
    process: Optional[Any] = None,
    *,
    os_module: Any = os,
    signal_module: Any = signal,
    subprocess_module: Any = subprocess,
) -> None:
    if process is not None and process.poll() is not None:
        return

    try:
        if hasattr(os_module, "killpg"):
            os_module.killpg(os_module.getpgid(pid), signal_module.SIGTERM)
        else:
            os_module.kill(pid, signal_module.SIGTERM)
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
        except subprocess_module.TimeoutExpired:
            pass

    try:
        if hasattr(os_module, "killpg"):
            os_module.killpg(os_module.getpgid(pid), signal_module.SIGKILL)
        elif process is not None:
            process.kill()
        else:
            os_module.kill(pid, signal_module.SIGKILL)
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
            except subprocess_module.TimeoutExpired:
                logger.error("Chrome process %s did not exit after SIGKILL", pid)