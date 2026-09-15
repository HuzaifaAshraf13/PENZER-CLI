"""Durable tool-call journal used for crash recovery."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

EVENTS_PATH = Path(".penzer") / "events.jsonl"
_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|credential)\b\s*[:=]\s*([^\s,;]+)"
)


def _redact(value):
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return value


def append_event(event_type: str, run_id: str, payload: dict | None = None) -> dict:
    event = {
        "event_type": str(event_type),
        "run_id": str(run_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": _redact(payload or {}),
    }
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")
    return event


def load_events(run_id: str | None = None) -> list[dict]:
    if not EVENTS_PATH.exists():
        return []
    events = []
    with EVENTS_PATH.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id is None or event.get("run_id") == run_id:
                events.append(event)
    return events


def pending_tool_calls(run_id: str) -> list[dict]:
    pending = {}
    terminal_types = {"tool_finished", "tool_blocked"}
    for event in load_events(run_id):
        payload = event.get("payload") or {}
        key = payload.get("idempotency_key")
        if not key:
            continue
        if event.get("event_type") == "tool_started":
            pending[key] = payload
        elif event.get("event_type") in terminal_types:
            pending.pop(key, None)
    return list(pending.values())


def resolve_pending_tool(run_id: str, pending: dict, decision: str) -> dict:
    event_type = "tool_finished" if str(decision).lower() in {"discard", "finished", "complete"} else "tool_blocked"
    return append_event(event_type, run_id, {
        "idempotency_key": pending.get("idempotency_key"),
        "tool_name": pending.get("tool_name"),
        "decision": decision,
    })
