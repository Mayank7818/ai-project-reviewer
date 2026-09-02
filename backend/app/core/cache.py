"""A bounded, expiring, thread-safe key-value store.

Deliberately written against a narrow `get / put / delete` interface, so
replacing it with Redis or a database table later means writing a new class with
the same methods and changing one factory — no caller changes.

Thread-safe because uvicorn runs sync dependencies in a worker thread pool, so
two requests can genuinely touch a store at once.

Not persistent and not shared between processes: restarting the backend clears
every store, which is the documented behaviour for this version.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    stored_at: float


class TTLStore(Generic[T]):
    """A bounded, expiring, thread-safe key-value store."""

    def __init__(self, *, max_entries: int, ttl_seconds: int) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, _Entry[T]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> T | None:
        """Return the value, or None if absent or expired."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if self._is_expired(entry):
                del self._entries[key]
                return None
            return entry.value

    def put(self, key: str, value: T) -> None:
        """Store a value, evicting the oldest entry if the store is full."""
        with self._lock:
            self._evict_expired()
            if key not in self._entries and len(self._entries) >= self._max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k].stored_at)
                del self._entries[oldest]
                logger.debug("Store full; evicted oldest entry")
            self._entries[key] = _Entry(value=value, stored_at=time.monotonic())

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired()
            return len(self._entries)

    def _is_expired(self, entry: _Entry[T]) -> bool:
        return (time.monotonic() - entry.stored_at) > self._ttl_seconds

    def _evict_expired(self) -> None:
        """Caller must hold the lock."""
        expired = [
            key for key, entry in self._entries.items() if self._is_expired(entry)
        ]
        for key in expired:
            del self._entries[key]
