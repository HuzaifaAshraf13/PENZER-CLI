"""
session/memory.py — Persistent agent memory + session history in one file.
"""
import json
import os
import logging
from typing import Any
from datetime import datetime

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
    """Add a fact to memory. Auto-saves. Dedupes exact matches."""
    if not item or not item.strip():
        return
    
    memory.setdefault("facts", [])
    
    # Dedupe: only add if not already present
    if item not in memory["facts"]:
        memory["facts"].append(item)
        
        # Prune old facts if list gets too long (keep last 50)
        if len(memory["facts"]) > 50:
            memory["facts"] = memory["facts"][-50:]
        
        # Auto-save
        save_memory(memory)


def get_memory_context(memory: dict) -> str:
    """Format memory as context for the LLM prompt."""
    if not memory or not memory.get("facts"):
        return ""
    
    facts = memory.get("facts", [])
    if not facts:
        return ""
    
    # Only include the last 5 most recent facts to stay concise
    recent = facts[-5:]
    context = "## Memory (recent facts)\n" + "\n".join(f"- {fact}" for fact in recent)
    return context


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