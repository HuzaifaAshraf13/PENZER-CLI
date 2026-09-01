import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from rich.console import Console
from rich.logging import RichHandler
from config import LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOGS_DIR, LOG_TO_CONSOLE

# Shared with cli.py — see module docstring above.
console = Console(force_terminal=True, width=100)


def setup_logging() -> None:
    """Initialize logging with file and console handlers."""

    # Create logs directory if it doesn't exist
    LOGS_DIR.mkdir(exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    console_level = getattr(logging, LOG_LEVEL.upper(), logging.ERROR)

    # Default interactive CLI behavior: keep logs in the file, not on the
    # terminal. User-facing status is handled by the CLI itself, not by a
    # logger handler that writes noise like "Completion evaluator timed out"
    # or "Continuing…" directly into the session. Enable console logging only
    # when explicitly requested via LOG_TO_CONSOLE=true.
    if LOG_TO_CONSOLE:
        console_handler = RichHandler(
            console=console,
            level=console_level,
            show_time=False,
            show_path=False,
            rich_tracebacks=True,
        )
        root_logger.addHandler(console_handler)

    # File handler with rotation — unchanged, this side was never the problem.
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(LOG_FORMAT)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Module loggers — top-level namespaces only; cli.py additionally
    # suppresses specific noisy children (tools.executor, agent.penzermodule,
    # etc.) after import. Both layers matter: this sets a sane default for
    # anything not explicitly named, cli.py's list quiets known-noisy ones
    # further. The RichHandler fix above means even an unsuppressed logger
    # won't corrupt the terminal anymore — it'll just be visible output.
    for _name in ("agent", "tools", "cli"):
        logging.getLogger(_name).setLevel(console_level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name."""
    return logging.getLogger(name)


# Initialize logging on module import
setup_logging()