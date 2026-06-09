"""
session/memory.py — Persistent agent memory + session history in one file.
"""

import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)
STORAGE_FILE = ".penzer.json"


def _load() -> dict:
    try:
        if os.path.exists(STORAGE_FILE):
            with open(STORAGE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        logger.warning(f"Failed to load storage: {e}")
    return {"memory": {}, "history": []}


def _save(data: dict) -> None:
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save storage: {e}")


# ── Memory ──────────────────────────────

def load_memory() -> dict:
    return _load().get("memory", {})


def save_memory(memory: dict) -> None:
    data = _load()
    data["memory"] = memory
    _save(data)


def remember(memory: dict, item: str) -> None:
    memory.setdefault("facts", [])
    if item not in memory["facts"]:
        memory["facts"].append(item)


def get_memory_context(memory: dict) -> str:
    if not memory:
        return ""
    lines = ["## Memory"]
    for key, value in memory.items():
        if isinstance(value, list) and value:
            lines.append(f"- {key}: {', '.join(str(v) for v in value[-10:])}")
        elif value:
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def clear_memory() -> None:
    data = _load()
    data["memory"] = {}
    _save(data)


# ── Session History ──────────────────────

def load_history() -> list:
    return _load().get("history", [])


def save_history(history: list) -> None:
    data = _load()
    data["history"] = history
    _save(data)


def clear_history() -> None:
    data = _load()
    data["history"] = []
    _save(data)


def clear_all() -> None:
    if os.path.exists(STORAGE_FILE):
        os.remove(STORAGE_FILE)