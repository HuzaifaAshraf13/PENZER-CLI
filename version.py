"""Version helpers for PENZER-CLI updates."""

import json
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.2.0"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(version))
    return tuple(int(part) for part in parts) if parts else (0,)


def _read_local_version(repo_root: Path | None = None) -> str | None:
    root = repo_root or Path(__file__).resolve().parent
    for candidate in (root / "setup.py", root / "pyproject.toml", root / "setup.cfg"):
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except Exception:
            continue
        match = re.search(r"version\s*=\s*[\"']([^\"']+)[\"']", text)
        if match:
            return match.group(1)
    return None


def get_version() -> str:
    return VERSION or _read_local_version() or "0.0.0"


def _latest_available_version(repo_root: Path | None = None) -> str:
    root = repo_root or Path(__file__).resolve().parent
    try:
        with urlopen("https://raw.githubusercontent.com/HuzaifaAshraf13/PENZER-CLI/main/version.json", timeout=5) as response:
            payload = json.load(response)
        latest = str(payload.get("version", "")).strip()
        if latest:
            return latest
    except Exception:
        pass
    return _read_local_version(root) or VERSION


def check_for_update() -> dict:
    """Check a remote manifest or local metadata for a newer published version."""
    repo_root = Path(__file__).resolve().parent
    current = VERSION
    latest = _latest_available_version(repo_root)

    if _version_tuple(latest) > _version_tuple(current):
        return {
            "update_available": True,
            "message": f"A newer version ({latest}) is available. Run update to install it.",
            "latest_version": latest,
        }

    if latest != current:
        return {
            "update_available": False,
            "message": "You are on the latest version.",
            "latest_version": latest,
        }

    return {
        "update_available": False,
        "message": "Unable to check for updates right now; using local version metadata.",
        "latest_version": latest,
    }


def perform_update() -> dict:
    """Perform a simple git pull-based update when the repo is a git checkout."""
    repo_root = Path(__file__).resolve().parent
    if not (repo_root / ".git").exists():
        return {"success": False, "message": "This installation is not a git checkout."}

    try:
        subprocess.run(["git", "-C", str(repo_root), "pull", "--ff-only"], check=True, capture_output=True, text=True)
        return {"success": True, "message": "Update completed successfully. Restart Penzer to use the new version."}
    except subprocess.CalledProcessError as exc:
        return {"success": False, "message": f"Update failed: {exc.stderr.strip() or exc.stdout.strip() or str(exc)}"}
