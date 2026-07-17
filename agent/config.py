"""
Central configuration for the Penzer orchestrator (agent.py) and the
managers it delegates to (agent/penzermodule/*).

Put new tunables here rather than scattering magic numbers through
orchestration code — as more managers/modules get added, this stays the
one place to look for "what number controls X".
"""
from typing import Callable

# ---------------------------------------------------------------------
# Iteration / runtime budgets
# ---------------------------------------------------------------------
TRIM_AT              = 30
KEEP_LAST            = 8
STUCK_MIN            = 2
MAX_FAILURES         = 3
ITER_EXTENSION_SIZE  = 8
MAX_RUNTIME_SECONDS  = 21600  # 6 hours
ABSOLUTE_MAX_ITER    = 5000
MAX_TOKENS_PER_RUN   = 2000000
TOOL_TIMEOUT         = 30
CHECKPOINT_EVERY     = 10
MEMORY_CRITICAL      = 85
COMPLEX_THRESHOLD    = 3
RATE_LIMIT_BASE      = 5.0
RATE_LIMIT_MAX       = 60.0
RATE_LIMIT_JITTER    = 2.0
WORKING_MEMORY_SIZE  = 7
# Circuit breaker: if _check_consistency() reports violations on this
# many consecutive checkpoints, the run force-stops rather than let a
# coordination bug (phase/queue/belief structures disagreeing) keep
# compounding silently for the rest of a long-running task.
MAX_CONSISTENCY_VIOLATIONS = 3
# If ResourceMonitor.check() itself fails this many times in a row, the
# monitor is treated as broken (unhealthy) rather than reporting "fine"
# forever — a monitoring system that silently no-ops on its own errors
# provides zero actual protection.
MAX_RESOURCE_CHECK_FAILURES = 5

# DEPRECATED — kept only so any external code still importing this name
# doesn't break. The live values used at runtime come from
# Planner._max_iter_for_complexity(); editing this dict has no effect on
# actual behavior. Retune budgets in the planner, not here.
ITER_BY_COMPLEXITY = {
    "simple":  5,
    "medium":  10,
    "complex": 20,
}

# ---------------------------------------------------------------------
# Tool display / recovery config
# ---------------------------------------------------------------------
TOOL_LABELS = {
    "browser": "\U0001F310", "terminal": "\u26A1", "run_python": "\U0001F40D",
    "run_bash": "\U0001F4DC", "file_editor": "\U0001F4C1", "memory": "\U0001F9E0", "planning": "\U0001F4CB",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
}
ERROR_PATTERNS = [
    ("timeout", "TIMEOUT"),
    ("permission", "PERMISSION"),
    ("not found", "NOT_FOUND"),
    ("syntax", "SYNTAX"),
    ("invalid", "INVALID"),
]
ACTION_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "terminal":    lambda a: f"-> {a.get('command', '')[:60]}",
    "browser":     lambda a: f"-> {a.get('action', '')}: {(a.get('query') or a.get('url', ''))[:50]}",
    "file_editor": lambda a: f"-> {a.get('action', '')}: {a.get('filepath', '')}",
    "memory":      lambda a: f"-> {a.get('action', '')}: {a.get('key', '')}",
    "planning":    lambda a: f"-> plan: {a.get('goal', '')[:50]}",
}