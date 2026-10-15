import threading
from typing import Optional


class KillSwitch:
    """Module-level singleton kill switch.

    When active, all submit_intent calls are blocked with 503.
    Thread-safe via a simple lock.
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
