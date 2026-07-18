"""
session/memory_graph.py
Knowledge Graph (Zep/Graphiti-style temporal facts).

Nodes are entities. Edges are (subject, relation, object) triples that
carry valid_from/valid_until instead of being overwritten. A new fact
that contradicts an existing one invalidates the old edge rather than
deleting or silently replacing it, so:
  - retrieval only ever surfaces currently-valid facts
  - "what did I used to believe" is still answerable from history
  - two disagreeing writes are visible as a conflict, not data loss

NOT currently called from agent.py / remember_user_facts() /
get_relevant_memories() — additive, dormant until wired in. Wiring it
in is a two-line change in those two functions (memory_core.py) when
you're ready: e.g. `extract_triples_heuristic(text)` -> `remember_triple(*t)`
for each triple.

BUGFIX (this pass): `remember_triple()` generated new edge ids as
`f"e{len(data['graph_edges']) + 1}"` — a length-based id. That's only
unique as long as the edge list never shrinks. `prune_invalidated_edges()`
*does* shrink it (it deletes old superseded edges), so after a prune,
the next `remember_triple()` call could mint an id that collides with
an edge already sitting in the list. `_next_edge_id()` now derives the
next id from the max numeric id actually present instead of the count,
so ids stay unique across the full prune/insert lifecycle.
"""
import json
import logging
import re
from datetime import datetime

from .memory_storage import _lock, _load, _save

logger = logging.getLogger(__name__)

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


def _next_edge_id(edges: list[dict]) -> str:
    """
    Derive the next edge id from the max numeric id currently present,
    not from len(edges). len-based ids collide once edges have been
    pruned (see module docstring bugfix note) — e.g. edges e1..e10
    exist, prune_invalidated_edges() removes e3 and e7 leaving 8 edges,
    and a len-based `f"e{len(edges)+1}"` would mint "e9", which never
    existed, but a second prune+insert cycle can absolutely produce a
    collision with a still-live id. Max-based generation is safe
    regardless of how many edges have been removed in between.
    """
    max_id = 0
    for e in edges:
        m = re.match(r"^e(\d+)$", str(e.get("id", "")))
        if m:
            max_id = max(max_id, int(m.group(1)))
    return f"e{max_id + 1}"


def get_or_create_node(name: str, node_type: str = "entity") -> str:
    """Dedupe by normalized name; returns the node id."""
    with _lock():
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
    with _lock():
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
            "id": _next_edge_id(data["graph_edges"]),
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
    with _lock():
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
    with _lock():
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