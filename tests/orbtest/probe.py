"""Async hub-side probe for tests: subscribes to orb/# as msg-hub and
collects everything, so tests can assert what the broker actually relayed."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import aiomqtt

from .broker import Broker


class HubProbe:
    def __init__(self, broker: Broker, client_id: str = "msg-hub-probe"):
        self.broker = broker
        self.client_id = client_id
        self.inbox: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def __aenter__(self) -> HubProbe:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), 10)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        async with aiomqtt.Client(
            hostname=self.broker.host,
            port=self.broker.port,
            username="msg-hub",
            password=self.broker.hub_password,
            identifier=self.client_id,
        ) as client:
            self._client = client
            await client.subscribe("orb/#", qos=1)
            self._ready.set()
            async for msg in client.messages:
                self.inbox.put_nowait((msg.topic.value, bytes(msg.payload or b"")))

    async def publish(self, topic: str, payload: Any, retain: bool = False) -> None:
        assert self._client is not None
        if isinstance(payload, dict | list):
            payload = json.dumps(payload, separators=(",", ":")).encode()
        elif isinstance(payload, str):
            payload = payload.encode()
        await self._client.publish(topic, payload, qos=1, retain=retain)

    async def expect(self, topic: str, timeout: float = 5.0) -> bytes:
        """Next payload on `topic`, skipping other topics."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"no message on {topic} within {timeout}s")
            t, p = await asyncio.wait_for(self.inbox.get(), remaining)
            if t == topic:
                return p

    async def expect_json(self, topic: str, timeout: float = 5.0) -> Any:
        return json.loads(await self.expect(topic, timeout))

    async def expect_nothing_on(self, topic: str, timeout: float = 1.5) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                t, p = await asyncio.wait_for(self.inbox.get(), remaining)
            except TimeoutError:
                return
            assert t != topic, f"unexpected message on {topic}: {p!r}"

    def drain(self) -> None:
        while not self.inbox.empty():
            self.inbox.get_nowait()

    async def clear_retained(self, orb_id: str) -> None:
        for leaf in ("hello", "status", "config", "inbox", "led"):
            await self.publish(f"orb/{orb_id}/{leaf}", b"", retain=True)
