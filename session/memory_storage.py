"""
session/memory_storage.py
Low-level storage layer for the memory system: on-disk section files,
process-local caching, and combined in-process + cross-process locking.

Nothing in here knows about episodic/semantic/graph *semantics* — it
only knows how to durably read and write a dict of named sections.
Everything above this layer (memory_core.py, memory_graph.py,
memory.py) goes through `_load()` / `_save()` / `_lock()`.

CACHING: every public function used to do a full read of all 11 JSON
section files on every call, even a single `kv_get`. `_load()` now
keeps a process-local cache keyed on the newest mtime across all
storage files. Repeated calls with no intervening write skip the file
reads entirely; a write from *this* process or any other (mtime
changes) invalidates it automatically. `_load()` always hands out a
deep copy of the cached dict, not the cached object itself — so a
caller mutating what it got back can never silently corrupt the cache
without going through `_save()`, regardless of the caller's control
flow. This trades a small amount of CPU (deep-copying an in-memory
dict, typically a few hundred entries at most) for skipping disk reads
entirely on cache hits.

LOCKING: `_lock()` is a combined in-process (threading.RLock) +
cross-process (fcntl.flock) reentrant lock. Every read/modify/write
cycle anywhere in the memory system goes through this, not a raw lock
directly. It tracks nesting depth so a function that calls _load()
and/or _save() from within its own `with _lock():` scope doesn't
self-deadlock on a fresh flock() call.
"""
import json
import logging
import threading
import copy
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path(".penzer") / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

STORAGE_DIR  = Path(".penzer")
STORAGE_FILE = STORAGE_DIR / "session.json"
STORAGE_DIR.mkdir(exist_ok=True)

MEMORY_FILES = {
    "episodic":      MEMORY_DIR / "episodic.json",
    "semantic":      MEMORY_DIR / "semantic.json",
    "insights":      MEMORY_DIR / "insights.json",
    "post_mortem":   MEMORY_DIR / "post_mortem.json",
    "kv":            MEMORY_DIR / "kv.json",
    "history":       MEMORY_DIR / "history.json",
    "skill_metrics": MEMORY_DIR / "skill_metrics.json",
    "checkpoints":   MEMORY_DIR / "checkpoints.json",
    "consolidation": MEMORY_DIR / "consolidation.json",
    "graph_nodes":   MEMORY_DIR / "graph_nodes.json",
    "graph_edges":   MEMORY_DIR / "graph_edges.json",
    "steps":         MEMORY_DIR / "steps.json",
}

LAST_RUN_PATH = STORAGE_DIR / "last_run.json"

MAX_HISTORY = 500


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

# threading.RLock protects _cache_state AND the file read/modify/write
# cycle in _load()/_save() — covers races between threads in the SAME
# process (e.g. cli.py's background MCP server thread vs the
# interactive loop).
_memory_lock = threading.RLock()

# fcntl-based file lock adds cross-PROCESS protection on top — if the
# MCP server (or a second CLI instance) ever runs as a separate OS
# process against the same storage dir, the threading lock alone does
# nothing for that, since each process has its own independent lock
# object. stdlib only (no new dependency); POSIX-only, with a safe
# no-op fallback elsewhere so this is never a hard requirement.
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

_LOCK_FILE_PATH = STORAGE_DIR / ".memory.lock"
_lock_depth = 0
_lock_fh = None


@contextmanager
def _lock():
    """
    Combined in-process + cross-process lock. Every read/modify/write
    cycle in the memory system goes through this, not a raw
    threading.RLock directly.

    Tracks nesting depth: only the OUTERMOST entry actually opens the
    lock file and takes the flock; nested entries just increment/
    decrement the depth counter (already safe to do without their own
    thread-lock, since `with _memory_lock:` below already serializes
    access — only one thread is ever "inside" this function's critical
    section at a time, nested or not). Without this depth tracking,
    any function that calls _load() and/or _save() from within its own
    `with _lock():` scope (nearly all of them) would try to flock() a
    brand-new file handle while the outer call's handle was still
    holding the lock — a guaranteed self-deadlock on the very first
    write, not a rare race.
    """
    global _lock_depth, _lock_fh
    with _memory_lock:
        _lock_depth += 1
        try:
            if not _HAS_FCNTL:
                yield
                return
            if _lock_depth == 1:
                STORAGE_DIR.mkdir(exist_ok=True)
                _lock_fh = open(_LOCK_FILE_PATH, "w")
                fcntl.flock(_lock_fh, fcntl.LOCK_EX)
            yield
        finally:
            _lock_depth -= 1
            if _lock_depth == 0 and _lock_fh is not None:
                fcntl.flock(_lock_fh, fcntl.LOCK_UN)
                _lock_fh.close()
                _lock_fh = None


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
    with _lock():
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
    with _lock():
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
    with _lock():
        data = _load()
        data["history"] = history[-MAX_HISTORY:]
        _save(data)


def clear_history() -> None:
    with _lock():
        data = _load()
        data["history"] = []
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