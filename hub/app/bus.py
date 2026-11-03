"""In-process event bus feeding the SSE endpoint (docs/hub-api.md "Live updates")."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any]

    def sse(self) -> str:
        return f"event: {self.name}\ndata: {json.dumps(self.data, separators=(',', ':'))}\n\n"


class EventBus:
    def __init__(self, maxsize: int = 256):
        self._subs: set[asyncio.Queue[Event]] = set()
        self._maxsize = maxsize
        self.history: list[Event] = []  # for tests

    def publish(self, name: str, **data: Any) -> None:
        ev = Event(name, data)
        self.history.append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                self._subs.discard(q)  # slow consumer: drop it, it will reconnect

    async def subscribe(self, heartbeat: float = 15.0) -> AsyncIterator[str]:
        q: asyncio.Queue[Event] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), heartbeat)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield ev.sse()
        finally:
            self._subs.discard(q)

    @property
    def subscribers(self) -> int:
        return len(self._subs)
