"""
session/memory.py

Research-backed memory architecture:
  - Episodic: raw events with Ebbinghaus decay (MemoryBank 2024)
  - Semantic: distilled cross-task insights (ExpeL AAAI 2024)
  - Post-mortems: verbal Reflexion per task (Shinn 2023)
  - Dual-tier retrieval: fast summaries for simple, deep for complex (HyMem 2026)
  - Forgetting curve: old unused memories decay and get pruned automatically
  - Insight extraction: cross-task patterns distilled into reusable rules
"""
import json
import math
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_DIR  = Path(".penzer")
STORAGE_FILE = STORAGE_DIR / "session.json"
STORAGE_DIR.mkdir(exist_ok=True)

MAX_EPISODIC    = 300
MAX_SEMANTIC    = 150
MAX_INSIGHTS    = 100   # ExpeL: cross-task extracted insights
MAX_POST_MORTEM = 50
MAX_HISTORY     = 500
MAX_CHECKPOINTS = 5

DECAY_HALF_LIFE_HOURS = 72   # Ebbinghaus: memory strength halves every 72h


def _fresh() -> dict:
    return {
        "episodic":     [],   # [{event, outcome, importance, timestamp, task_type, strength}]
        "semantic":     [],   # [{pattern, confidence, times_validated, timestamp}]
        "insights":     [],   # [{insight, source_tasks, confidence, timestamp}] — ExpeL
        "post_mortem":  [],   # [{task_type, what_worked, what_failed, next_time, timestamp}]
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
                for k, v in _fresh().items():
                    data.setdefault(k, v)
                return data
    except Exception as e:
        logger.debug("Storage load failed: %s", e)
    return _fresh()


def _save(data: dict) -> None:
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Storage save: %s", e)


# ── Ebbinghaus decay score ────────────────────────────────────
# Memory strength decays exponentially with time unless reinforced

def _decay(timestamp: str, base_strength: float = 1.0) -> float:
    try:
        then  = datetime.fromisoformat(timestamp)
        hours = (datetime.now() - then).total_seconds() / 3600
        return base_strength * math.exp(-0.693 * hours / DECAY_HALF_LIFE_HOURS)
    except Exception:
        return 0.5


def _recency(timestamp: str) -> float:
    try:
        then  = datetime.fromisoformat(timestamp)
        hours = (datetime.now() - then).total_seconds() / 3600
        return math.exp(-0.01 * hours)
    except Exception:
        return 0.5


def _relevance(text: str, query: str) -> float:
    q_words = set(query.lower().split())
    t_words = set(text.lower().split())
    return len(q_words & t_words) / len(q_words) if q_words else 0.0


# ── Episodic Memory ──────────────────────────────────────────

def remember_episodic(event: str, outcome: str, importance: float = 0.5, task_type: str = "") -> None:
    data = _load()
    data["episodic"].append({
        "event":     event,
        "outcome":   outcome,
        "importance": min(1.0, max(0.0, importance)),
        "task_type": task_type,
        "timestamp": datetime.now().isoformat(),
        "strength":  1.0,  # starts at full strength, decays over time
        "access_count": 0,
    })
    # Prune: remove entries with decayed strength < 0.1 first, then by importance
    data["episodic"] = [
        e for e in data["episodic"]
        if _decay(e["timestamp"], e.get("strength", 1.0)) > 0.1
    ]
    if len(data["episodic"]) > MAX_EPISODIC:
        data["episodic"] = sorted(
            data["episodic"],
            key=lambda x: _decay(x["timestamp"], x.get("strength", 1.0)) * x["importance"],
        )[-MAX_EPISODIC:]
    _save(data)


def reinforce_episodic(event_text: str) -> None:
    """
    Reinforce a memory using spaced repetition intervals (MemoryBank).
    Each access extends the decay half-life: 1h → 24h → 72h → 168h → 336h
    """
    SR_INTERVALS = [1, 24, 72, 168, 336]  # hours — spaced repetition schedule
    data = _load()
    for ep in data["episodic"]:
        if event_text[:50] in ep["event"]:
            count    = ep.get("access_count", 0) + 1
            interval = SR_INTERVALS[min(count, len(SR_INTERVALS) - 1)]
            # Strength reflects current SR interval as fraction of max
            ep["strength"]      = min(3.0, interval / SR_INTERVALS[-1] * 3.0)
            ep["timestamp"]     = datetime.now().isoformat()
            ep["access_count"]  = count
    _save(data)


# ── Semantic Memory ──────────────────────────────────────────

def remember_semantic(pattern: str, confidence: float = 0.6) -> None:
    data = _load()
    existing = [s for s in data["semantic"] if s["pattern"] == pattern]
    if existing:
        existing[0]["confidence"]      = min(1.0, existing[0]["confidence"] + 0.05)
        existing[0]["times_validated"] += 1
        existing[0]["timestamp"]       = datetime.now().isoformat()
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


# ── Insight Extraction (ExpeL pattern) ──────────────────────
# Cross-task distilled rules — more generalizable than episodic

def store_insight(insight: str, source_tasks: list[str], confidence: float = 0.7) -> None:
    data = _load()
    existing = [i for i in data["insights"] if i["insight"] == insight]
    if existing:
        existing[0]["confidence"]   = min(1.0, existing[0]["confidence"] + 0.1)
        existing[0]["source_tasks"] = list(set(existing[0]["source_tasks"] + source_tasks))
        existing[0]["timestamp"]    = datetime.now().isoformat()
    else:
        data["insights"].append({
            "insight":      insight,
            "source_tasks": source_tasks,
            "confidence":   confidence,
            "timestamp":    datetime.now().isoformat(),
        })
    if len(data["insights"]) > MAX_INSIGHTS:
        data["insights"] = sorted(
            data["insights"], key=lambda x: x["confidence"]
        )[-MAX_INSIGHTS:]
    _save(data)


def get_insights(query: str, n: int = 3) -> list[dict]:
    data = _load()
    scored = [
        (_relevance(i["insight"], query) * 0.6 + i["confidence"] * 0.4, i)
        for i in data["insights"]
        if _relevance(i["insight"], query) > 0
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [i for _, i in scored[:n]]


# ── Trajectory Retrieval (ExpeL — missing piece) ────────────
# Retrieve similar past tool sequences, not just insights

def get_similar_trajectories(query: str, n: int = 3) -> list[dict]:
    """Retrieve episodic memories whose tool sequences match this query."""
    data   = _load()
    scored = []
    for ep in data["episodic"]:
        rel = _relevance(ep["event"] + " " + ep["outcome"], query)
        dec = _decay(ep["timestamp"], ep.get("strength", 1.0))
        if rel > 0.1:
            scored.append((rel * 0.7 + dec * 0.3, ep))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ep for _, ep in scored[:n]]


# ── Post-Mortem (Reflexion) ──────────────────────────────────

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
    scored = [
        (_relevance(pm["task_type"] + " " + pm["what_worked"], query), pm)
        for pm in data["post_mortem"]
        if _relevance(pm["task_type"], query) > 0
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [pm for _, pm in scored[:n]]


# ── Dual-Tier Retrieval (HyMem 2026) ────────────────────────
# Fast path: summary-level for simple queries
# Deep path: full scoring for complex queries

def get_relevant_memories(query: str, n: int = 5, deep: bool = False) -> str:
    data    = _load()
    query_l = query.lower()

    # Fast path: simple queries get summary-level context only
    if not deep:
        lines = ["## Memory"]
        # Top semantic patterns (fastest, most distilled)
        for sem in sorted(data["semantic"], key=lambda x: x["confidence"], reverse=True)[:2]:
            if _relevance(sem["pattern"], query) > 0.2:
                lines.append(f"- [learned] {sem['pattern']}")
        # Top insights
        for ins in get_insights(query, n=2):
            lines.append(f"- [insight] {ins['insight']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    # Deep path: full scoring across all memory types
    scored = []
    for ep in data["episodic"]:
        text  = f"{ep['event']} {ep['outcome']}"
        score = (
            _recency(ep["timestamp"])              * 0.25
            + _relevance(text, query)              * 0.45
            + ep["importance"]                     * 0.15
            + _decay(ep["timestamp"], ep.get("strength", 1.0)) * 0.15
        )
        scored.append((score, "episodic", ep))

    for sem in data["semantic"]:
        score = (
            _recency(sem["timestamp"])             * 0.2
            + _relevance(sem["pattern"], query)    * 0.5
            + sem["confidence"]                    * 0.3
        )
        scored.append((score, "semantic", sem))

    for ins in data["insights"]:
        score = (
            _relevance(ins["insight"], query)      * 0.6
            + ins["confidence"]                    * 0.4
        )
        scored.append((score, "insight", ins))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:n]

    if not top:
        return ""

    lines = ["## Relevant Memory"]
    for _, kind, item in top:
        if kind == "episodic":
            lines.append(f"- [event] {item['event']} → {item['outcome']}")
        elif kind == "semantic":
            lines.append(f"- [learned] {item['pattern']} ({item['confidence']:.0%} confidence)")
        else:
            lines.append(f"- [insight] {item['insight']}")

    # Reinforce accessed episodic memories
    for _, kind, item in top:
        if kind == "episodic":
            reinforce_episodic(item["event"])

    return "\n".join(lines)


# ── Complexity Scoring (HyMem — replaces keyword heuristic) ──

COMPLEXITY_SIGNALS = {
    "high":   ["build", "create", "deploy", "configure", "analyze", "research",
               "setup", "install", "compare", "generate", "multiple", "then",
               "after", "first", "finally", "step by step"],
    "medium": ["find", "check", "get", "show", "list", "what is", "how to"],
    "low":    ["hi", "hello", "what", "who", "when", "where", "why"],
}

def score_complexity(query: str) -> float:
    """
    Score query complexity 0.0-1.0 numerically.
    HyMem uses this to decide fast vs deep retrieval.
    """
    q = query.lower()
    high_hits   = sum(1 for s in COMPLEXITY_SIGNALS["high"]   if s in q)
    medium_hits = sum(1 for s in COMPLEXITY_SIGNALS["medium"] if s in q)
    low_hits    = sum(1 for s in COMPLEXITY_SIGNALS["low"]    if s in q)
    word_count  = len(q.split())

    score = (
        high_hits   * 0.4
        + medium_hits * 0.2
        + (word_count / 20) * 0.3   # longer = more complex
        - low_hits  * 0.1
    )
    return min(1.0, max(0.0, score))


# ── Legacy shims (agent.py compatibility) ────────────────────

def load_memory() -> dict:
    return {}


def save_memory(memory: dict) -> None:
    pass


def remember(memory: dict, item: str) -> None:
    pass


def get_memory_context(memory: dict) -> str:
    return ""


def clear_memory() -> None:
    data = _load()
    data["episodic"]  = []
    data["semantic"]  = []
    data["insights"]  = []
    data["post_mortem"] = []
    _save(data)


# ── Skill Metrics ────────────────────────────────────────────

def update_skill_metric(skill_name: str, success: bool) -> None:
    data = _load()
    m    = data["skill_metrics"]
    if skill_name not in m:
        m[skill_name] = {"success_count": 0, "failure_count": 0, "last_used": ""}
    if success:
        m[skill_name]["success_count"] += 1
    else:
        m[skill_name]["failure_count"] += 1
    total = m[skill_name]["success_count"] + m[skill_name]["failure_count"]
    m[skill_name]["success_rate"] = m[skill_name]["success_count"] / total if total else 0
    m[skill_name]["last_used"]    = datetime.now().isoformat()
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
        "insights":    len(data["insights"]),
        "post_mortem": len(data["post_mortem"]),
        "history":     len(data["history"]),
        "skills":      len(data["skill_metrics"]),
        "file":        str(STORAGE_FILE),
    }


def clear_all() -> None:
    if STORAGE_FILE.exists():
        STORAGE_FILE.unlink()