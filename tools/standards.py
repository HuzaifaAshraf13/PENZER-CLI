# tools/standards.py
"""
Tool standardization utilities for consistent interface across all tools.

All tools should return a standardized ToolResult with:
- status: "success" | "error" | "warning"
- data: actual result (optional)
- error: error message (optional)
- metadata: operation metadata (optional)
"""

from typing import Any, Dict, Optional
from enum import Enum


class ToolStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"


class ToolResult:
    """Standardized tool result wrapper."""
    
    def __init__(
        self,
        status: ToolStatus,
        data: Optional[Any] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.status = status.value if isinstance(status, ToolStatus) else status
        self.data = data
        self.error = error
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {"status": self.status}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        if self.metadata:
            result["metadata"] = self.metadata
        return result
    
    def is_success(self) -> bool:
        return self.status == ToolStatus.SUCCESS.value
    
    def is_error(self) -> bool:
        return self.status == ToolStatus.ERROR.value


def success(data: Any = None, metadata: Optional[Dict] = None) -> Dict:
    """Create success result."""
    return ToolResult(ToolStatus.SUCCESS, data=data, metadata=metadata).to_dict()


def error(message: str, metadata: Optional[Dict] = None) -> Dict:
    """Create error result."""
    return ToolResult(ToolStatus.ERROR, error=message, metadata=metadata).to_dict()


def warning(data: Any = None, message: str = "", metadata: Optional[Dict] = None) -> Dict:
    """Create warning result (partial success)."""
    return ToolResult(ToolStatus.WARNING, data=data, error=message, metadata=metadata).to_dict()
