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
  - Consolidation   : episodic -> semantic distillation on schedule
  - Episodic replay : compressed narrative of past similar runs

CACHING (this pass): every public function used to do a full read of all
11 JSON section files on every call, even a single `kv_get`. `_load()`
now keeps a process-local cache keyed on the newest mtime across all
storage files. Repeated calls with no intervening write skip the file
reads entirely; a write from *this* process or any other (mtime changes)
invalidates it automatically. `_load()` always hands out a deep copy of
the cached dict, not the cached object itself — so a caller mutating
what it got back can never silently corrupt the cache without going
through `_save()`, regardless of the caller's control flow. This trades
a small amount of CPU (deep-copying an in-memory dict, typically a few
hundred entries at most) for skipping disk reads entirely on cache hits.

REFACTOR NOTES (this pass, on top of the previous one):
  - Fixed a real data-loss bug in consolidate_memory(): `data` was
    snapshotted at the top of the function, before the loop that calls
    remember_semantic() (which does its own independent load/save per
    call). The function only reloaded `data` inside the
    `if consolidated_events:` branch. When no cluster qualified for
    pruning (e.g. every episode in it had importance >= 0.7, or no
    cluster reached 3 episodes), `data` stayed stale, and the final
    `_save(data)` at the end overwrote semantic.json (and everything
    else) with that stale snapshot — silently erasing the very semantic
    patterns remember_semantic() had just written. Fix: always reload
    right before the final save, not conditionally.
  - get_relevant_memories(deep=True) called reinforce_episodic() once
    per retrieved item, and that function does its own full
    _load()+_save() cycle (all 11 sections) each time — on top of the
    _load() already done at the top of get_relevant_memories. For n=5
    that's up to 6 full read/write cycles per call, and this runs on
    every agent turn. Factored the mutation into
    `_reinforce_episodic_in_data()` so it can run against the data
    already loaded by the caller; get_relevant_memories now does a
    single _save() at the end instead of one per item.
  - Everything else (the previous pass's `_score_and_rank` extraction,
    the negation-aware detect_conflict fix, get_skill_metric, kv_*,
    the knowledge-graph layer) is unchanged.

KNOWLEDGE GRAPH (Zep/Graphiti-style temporal facts) — unchanged, still
additive and currently NOT wired into remember_user_facts() or
get_relevant_memories(). None of agent.py's imports touch it, so it's
dead code from the running agent's point of view until something calls
extract_triples_heuristic()/remember_triple()/get_graph_context(). Left
as-is rather than guessing you want it live; wiring it in is a two-line
change in those two functions whenever you do.
"""
import json
import math
import logging
import re
import copy
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
    "graph_nodes": MEMORY_DIR / "graph_nodes.json",
    "graph_edges": MEMORY_DIR / "graph_edges.json",
    "steps": MEMORY_DIR / "steps.json",
}
LAST_RUN_PATH = STORAGE_DIR / "last_run.json"

MAX_EPISODIC      = 300
MAX_SEMANTIC       = 150
MAX_INSIGHTS       = 100
MAX_POST_MORTEM    = 50
MAX_HISTORY        = 500
MAX_CHECKPOINTS    = 5
MAX_STEPS          = 500  # persisted step log, across all runs (see append_steps/get_steps)
CONSOLIDATE_EVERY  = 20   # consolidate after every N new episodic entries

# Category-specific half-lives (hours)
DECAY_RATES = {
    "episodic": 72,    # raw events — fade in 3 days
    "semantic": 336,   # learned patterns — fade in 2 weeks
    "insights": 504,   # cross-task rules — fade in 3 weeks
    "kv":       720,   # user facts — fade in 1 month
}
# Spaced repetition intervals (hours)
SR_INTERVALS = [1, 24, 72, 168, 336]

_USER_FACT_PATTERNS = [
    (r"\bmy name is\s+([A-Za-z][A-Za-z.-]+)\b", "user.name"),
    (r"\bcall me\s+([A-Za-z][A-Za-z.-]+)\b", "user.name"),
    (r"\bmy email(?: is|:)?\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", "user.email"),
    (r"\bmy phone(?: is|:)?\s*([+()0-9 .-]{4,})", "user.phone"),
    (r"\bmy (?:public )?ip(?: is|:)?\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", "user.ip"),
    (r"\bmy preference(?: is|:)?\s*([^.!?]+)", "user.preference"),
    (r"\bi prefer\s+([^.!?]+)", "user.preference"),
]

# Rough complexity signals for score_complexity()
_MULTISTEP_WORDS = {
    "then", "after", "next", "finally", "and then", "followed by",
    "once", "before that", "afterwards",
}
_COMPLEX_VERBS = {
    "analyze", "refactor", "migrate", "orchestrate", "integrate",
    "design", "architect", "optimize", "coordinate", "deploy",
}


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
        "graph_nodes":   {},   # node_id -> {name, type, attrs, created_at}
        "graph_edges":   [],   # {id, subject_id, relation, object_id, confidence,
                                #  valid_from, valid_until, source_event}
        "steps":         [],   # {id, run_id, iteration, phase, kind, description, timestamp, ...extra}
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


_cache_state: dict = {"data": None, "mtime": None}


def _newest_mtime() -> float | None:
    """Latest mtime across every storage file. Used to detect whether
    anything has changed on disk (from this process or another) since
    the cache was last populated."""
    try:
        latest = STORAGE_FILE.stat().st_mtime if STORAGE_FILE.exists() else 0.0
        for p in MEMORY_FILES.values():
            if p.exists():
                m = p.stat().st_mtime
                if m > latest:
                    latest = m
        return latest
    except Exception:
        return None  # any stat failure -> treat as "can't trust the cache"


def _load() -> dict:
    global _cache_state
    mtime = _newest_mtime()
    if (
        _cache_state["data"] is not None
        and mtime is not None
        and _cache_state["mtime"] == mtime
    ):
        return copy.deepcopy(_cache_state["data"])

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

    _cache_state = {"data": data, "mtime": mtime}
    return copy.deepcopy(data)


def _save(data: dict) -> None:
    global _cache_state
    try:
        for key in ["episodic", "semantic", "insights", "post_mortem", "kv",
                    "history", "skill_metrics", "checkpoints", "consolidation",
                    "graph_nodes", "graph_edges", "steps"]:
            _save_section(key, data.get(key, _fresh().get(key)))
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        _cache_state = {"data": copy.deepcopy(data), "mtime": _newest_mtime()}
    except Exception as e:
        logger.error("Storage save: %s", e)
        # A failed save may have left disk and cache disagreeing about
        # what's true — force the next _load() to re-read from disk
        # rather than serve a cache that might now be wrong.
        _cache_state = {"data": None, "mtime": None}


def remember_user_facts(text: str) -> list[dict]:
    """Extract simple user facts from natural language and store them in KV memory."""
    if not text:
        return []
    stored = []
    for pattern, key in _USER_FACT_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(1).strip().rstrip(".?!")
            if not value:
                continue
            kv_store(key, value)
            stored.append({"key": key, "value": value})
    return stored


def save_last_run(snapshot: dict) -> None:
    try:
        LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LAST_RUN_PATH, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        logger.error("Save last run: %s", e)


def load_last_run() -> dict | None:
    if not LAST_RUN_PATH.exists():
        return None
    try:
        with open(LAST_RUN_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.error("Load last run: %s", e)
    return None


def clear_last_run() -> None:
    try:
        if LAST_RUN_PATH.exists():
            LAST_RUN_PATH.unlink()
    except Exception as e:
        logger.error("Clear last run: %s", e)


def load_history() -> list:
    data = _load()
    return data.get("history", [])


def save_history(history: list) -> None:
    data = _load()
    data["history"] = history[-MAX_HISTORY:]
    _save(data)


def clear_history() -> None:
    data = _load()
    data["history"] = []
    _save(data)


# -- Decay --------------------------------------------------------------
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


def _score_and_rank(items: list, score_fn, n: int = 5, min_score: float = 0.0) -> list:
    """
    Shared scored-retrieval helper.
    score_fn(item) -> float. Items scoring <= min_score are dropped, the
    rest are sorted descending and truncated to n.
    """
    scored = [(score_fn(item), item) for item in items]
    scored = [(s, item) for s, item in scored if s > min_score]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:n]]


# -- Episodic Memory ------------------------------------------------------
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


def _reinforce_episodic_in_data(data: dict, event_text: str) -> None:
    """Mutates `data["episodic"]` in place. Shared by `reinforce_episodic()`
    (which wraps this with its own load/save for standalone callers) and
    `get_relevant_memories()` (which batches all reinforcements into the
    single `data` object it already loaded, instead of one load/save per
    retrieved item)."""
    for ep in data["episodic"]:
        if event_text[:50] in ep["event"]:
            count              = ep.get("access_count", 0) + 1
            interval           = SR_INTERVALS[min(count, len(SR_INTERVALS) - 1)]
            ep["strength"]     = min(3.0, interval / SR_INTERVALS[-1] * 3.0)
            ep["timestamp"]    = datetime.now().isoformat()
            ep["access_count"] = count


def reinforce_episodic(event_text: str) -> None:
    """Spaced repetition — each access extends the half-life."""
    data = _load()
    _reinforce_episodic_in_data(data, event_text)
    _save(data)


# -- Associative Retrieval -------------------------------------------------
# When one episode is retrieved, chain-pull related ones
def get_associated(event: dict, n: int = 2) -> list[dict]:
    """Pull episodes related to the given one by task_type or keyword overlap."""
    data    = _load()
    base_tt = event.get("task_type", "")
    base_ev = event.get("event", "")

    def score(ep: dict) -> float:
        if ep is event or ep.get("event") == base_ev:
            return 0.0
        type_match    = 1.0 if ep.get("task_type") == base_tt and base_tt else 0.0
        keyword_match = _relevance(ep["event"], base_ev)
        return type_match * 0.6 + keyword_match * 0.4

    return _score_and_rank(data["episodic"], score, n=n, min_score=0.1)


# -- Episodic Replay --------------------------------------------------------
# Compressed narrative of past relevant runs for task context
def get_episode_replay(query: str, n: int = 3) -> str:
    """
    Returns a compressed narrative of the N most relevant past episodes.
    Format: "Last time [task]: tried [tools], [outcome]. What worked: [X]."
    """
    data = _load()

    def score(ep: dict) -> float:
        rel = _relevance(ep["event"] + " " + ep["task_type"], query)
        if rel <= 0.1:
            return 0.0
        dec = _decay(ep["timestamp"], ep.get("strength", 1.0), "episodic")
        return rel * 0.6 + dec * 0.4

    top = _score_and_rank(data["episodic"], score, n=n, min_score=0.0)
    if not top:
        return ""
    lines = ["## Episode Replay (past similar runs)"]
    for ep in top:
        lines.append(f"- [{ep.get('task_type', 'task')}] {ep['event'][:80]} -> {ep['outcome']}")
        for assoc in get_associated(ep, n=1):
            lines.append(f"    related: {assoc['event'][:60]} -> {assoc['outcome']}")
    return "\n".join(lines)


# -- Semantic Memory --------------------------------------------------------
def remember_semantic(pattern: str, confidence: float = 0.6) -> None:
    """Store + run conflict detection before saving."""
    data = _load()
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
        data["semantic"] = sorted(data["semantic"], key=lambda x: x["confidence"])[-MAX_SEMANTIC:]
    _save(data)


# -- Conflict Detection -----------------------------------------------------
# Detect contradicting semantic memories — same topic, opposite advice
NEGATION_PAIRS = [
    ("always", "never"), ("use", "avoid"), ("works", "fails"),
    ("do", "don't"), ("can", "cannot"), ("enable", "disable"),
]
_NEGATION_WORDS = {w for pair in NEGATION_PAIRS for w in pair}


def detect_conflict(new_pattern: str, existing: list[dict]) -> dict | None:
    """
    Check if new_pattern contradicts an existing semantic memory.
    Returns the conflicting entry if found, else None.
    Topic overlap excludes the negation-pair words themselves, so common
    low-signal words ("do"/"use"/"can") don't register unrelated
    sentences as false conflicts. Requires >= 0.5 topic overlap before a
    negation pair is even checked.
    """
    new_words = set(new_pattern.lower().split())
    new_topic = new_words - _NEGATION_WORDS
    for sem in existing:
        old_words = set(sem["pattern"].lower().split())
        old_topic = old_words - _NEGATION_WORDS
        topic_overlap = len(new_topic & old_topic) / max(len(new_topic), 1)
        if topic_overlap < 0.5:
            continue
        for pos, neg in NEGATION_PAIRS:
            if (pos in new_words and neg in old_words) or \
               (neg in new_words and pos in old_words):
                return sem
    return None


# -- Insight Extraction (ExpeL) ---------------------------------------------
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
        data["insights"] = sorted(data["insights"], key=lambda x: x["confidence"])[-MAX_INSIGHTS:]
    _save(data)


def get_insights(query: str, n: int = 3) -> list[dict]:
    data = _load()

    def score(i: dict) -> float:
        rel = _relevance(i["insight"], query)
        return rel * 0.6 + i["confidence"] * 0.4 if rel > 0 else 0.0

    return _score_and_rank(data["insights"], score, n=n, min_score=0.0)


# -- Trajectory Retrieval (ExpeL) --------------------------------------------
def get_similar_trajectories(query: str, n: int = 3) -> list[dict]:
    data = _load()

    def score(ep: dict) -> float:
        rel = _relevance(ep["event"] + " " + ep["outcome"], query)
        if rel <= 0.1:
            return 0.0
        return rel * 0.7 + _decay(ep["timestamp"], ep.get("strength", 1.0), "episodic") * 0.3

    return _score_and_rank(data["episodic"], score, n=n, min_score=0.0)


# -- Post-Mortem (Reflexion) -------------------------------------------------
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

    def score(pm: dict) -> float:
        gate = _relevance(pm["task_type"], query)
        if gate <= 0:
            return 0.0
        return _relevance(pm["task_type"] + " " + pm["what_worked"], query)

    return _score_and_rank(data["post_mortem"], score, n=n, min_score=0.0)


# -- Dual-Tier Retrieval (HyMem 2026) ----------------------------------------
def semantic_search(query: str, n: int = 5) -> list[dict]:
    """Return semantically relevant memories using keyword overlap and recency."""
    data = _load()
    pool = list(data.get("semantic", [])) + list(data.get("episodic", []))

    def score(item: dict) -> float:
        if "pattern" in item:
            return _relevance(item.get("pattern", ""), query) + item.get("confidence", 0.0) * 0.2
        return _relevance(item.get("event", "") + " " + item.get("outcome", ""), query) * 0.7 \
            + _recency(item.get("timestamp", "")) * 0.2

    return _score_and_rank(pool, score, n=n, min_score=0.0)


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

    def score(entry) -> float:
        kind, item = entry
        if kind == "episodic":
            text = f"{item['event']} {item['outcome']}"
            return (
                _recency(item["timestamp"]) * 0.20
                + _relevance(text, query) * 0.40
                + item["importance"] * 0.15
                + _decay(item["timestamp"], item.get("strength", 1.0), "episodic") * 0.25
            )
        if kind == "semantic":
            return (
                _recency(item["timestamp"]) * 0.15
                + _relevance(item["pattern"], query) * 0.50
                + item["confidence"] * 0.25
                + _decay(item["timestamp"], 1.0, "semantic") * 0.10
            )
        # insight
        return (
            _relevance(item["insight"], query) * 0.65
            + item["confidence"] * 0.25
            + _decay(item["timestamp"], 1.0, "insights") * 0.10
        )

    pool = (
        [("episodic", ep) for ep in data["episodic"]]
        + [("semantic", sem) for sem in data["semantic"]]
        + [("insight", ins) for ins in data["insights"]]
    )
    top = _score_and_rank(pool, score, n=n, min_score=0.0)
    if not top:
        return ""

    lines = ["## Relevant Memory"]
    reinforced_any = False
    for kind, item in top:
        if kind == "episodic":
            lines.append(f"- [event] {item['event']} -> {item['outcome']}")
            for assoc in get_associated(item, n=1):
                lines.append(f"    -> related: {assoc['event'][:60]} -> {assoc['outcome']}")
            # Batched into this function's own `data` instead of a
            # separate full load/save per item (was up to n extra full
            # read/write cycles per call).
            _reinforce_episodic_in_data(data, item["event"])
            reinforced_any = True
        elif kind == "semantic":
            lines.append(f"- [learned] {item['pattern']} ({item['confidence']:.0%})")
        else:
            lines.append(f"- [insight] {item['insight']}")
    if reinforced_any:
        _save(data)
    return "\n".join(lines)


# -- KV Store (explicit user facts) ------------------------------------------
def _normalize_kv_entry(entry) -> dict:
    """
    Coerce whatever is on disk for a KV entry into {"value", "timestamp"}.
    Handles: missing entry, or a legacy flat value stored directly under
    the key (no timestamp wrapper).
    """
    if isinstance(entry, dict) and "value" in entry:
        return {"value": entry["value"], "timestamp": entry.get("timestamp", datetime.now().isoformat())}
    if entry is not None:
        return {"value": entry, "timestamp": datetime.now().isoformat()}
    return None


def kv_store(key: str, value) -> str:
    """Returns a confirmation string, not None — agent.py's memory tool
    passes this return value straight back to the LLM as tool output."""
    data = _load()
    data["kv"][key] = {"value": value, "timestamp": datetime.now().isoformat()}
    _save(data)
    return f"Stored {key} = {value}"


def kv_get(key: str, default=None):
    data  = _load()
    entry = _normalize_kv_entry(data["kv"].get(key))
    if entry:
        return entry["value"]
    return default if default is not None else f"No value stored for '{key}'"


def kv_list() -> dict:
    data = _load()
    out = {}
    for k, v in data["kv"].items():
        entry = _normalize_kv_entry(v)
        if entry:
            out[k] = entry["value"]
    return out


def kv_delete(key: str) -> str:
    data = _load()
    if key in data["kv"]:
        del data["kv"][key]
        _save(data)
        return f"Deleted {key}"
    return f"No value stored for '{key}'"


def get_relevant_kv_facts(query: str, n: int = 3) -> list[dict]:
    """Return KV facts whose key/value overlaps the query, decayed by the 'kv' category."""
    data = _load()
    normalized = {k: _normalize_kv_entry(v) for k, v in data["kv"].items()}
    normalized = {k: v for k, v in normalized.items() if v}

    def score(entry) -> float:
        key, val = entry
        text = f"{key} {val['value']}"
        rel  = _relevance(text, query)
        if rel <= 0:
            return 0.0
        return rel * 0.7 + _decay(val["timestamp"], 1.0, "kv") * 0.3

    top = _score_and_rank(list(normalized.items()), score, n=n, min_score=0.0)
    return [{"key": k, "value": v["value"]} for k, v in top]


# -- Knowledge Graph (Zep/Graphiti-style temporal facts) ----------------------
#
# Nodes are entities. Edges are (subject, relation, object) triples that
# carry valid_from/valid_until instead of being overwritten. A new fact
# that contradicts an existing one invalidates the old edge rather than
# deleting or silently replacing it, so:
#   - retrieval only ever surfaces currently-valid facts
#   - "what did I used to believe" is still answerable from history
#   - two disagreeing writes are visible as a conflict, not data loss
#
# NOT currently called from agent.py / remember_user_facts /
# get_relevant_memories — additive, dormant until wired in.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "am",
    "to", "of", "in", "on", "at", "for", "with", "and", "or", "my", "i",
    "it", "this", "that", "as", "by",
}
_TRIPLE_PATTERNS = [
    # "my X is Y" / "my X are Y"
    (r"\bmy ([a-z0-9_ ]{2,30}?) (?:is|are)\s+([^.!?]+)", "has"),
    # "I use X" / "I prefer X" / "I like X"
    (r"\bi (?:use|prefer|like)\s+([^.!?]+)", "user_prefers"),
    # "X uses Y" / "X runs on Y" / "X is built on Y"
    (r"\b([a-z0-9_-]{2,30}) (?:uses|runs on|is built on|depends on)\s+([^.!?]+)", "uses"),
    # "X works at Y" / "X lives in Y"
    (r"\bi work at\s+([^.!?]+)", "works_at"),
    (r"\bi live in\s+([^.!?]+)", "lives_in"),
]


def _now() -> str:
    return datetime.now().isoformat()


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower()).strip(".,!? ")


def get_or_create_node(name: str, node_type: str = "entity") -> str:
    """Dedupe by normalized name; returns the node id."""
    norm = _norm_name(name)
    if not norm:
        return ""
    data = _load()
    for node_id, node in data["graph_nodes"].items():
        if _norm_name(node["name"]) == norm:
            return node_id
    node_id = f"n{len(data['graph_nodes']) + 1}_{abs(hash(norm)) % 100000}"
    data["graph_nodes"][node_id] = {
        "name": name.strip(),
        "type": node_type,
        "attrs": {},
        "created_at": _now(),
    }
    _save(data)
    return node_id


def remember_triple(
    subject: str, relation: str, obj: str,
    confidence: float = 0.8, source_event: str = "",
) -> None:
    """
    Store a (subject, relation, object) fact.
    If a currently-valid edge already exists for this (subject, relation)
    pointing at a DIFFERENT object, that old edge is invalidated
    (valid_until set to now) rather than deleted or overwritten.
    """
    subj_id = get_or_create_node(subject)
    obj_id  = get_or_create_node(obj)
    if not subj_id or not obj_id:
        return
    data = _load()
    now  = _now()
    for edge in data["graph_edges"]:
        if (edge["subject_id"] == subj_id and edge["relation"] == relation
                and edge["valid_until"] is None):
            if edge["object_id"] == obj_id:
                # Same fact restated — just bump confidence, no new edge needed.
                edge["confidence"] = min(1.0, edge["confidence"] + 0.05)
                _save(data)
                return
            # Contradiction: invalidate the old edge, don't delete it.
            edge["valid_until"] = now
    data["graph_edges"].append({
        "id": f"e{len(data['graph_edges']) + 1}",
        "subject_id": subj_id,
        "relation": relation,
        "object_id": obj_id,
        "confidence": confidence,
        "valid_from": now,
        "valid_until": None,
        "source_event": source_event[:100],
    })
    _save(data)


def invalidate_triple(subject: str, relation: str) -> bool:
    """Explicitly mark all currently-valid edges for (subject, relation) as no longer true."""
    subj_id = get_or_create_node(subject)
    data = _load()
    changed = False
    for edge in data["graph_edges"]:
        if (edge["subject_id"] == subj_id and edge["relation"] == relation
                and edge["valid_until"] is None):
            edge["valid_until"] = _now()
            changed = True
    if changed:
        _save(data)
    return changed


def extract_triples_heuristic(text: str) -> list[tuple[str, str, str]]:
    """
    Fast, no-LLM triple extraction using a handful of common patterns.
    Deliberately narrow (precision over recall) — false triples pollute
    the graph, and this runs on every turn, so it stays conservative.
    Use extract_triples_llm() when you want fuller coverage.
    """
    if not text:
        return []
    triples = []
    lowered = text.lower()
    for pattern, relation in _TRIPLE_PATTERNS:
        for m in re.finditer(pattern, lowered):
            groups = [g.strip().rstrip(".!?") for g in m.groups() if g]
            if len(groups) == 2:
                subj, obj = groups
            elif len(groups) == 1:
                subj, obj = "user", groups[0]
            else:
                continue
            if subj and obj and subj not in _STOPWORDS and obj not in _STOPWORDS:
                triples.append((subj, relation, obj))
    return triples


async def extract_triples_llm(text: str, llm) -> list[tuple[str, str, str]]:
    """
    Higher-recall extraction via the agent's own LLM. Returns [] on any
    failure so callers can always fall back to the heuristic extractor.
    """
    if not text:
        return []
    try:
        import asyncio
        r = await asyncio.wait_for(
            llm.chat(
                system=(
                    "Extract factual (subject, relation, object) triples from the text. "
                    "Only extract durable facts (preferences, ownership, location, tools "
                    "used, roles) — not one-off actions. Return a JSON array of 3-element "
                    'arrays: [["subject", "relation", "object"], ...]. Empty array if none. '
                    "No markdown, no preamble."
                ),
                messages=[{"role": "user", "content": text}],
            ),
            timeout=10,
        )
        raw  = r.get("content", "[]").strip().strip("```").lstrip("json").strip()
        rows = json.loads(raw)
        return [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in rows
            if isinstance(row, list) and len(row) == 3
        ]
    except Exception as e:
        logger.debug("Triple extraction: %s", e)
        return []


def query_graph(entity_name: str, hops: int = 1) -> list[dict]:
    """
    BFS from a named entity through currently-valid edges only.
    Returns a flat list of {subject, relation, object, confidence}.
    """
    data = _load()
    norm = _norm_name(entity_name)
    start_ids = {
        node_id for node_id, node in data["graph_nodes"].items()
        if norm in _norm_name(node["name"]) or _norm_name(node["name"]) in norm
    }
    if not start_ids:
        return []
    id_to_name = {nid: n["name"] for nid, n in data["graph_nodes"].items()}
    visited = set(start_ids)
    frontier = set(start_ids)
    facts = []
    for _ in range(max(1, hops)):
        next_frontier = set()
        for edge in data["graph_edges"]:
            if edge["valid_until"] is not None:
                continue  # superseded fact — skip
            if edge["subject_id"] in frontier or edge["object_id"] in frontier:
                facts.append({
                    "subject": id_to_name.get(edge["subject_id"], "?"),
                    "relation": edge["relation"],
                    "object": id_to_name.get(edge["object_id"], "?"),
                    "confidence": edge["confidence"],
                })
                next_frontier.update([edge["subject_id"], edge["object_id"]])
        next_frontier -= visited
        if not next_frontier:
            break
        visited |= next_frontier
        frontier = next_frontier
    return facts


def get_graph_context(query: str, n: int = 5) -> str:
    """
    Match query words against node names, pull currently-valid facts for
    the matched entities, and format for prompt injection.
    """
    data = _load()
    q_words = {w for w in query.lower().split() if w not in _STOPWORDS and len(w) > 2}
    if not q_words:
        return ""
    matched_ids = [
        node_id for node_id, node in data["graph_nodes"].items()
        if any(w in _norm_name(node["name"]) for w in q_words)
    ]
    if not matched_ids:
        return ""
    id_to_name = {nid: n["name"] for nid, n in data["graph_nodes"].items()}
    seen = set()
    lines = ["## Knowledge Graph"]
    for node_id in matched_ids:
        for edge in data["graph_edges"]:
            if edge["valid_until"] is not None:
                continue
            if edge["subject_id"] != node_id and edge["object_id"] != node_id:
                continue
            key = edge["id"]
            if key in seen:
                continue
            seen.add(key)
            subj = id_to_name.get(edge["subject_id"], "?")
            obj  = id_to_name.get(edge["object_id"], "?")
            lines.append(f"- {subj} --[{edge['relation']}]--> {obj} ({edge['confidence']:.0%})")
            if len(lines) - 1 >= n:
                return "\n".join(lines)
    return "\n".join(lines) if len(lines) > 1 else ""


def prune_invalidated_edges(older_than_days: int = 90) -> int:
    """
    Superseded edges (valid_until set) are kept for a while so history
    stays inspectable, then pruned. Currently-valid edges (valid_until
    is None) are never pruned by this. Returns count removed.
    """
    data = _load()
    cutoff_hours = older_than_days * 24
    before = len(data["graph_edges"])

    def keep(edge: dict) -> bool:
        if edge["valid_until"] is None:
            return True
        try:
            hours = (datetime.now() - datetime.fromisoformat(edge["valid_until"])).total_seconds() / 3600
            return hours < cutoff_hours
        except Exception:
            return True

    data["graph_edges"] = [e for e in data["graph_edges"] if keep(e)]
    removed = before - len(data["graph_edges"])
    if removed:
        _save(data)
    return removed


# -- Step Log (structured, retrievable "what is the agent doing") -----------
#
# Distinct from `history` (the raw LLM message transcript) and `trace`
# (agent.py's own in-memory per-run list) — this is a durable, queryable
# record of human-readable steps, tagged with a `kind` that's intentionally
# open-ended so new step types don't require touching this module. Steps
# are appended in batches (one call per agent iteration, not one call per
# step) for the same reason `get_relevant_memories` batches its
# reinforcement writes — avoiding an extra full load/save cycle per item.
def append_steps(run_id: str, new_steps: list[dict]) -> None:
    """Append a batch of steps for one run in a single load/save cycle."""
    if not new_steps:
        return
    data = _load()
    next_id = len(data["steps"]) + 1
    for i, s in enumerate(new_steps):
        entry = dict(s)
        entry["run_id"] = run_id
        entry.setdefault("timestamp", datetime.now().isoformat())
        entry["id"] = next_id + i
        data["steps"].append(entry)
    if len(data["steps"]) > MAX_STEPS:
        data["steps"] = data["steps"][-MAX_STEPS:]
    _save(data)


def get_steps(run_id: str | None = None, n: int = 100) -> list[dict]:
    """Retrieve the most recent steps, optionally filtered to one run.
    Works from any process — a UI or a different agent instance can call
    this to show what a specific run (past or in-progress) actually did."""
    data = _load()
    steps = data["steps"]
    if run_id:
        steps = [s for s in steps if s.get("run_id") == run_id]
    return steps[-n:]


def clear_steps(run_id: str | None = None) -> int:
    """Clear steps, optionally only for one run. Returns count removed."""
    data = _load()
    before = len(data["steps"])
    if run_id:
        data["steps"] = [s for s in data["steps"] if s.get("run_id") != run_id]
    else:
        data["steps"] = []
    removed = before - len(data["steps"])
    if removed:
        _save(data)
    return removed


# -- Complexity scoring -------------------------------------------------------
def score_complexity(text: str) -> float:
    """
    Heuristic 0.0-1.0 complexity score used to pick iteration budget.
    Signals: length, multi-step language, count of distinct action verbs,
    presence of conjunctions chaining several asks together.
    """
    if not text:
        return 0.0
    lowered = text.lower()
    words   = lowered.split()
    score   = 0.0
    score += min(0.3, len(words) / 200 * 0.3)
    step_hits = sum(1 for w in _MULTISTEP_WORDS if w in lowered)
    score += min(0.3, step_hits * 0.15)
    verb_hits = sum(1 for v in _COMPLEX_VERBS if v in lowered)
    score += min(0.25, verb_hits * 0.12)
    conj_hits = lowered.count(" and ") + lowered.count(",")
    score += min(0.15, conj_hits * 0.03)
    return round(min(1.0, score), 3)


# -- Skill metrics & checkpoints ----------------------------------------------
def _normalize_skill_metric(m) -> dict:
    """
    Coerce whatever is already on disk for a skill into the
    {"uses", "successes"} shape. Handles: missing entry, a bare int
    (legacy "count"-only schema), or a dict missing one of the keys.
    """
    if isinstance(m, dict):
        return {"uses": m.get("uses", 0), "successes": m.get("successes", 0)}
    if isinstance(m, (int, float)):
        return {"uses": int(m), "successes": 0}
    return {"uses": 0, "successes": 0}


def update_skill_metric(skill_name: str, success: bool) -> None:
    data = _load()
    m = _normalize_skill_metric(data["skill_metrics"].get(skill_name))
    m["uses"] += 1
    if success:
        m["successes"] += 1
    data["skill_metrics"][skill_name] = m
    _save(data)


def get_skill_metric(skill_name: str) -> dict:
    """
    agent.py's `_tool_confidence` imports this and calls it every time a
    tool is scored.
    """
    data = _load()
    m = _normalize_skill_metric(data["skill_metrics"].get(skill_name))
    uses = m["uses"]
    success_rate = min(1.0, m["successes"] / uses) if uses else 0.5  # neutral prior
    return {"uses": uses, "successes": m["successes"], "success_rate": success_rate}


def add_checkpoint(checkpoint: dict) -> None:
    data = _load()
    checkpoint = dict(checkpoint)
    checkpoint.setdefault("timestamp", datetime.now().isoformat())
    data["checkpoints"].append(checkpoint)
    if len(data["checkpoints"]) > MAX_CHECKPOINTS:
        data["checkpoints"] = data["checkpoints"][-MAX_CHECKPOINTS:]
    _save(data)


def get_storage_summary() -> dict:
    data = _load()
    return {
        "episodic":      len(data["episodic"]),
        "semantic":      len(data["semantic"]),
        "insights":      len(data["insights"]),
        "post_mortem":   len(data["post_mortem"]),
        "kv":            len(data["kv"]),
        "skill_metrics": len(data["skill_metrics"]),
        "checkpoints":   len(data["checkpoints"]),
        "pending_consolidation": data["consolidation"].get("count", 0),
    }


# -- Memory Consolidation ------------------------------------------------------
# Cluster episodic -> distil into semantic -> prune raw episodes
def should_consolidate() -> bool:
    data  = _load()
    count = data.get("consolidation", {}).get("count", 0)
    return count >= CONSOLIDATE_EVERY


async def consolidate_memory(llm) -> None:
    """
    Background consolidation:
    1. Cluster episodic memories by task_type
    2. Distil each cluster into a semantic pattern via LLM
    3. Store the distilled pattern, then prune the consolidated episodes
       (keep high-importance ones, since they still carry unique detail)

    Fix: `data` is now reloaded unconditionally right before the final
    save, not only inside `if consolidated_events:`. remember_semantic()
    calls earlier in this function each do their own independent
    load/save, so the `data` object grabbed at the top of this function
    goes stale the moment the first of those calls runs. Saving that
    stale copy at the end (which happened whenever no cluster qualified
    for pruning) silently wiped out every semantic pattern this same run
    had just written.
    """
    import asyncio
    data = _load()
    if not data["episodic"]:
        return
    clusters: dict[str, list] = {}
    for ep in data["episodic"]:
        tt = ep.get("task_type", "general")
        clusters.setdefault(tt, []).append(ep)

    consolidated_events = set()
    for task_type, episodes in clusters.items():
        if len(episodes) < 3:
            continue
        try:
            summaries = "\n".join(
                f"- {ep['event'][:60]} -> {ep['outcome']}"
                for ep in episodes[:8]
            )
            r = await asyncio.wait_for(
                llm.chat(
                    system=(
                        "Distil these experiences into ONE reusable pattern. "
                        "Return a single sentence describing what tends to work "
                        "or fail for this kind of task. No preamble."
                    ),
                    messages=[{"role": "user", "content": summaries}],
                ),
                timeout=30,
            )
            pattern = str(r.get("content", "")).strip()
            if pattern:
                remember_semantic(pattern, confidence=0.6)
                for ep in episodes[:8]:
                    if ep.get("importance", 0.5) < 0.7:
                        consolidated_events.add(ep["event"])
        except Exception as e:
            logger.debug("Consolidation failed for %s: %s", task_type, e)

    # Always reload before the final save — see fix note above.
    data = _load()
    if consolidated_events:
        data["episodic"] = [
            ep for ep in data["episodic"] if ep["event"] not in consolidated_events
        ]
    data["consolidation"] = {"count": 0, "last_run": datetime.now().isoformat()}
    _save(data)