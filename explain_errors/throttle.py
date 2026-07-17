import threading
import time
from collections import deque


class SlidingWindowThrottle:
    """Allow up to `max_calls` within a trailing `window_seconds` window.

    Thread-safe; not process-safe. Limits apply per worker process.
    """

    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Record and permit a call if under the limit. Thread-safe."""
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] >= self.window_seconds:
                self._calls.popleft()

            if len(self._calls) >= self.max_calls:
                return False

            self._calls.append(now)
            return True
