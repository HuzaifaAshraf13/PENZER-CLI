"""PENZER — ResourceMonitor

Self-contained; no agent-state coupling, so moved verbatim (plus a
reliability pass — see check()/stats()/reset_timer() docstrings below).
"""
import time, logging
import psutil
from agent.config import MEMORY_CRITICAL, MAX_RESOURCE_CHECK_FAILURES

logger = logging.getLogger(__name__)

_WARN_INTERVAL_SEC = 60  # throttle the "memory high" log to once/minute

class ResourceMonitor:
    def __init__(self):
        self._proc = psutil.Process()
        self._start = time.time()
        self._consecutive_check_failures = 0
        self._last_warn_at = 0.0

    def reset_timer(self) -> None:
        """Resets the elapsed-time baseline used by stats(). A single
        ResourceMonitor instance is created once in PenzerAgent.__init__
        and can outlive many separate run() calls on the same agent —
        without resetting, stats()["elapsed_sec"] silently accumulates
        across every run that instance has ever handled, not just the
        current one, making it meaningless for diagnosing the task
        actually in front of you at checkpoint time. Called once per run
        via PenzerAgent._reset()."""
        self._start = time.time()

    def check(self) -> tuple[bool, str]:
        """
        Returns (ok, message). ok=False is the actual safety backstop
        agent.py's _check_stop_conditions() relies on to end a run before
        it OOMs the process — so a check() that silently swallows its own
        exceptions and always reports "fine" defeats that backstop
        entirely, with no trace of why. Previously any exception here
        (psutil.AccessDenied, a transient proc-handle glitch, anything)
        was caught and discarded with a bare `except Exception: pass`, so
        a permanently broken monitor — e.g. no permission to read process
        memory in a locked-down container — silently became a permanent
        no-op for the rest of that agent instance's lifetime, with
        nothing in the logs to explain why memory protection stopped
        working.

        Now: a single failure is logged (not silent) and still reports
        "fine" — one transient hiccup shouldn't end an otherwise-healthy
        run. But MAX_RESOURCE_CHECK_FAILURES consecutive failures is
        treated as the monitor itself being broken and reported as
        unhealthy — better to stop a run than keep silently claiming to
        guard memory while doing nothing.
        """
        try:
            mem = self._proc.memory_percent()
        except Exception as e:
            self._consecutive_check_failures += 1
            logger.warning(
                "Resource check failed (%d consecutive): %s",
                self._consecutive_check_failures, e,
            )
            if self._consecutive_check_failures >= MAX_RESOURCE_CHECK_FAILURES:
                return False, (
                    f"Resource monitor unavailable after "
                    f"{self._consecutive_check_failures} consecutive failures: {e}"
                )
            return True, ""
        self._consecutive_check_failures = 0
        if mem > MEMORY_CRITICAL:
            return False, f"Memory critical: {mem:.1f}%"
        if mem > 70:
            now = time.time()
            if now - self._last_warn_at > _WARN_INTERVAL_SEC:
                logger.warning("Memory high: %.1f%%", mem)
                self._last_warn_at = now
        return True, ""

    def stats(self) -> dict:
        try:
            return {
                "memory_mb":   round(self._proc.memory_info().rss / 1e6, 1),
                "elapsed_sec": round(time.time() - self._start, 1),
            }
        except Exception as e:
            logger.debug("ResourceMonitor.stats() failed: %s", e)
            return {}