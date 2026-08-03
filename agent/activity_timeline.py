from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class ActivityTimeline:
    def __init__(self, stream_handler: Callable[[dict], None] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._stream_handler = stream_handler

    def add_event(self, event: dict[str, Any]) -> str:
        if not event.get("id"):
            event = dict(event)
            event["id"] = f"evt-{uuid.uuid4().hex[:8]}"
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("status", "running")
        event.setdefault("details", {})
        self.events.append(event)
        if self._stream_handler is not None:
            self._stream_handler(event)
        return event["id"]

    def update_event(self, event_id: str, **updates: Any) -> dict[str, Any] | None:
        for event in self.events:
            if event.get("id") != event_id:
                continue
            event.update(updates)
            if self._stream_handler is not None:
                self._stream_handler(event)
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
            line = f"{icon} [{event.get('event_type','activity')}] {title}"
            if message:
                line += f" — {message}"
            if event_id:
                line += f" ({event_id})"
            lines.append(line)
            details = event.get("details") or {}
            if details:
                for key, value in details.items():
                    if value in (None, ""):
                        continue
                    if isinstance(value, (list, tuple)):
                        value = ", ".join(str(item) for item in value)
                    if isinstance(value, str) and "\n" in value:
                        lines.append(f"    • {key}:")
                        for detail_line in value.splitlines():
                            lines.append(f"      {detail_line}")
                    else:
                        lines.append(f"    • {key}: {value}")
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
        total_duration = sum(
            float(event.get("details", {}).get("duration_sec", 0) or 0)
            for event in events
            if isinstance(event.get("details", {}).get("duration_sec", None), (int, float))
        )
        running = sum(1 for event in events if event.get("status") == "running")
        success = sum(1 for event in events if event.get("status") == "success")
        failed = sum(1 for event in events if event.get("status") == "failed")
        lines: list[str] = ["[timeline drawer] Execution activity"]
        lines.append(
            f"Total events: {len(events)}  |  running: {running}  success: {success}  failed: {failed}  |  Duration: {total_duration:.1f}s"
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
        def _short_text(value: Any, limit: int = 60) -> str:
            text = str(value or "").replace("\n", " ").strip()
            if len(text) <= limit:
                return text
            return text[: limit - 1].rstrip() + "…"

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
        compact = event.get("status") == "success"

        if event.get("event_type") == "terminal":
            if details.get("cwd"):
                card_lines.append(f"    • cwd: {details.get('cwd')}")
            if details.get("exit_code") is not None:
                card_lines.append(f"    • exit code: {details.get('exit_code')}")
            if details.get("duration_sec") is not None:
                card_lines.append(f"    • duration: {details.get('duration_sec')}s")
            if compact:
                stdout = details.get("stdout")
                stderr = details.get("stderr")
                if isinstance(stdout, str) and stdout.strip():
                    first = stdout.strip().splitlines()[0]
                    card_lines.append(f"    • stdout: {first}")
                elif isinstance(stderr, str) and stderr.strip():
                    first = stderr.strip().splitlines()[0]
                    card_lines.append(f"    • stderr: {first}")
            else:
                if details.get("mode"):
                    card_lines.append(f"    • mode: {details.get('mode')}")
                stdout = details.get("stdout")
                if isinstance(stdout, str) and stdout.strip():
                    snippet = stdout.strip().splitlines()[:3]
                    card_lines.append("    • stdout:")
                    for line in snippet:
                        card_lines.append(f"      {line}")
                stderr = details.get("stderr")
                if isinstance(stderr, str) and stderr.strip():
                    snippet = stderr.strip().splitlines()[:3]
                    card_lines.append("    • stderr:")
                    for line in snippet:
                        card_lines.append(f"      {line}")
        elif event.get("event_type") == "file_operation":
            operation = details.get("operation", "file")
            path = details.get("path") or details.get("filepath") or details.get("file")
            if path:
                card_lines.append(f"    • file: {Path(path).name}")
            if operation == "read":
                size = details.get("size_bytes")
                lines_count = details.get("lines")
                if size is not None:
                    card_lines.append(f"    • size: {size} bytes")
                if lines_count is not None:
                    card_lines.append(f"    • lines: {lines_count}")
            elif operation == "list":
                count = details.get("count")
                if count is not None:
                    card_lines.append(f"    • entries: {count}")
            elif operation in {"write", "append", "replace"}:
                if details.get("lines_added") is not None or details.get("lines_removed") is not None:
                    card_lines.append(
                        f"    • +{details.get('lines_added', 0)}  -{details.get('lines_removed', 0)}"
                    )
                if details.get("bytes_written") is not None:
                    card_lines.append(f"    • bytes: {details.get('bytes_written')}")
                if details.get("bytes_added") is not None:
                    card_lines.append(f"    • bytes added: {details.get('bytes_added')}")
                if details.get("replacements") is not None:
                    card_lines.append(f"    • replacements: {details.get('replacements')}")
            if compact:
                if details.get("diff_preview"):
                    card_lines.append("    • View diff")
            else:
                if details.get("diff_preview"):
                    card_lines.append("    • diff preview available")
        elif event.get("event_type") == "search":
            query = details.get("query") or event.get("message")
            if query:
                card_lines.append(f"    • query: {_short_text(query, 60)}")
            if details.get("result_count") is not None:
                card_lines.append(f"    • results: {details.get('result_count')}")
            if details.get("generated_skills"):
                card_lines.append(f"    • matched skills: {len(details.get('generated_skills'))}")
        elif event.get("event_type") == "memory":
            if details.get("result"):
                card_lines.append(f"    • result: {_short_text(details.get('result'), 60)}")
            elif details.get("args"):
                card_lines.append(f"    • args: {_short_text(details.get('args'), 60)}")
            if details.get("past_memory") is not None:
                card_lines.append(f"    • past memory: {details.get('past_memory')}")
        elif event.get("event_type") == "skill":
            matched = details.get("matched_skills") or details.get("matched_core") or details.get("matched_generated")
            if matched:
                if isinstance(matched, (list, tuple)):
                    card_lines.append(f"    • matched: {', '.join(str(item) for item in matched[:5])}")
                    if len(matched) > 5:
                        card_lines.append(f"    • and {len(matched) - 5} more…")
                else:
                    card_lines.append(f"    • matched: {_short_text(matched, 60)}")
        elif event.get("event_type") == "thinking":
            if details.get("goal"):
                card_lines.append(f"    • goal: {_short_text(details.get('goal'), 60)}")
            if details.get("reason"):
                card_lines.append(f"    • reason: {_short_text(details.get('reason'), 60)}")
        elif event.get("event_type") == "plugin":
            if details.get("name"):
                card_lines.append(f"    • name: {details.get('name')}")
        elif event.get("event_type") == "tool":
            if details.get("tool"):
                card_lines.append(f"    • tool: {details.get('tool')}")
            if details.get("result") is not None:
                if compact:
                    card_lines.append(f"    • result: {_short_text(details.get('result'), 60)}")
                else:
                    card_lines.append(f"    • result: {_short_text(details.get('result'), 120)}")
            if not compact and details.get("args"):
                card_lines.append(f"    • args: {_short_text(details.get('args'), 120)}")
        elif event.get("event_type") == "summary":
            for key, value in details.items():
                if value in (None, ""):
                    continue
                card_lines.append(f"    • {key}: {value}")
        else:
            for key, value in details.items():
                if value in (None, ""):
                    continue
                card_lines.append(f"    • {key}: {value}")
        return card_lines

    def render_summary(self) -> str:
        if not self.events:
            return "[timeline] no activity recorded"
        summary: list[str] = ["[timeline summary]"]
        summary_events = [event for event in self.events if event.get("event_type") == "summary"]
        for event in summary_events:
            summary.append(f"- {event.get('title')}: {event.get('message')}")
            details = event.get("details") or {}
            for key, value in details.items():
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


def _drawer_icon(event_type: str) -> str:
    return {
        "terminal": "🖥",
        "file_operation": "📄",
        "search": "🔍",
        "memory": "🧠",
        "plugin": "🔌",
        "tool": "⚙",
        "summary": "✅",
    }.get(event_type, "•")


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
