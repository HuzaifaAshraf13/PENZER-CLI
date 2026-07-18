"""
session/memory_core.py
Research-backed memory subsystems built on top of memory_storage.py:
  - Episodic        : raw events with Ebbinghaus decay (MemoryBank 2024)
  - Semantic        : distilled cross-task insights (ExpeL AAAI 2024)
  - Post-mortems    : verbal Reflexion per task (Shinn 2023)
  - Dual-tier        : fast summaries vs deep scoring (HyMem 2026)
  - KV Store        : explicit user-facing key-value facts
  - Conflict detect : contradicting semantics flagged + resolved
  - Assoc retrieval : chained related episode pull
  - Category decay  : each memory type decays at its own rate

All public functions here take care of their own locking via
`memory_storage._lock()` and go through `memory_storage._load()` /
`_save()` — there is no separate cache or file handling in this
module.
"""
import math
import logging
import re
from datetime import datetime

from .memory_storage import _lock, _load, _save

logger = logging.getLogger(__name__)

MAX_EPISODIC    = 300
MAX_SEMANTIC    = 150
MAX_INSIGHTS    = 100
MAX_POST_MORTEM = 50

CONSOLIDATE_EVERY = 20  # consolidate after every N new episodic entries (see memory.py)

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
    importance: float = 0.5, task_type: str = "",
    iterations_used: int | None = None,
) -> None:
    with _lock():
        data = _load()
        entry = {
            "event":       event,
            "outcome":     outcome,
            "importance":  min(1.0, max(0.0, importance)),
            "task_type":   task_type,
            "timestamp":   datetime.now().isoformat(),
            "strength":    1.0,
            "access_count": 0,
        }
        if iterations_used is not None:
            entry["iterations_used"] = iterations_used
        data["episodic"].append(entry)
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


def estimate_iterations_needed(task_type_hint: str, min_samples: int = 2) -> int | None:
    """
    Look at past successful episodes with an overlapping task_type and
    return a suggested iteration budget, or None if there isn't enough
    data yet. Complements score_complexity()'s purely lexical guess —
    "check open ports" scores as simple by word-pattern alone even
    though it took 7 real tool calls last time; this lets a repeat of
    that kind of task start with a realistic budget instead of relying
    on the iteration-extension mechanism to bail it out again each time.
    Uses the max observed (not average) plus a small margin, since
    under-budgeting costs a full re-run/extension while over-budgeting
    just means an unused ceiling.
    """
    if not task_type_hint:
        return None
    data = _load()
    samples = [
        e["iterations_used"] for e in data["episodic"]
        if e.get("iterations_used")
        and e.get("outcome") == "success"
        and _relevance(e.get("task_type", ""), task_type_hint) > 0.3
    ]
    if len(samples) < min_samples:
        return None
    return max(samples) + 2


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
    with _lock():
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
    with _lock():
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
    with _lock():
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
    with _lock():
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
    with _lock():
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
    with _lock():
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
    with _lock():
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