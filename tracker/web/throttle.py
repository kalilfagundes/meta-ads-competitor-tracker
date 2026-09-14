"""Login throttle: after too many failed sign-ins from one address, refuse that
address for a while, even with the right password. A second, looser limit counts
failures per account, so guessing one account's password stays slow even when the
attacker can change IP (or fake it, when the app sits behind a network that makes
every visitor look like a trusted proxy).

Kept in memory: the web app is a single process, and a restart clearing the
counters is acceptable. The address is the client IP as uvicorn resolves it; behind
a trusted reverse proxy that is the X-Forwarded-For IP (see FORWARDED_ALLOW_IPS).
Accounts are counted only for usernames that exist, so junk names can't grow the map.
"""

from __future__ import annotations

import math
import threading
import time

MAX_FAILURES = 5
ACCOUNT_MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60


class LoginThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS,
                 clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self._clock = clock
        self._failures: dict[str, list[float]] = {}   # address -> recent failure times
        self._lock = threading.Lock()                  # routes run in a threadpool

    def retry_after(self, address: str) -> int:
        """Seconds until `address` may try again; 0 when it isn't blocked."""
        with self._lock:
            now = self._clock()
            recent = [t for t in self._failures.get(address, ()) if now - t < self.window]
            if len(recent) < self.max_failures:
                return 0
            # Only the last `max_failures` are kept, so the oldest one frees the address.
            return max(1, math.ceil(recent[0] + self.window - now))

    def record_failure(self, address: str) -> None:
        with self._lock:
            now = self._clock()
            # Forget addresses whose failures have all aged out, so the map stays small.
            for key in [k for k, times in self._failures.items() if now - times[-1] >= self.window]:
                del self._failures[key]
            times = self._failures.setdefault(address, [])
            times.append(now)
            del times[:-self.max_failures]

    def reset(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


login_throttle = LoginThrottle()                                     # per client address
account_throttle = LoginThrottle(max_failures=ACCOUNT_MAX_FAILURES)  # per user id
