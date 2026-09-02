"""
session/memory.py
Top-level orchestrator for the memory system. This is the module the
rest of the codebase (agent.py, cli.py, the MCP server, etc.) imports
from — everything from memory_storage.py, memory_core.py, and
memory_graph.py is re-exported here so existing `from session.memory
import X` call sites keep working unchanged after the split.

Split (this pass) out of a single memory.py into:
  session/memory.py            # this file — orchestrator + re-exports
  session/memory_storage.py    # storage/cache/locks
  session/memory_core.py       # episodic/semantic/KV/retrieval
  session/memory_graph.py      # graph subsystem

Owns, in addition to re-exporting the above:
  - skill metrics (per-tool success-rate tracking)
  - checkpoints
  - the structured step log
  - scheduled episodic -> semantic consolidation

BUGFIX (this pass): `append_steps()` generated new step ids as
`next_id = len(data["steps"]) + 1` — a length-based id, same class of
bug as the one fixed in memory_graph.py's edge ids. `data["steps"]` is
truncated to the last MAX_STEPS entries whenever it grows past that
cap, which *shrinks* the list without touching the ids already
assigned to the entries that remain. The next `append_steps()` call
then computes next_id from the new (smaller) length and can mint an id
that's already in use by one of the surviving older steps — two steps
silently sharing an id, which breaks anything that looks a step up by
id (or assumes ids are unique) once the log has wrapped around once.
`_next_step_id()` now derives the next id from the max id actually
present in the log, which stays correct across any number of
truncations.

REFACTOR NOTE (carried over): consolidate_memory() always reloads
`data` right before its final save rather than only when
`consolidated_events` was non-empty — see the docstring on
consolidate_memory() below for why the old conditional reload was a
real data-loss bug (it could silently overwrite semantic.json with a
stale snapshot, wiping out patterns remember_semantic() had just
written in the same run).
"""
import json
import logging
from datetime import datetime

from .memory_storage import (  # noqa: F401  (re-exported for callers)
    MEMORY_DIR,
    STORAGE_DIR,
    STORAGE_FILE,
    MEMORY_FILES,
    LAST_RUN_PATH,
    MAX_HISTORY,
    _lock,
    _load,
    _save,
    save_last_run,
    load_last_run,
    clear_last_run,
    load_history,
    save_history,
    clear_history,
    get_storage_summary,
)
from .memory_core import (  # noqa: F401  (re-exported for callers)
    MAX_EPISODIC,
    MAX_SEMANTIC,
    MAX_INSIGHTS,
    MAX_POST_MORTEM,
    DECAY_RATES,
    SR_INTERVALS,
    NEGATION_PAIRS,
    remember_episodic,
    estimate_iterations_needed,
    reinforce_episodic,
    get_associated,
    get_episode_replay,
    remember_semantic,
    detect_conflict,
    store_insight,
    get_insights,
    get_similar_trajectories,
    store_post_mortem,
    get_post_mortems,
    semantic_search,
    get_relevant_memories,
    kv_store,
    kv_get,
    kv_list,
    kv_delete,
    get_relevant_kv_facts,
    remember_user_facts,
    score_complexity,
)
from .memory_graph import (  # noqa: F401  (re-exported for callers)
    get_or_create_node,
    remember_triple,
    invalidate_triple,
    extract_triples_heuristic,
    extract_triples_llm,
    query_graph,
    get_graph_context,
    prune_invalidated_edges,
)

logger = logging.getLogger(__name__)

MAX_CHECKPOINTS = 5
MAX_STEPS       = 500  # persisted step log, across all runs (see append_steps/get_steps)


class MemoryManager:
    """Frontier-style memory interface for the agent.

    This keeps the existing storage layer intact while exposing a single,
    consistent API for the agent to think in terms of: events, facts, and
    semantic patterns, all retrieved through one context-builder.
    """

    def __init__(self):
        self._last_query = ""

    def remember_event(self, event: str, outcome: str, *, importance: float = 0.5,
                       task_type: str = "", iterations_used: int | None = None) -> dict:
        remember_episodic(event, outcome, importance=importance, task_type=task_type,
                          iterations_used=iterations_used)
        return {"event": event, "outcome": outcome, "task_type": task_type, "importance": importance}

    def remember_fact(self, key: str, value: object) -> dict:
        if not key:
            raise ValueError("Memory fact key cannot be empty")
        kv_store(str(key), value)
        return {"key": str(key), "value": value}

    def remember_semantic(self, pattern: str, confidence: float = 0.6) -> dict:
        remember_semantic(pattern, confidence=confidence)
        return {"pattern": pattern, "confidence": confidence}

    def remember_user_facts(self, text: str) -> list[dict]:
        return remember_user_facts(text)

    def search(self, query: str, n: int = 5) -> list[dict]:
        results = []
        for fact in get_relevant_kv_facts(query, n=n):
            results.append({"kind": "fact", "key": fact.get("key"), "value": fact.get("value")})
        for item in semantic_search(query, n=n):
            results.append({"kind": "semantic", "pattern": item.get("pattern"), "confidence": item.get("confidence")})
        for ep in get_relevant_memories(query, n=n, deep=False).splitlines():
            if ep.strip():
                results.append({"kind": "episodic", "text": ep.strip()})
        return self._dedupe(results)

    @staticmethod
    def _dedupe(items: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[str] = set()
        for item in items:
            marker = json.dumps(item, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped

    def get_context(self, query: str, n: int = 5, deep: bool = False) -> str:
        if not query:
            return ""
        self._last_query = query
        blocks: list[str] = []

        kv_facts = get_relevant_kv_facts(query, n=max(2, n))
        recent_facts = kv_list()
        fact_entries = self._dedupe([
            {"key": f.get("key"), "value": f.get("value")} for f in kv_facts
        ] + [{"key": k, "value": v} for k, v in recent_facts.items()])
        if fact_entries:
            lines = []
            for fact in fact_entries[:n]:
                lines.append(f"- {fact.get('key')}: {fact.get('value')}")
            blocks.append("## Stored Facts\n" + "\n".join(lines))

        semantic_hits = semantic_search(query, n=max(2, n))
        if semantic_hits:
            lines = []
            for item in self._dedupe(semantic_hits)[:n]:
                text = item.get("pattern") or item.get("event") or "related memory"
                lines.append(f"- {text}")
            blocks.append("## Semantic Memory\n" + "\n".join(lines))

        memories = get_relevant_memories(query, n=n, deep=deep)
        if memories and memories.strip():
            blocks.append("## Relevant Memories\n" + memories.strip())

        return "\n\n".join(blocks)

    def get_summary(self) -> dict:
        data = _load()
        return {
            "episodic": len(data["episodic"]),
            "semantic": len(data["semantic"]),
            "facts": len(data["kv"]),
            "graph_edges": len(data["graph_edges"]),
            "steps": len(data["steps"]),
        }

    def clear(self) -> None:
        with _lock():
            data = _load()
            data["episodic"] = []
            data["semantic"] = []
            data["insights"] = []
            data["post_mortem"] = []
            data["kv"] = {}
            data["history"] = []
            data["checkpoints"] = []
            data["graph_nodes"] = {}
            data["graph_edges"] = []
            data["steps"] = []
            data["consolidation"] = {"count": 0, "last_run": ""}
            _save(data)


# -- Skill metrics -------------------------------------------------------
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
    with _lock():
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


# -- Checkpoints -----------------------------------------------------------
def add_checkpoint(checkpoint: dict) -> None:
    with _lock():
        data = _load()
        checkpoint = dict(checkpoint)
        checkpoint.setdefault("timestamp", datetime.now().isoformat())
        data["checkpoints"].append(checkpoint)
        if len(data["checkpoints"]) > MAX_CHECKPOINTS:
            data["checkpoints"] = data["checkpoints"][-MAX_CHECKPOINTS:]
        _save(data)


# -- Step Log (structured, retrievable "what is the agent doing") -----------
#
# Distinct from `history` (the raw LLM message transcript) and `trace`
# (agent.py's own in-memory per-run list) — this is a durable, queryable
# record of human-readable steps, tagged with a `kind` that's intentionally
# open-ended so new step types don't require touching this module. Steps
# are appended in batches (one call per agent iteration, not one call per
# step) for the same reason `get_relevant_memories` batches its
# reinforcement writes — avoiding an extra full load/save cycle per item.
def _next_step_id(steps: list[dict]) -> int:
    """Max-based id generation — see module bugfix note at the top of
    this file for why len-based generation breaks once the step log has
    been truncated at least once."""
    max_id = 0
    for s in steps:
        try:
            max_id = max(max_id, int(s.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return max_id + 1


def append_steps(run_id: str, new_steps: list[dict]) -> None:
    """Append a batch of steps for one run in a single load/save cycle."""
    with _lock():
        if not new_steps:
            return
        data = _load()
        next_id = _next_step_id(data["steps"])
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


def get_run_trace(run_id: str | None = None, n: int = 100) -> list[dict]:
    """Replay a run's persisted step trace for regression and inspection."""
    return get_steps(run_id, n)


def render_run_trace(run_id: str | None = None, n: int = 100) -> str:
    """Render a replayable summary of a persisted run trace."""
    steps = get_run_trace(run_id, n)
    lines = []
    for s in steps:
        lines.append(
            f"{s.get('iteration', '?'):>3} "
            f"{s.get('phase', '?'):<10} "
            f"{s.get('kind', '?'):<12} "
            f"{s.get('description', '')}"
        )
    return "\n".join(lines)


def clear_steps(run_id: str | None = None) -> int:
    """Clear steps, optionally only for one run. Returns count removed."""
    with _lock():
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


# -- Memory Consolidation ------------------------------------------------------
# Cluster episodic -> distil into semantic -> prune raw episodes
CONSOLIDATE_EVERY = 20  # consolidate after every N new episodic entries


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

    `data` is reloaded unconditionally right before the final save, not
    only inside `if consolidated_events:`. remember_semantic() calls
    earlier in this function each do their own independent load/save, so
    the `data` object grabbed at the top of this function goes stale the
    moment the first of those calls runs. Saving that stale copy at the
    end (which happened whenever no cluster qualified for pruning) would
    silently wipe out every semantic pattern this same run had just
    written.
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

    # Always reload before the final save — see fix note in the module
    # and function docstrings above.
    data = _load()
    if consolidated_events:
        data["episodic"] = [
            ep for ep in data["episodic"] if ep["event"] not in consolidated_events
        ]
    data["consolidation"] = {"count": 0, "last_run": datetime.now().isoformat()}
    _save(data)