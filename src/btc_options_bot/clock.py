from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Protocol

from .utils import UTC


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Deterministic clock for tests and simulations."""

    def __init__(self, value: datetime) -> None:
        self._value = value.astimezone(UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._value

    def set(self, value: datetime) -> None:
        with self._lock:
            self._value = value.astimezone(UTC)

    def advance(self, seconds: float | timedelta) -> datetime:
        delta = seconds if isinstance(seconds, timedelta) else timedelta(seconds=seconds)
        with self._lock:
            self._value += delta
            return self._value


def sleep_seconds(seconds: float, stop_event: threading.Event | None = None) -> None:
    if stop_event is None:
        time.sleep(max(0.0, seconds))
    else:
        stop_event.wait(max(0.0, seconds))
