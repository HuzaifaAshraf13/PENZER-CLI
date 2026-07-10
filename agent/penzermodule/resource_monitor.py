"""PENZER — ResourceMonitor

Self-contained; no agent-state coupling, so moved verbatim.
"""
import time, logging
import psutil

logger = logging.getLogger(__name__)

MEMORY_CRITICAL = 85

class ResourceMonitor:
    def __init__(self):
        self._proc  = psutil.Process()
        self._start = time.time()

    def check(self) -> tuple[bool, str]:
        try:
            mem = self._proc.memory_percent()
            if mem > MEMORY_CRITICAL:
                return False, f"Memory critical: {mem:.1f}%"
            if mem > 70:
                logger.warning("Memory high: %.1f%%", mem)
        except Exception:
            pass
        return True, ""

    def stats(self) -> dict:
        try:
            return {
                "memory_mb":   round(self._proc.memory_info().rss / 1e6, 1),
                "elapsed_sec": round(time.time() - self._start, 1),
            }
        except Exception:
            return {}