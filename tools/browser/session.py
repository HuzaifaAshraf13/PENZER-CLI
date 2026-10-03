"""State owned by one Penzer browser session."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CDPSession:
    session_id: str
    client: Optional[Any] = None
    root_client: Optional[Any] = None
    target_id: str = ""
    port: int = 0
    process: Optional[Any] = None
    element_refs: Dict[str, Dict] = field(default_factory=dict)
    target_clients: Dict[str, Any] = field(default_factory=dict)
    target_sessions: Dict[str, str] = field(default_factory=dict)
    event_tasks: List[asyncio.Task] = field(default_factory=list)
    network_events: List[Dict[str, Any]] = field(default_factory=list)
    next_ref: int = 1
    console_messages: List[Dict[str, str]] = field(default_factory=list)
    last_error: str = ""
    action_count: int = 0
    url: str = ""