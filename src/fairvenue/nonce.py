"""Thread-safe per-process nonce generation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class NonceManager:
    """Generate monotonically increasing millisecond nonces for one agent key."""

    def __init__(self, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._previous = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            value = max(self._clock_ms(), self._previous + 1)
            self._previous = value
            return value
