# session/session.py
import time
import uuid
from typing import List, Dict, Any

class Session:
    """
    Simple session manager that tracks messages and task state.
    messages is a list of dicts: {"role": "user|agent|tool_output", "content": "text", "ts": float}
    """
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = time.time()
        self.messages: List[Dict[str, Any]] = []
        self.metadata: Dict[str, Any] = {}
        self.memory: Dict[str, Any] = {}  # small key-value memory store

    def add_message(self, role: str, content: Any):
        if isinstance(content, (dict, list)):
            content = str(content)
        entry = {"role": role, "content": content, "ts": time.time()}
        self.messages.append(entry)

    def get_history(self, limit: int | None = None):
        if limit is None:
            return list(self.messages)
        return list(self.messages)[-limit:]

    def clear(self):
        self.messages.clear()
        self.metadata.clear()
        self.memory.clear()

    def set_memory(self, key: str, value: Any):
        self.memory[key] = value

    def get_memory(self, key: str, default=None):
        return self.memory.get(key, default)
