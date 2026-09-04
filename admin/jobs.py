"""Explicit one-off maintenance jobs for Penzer."""

from __future__ import annotations

import time
from pathlib import Path


def cleanup_old_sessions(max_age_days: int = 30) -> int:
    """Remove stale session snapshots and return the number removed."""
    root = Path(__file__).resolve().parent.parent / "session"
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in root.glob("*.json"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def consolidate_memory_if_needed() -> bool:
    """Run memory consolidation when the configured backend requests it."""
    from session.memory import should_consolidate
    return bool(should_consolidate())


def reindex_skills() -> int:
    """Load skills and return the number available to the runtime."""
    from agent.skills import load_all_skills
    data = load_all_skills()
    return len(data.get("core", [])) + len(data.get("generated", []))
