"""
session/memory.py

Memory architecture based on research:
  - Episodic: what happened (event + outcome + timestamp + importance)
  - Semantic: what we learned (distilled patterns, validated over time)
  - Post-mortems: verbal Reflexion stored per task type
  - Retrieval: scored by recency × relevance × importance (not just last N facts)
  - Skill metrics: success/failure tracking per skill
"""
import json
import os
import math
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_DIR  = Path(".penzer")
STORAGE_FILE = STORAGE_DIR / "session.json"

STORAGE_DIR.mkdir(exist_ok=True)

MAX_EPISODIC    = 200
MAX_SEMANTIC    = 100
MAX_POST_MORTEM = 50
MAX_HISTORY     = 500
MAX_CHECKPOINTS = 5


# ── Storage ──────────────────────────────────────────────────

def _fresh() -> dict:
    return {
        "episodic":    [],   # [{event, outcome, importance, timestamp, task_type}]
        "semantic":    [],   # [{pattern, confidence, times_validated, timestamp}]
        "post_mortem": [],   # [{task_type, what_worked, what_failed, next_time, timestamp}]
        "history":     [],
        "skill_metrics": {},
        "checkpoints": [],
    }


def _load() -> dict:
    try:
        if STORAGE_FILE.exists():
            with open(STORAGE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, default in _fresh().items():
                    data.setdefault(k, default)
                return data
    except Exception as e:
        logger.debug("Storage load failed: %s", e)
    return _fresh()


def _save(data: dict) -> None:
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Storage save failed: %s", e)


# ── Episodic Memory ──────────────────────────────────────────
# Stores raw events: what tool ran, what happened, outcome

def remember_episodic(event: str, outcome: str, importance: float = 0.5, task_type: str = "") -> None:
    data = _load()
    data["episodic"].append({
        "event":     event,
        "outcome":   outcome,
        "importance": min(1.0, max(0.0, importance)),
        "task_type": task_type,
        "timestamp": datetime.now().isoformat(),
    })
    if len(data["episodic"]) > MAX_EPISODIC:
        # Prune lowest importance episodic memories
        data["episodic"] = sorted(
            data["episodic"], key=lambda x: x["importance"]
        )[-MAX_EPISODIC:]
    _save(data)


# ── Semantic Memory ──────────────────────────────────────────
# Stores distilled patterns validated over multiple episodes

def remember_semantic(pattern: str, confidence: float = 0.6) -> None:
    data = _load()
    existing = [s for s in data["semantic"] if s["pattern"] == pattern]
    if existing:
        existing[0]["confidence"]       = min(1.0, existing[0]["confidence"] + 0.05)
        existing[0]["times_validated"] += 1
        existing[0]["timestamp"]        = datetime.now().isoformat()
    else:
        data["semantic"].append({
            "pattern":         pattern,
            "confidence":      confidence,
            "times_validated": 1,
            "timestamp":       datetime.now().isoformat(),
        })
    if len(data["semantic"]) > MAX_SEMANTIC:
        data["semantic"] = sorted(
            data["semantic"], key=lambda x: x["confidence"]
        )[-MAX_SEMANTIC:]
    _save(data)


# ── Post-Mortem (Reflexion) ──────────────────────────────────
# Verbal RL: after each complex task write what worked/failed

def store_post_mortem(task_type: str, what_worked: str, what_failed: str, next_time: str) -> None:
    data = _load()
    data["post_mortem"].append({
        "task_type":   task_type,
        "what_worked": what_worked,
        "what_failed": what_failed,
        "next_time":   next_time,
        "timestamp":   datetime.now().isoformat(),
    })
    if len(data["post_mortem"]) > MAX_POST_MORTEM:
        data["post_mortem"] = data["post_mortem"][-MAX_POST_MORTEM:]
    _save(data)


def get_post_mortems(query: str, n: int = 2) -> list[dict]:
    data = _load()
    scored = []
    query_words = set(query.lower().split())
    for pm in data["post_mortem"]:
        words   = set(pm["task_type"].lower().split())
        overlap = len(query_words & words)
        if overlap > 0:
            scored.append((overlap, pm))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [pm for _, pm in scored[:n]]


# ── Retrieval (recency × relevance × importance) ─────────────

def _recency_score(timestamp: str) -> float:
    try:
        then    = datetime.fromisoformat(timestamp)
        hours   = (datetime.now() - then).total_seconds() / 3600
        return math.exp(-0.01 * hours)  # slow decay
    except Exception:
        return 0.5


def _relevance_score(text: str, query: str) -> float:
    query_words = set(query.lower().split())
    text_words  = set(text.lower().split())
    if not query_words:
        return 0.0
    overlap = len(query_words & text_words)
    return overlap / len(query_words)


def get_relevant_memories(query: str, n: int = 5) -> str:
    data = _load()
    scored = []

    for ep in data["episodic"]:
        text  = f"{ep['event']} {ep['outcome']}"
        score = (
            _recency_score(ep["timestamp"]) * 0.3
            + _relevance_score(text, query)  * 0.5
            + ep["importance"]               * 0.2
        )
        scored.append((score, "episodic", ep))

    for sem in data["semantic"]:
        score = (
            _recency_score(sem["timestamp"])             * 0.2
            + _relevance_score(sem["pattern"], query)    * 0.5
            + sem["confidence"]                          * 0.3
        )
        scored.append((score, "semantic", sem))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:n]

    if not top:
        return ""

    lines = ["## Relevant Memory"]
    for _, kind, item in top:
        if kind == "episodic":
            lines.append(f"- [event] {item['event']} → {item['outcome']}")
        else:
            lines.append(f"- [learned] {item['pattern']} (confidence: {item['confidence']:.2f})")

    return "\n".join(lines)


# ── Legacy helpers (used by agent.py) ───────────────────────

def load_memory() -> dict:
    d = _load()
    # Return a lightweight memory dict agent.py can carry around
    return {"_ref": True}


def save_memory(memory: dict) -> None:
    pass  # episodic/semantic saved directly — no monolithic memory dict needed


def remember(memory: dict, item: str) -> None:
    pass  # use remember_episodic / remember_semantic directly


def get_memory_context(memory: dict) -> str:
    return ""  # agent.py calls get_relevant_memories(goal) instead


def clear_memory() -> None:
    data = _load()
    data["episodic"]    = []
    data["semantic"]    = []
    data["post_mortem"] = []
    _save(data)


# ── Skill Metrics ────────────────────────────────────────────

def update_skill_metric(skill_name: str, success: bool) -> None:
    data = _load()
    m    = data["skill_metrics"]
    if skill_name not in m:
        m[skill_name] = {"success_count": 0, "failure_count": 0}
    if success:
        m[skill_name]["success_count"] += 1
    else:
        m[skill_name]["failure_count"] += 1
    total = m[skill_name]["success_count"] + m[skill_name]["failure_count"]
    m[skill_name]["success_rate"] = m[skill_name]["success_count"] / total if total else 0
    _save(data)


def get_skill_metric(skill_name: str) -> dict:
    return _load()["skill_metrics"].get(
        skill_name, {"success_count": 0, "failure_count": 0, "success_rate": 0.0}
    )


# ── History ──────────────────────────────────────────────────

def load_history() -> list:
    h = _load().get("history", [])
    return h[-MAX_HISTORY:]


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


# ── Utils ────────────────────────────────────────────────────

def get_storage_summary() -> dict:
    data = _load()
    return {
        "episodic":    len(data["episodic"]),
        "semantic":    len(data["semantic"]),
        "post_mortem": len(data["post_mortem"]),
        "history":     len(data["history"]),
        "skills":      len(data["skill_metrics"]),
        "file":        str(STORAGE_FILE),
    }


def clear_all() -> None:
    if STORAGE_FILE.exists():
        STORAGE_FILE.unlink()