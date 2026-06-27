"""
session/memory.py — Simple persistent storage.

Single file: .penzer/session.json
No backups. No compression. No noise.
"""
import json
import os
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_DIR  = Path(".penzer")
STORAGE_FILE = STORAGE_DIR / "session.json"

STORAGE_DIR.mkdir(exist_ok=True)

MAX_HISTORY   = 500
MAX_FACTS     = 100
MAX_CHECKPOINTS = 5


def _fresh() -> dict:
    return {
        "memory":       {"facts": []},
        "history":      [],
        "skill_metrics": {},
        "checkpoints":  [],
    }


def _load() -> dict:
    try:
        if STORAGE_FILE.exists():
            with open(STORAGE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in ("memory", "history", "skill_metrics", "checkpoints"):
                    data.setdefault(key, {} if key in ("memory", "skill_metrics") else [])
                return data
    except Exception as e:
        logger.debug("Storage load failed: %s — starting fresh", e)
    return _fresh()


def _save(data: dict) -> None:
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Storage save failed: %s", e)


# ── Memory ───────────────────────────────────────────────────

def load_memory() -> dict:
    return _load().get("memory", {})


def save_memory(memory: dict) -> None:
    data = _load()
    data["memory"] = memory
    _save(data)


def remember(memory: dict, item: str) -> None:
    if not item or not item.strip():
        return
    memory.setdefault("facts", [])
    # Migrate old dict-style facts to plain strings
    memory["facts"] = [
        f["text"] if isinstance(f, dict) else f
        for f in memory["facts"]
    ]
    if item not in memory["facts"]:
        memory["facts"].append(item)
        if len(memory["facts"]) > MAX_FACTS:
            memory["facts"] = memory["facts"][-MAX_FACTS:]
        save_memory(memory)


def get_memory_context(memory: dict) -> str:
    facts = memory.get("facts", [])
    if not facts:
        return ""
    # Normalise mixed formats
    plain = [f["text"] if isinstance(f, dict) else f for f in facts]
    recent = plain[-3:]
    return "## Memory\n" + "\n".join(f"- {f}" for f in recent)


def clear_memory() -> None:
    data = _load()
    data["memory"] = {"facts": []}
    _save(data)


# ── Skill Metrics ────────────────────────────────────────────

def load_skill_metrics() -> dict:
    return _load().get("skill_metrics", {})


def save_skill_metrics(metrics: dict) -> None:
    data = _load()
    data["skill_metrics"] = metrics
    _save(data)


def update_skill_metric(skill_name: str, success: bool) -> None:
    metrics = load_skill_metrics()
    if skill_name not in metrics:
        metrics[skill_name] = {"success_count": 0, "failure_count": 0}
    if success:
        metrics[skill_name]["success_count"] += 1
    else:
        metrics[skill_name]["failure_count"] += 1
    total = metrics[skill_name]["success_count"] + metrics[skill_name]["failure_count"]
    metrics[skill_name]["success_rate"] = metrics[skill_name]["success_count"] / total if total else 0
    save_skill_metrics(metrics)


def get_skill_metric(skill_name: str) -> dict:
    return load_skill_metrics().get(skill_name, {"success_count": 0, "failure_count": 0, "success_rate": 0.0})


# ── History ──────────────────────────────────────────────────

def load_history() -> list:
    h = _load().get("history", [])
    return h[-MAX_HISTORY:] if len(h) > MAX_HISTORY else h


def save_history(history: list) -> None:
    data = _load()
    data["history"] = history[-MAX_HISTORY:]
    _save(data)


def clear_history() -> None:
    data = _load()
    data["history"] = []
    _save(data)


# ── Checkpoints ──────────────────────────────────────────────

def add_checkpoint(checkpoint: dict) -> None:
    data = _load()
    data["checkpoints"].append(checkpoint)
    data["checkpoints"] = data["checkpoints"][-MAX_CHECKPOINTS:]
    _save(data)


def load_checkpoints() -> list:
    return _load().get("checkpoints", [])


def clear_checkpoints() -> None:
    data = _load()
    data["checkpoints"] = []
    _save(data)


# ── Full wipe ────────────────────────────────────────────────

def get_storage_summary() -> dict:
    data = _load()
    return {
        "facts":    len(data["memory"].get("facts", [])),
        "history":  len(data["history"]),
        "skills":   len(data["skill_metrics"]),
        "file":     str(STORAGE_FILE),
    }


def clear_all() -> None:
    if STORAGE_FILE.exists():
        STORAGE_FILE.unlink()