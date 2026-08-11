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
        self._lock = asyncio.Lock()

    async def run(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            task = self._pending.get(key)
            if task is None:
                task = asyncio.create_task(factory())
                self._pending[key] = task
                task.add_done_callback(
                    lambda completed: asyncio.create_task(self._clear(key, completed))
                )
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._pending.get(key) is task:
                        self._pending.pop(key, None)

    async def _clear(self, key: str, task: asyncio.Task[Any]) -> None:
        async with self._lock:
            if self._pending.get(key) is task:
                self._pending.pop(key, None)


class QuotaExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


class SlidingQuota:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def consume(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        async with self._lock:
            events = self._events[key]
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if len(events) >= limit:
                retry = max(1, int(window_seconds - (now - events[0])))
                raise QuotaExceeded(retry)
            events.append(now)
