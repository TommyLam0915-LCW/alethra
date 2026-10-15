import threading
from typing import Optional


class KillSwitch:
    """Module-level singleton kill switch.

    When active, all submit_intent calls are blocked with 503.
    Thread-safe via a simple lock.

    NOTE: This kill-switch is process-local (in-memory). For multi-worker
    deployments (e.g. gunicorn with multiple workers, or multiple pods/containers),
    this MUST be replaced with a distributed lock/flag — for example a Redis
    SETNX key or a database row — so that all workers observe the same state.
    A process-local flag can only halt the single process that set it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._reason: Optional[str] = None

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def activate(self, reason: str) -> None:
        with self._lock:
            self._active = True
            self._reason = reason

    def deactivate(self) -> None:
        with self._lock:
            self._active = False
            self._reason = None

    @property
    def reason(self) -> Optional[str]:
        with self._lock:
            return self._reason


kill_switch = KillSwitch()
