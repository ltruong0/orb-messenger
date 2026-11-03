"""MQTT client for the hub: one connection as `msg-hub`, subscribed to orb/#.

Reconnects with backoff. Publishes raise MqttUnavailable while disconnected
so API handlers can answer 503 instead of silently queueing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

import aiomqtt

log = logging.getLogger("hub.mqtt")

Handler = Callable[[str, bytes, bool], Awaitable[None]]


class MqttUnavailable(Exception):
    pass


class Mqtt:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        handler: Handler,
        client_id: str = "msg-hub",
        max_backoff: float = 30.0,
        on_connect: Callable[[], Awaitable[None]] | None = None,
    ):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.handler = handler
        self.on_connect = on_connect
        self.client_id = client_id
        self.max_backoff = max_backoff
        self.connected = asyncio.Event()
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task | None = None

    async def start(self, wait: float = 10.0) -> None:
        self._task = asyncio.create_task(self._run(), name="hub-mqtt")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.connected.wait(), wait)
        if not self.connected.is_set():
            log.warning("mqtt not connected after %.0fs; continuing to retry", wait)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._client = None
        self.connected.clear()

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self.host,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    identifier=self.client_id,
                    keepalive=30,
                    clean_session=True,
                ) as client:
                    self._client = client
                    await client.subscribe("orb/#", qos=1)
                    self.connected.set()
                    backoff = 1.0
                    log.info("mqtt connected to %s:%s as %s", self.host, self.port, self.user)
                    if self.on_connect is not None:
                        try:
                            await self.on_connect()
                        except Exception:
                            log.exception("on_connect hook failed")
                    async for msg in client.messages:
                        try:
                            await self.handler(
                                msg.topic.value, bytes(msg.payload or b""), bool(msg.retain)
                            )
                        except Exception:
                            log.exception("error handling %s", msg.topic.value)
            except aiomqtt.MqttError as e:
                log.warning("mqtt disconnected (%s); retry in %.0fs", e, backoff)
            finally:
                self._client = None
                self.connected.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff)

    async def publish(self, topic: str, payload: bytes, retain: bool) -> None:
        client = self._client
        if client is None or not self.connected.is_set():
            raise MqttUnavailable("mqtt not connected")
        t0 = time.monotonic()
        try:
            await client.publish(topic, payload, qos=1, retain=retain)
        except aiomqtt.MqttError as e:
            raise MqttUnavailable(str(e)) from e
        log.debug(
            "published %s (%d bytes, retain=%s) in %.0fms",
            topic,
            len(payload),
            retain,
            (time.monotonic() - t0) * 1000,
        )
