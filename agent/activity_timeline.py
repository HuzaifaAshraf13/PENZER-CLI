from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DETAIL_LIMIT_COMPACT = 60
DETAIL_LIMIT_FULL = 120


class ActivityTimeline:
    def __init__(self, stream_handler: Callable[[dict], None] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._stream_handler = stream_handler

    def set_stream_handler(self, handler: Callable[[dict], None] | None) -> None:
        """Replace the optional consumer used by interactive renderers."""
        self._stream_handler = handler

    def add_event(self, event: dict[str, Any]) -> str:
        event = dict(event)
        if not event.get("id"):
            event["id"] = f"evt-{uuid.uuid4().hex[:8]}"
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("status", "running")
        # copy details so callers reusing a shared dict template don't alias it
        event["details"] = dict(event.get("details") or {})
        self.events.append(event)
        if self._stream_handler is not None:
            self._stream_handler(dict(event, details=dict(event["details"])))
        return event["id"]

    def update_event(self, event_id: str, **updates: Any) -> dict[str, Any] | None:
        for event in self.events:
            if event.get("id") != event_id:
                continue
            event.update(updates)
            if self._stream_handler is not None:
                self._stream_handler(dict(event, details=dict(event.get("details") or {})))
            return event
        return None

    def render_text(self) -> str:
        if not self.events:
            return "[timeline] no activity yet"
        lines: list[str] = []
        for event in self.events:
            icon = _icon_for_status(event.get("status", "running"))
            title = event.get("title") or event.get("event_type") or "Activity"
            message = event.get("message") or ""
            event_id = event.get("id", "")
            line = f"{icon} [{event.get('event_type', 'activity')}] {title}"
            if message:
                line += f" — {message}"
            if event_id:
                line += f" ({event_id})"
            lines.append(line)
            lines.extend(_format_details(event.get("details") or {}))
        return "\n".join(lines)

    def render_event(self, event_id: str) -> str:
        for event in self.events:
            if event.get("id") == event_id:
                return self._render_single_event(event)
        return ""

    def _render_single_event(self, event: dict[str, Any]) -> str:
        icon = _icon_for_status(event.get("status", "running"))
        title = event.get("title") or event.get("event_type") or "Activity"
        message = event.get("message") or ""
        bits = [f"{icon} {title}"]
        if message:
            bits.append(message)
        details = event.get("details") or {}
        if details:
            detail_text = ", ".join(f"{k}={v}" for k, v in details.items() if v not in (None, ""))
            if detail_text:
                bits.append(detail_text)
        return " | ".join(bits)

    def render_drawer(self) -> str:
        if not self.events:
            return "[timeline drawer] no activity recorded"
        events = sorted(self.events, key=lambda evt: evt.get("timestamp", ""))

        total_duration = 0.0
        for event in events:
            duration = (event.get("details") or {}).get("duration_sec")
            if isinstance(duration, (int, float)):
                total_duration += float(duration)

        running = sum(1 for event in events if event.get("status") == "running")
        success = sum(1 for event in events if event.get("status") == "success")
        failed = sum(1 for event in events if event.get("status") == "failed")
        completed = sum(1 for event in events if event.get("status") != "running")

        lines: list[str] = ["[timeline drawer] Execution activity"]
        lines.append(
            f"Total events: {len(events)}  |  completed: {completed}/{len(events)}  "
            f"running: {running}  success: {success}  failed: {failed}  |  "
            f"Duration: {total_duration:.1f}s"
        )
        lines.append("")

        current_type = None
        for event in events:
            event_type = event.get("event_type", "other")
            heading = _drawer_heading(event_type)
            if heading != current_type:
                lines.append(heading)
                current_type = heading
            lines.extend(self._render_drawer_card(event))
            lines.append("")
        return "\n".join(lines).rstrip()

    def _render_drawer_card(self, event: dict[str, Any]) -> list[str]:
        icon = _drawer_icon(event.get("event_type", "other"))
        status = event.get("status", "running")
        title = event.get("title") or event.get("event_type") or "Activity"
        message = event.get("message") or ""
        status_badge = {
            "running": "[yellow]running[/yellow]",
            "success": "[green]success[/green]",
            "failed": "[red]failed[/red]",
            "warning": "[orange1]warning[/orange1]",
        }.get(status, status)

        header = f"{icon} {title}"
        if message:
            header += f" — {message}"
        header += f" [{status_badge}]"

        details = event.get("details") or {}
        card_lines = [header]
        compact = status == "success"

        event_type = event.get("event_type")
        handler = self._DRAWER_HANDLERS.get(event_type, _render_generic_details)
        card_lines.extend(handler(details, compact))
        return card_lines

    def render_summary(self) -> str:
        if not self.events:
            return "[timeline] no activity recorded"
        summary: list[str] = ["[timeline summary]"]
        summary_events = [event for event in self.events if event.get("event_type") == "summary"]
        for event in summary_events:
            summary.append(f"- {event.get('title')}: {event.get('message')}")
            for key, value in (event.get("details") or {}).items():
                if value in (None, ""):
                    continue
                summary.append(f"    • {key}: {value}")
        issues = [event for event in self.events if event.get("status") in {"failed", "warning"}]
        for event in issues:
            summary.append(f"- {event.get('title')} [{event.get('status')}]")
        if not summary_events:
            if issues:
                summary.append("- No execution summary event recorded.")
            elif self.events:
                summary.append(f"- {len(self.events)} activity events recorded.")
            else:
                summary.append("- No issues reported.")
        return "\n".join(summary)

    # populated after the module-level render helpers are defined, see bottom of file
    _DRAWER_HANDLERS: dict[str, Callable[[dict[str, Any], bool], list[str]]] = {}


def _short_text(value: Any, limit: int = DETAIL_LIMIT_COMPACT) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_details(details: dict[str, Any], prefix: str = "    • ") -> list[str]:
    """Shared formatter used by render_text for generic key/value detail dumps."""
    lines: list[str] = []
    for key, value in details.items():
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        if isinstance(value, str) and "\n" in value:
            lines.append(f"{prefix}{key}:")
            for detail_line in value.splitlines():
                lines.append(f"      {detail_line}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return lines


def _render_generic_details(details: dict[str, Any], compact: bool) -> list[str]:
    return _format_details(details)


def _render_terminal_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    if details.get("cwd"):
        lines.append(f"    • cwd: {details.get('cwd')}")
    if details.get("exit_code") is not None:
        lines.append(f"    • exit code: {details.get('exit_code')}")
    if details.get("duration_sec") is not None:
        lines.append(f"    • duration: {details.get('duration_sec')}s")
    if compact:
        stdout = details.get("stdout")
        stderr = details.get("stderr")
        if isinstance(stdout, str) and stdout.strip():
            lines.append(f"    • stdout: {stdout.strip().splitlines()[0]}")
        elif isinstance(stderr, str) and stderr.strip():
            lines.append(f"    • stderr: {stderr.strip().splitlines()[0]}")
    else:
        if details.get("mode"):
            lines.append(f"    • mode: {details.get('mode')}")
        stdout = details.get("stdout")
        if isinstance(stdout, str) and stdout.strip():
            lines.append("    • stdout:")
            for line in stdout.strip().splitlines()[:3]:
                lines.append(f"      {line}")
        stderr = details.get("stderr")
        if isinstance(stderr, str) and stderr.strip():
            lines.append("    • stderr:")
            for line in stderr.strip().splitlines()[:3]:
                lines.append(f"      {line}")
    return lines


def _render_file_operation_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    operation = details.get("operation", "file")
    path = details.get("path") or details.get("filepath") or details.get("file")
    if path:
        lines.append(f"    • file: {Path(path).name}")
    if operation == "read":
        if details.get("size_bytes") is not None:
            lines.append(f"    • size: {details.get('size_bytes')} bytes")
        if details.get("lines") is not None:
            lines.append(f"    • lines: {details.get('lines')}")
    elif operation == "list":
        if details.get("count") is not None:
            lines.append(f"    • entries: {details.get('count')}")
    elif operation in {"write", "append", "replace"}:
        if details.get("lines_added") is not None or details.get("lines_removed") is not None:
            lines.append(f"    • +{details.get('lines_added', 0)}  -{details.get('lines_removed', 0)}")
        if details.get("bytes_written") is not None:
            lines.append(f"    • bytes: {details.get('bytes_written')}")
        if details.get("bytes_added") is not None:
            lines.append(f"    • bytes added: {details.get('bytes_added')}")
        if details.get("replacements") is not None:
            lines.append(f"    • replacements: {details.get('replacements')}")
    if details.get("diff_preview"):
        lines.append("    • View diff" if compact else "    • diff preview available")
    return lines


def _render_search_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    query = details.get("query")
    if query:
        lines.append(f"    • query: {_short_text(query, DETAIL_LIMIT_COMPACT)}")
    if details.get("result_count") is not None:
        lines.append(f"    • results: {details.get('result_count')}")
    return lines


def _render_memory_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    if details.get("result"):
        lines.append(f"    • result: {_short_text(details.get('result'), DETAIL_LIMIT_COMPACT)}")
    elif details.get("args"):
        lines.append(f"    • args: {_short_text(details.get('args'), DETAIL_LIMIT_COMPACT)}")
    if details.get("past_memory") is not None:
        lines.append(f"    • past memory: {details.get('past_memory')}")
    return lines


def _render_skill_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    matched = details.get("matched_skills") or details.get("matched_core")
    if matched:
        if isinstance(matched, (list, tuple)):
            lines.append(f"    • matched: {', '.join(str(item) for item in matched[:5])}")
            if len(matched) > 5:
                lines.append(f"    • and {len(matched) - 5} more…")
        else:
            lines.append(f"    • matched: {_short_text(matched, DETAIL_LIMIT_COMPACT)}")
    return lines


def _render_thinking_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    if details.get("goal"):
        lines.append(f"    • goal: {_short_text(details.get('goal'), DETAIL_LIMIT_COMPACT)}")
    if details.get("reason"):
        lines.append(f"    • reason: {_short_text(details.get('reason'), DETAIL_LIMIT_COMPACT)}")
    return lines


def _render_plugin_details(details: dict[str, Any], compact: bool) -> list[str]:
    if details.get("name"):
        return [f"    • name: {details.get('name')}"]
    return []


def _render_tool_details(details: dict[str, Any], compact: bool) -> list[str]:
    lines: list[str] = []
    if details.get("tool"):
        lines.append(f"    • tool: {details.get('tool')}")
    if details.get("result") is not None:
        limit = DETAIL_LIMIT_COMPACT if compact else DETAIL_LIMIT_FULL
        lines.append(f"    • result: {_short_text(details.get('result'), limit)}")
    if not compact and details.get("args"):
        lines.append(f"    • args: {_short_text(details.get('args'), DETAIL_LIMIT_FULL)}")
    return lines


def _render_summary_details(details: dict[str, Any], compact: bool) -> list[str]:
    return _format_details(details)


ActivityTimeline._DRAWER_HANDLERS = {
    "terminal": _render_terminal_details,
    "file_operation": _render_file_operation_details,
    "search": _render_search_details,
    "memory": _render_memory_details,
    "skill": _render_skill_details,
    "thinking": _render_thinking_details,
    "plugin": _render_plugin_details,
    "tool": _render_tool_details,
    "summary": _render_summary_details,
}


def make_activity_event(
    event_type: str,
    title: str,
    message: str = "",
    status: str = "running",
    details: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    event = {
        "event_type": event_type,
        "title": title,
        "message": message,
        "status": status,
        "details": details or {},
    }
    event.update(extra)
    return event


def _icon_for_status(status: str) -> str:
    return {
        "running": "⏳",
        "success": "✅",
        "failed": "❌",
        "warning": "⚠",
    }.get(status, "•")


def _drawer_heading(event_type: str) -> str:
    return {
        "terminal": "🖥 Terminal",
        "file_operation": "📄 File Changes",
        "search": "🔍 Search",
        "memory": "🧠 Memory",
        "skill": "✨ Skill matching",
        "thinking": "💭 Planning",
        "plugin": "🔌 Plugins",
        "tool": "⚙ Skills",
        "summary": "✅ Summary",
    }.get(event_type, "• Other")


def _drawer_icon(event_type: str) -> str:
    return {
        "terminal": "🖥",
        "file_operation": "📄",
        "search": "🔍",
        "memory": "🧠",
        "skill": "✨",
        "thinking": "💭",
        "plugin": "🔌",
        "tool": "⚙",
        "summary": "✅",
    }.get(event_type, "•")


_default_timeline: ActivityTimeline | None = None


def set_activity_timeline(timeline: ActivityTimeline | None) -> ActivityTimeline | None:
    global _default_timeline
    _default_timeline = timeline
    return _default_timeline


def get_activity_timeline() -> ActivityTimeline | None:
    return _default_timeline


def emit_activity_event(
    event_type: str,
    title: str,
    message: str = "",
    status: str = "running",
    details: dict[str, Any] | None = None,
    **extra: Any,
) -> str | None:
    timeline = get_activity_timeline()
    if timeline is None:
        return None
    return timeline.add_event(make_activity_event(event_type, title, message, status, details, **extra))


def update_activity_event(event_id: str, **updates: Any) -> dict[str, Any] | None:
    timeline = get_activity_timeline()
    if timeline is None:
        return None
    return timeline.update_event(event_id, **updates)