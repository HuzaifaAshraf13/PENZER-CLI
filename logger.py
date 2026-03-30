"""
Logging infrastructure for PENZER-CLI
Structured logging with file rotation and colored console output
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import coloredlogs

from config import LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOGS_DIR


def setup_logging() -> None:
    """Initialize logging with file and console handlers."""
    
    # Create logs directory if it doesn't exist
    LOGS_DIR.mkdir(exist_ok=True)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Console handler with colored output
    console_handler = logging.StreamHandler(sys.stdout)
    console_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    console_handler.setLevel(console_level)
    
    # Use coloredlogs for pretty console output
    formatter = coloredlogs.ColoredFormatter(
        fmt='[%(levelname)s] %(name)s - %(message)s',
        level_styles={
            'DEBUG': {'color': 'white', 'faint': True},
            'INFO': {'color': 'cyan'},
            'WARNING': {'color': 'yellow'},
            'ERROR': {'color': 'red'},
            'CRITICAL': {'color': 'red', 'bold': True}
        },
        field_styles={
            'levelname': {'bold': True}
        }
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(LOG_FORMAT)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    # Module loggers
    agent_logger = logging.getLogger("agent")
    agent_logger.setLevel(console_level)
    
    tools_logger = logging.getLogger("tools")
    tools_logger.setLevel(console_level)
    
    cli_logger = logging.getLogger("cli")
    cli_logger.setLevel(console_level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name."""
    return logging.getLogger(name)


# Initialize logging on module import
setup_logging()
