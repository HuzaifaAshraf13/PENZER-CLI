"""Interactive terminal shell.

The agent remains responsible for execution and state. This module owns only
input editing and the small amount of presentation state needed by the shell.
"""

from __future__ import annotations

import os
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style


SLASH_COMMANDS = (
    "/help", "/status", "/tools", "/skills", "/memory", "/plan",
    "/permissions", "/model", "/config", "/session", "/history", "/clear",
    "/compact", "/retry", "/stop", "/resume", "/exit", "/plugins", "/doctor",
    "/activity", "/checkpoints", "/dashboard", "/benchmark", "/profile",
    "/apikey", "/update", "/state",
)


def normalize_command(value: str) -> str:
    """Accept both documented slash commands and legacy bare commands."""
    value = value.strip()
    if value.startswith("/"):
        return value[1:].lstrip()
    return value


class SlashCommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if " " in text or not text.startswith("/"):
            return
        for command in SLASH_COMMANDS:
            if command.startswith(text):
                yield Completion(command, start_position=-len(text))


class InteractiveTerminal:
    """Prompt-toolkit shell with history, multiline editing and status bar."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        root = cwd or Path.cwd()
        history_path = root / "data" / "penzer_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self.status = "IDLE"
        self.session_id = os.urandom(2).hex()
        self._session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=SlashCommandCompleter(),
            multiline=True,
            key_bindings=self._key_bindings(),
            style=Style.from_dict({
                "prompt": "#00d7ff bold",
                "bottom-toolbar": "#808080",
            }),
        )

    def _key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("escape", "enter")
        def shifted_enter(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-l")
        def redraw(event) -> None:
            event.app.renderer.clear()

        @bindings.add("c-d")
        def exit_on_empty(event) -> None:
            if not event.app.current_buffer.text:
                event.app.exit(exception=EOFError())

        @bindings.add("escape")
        def cancel_input(event) -> None:
            event.app.current_buffer.reset()

        return bindings

    def set_status(self, value: str) -> None:
        self.status = value.upper()

    @staticmethod
    def format_event(event: dict) -> str:
        """Format one structured activity event without relying on color."""
        if event.get("event_type") == "terminal":
            return ""
        status = event.get("status", "running")
        symbol = {"running": "◐", "success": "✓", "failed": "×", "warning": "!"}.get(status, "•")
        title = event.get("title") or event.get("event_type", "activity")
        title = title.removesuffix(" activity")
        message = " ".join(str(event.get("message") or "").split())
        if status == "success":
            message = "completed"
        elif status == "failed":
            message = "failed"
        elif status == "warning":
            message = "needs attention"
        if len(message) > 100:
            message = message[:97] + "..."
        return f"{symbol} {title}" + (f" · {message}" if message else "")

    def event_status(self, event: dict) -> str:
        """Map activity outcomes to the compact agent state vocabulary."""
        if event.get("event_type") == "summary" and event.get("status") == "success":
            return "IDLE"
        return {
            "running": "RUNNING",
            "success": "RUNNING",
            "failed": "FAILED",
            "warning": "WAITING",
        }.get(event.get("status", "running"), "RUNNING")

    @staticmethod
    def format_plan(plan: list[dict]) -> str:
        """Render the agent's high-level plan without exposing reasoning."""
        symbols = {"done": "✓", "success": "✓", "running": "◐", "blocked": "×", "pending": "○"}
        lines = ["● Planning"]
        for index, step in enumerate(plan, 1):
            status = step.get("status", "pending")
            symbol = symbols.get(status, "○")
            lines.append(f"  {index}. {symbol} {step.get('title', 'Untitled step')}")
        return "\n".join(lines)

    def toolbar(self) -> str:
        width = os.get_terminal_size().columns if os.isatty(1) else 80
        mode = "compact" if width < 90 else "interactive"
        cwd = str(Path.cwd()).replace(str(Path.home()), "~", 1)
        model = os.getenv("LLM_MODEL", "default")
        text = f"{model} · {cwd} · {self.status} · ASK · {mode} · ?"
        return text if len(text) <= width - 2 else text[: max(20, width - 5)] + "..."

    async def get_input(self) -> str:
        return await self._session.prompt_async(
            message=[("class:prompt", "› ")],
            bottom_toolbar=self.toolbar,
        )

    def header(self, state: str = "IDLE") -> str:
        self.set_status(state)
        return f"PENZER                              session: {self.session_id}  ● {self.status}"