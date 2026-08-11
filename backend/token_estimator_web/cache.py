"""Small async-safe TTL/LRU caches and quota windows for one-process beta hosting."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class _Entry:
    value: Any
    expires: float
    weight: int
    group: str | None


class AsyncTTLCache:
    def __init__(self, max_entries: int, max_weight: int, ttl_seconds: int) -> None:
        self.max_entries = max_entries
        self.max_weight = max_weight
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._weight = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires <= time.monotonic():
                self._remove(key)
                return None
            self._entries.move_to_end(key)
            return entry.value

    async def set(
        self,
        key: str,
        value: Any,
        weight: int = 1,
        *,
        group: str | None = None,
        max_group_entries: int | None = None,
    ) -> None:
        async with self._lock:
            self._remove(key)
            self._entries[key] = _Entry(
                value, time.monotonic() + self.ttl_seconds, weight, group
            )
            self._weight += weight
            if group is not None and max_group_entries is not None:
                grouped = [
                    entry_key for entry_key, entry in self._entries.items()
                    if entry.group == group
                ]
                for oldest in grouped[:-max_group_entries]:
                    self._remove(oldest)
            while self._entries and (
                len(self._entries) > self.max_entries or self._weight > self.max_weight
            ):
                oldest = next(iter(self._entries))
                self._remove(oldest)

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry:
            self._weight -= entry.weight


class SingleFlight:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Task[Any]] = {}
        self._waiters: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            task = self._pending.get(key)
            if task is None:
                task = asyncio.create_task(factory())
                self._pending[key] = task
                self._waiters[key] = 0
            self._waiters[key] += 1
        try:
            return await asyncio.shield(task)
        finally:
            async with self._lock:
                if self._pending.get(key) is task:
                    remaining = self._waiters[key] - 1
                    if remaining > 0:
                        self._waiters[key] = remaining
                    else:
                        if not task.done():
                            task.cancel()
                        self._waiters.pop(key, None)
                        self._pending.pop(key, None)


class QuotaExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


class SlidingQuota:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def consume(self, key: str, limit: int, window_seconds: int) -> None:
        await self.consume_many([(key, limit, window_seconds)])

    async def consume_many(
        self, limits: list[tuple[str, int, int]],
    ) -> None:
        """Atomically validate and record one event across several windows."""
        now = time.monotonic()
        async with self._lock:
            for key, _, window_seconds in limits:
                events = self._events[key]
                while events and now - events[0] >= window_seconds:
                    events.popleft()
            for key, limit, window_seconds in limits:
                events = self._events[key]
                if len(events) >= limit:
                    retry = max(1, int(window_seconds - (now - events[0])))
                    raise QuotaExceeded(retry)
            for key, _, _ in limits:
                self._events[key].append(now)
