"""
session/memory.py

Research-backed memory architecture:
  - Episodic        : raw events with Ebbinghaus decay (MemoryBank 2024)
  - Semantic        : distilled cross-task insights (ExpeL AAAI 2024)
  - Post-mortems    : verbal Reflexion per task (Shinn 2023)
  - Dual-tier       : fast summaries vs deep scoring (HyMem 2026)
  - KV Store        : explicit user-facing key-value facts
  - Conflict detect : contradicting semantics flagged + resolved
  - Assoc retrieval : chained related episode pull
  - Category decay  : each memory type decays at its own rate
  - Consolidation   : episodic → semantic distillation on schedule
  - Episodic replay : compressed narrative of past similar runs
"""
import json
import math
import logging
from datetime import datetime
from pathlib import Path


MEMORY_DIR = Path(".penzer") / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

STORAGE_DIR  = Path(".penzer")
STORAGE_FILE = STORAGE_DIR / "session.json"
STORAGE_DIR.mkdir(exist_ok=True)
MEMORY_FILES = {
    "episodic": MEMORY_DIR / "episodic.json",
    "semantic": MEMORY_DIR / "semantic.json",
    "insights": MEMORY_DIR / "insights.json",
    "post_mortem": MEMORY_DIR / "post_mortem.json",
    "kv": MEMORY_DIR / "kv.json",
    "history": MEMORY_DIR / "history.json",
    "skill_metrics": MEMORY_DIR / "skill_metrics.json",
    "checkpoints": MEMORY_DIR / "checkpoints.json",
    "consolidation": MEMORY_DIR / "consolidation.json",
}

MAX_EPISODIC     = 300
MAX_SEMANTIC     = 150
MAX_INSIGHTS     = 100
MAX_POST_MORTEM  = 50
MAX_HISTORY      = 500
MAX_CHECKPOINTS  = 5
CONSOLIDATE_EVERY = 20   # consolidate after every N new episodic entries

# Category-specific half-lives (hours)
DECAY_RATES = {
    "episodic": 72,    # raw events — fade in 3 days
    "semantic": 336,   # learned patterns — fade in 2 weeks
    "insights": 504,   # cross-task rules — fade in 3 weeks
    "kv":       720,   # user facts — fade in 1 month
}

# Spaced repetition intervals (hours)
SR_INTERVALS = [1, 24, 72, 168, 336]


def _fresh() -> dict:
    return {
        "episodic":      [],
        "semantic":      [],
        "insights":      [],
        "post_mortem":   [],
        "kv":            {},
        "history":       [],
        "skill_metrics": {},
        "checkpoints":   [],
        "consolidation": {"count": 0, "last_run": ""},
    }


def _load_section(section: str) -> any:
    path = MEMORY_FILES.get(section)
    if not path:
        return None
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Section load %s: %s", section, e)
    return None


def _save_section(section: str, value: any) -> None:
    path = MEMORY_FILES.get(section)
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(value, f, indent=2)
    except Exception as e:
        logger.error("Section save %s: %s", section, e)


def _load() -> dict:
    data = _fresh()
    try:
        for key in data:
            loaded = _load_section(key)
            if loaded is not None:
                data[key] = loaded
        if STORAGE_FILE.exists():
            with open(STORAGE_FILE) as f:
                legacy = json.load(f)
            if isinstance(legacy, dict):
                for k, v in legacy.items():
                    data.setdefault(k, v)
    except Exception as e:
        logger.debug("Storage load: %s", e)
    return data


def _save(data: dict) -> None:
    try:
        for key in ["episodic", "semantic", "insights", "post_mortem", "kv", "history", "skill_metrics", "checkpoints", "consolidation"]:
            _save_section(key, data.get(key, _fresh().get(key)))
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Storage save: %s", e)


# ── Decay ────────────────────────────────────────────────────
# Each category has its own half-life (category-specific forgetting curve)

def _decay(timestamp: str, strength: float = 1.0, category: str = "episodic") -> float:
    try:
        half_life = DECAY_RATES.get(category, 72)
        hours     = (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds() / 3600
        return strength * math.exp(-0.693 * hours / half_life)
    except Exception:
        return 0.5


def _recency(timestamp: str) -> float:
    try:
        hours = (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds() / 3600
        return math.exp(-0.01 * hours)
    except Exception:
        return 0.5


def _relevance(text: str, query: str) -> float:
    q = set(query.lower().split())
    t = set(text.lower().split())
    return len(q & t) / len(q) if q else 0.0


# ── Episodic Memory ──────────────────────────────────────────

def remember_episodic(
    event: str, outcome: str,
    importance: float = 0.5, task_type: str = ""
) -> None:
    data = _load()
    data["episodic"].append({
        "event":       event,
        "outcome":     outcome,
        "importance":  min(1.0, max(0.0, importance)),
        "task_type":   task_type,
        "timestamp":   datetime.now().isoformat(),
        "strength":    1.0,
        "access_count": 0,
    })
    # Prune decayed entries
    data["episodic"] = [
        e for e in data["episodic"]
        if _decay(e["timestamp"], e.get("strength", 1.0), "episodic") > 0.05
    ]
    if len(data["episodic"]) > MAX_EPISODIC:
        data["episodic"] = sorted(
            data["episodic"],
            key=lambda x: _decay(x["timestamp"], x.get("strength", 1.0), "episodic") * x["importance"]
        )[-MAX_EPISODIC:]

    # Track consolidation schedule
    data["consolidation"]["count"] = data["consolidation"].get("count", 0) + 1
    _save(data)


def reinforce_episodic(event_text: str) -> None:
    """Spaced repetition — each access extends the half-life."""
    data = _load()
    for ep in data["episodic"]:
        if event_text[:50] in ep["event"]:
            count           = ep.get("access_count", 0) + 1
            interval        = SR_INTERVALS[min(count, len(SR_INTERVALS) - 1)]
            ep["strength"]  = min(3.0, interval / SR_INTERVALS[-1] * 3.0)
            ep["timestamp"] = datetime.now().isoformat()
            ep["access_count"] = count
    _save(data)


# ── Associative Retrieval ────────────────────────────────────
# When one episode is retrieved, chain-pull related ones

def get_associated(event: dict, n: int = 2) -> list[dict]:
    """Pull episodes related to the given one by task_type or keyword overlap."""
    data    = _load()
    base_tt = event.get("task_type", "")
    base_ev = event.get("event", "")
    scored  = []
    for ep in data["episodic"]:
        if ep is event or ep.get("event") == base_ev:
            continue
        type_match    = 1.0 if ep.get("task_type") == base_tt and base_tt else 0.0
        keyword_match = _relevance(ep["event"], base_ev)
        score         = type_match * 0.6 + keyword_match * 0.4
        if score > 0.1:
            scored.append((score, ep))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ep for _, ep in scored[:n]]


# ── Episodic Replay ──────────────────────────────────────────
# Compressed narrative of past relevant runs for task context

def get_episode_replay(query: str, n: int = 3) -> str:
    """
    Returns a compressed narrative of the N most relevant past episodes.
    Format: "Last time [task]: tried [tools], [outcome]. What worked: [X]."
    """
    data   = _load()
    scored = []
    for ep in data["episodic"]:
        rel = _relevance(ep["event"] + " " + ep["task_type"], query)
        dec = _decay(ep["timestamp"], ep.get("strength", 1.0), "episodic")
        if rel > 0.1:
            scored.append((rel * 0.6 + dec * 0.4, ep))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:n]
    if not top:
        return ""

    lines = ["## Episode Replay (past similar runs)"]
    for _, ep in top:
        lines.append(
            f"- [{ep.get('task_type', 'task')}] {ep['event'][:80]} → {ep['outcome']}"
        )
        # Pull associated episodes for richer context
        associated = get_associated(ep, n=1)
        for assoc in associated:
            lines.append(f"    related: {assoc['event'][:60]} → {assoc['outcome']}")

    return "\n".join(lines)


# ── Semantic Memory ──────────────────────────────────────────

def remember_semantic(pattern: str, confidence: float = 0.6) -> None:
    """Store + run conflict detection before saving."""
    data = _load()
    # Conflict detection before storing
    conflict = detect_conflict(pattern, data["semantic"])
    if conflict:
        if conflict["confidence"] >= confidence:
            logger.debug("Conflict: keeping existing '%s'", conflict["pattern"][:60])
            return
        else:
            data["semantic"].remove(conflict)
            logger.debug("Conflict: replacing lower-confidence pattern")

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


# ── Conflict Detection ───────────────────────────────────────
# Detect contradicting semantic memories — same topic, opposite advice

NEGATION_PAIRS = [
    ("always", "never"), ("use", "avoid"), ("works", "fails"),
    ("do", "don't"), ("can", "cannot"), ("enable", "disable"),
]

def detect_conflict(new_pattern: str, existing: list[dict]) -> dict | None:
    """
    Check if new_pattern contradicts an existing semantic memory.
    Returns the conflicting entry if found, else None.
    """
    new_words = set(new_pattern.lower().split())
    for sem in existing:
        old_words = set(sem["pattern"].lower().split())
        topic_overlap = len(new_words & old_words) / max(len(new_words), 1)
        if topic_overlap < 0.4:
            continue
        # Check for negation pairs indicating contradiction
        for pos, neg in NEGATION_PAIRS:
            if (pos in new_words and neg in old_words) or \
               (neg in new_words and pos in old_words):
                return sem
    return None


# ── Insight Extraction (ExpeL) ───────────────────────────────

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
    data   = _load()
    scored = [
        (_relevance(i["insight"], query) * 0.6 + i["confidence"] * 0.4, i)
        for i in data["insights"]
        if _relevance(i["insight"], query) > 0
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [i for _, i in scored[:n]]


# ── Trajectory Retrieval (ExpeL) ─────────────────────────────

def get_similar_trajectories(query: str, n: int = 3) -> list[dict]:
    data   = _load()
    scored = [
        (_relevance(ep["event"] + " " + ep["outcome"], query) * 0.7
         + _decay(ep["timestamp"], ep.get("strength", 1.0), "episodic") * 0.3, ep)
        for ep in data["episodic"]
        if _relevance(ep["event"] + " " + ep["outcome"], query) > 0.1
    ]
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
    data   = _load()
    scored = [
        (_relevance(pm["task_type"] + " " + pm["what_worked"], query), pm)
        for pm in data["post_mortem"]
        if _relevance(pm["task_type"], query) > 0
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [pm for _, pm in scored[:n]]


# ── Dual-Tier Retrieval (HyMem 2026) ────────────────────────

def semantic_search(query: str, n: int = 5) -> list[dict]:
    """Return semantically relevant memories using keyword overlap and recency."""
    data = _load()
    scored = []
    for sem in data.get("semantic", []):
        score = _relevance(sem.get("pattern", ""), query) + sem.get("confidence", 0.0) * 0.2
        if score > 0.0:
            scored.append((score, sem))
    for ep in data.get("episodic", []):
        score = _relevance(ep.get("event", "") + " " + ep.get("outcome", ""), query) * 0.7 + _recency(ep.get("timestamp", "")) * 0.2
        if score > 0.0:
            scored.append((score, ep))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:n]]


def get_relevant_memories(query: str, n: int = 5, deep: bool = False) -> str:
    data = _load()

    if not deep:
        lines = ["## Memory"]
        for sem in sorted(data["semantic"], key=lambda x: x["confidence"], reverse=True)[:2]:
            if _relevance(sem["pattern"], query) > 0.2:
                lines.append(f"- [learned] {sem['pattern']}")
        for ins in get_insights(query, n=2):
            lines.append(f"- [insight] {ins['insight']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    scored = []
    for ep in data["episodic"]:
        text  = f"{ep['event']} {ep['outcome']}"
        score = (
            _recency(ep["timestamp"])                                       * 0.20
            + _relevance(text, query)                                        * 0.40
            + ep["importance"]                                               * 0.15
            + _decay(ep["timestamp"], ep.get("strength", 1.0), "episodic")  * 0.25
        )
        scored.append((score, "episodic", ep))

    for sem in data["semantic"]:
        score = (
            _recency(sem["timestamp"])                                       * 0.15
            + _relevance(sem["pattern"], query)                              * 0.50
            + sem["confidence"]                                              * 0.25
            + _decay(sem["timestamp"], 1.0, "semantic")                      * 0.10
        )
        scored.append((score, "semantic", sem))

    for ins in data["insights"]:
        score = (
            _relevance(ins["insight"], query)                                * 0.65
            + ins["confidence"]                                              * 0.25
            + _decay(ins["timestamp"], 1.0, "insights")                      * 0.10
        )
        scored.append((score, "insight", ins))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:n]

    if not top:
        return ""

    lines = ["## Relevant Memory"]
    for _, kind, item in top:
        if kind == "episodic":
            # Also pull associated episodes
            lines.append(f"- [event] {item['event']} → {item['outcome']}")
            for assoc in get_associated(item, n=1):
                lines.append(f"    ↳ related: {assoc['event'][:60]} → {assoc['outcome']}")
            reinforce_episodic(item["event"])
        elif kind == "semantic":
            lines.append(f"- [learned] {item['pattern']} ({item['confidence']:.0%})")
        else:
            lines.append(f"- [insight] {item['insight']}")

    return "\n".join(lines)


# ── Memory Consolidation ─────────────────────────────────────
# Cluster episodic → distil into semantic → prune raw episodes

def should_consolidate() -> bool:
    data  = _load()
    count = data.get("consolidation", {}).get("count", 0)
    return count >= CONSOLIDATE_EVERY


async def consolidate_memory(llm) -> None:
    """
    Background consolidation:
    1. Cluster episodic memories by task_type
    2. Distil each cluster into a semantic pattern via LLM
    3. Delete consolidated episodes (keep high-importance ones)
    """
    import asyncio
    data = _load()
    if not data["episodic"]:
        return

    # Group by task_type
    clusters: dict[str, list] = {}
    for ep in data["episodic"]:
        tt = ep.get("task_type", "general")
        clusters.setdefault(tt, []).append(ep)

    new_patterns = []
    for task_type, episodes in clusters.items():
        if len(episodes) < 3:
            continue
        try:
            summaries = "\n".join(
                f"- {ep['event'][:60]} → {ep['outcome']}"
                for ep in episodes[:8]
            )
            r = await asyncio.wait_for(
                llm.chat(
                    system=(
                        "Distil these experiences into ONE reusable pattern. "
                        "Return a single sentence starting with an action verb. "
                        "No JSON, just the sentence."
                    ),
                    messages=[{"role": "user", "content":
                        f"Task type: {task_type}\nExperiences:\n{summaries}"}],
                ),
                timeout=10,
            )
            pattern = r.get("content", "").strip()
            if pattern:
                new_patterns.append((pattern, task_type, episodes))
        except Exception as e:
            logger.debug("Consolidation LLM: %s", e)

    for pattern, task_type, episodes in new_patterns:
        # Store distilled pattern as semantic memory
        avg_conf = sum(ep["importance"] for ep in episodes) / len(episodes)
        existing = [s for s in data["semantic"] if s["pattern"] == pattern]
        if not existing:
            data["semantic"].append({
                "pattern":         pattern,
                "confidence":      min(1.0, avg_conf),
                "times_validated": len(episodes),
                "timestamp":       datetime.now().isoformat(),
            })
        # Prune consolidated episodes (keep importance > 0.8)
        data["episodic"] = [
            ep for ep in data["episodic"]
            if ep not in episodes or ep["importance"] > 0.8
        ]

    data["consolidation"]["count"]    = 0
    data["consolidation"]["last_run"] = datetime.now().isoformat()
    _save(data)
    logger.info("Memory consolidation done — %d new patterns", len(new_patterns))


# ── Complexity Scoring (HyMem) ───────────────────────────────

COMPLEXITY_SIGNALS = {
    "high":   ["build", "create", "deploy", "configure", "analyze", "research",
               "setup", "install", "compare", "generate", "multiple", "then",
               "after", "first", "finally", "step by step"],
    "medium": ["find", "check", "get", "show", "list", "what is", "how to"],
    "low":    ["hi", "hello", "what", "who", "when", "where", "why"],
}


def score_complexity(query: str) -> float:
    q           = query.lower()
    high_hits   = sum(1 for s in COMPLEXITY_SIGNALS["high"]   if s in q)
    medium_hits = sum(1 for s in COMPLEXITY_SIGNALS["medium"] if s in q)
    low_hits    = sum(1 for s in COMPLEXITY_SIGNALS["low"]    if s in q)
    word_count  = len(q.split())
    score = (
        high_hits   * 0.4
        + medium_hits * 0.2
        + (word_count / 20) * 0.3
        - low_hits  * 0.1
    )
    return min(1.0, max(0.0, score))


# ── KV Store ─────────────────────────────────────────────────

def kv_store(key: str, value: str) -> str:
    data = _load()
    data.setdefault("kv", {})
    data["kv"][key] = {"value": value, "timestamp": datetime.now().isoformat()}
    _save(data)
    return f"Stored: {key}"


def get_relevant_kv_facts(query: str, n: int = 3) -> list[dict]:
    data = _load()
    items = []
    q = (query or "").lower()
    query_tokens = set(q.split())
    ip_keywords = {"ip", "address", "public", "external", "my", "what"}

    for key, entry in data.get("kv", {}).items():
        value = str(entry.get("value", ""))
        key_text = f"{key} {value}".lower()
        score = 0.0
        if key.lower() in q:
            score += 0.8
        if q and value.lower() in q:
            score += 0.7
        if any(token in key_text for token in ["ip", "address", "public"]):
            score += 0.2
        if query_tokens and any(token in key_text for token in query_tokens):
            score += 0.3
        if any(token in q for token in ip_keywords) and any(token in key_text for token in ip_keywords):
            score += 0.4
        score += _relevance(key_text, q) * 0.6
        if score > 0.1:
            items.append({"key": key, "value": value, "timestamp": entry.get("timestamp", ""), "score": score})
    items.sort(key=lambda item: item["score"], reverse=True)
    return items[:n]


def kv_get(key: str) -> str:
    data  = _load()
    entry = data.get("kv", {}).get(key)
    if not entry:
        return f"No value found for '{key}'"
    return entry["value"]


def kv_list() -> str:
    data = _load()
    kv   = data.get("kv", {})
    if not kv:
        return "No stored keys"
    lines = [f"{k}: {v['value'][:80]}" for k, v in kv.items()]
    return "\n".join(lines)


def kv_delete(key: str) -> str:
    data = _load()
    if key in data.get("kv", {}):
        del data["kv"][key]
        _save(data)
        return f"Deleted: {key}"
    return f"Key '{key}' not found"


# ── Skill Metrics ─────────────────────────────────────────────

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


# ── History ───────────────────────────────────────────────────

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


# ── Checkpoints ───────────────────────────────────────────────

def add_checkpoint(checkpoint: dict) -> None:
    data = _load()
    data["checkpoints"].append(checkpoint)
    data["checkpoints"] = data["checkpoints"][-MAX_CHECKPOINTS:]
    _save(data)


def load_checkpoints() -> list:
    return _load().get("checkpoints", [])


# ── Legacy shims ──────────────────────────────────────────────

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
    data["episodic"]    = []
    data["semantic"]    = []
    data["insights"]    = []
    data["post_mortem"] = []
    data["kv"]          = {}
    _save(data)


# ── Utils ─────────────────────────────────────────────────────

def get_storage_summary() -> dict:
    data = _load()
    return {
        "episodic":    len(data["episodic"]),
        "semantic":    len(data["semantic"]),
        "insights":    len(data["insights"]),
        "post_mortem": len(data["post_mortem"]),
        "kv_keys":     len(data.get("kv", {})),
        "history":     len(data["history"]),
        "skills":      len(data["skill_metrics"]),
        "file":        str(STORAGE_FILE),
    }


def clear_all() -> None:
    if STORAGE_FILE.exists():
        STORAGE_FILE.unlink()
    for path in MEMORY_FILES.values():
        if path.exists():
            path.unlink()