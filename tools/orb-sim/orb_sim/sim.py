"""OrbSim: an orb over MQTT, minus the pixels.

Mirrors docs/firmware.md: connects with an LWT, publishes status + hello,
subscribes to its five topics, walks the same state machine, reports
received / read / react events, syncs the sticker cache, fetches photos
and refuses anything that breaks the contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import aiomqtt
import httpx

from .contracts import ContractError, Contracts
from .nvs import Nvs
from .render import Console, led_text

FW_VERSION = "0.2.0-sim"
MODEL = "orb-sim"


class State(StrEnum):
    UNREACHABLE = "unreachable"  # Wi-Fi/MQTT down
    UNCONFIGURED = "unconfigured"  # connected, no retained config yet
    PAIRING = "pairing"  # config.state == pending
    IDLE = "idle"
    UNREAD = "unread"
    SHOW = "show"
    RECEIPT = "receipt"
    NOTHING = "nothing"  # tap in idle with nothing recent: "no new messages"


# local LED defaults per mode (docs/hardware.md); payload values win
LED_DEFAULTS: dict[str, dict[str, str]] = {
    "unread": {"color": "#FF2D2D", "pattern": "breathe"},
    "read": {"color": "#2EC86E", "pattern": "pulse"},
    "pair": {"color": "#4682FF", "pattern": "breathe"},
    "off": {},
}


def default_orb_id() -> str:
    """orb- + last 2 bytes of this machine's MAC, like a real orb."""
    return f"orb-{uuid.getnode() & 0xFFFF:04x}"


@dataclass
class Settings:
    host: str
    port: int = 1883
    user: str = "orb"
    password: str = ""
    orb_id: str = field(default_factory=default_orb_id)
    state_dir: Path = Path("~/.orb-sim")
    fresh: bool = False
    keepalive: int = 30
    strict: bool = True  # contract violation stops the sim
    show_timeout: float = 60.0  # SHOW -> IDLE
    receipt_timeout: float = 30.0  # RECEIPT -> previous
    reshow_window: float = 600.0  # a tap in IDLE re-opens a message read this recently
    nothing_timeout: float = 2.5  # NOTHING -> IDLE
    http_timeout: float = 10.0
    max_backoff: float = 30.0
    caps: tuple[str, ...] = ("touch", "led")


def jpeg_is_baseline(data: bytes) -> tuple[bool, str]:
    """Walk the JPEG markers; JPEGDEC on the orb can only decode SOF0 (baseline)."""
    if data[:2] != b"\xff\xd8":
        return False, "not a JPEG (no SOI marker)"
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return False, f"corrupt marker stream at byte {i}"
        marker = data[i + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01 or marker == 0xFF:
            i += 2
            continue
        if marker == 0xC0:
            return True, "baseline"
        if marker == 0xC2:
            return False, "progressive JPEG (SOF2); the hub must emit baseline"
        if marker in (0xC1, 0xC3) or (0xC5 <= marker <= 0xCF and marker not in (0xC8, 0xCC)):
            return False, f"unsupported JPEG SOF marker 0x{marker:02x}"
        if marker == 0xDA:
            return False, "scan started before any SOF marker"
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        i += 2 + length
    return False, "no SOF marker found"


class OrbSim:
    def __init__(
        self,
        settings: Settings,
        contracts: Contracts | None = None,
        console: Console | None = None,
    ):
        self.s = settings
        self.id = settings.orb_id
        self.contracts = contracts or Contracts()
        self.console = console or Console(prefix=f"[{self.id}] ")
        self.nvs = Nvs(settings.state_dir, self.id, fresh=settings.fresh)

        self.state = State.UNREACHABLE
        self.connected = False
        self.config: dict[str, Any] | None = None
        self.message: dict[str, Any] | None = None  # current (retained) inbox
        self.led: dict[str, Any] = {"mode": "off"}
        self.notice: dict[str, Any] | None = None
        self.stickers: dict[str, Any] | None = None
        self.photo: bytes | None = None
        self.error: ContractError | None = None
        self.published: list[tuple[str, Any]] = []  # (topic, payload) for tests

        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task | None = None
        self._timer: asyncio.Task | None = None
        self._hold_until = 0.0
        self._changed = asyncio.Event()
        self._prev_state = State.IDLE
        self._read_at: float | None = None  # when the current message was opened (this run)
        self.listeners: list[Callable[[OrbSim], None]] = []  # called on every change

    # ------------------------------------------------------------ lifecycle
    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run(), name=f"orb-sim:{self.id}")
        return self._task

    async def run(self) -> None:
        backoff = 1.0
        while True:
            if (hold := self._hold_until - time.monotonic()) > 0:
                await asyncio.sleep(hold)
            try:
                async with aiomqtt.Client(
                    hostname=self.s.host,
                    port=self.s.port,
                    username=self.s.user,
                    password=self.s.password,
                    identifier=self.id,
                    keepalive=self.s.keepalive,
                    will=aiomqtt.Will(self._topic("status"), b"offline", qos=1, retain=True),
                ) as client:
                    self._client = client
                    backoff = 1.0
                    await self._on_connect(client)
                    async for msg in client.messages:
                        await self._handle(msg.topic.value, bytes(msg.payload or b""))
            except aiomqtt.MqttError as e:
                self._client = None
                self.connected = False
                self._set_state(State.UNREACHABLE)
                self.console.say("net", f"broker unreachable ({e}); retry in {backoff:.0f}s")
                self._touch()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.s.max_backoff)
            except ContractError as e:
                self._client = None
                self.connected = False
                self.error = e
                self.console.say("error", f"CONTRACT VIOLATION in {e.kind}:")
                for p in e.problems:
                    self.console.say("error", f"  {p}")
                self.console.say("error", f"  payload: {_short(e.payload)}")
                self._touch()
                if self.s.strict:
                    raise
                await asyncio.sleep(backoff)
            finally:
                self._client = None
                self.connected = False

    async def stop(self) -> None:
        """Clean shutdown: tell the hub we're offline, then disconnect."""
        if self._client is not None:
            with contextlib.suppress(aiomqtt.MqttError):
                await self._client.publish(self._topic("status"), b"offline", qos=1, retain=True)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, ContractError):
                await self._task
        if self._timer is not None:
            self._timer.cancel()

    async def wait_for(self, predicate: Callable[[OrbSim], Any], timeout: float = 10.0) -> Any:
        """Await until predicate(self) is truthy. Raises the sim's ContractError if one hit."""
        deadline = time.monotonic() + timeout
        while True:
            self._changed.clear()
            if self.error is not None and self.s.strict:
                raise self.error
            if value := predicate(self):
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.id}: condition not met within {timeout}s")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), remaining)

    # ------------------------------------------------------------ touch input
    async def tap(self) -> str:
        """A tap on the screen, with the firmware's rules (docs/firmware.md state table).

        Returns what it did: "read", "reshow", "dismiss", "nothing" or "ignored".
        """
        if self.state == State.RECEIPT:
            self._arm_timer(0, State.RECEIPT, None)
            return "dismiss"
        if self.state in (State.SHOW, State.NOTHING):
            self._arm_timer(0, self.state, State.IDLE)
            return "dismiss"
        if self.state == State.UNREAD:
            await self.read()
            return "read"
        if self.state == State.IDLE:
            recent = (
                self._read_at is not None
                and time.monotonic() - self._read_at < self.s.reshow_window
            )
            if self.message is not None and recent:
                await self.read()
                return "reshow"
            self._set_state(State.NOTHING)
            self.console.screen(self.state, "no new messages", self._last_line())
            self._arm_timer(self.s.nothing_timeout, State.NOTHING, State.IDLE)
            self._touch()
            return "nothing"
        return "ignored"

    def _last_line(self) -> str:
        if not self.message:
            return ""
        when = time.strftime("%H:%M", time.localtime(self.message["ts"]))
        return f"last one from {self.message['from_name']} · {when}"

    async def read(self) -> bool:
        """Open the current message (library API; `tap()` applies the screen rules).

        Returns True if this was the first read.
        """
        if self.message is None:
            self.console.say("info", "tap: nothing to show")
            return False
        msg = self.message
        first = msg["id"] != self.nvs.data.last_read_id
        self._read_at = time.monotonic()
        self._set_state(State.SHOW)
        self._render_message(msg)
        if first:
            await self._publish_event({"type": "read", "msg_id": msg["id"]})
            self.nvs.data.last_read_id = msg["id"]
            self.nvs.save()
        self._arm_timer(self.s.show_timeout, State.SHOW, State.IDLE)
        self._touch()
        return first

    async def react(self, sticker: str = "purple-heart") -> bool:
        """Long-press: send a sticker back to the sender of the current message."""
        if self.message is None:
            self.console.say("info", "long-press: no message to react to")
            return False
        await self._publish_event(
            {"type": "react", "msg_id": self.message["id"], "sticker": sticker}
        )
        self.console.say("info", f"sent {sticker} back")
        return True

    async def drop(self, seconds: float = 5.0) -> None:
        """Yank the network: close the socket without DISCONNECT so the LWT fires."""
        self._hold_until = time.monotonic() + seconds
        client = self._client
        if client is None:
            self.console.say("info", "already offline")
            return
        self.console.say("net", f"dropping connection for {seconds:.0f}s (LWT should fire)")
        # aiomqtt has no unclean-disconnect API. shutdown() sends a FIN with no
        # MQTT DISCONNECT (so the broker fires the will) and makes the fd
        # readable, so aiomqtt's reader sees EOF and raises MqttError. close()
        # would leave the registered reader dangling and hang instead.
        sock = client._client.socket()
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    async def reboot(self, seconds: float = 1.0) -> None:
        """Power-cycle: forget everything in RAM (NVS survives), reconnect and let
        the retained topics rebuild the state, exactly like a real orb."""
        self.console.say("net", "rebooting (RAM cleared, NVS kept)")
        if self._timer is not None:
            self._timer.cancel()
        self.config = None
        self.message = None
        self.photo = None
        self.led = {"mode": "off"}
        self.notice = None
        self.stickers = None
        self._prev_state = State.IDLE
        self.connected = False
        self._set_state(State.UNREACHABLE)
        self._touch()
        await self.drop(seconds)

    # ------------------------------------------------------------ MQTT in
    async def _on_connect(self, client: aiomqtt.Client) -> None:
        self.connected = True
        self.console.say("net", f"connected to {self.s.host}:{self.s.port} as {self.id}")
        hello = {
            "fw": FW_VERSION,
            "model": MODEL,
            "w": 466,
            "h": 466,
            "caps": list(self.s.caps),
        }
        self.contracts.validate("hello", hello)
        await client.publish(self._topic("status"), b"online", qos=1, retain=True)
        await client.publish(self._topic("hello"), _dumps(hello), qos=1, retain=True)
        self.published += [(self._topic("status"), "online"), (self._topic("hello"), hello)]
        self.console.say("tx", "status online, hello")
        for t in ("config", "inbox", "led", "notice"):
            await client.subscribe(self._topic(t), qos=1)
        await client.subscribe("orb/all/stickers", qos=1)
        if self.config is None:
            self._set_state(State.UNCONFIGURED)
            self.console.screen(self.state, "waiting for config")
        else:
            self._set_state(self._state_for_config())
        self._touch()

    async def _handle(self, topic: str, payload: bytes) -> None:
        kind = "stickers" if topic == "orb/all/stickers" else topic.rsplit("/", 1)[-1]
        if not payload:
            self._cleared(kind)
            return
        data = self.contracts.parse(kind, payload)
        self.console.say("rx", f"{kind} {_short(data)}")
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            self.console.say("warn", f"ignoring unknown topic {topic}")
            return
        await handler(data)
        self._touch()

    def _cleared(self, kind: str) -> None:
        self.console.say("rx", f"{kind} cleared (zero-length retained)")
        if kind == "config":
            self.config = None
            self._set_state(State.UNCONFIGURED)
            self.console.screen(self.state, "forgotten by the hub; waiting for config")
        elif kind == "inbox":
            self.message = None
            self.photo = None
            if self.state in (State.UNREAD, State.SHOW):
                self._set_state(State.IDLE)
                self._render_idle()
        elif kind == "led":
            self.led = {"mode": "off"}
            self.console.say("led", led_text(self.led))
        elif kind == "stickers":
            self.stickers = None
        self._touch()

    async def _on_config(self, data: dict[str, Any]) -> None:
        self.config = data
        if data["state"] == "pending":
            self._set_state(State.PAIRING)
            self.console.screen(self.state, f"pending, code {data['code']}", f"{self.id}")
            return
        quiet = data.get("quiet")
        sub = f"owner={data.get('owner')} brightness={data.get('brightness')}"
        if quiet:
            sub += f" quiet={quiet['start']}-{quiet['end']} tz={data.get('tz')}"
        self.console.say("info", f"active: {sub} media_base={data['media_base']}")
        if self.state in (State.PAIRING, State.UNCONFIGURED):
            self._set_state(self._state_for_config())
            if self.state == State.IDLE:
                self._render_idle()
        if self.stickers is not None and self.stickers.get("rev") != self.nvs.data.stk_rev:
            await self._sync_stickers(self.stickers)

    def _state_for_config(self) -> State:
        if self.config is None:
            return State.UNCONFIGURED
        if self.config["state"] == "pending":
            return State.PAIRING
        if self.message and self.message["id"] != self.nvs.data.last_read_id:
            return State.UNREAD
        return State.IDLE

    async def _on_inbox(self, data: dict[str, Any]) -> None:
        if not self.message or data["id"] != self.message["id"]:
            self._read_at = None
        self.message = data
        self.photo = None
        if data["type"] == "photo":
            await self._fetch_photo(data)
        if data["id"] == self.nvs.data.last_read_id:
            # retained re-delivery of something we already read (reboot)
            self._set_state(State.IDLE)
            self._render_idle()
            return
        if data["id"] != self.nvs.data.last_received_id:
            await self._publish_event({"type": "received", "msg_id": data["id"]})
            self.nvs.data.last_received_id = data["id"]
            self.nvs.save()
        self._set_state(State.UNREAD)
        self.console.screen(self.state, f"New from {data['from_name']}", "tap to open")

    async def _on_led(self, data: dict[str, Any]) -> None:
        self.led = {**LED_DEFAULTS.get(data["mode"], {}), **data}
        self.console.say("led", led_text(self.led))

    async def _on_notice(self, data: dict[str, Any]) -> None:
        self.notice = data
        if self.state != State.RECEIPT:
            self._prev_state = self.state
        self._set_state(State.RECEIPT)
        if data["type"] == "read_receipt":
            self.console.screen(self.state, f"{data['by']} read it", data["msg_id"])
        else:
            self.console.screen(self.state, f"{data['by']} sent {data['sticker']}", data["msg_id"])
        self._arm_timer(self.s.receipt_timeout, State.RECEIPT, None)

    async def _on_stickers(self, data: dict[str, Any]) -> None:
        self.stickers = data
        if data["rev"] == self.nvs.data.stk_rev:
            self.console.say("info", f"stickers rev {data['rev']} already cached")
            return
        await self._sync_stickers(data)

    # ------------------------------------------------------------ media
    def _media_base(self) -> str | None:
        if self.config and self.config["state"] == "active":
            return self.config["media_base"]
        return None

    async def _fetch(self, path: str) -> bytes:
        base = self._media_base()
        if base is None:
            raise ContractError(
                "inbox", [f"media {path} referenced but no active config/media_base"]
            )
        url = f"{base}{path}"
        async with httpx.AsyncClient(timeout=self.s.http_timeout) as http:
            r = await http.get(url)
        if r.status_code != 200:
            raise ContractError("media", [f"GET {url} -> HTTP {r.status_code}"])
        return r.content

    async def _fetch_photo(self, msg: dict[str, Any]) -> None:
        data = await self._fetch(msg["media"])
        ok, why = jpeg_is_baseline(data)
        if not ok:
            raise ContractError("inbox", [f"photo {msg['media']}: {why}"], msg)
        self.photo = data
        self.console.say("info", f"photo {msg['media']} fetched: {len(data)} bytes, {why}")

    async def _sync_stickers(self, manifest: dict[str, Any]) -> None:
        if self._media_base() is None:
            self.console.say("info", f"stickers rev {manifest['rev']}: deferred until active")
            return
        wanted = {item["key"]: item for item in manifest["items"]}
        index = dict(self.nvs.data.stk_index)
        downloaded = 0
        for key, item in wanted.items():
            path = self.nvs.sticker_path(key)
            if index.get(key) == item["sha256"] and path.exists():
                continue
            try:
                data = await self._fetch(item["url"])
            except ContractError as e:
                self.console.say("warn", f"sticker {key}: {e}; will retry on next manifest")
                return
            digest = hashlib.sha256(data).hexdigest()
            if digest != item["sha256"]:
                raise ContractError(
                    "stickers", [f"{key}: sha256 {digest} != manifest {item['sha256']}"], item
                )
            path.write_bytes(data)
            index[key] = digest
            downloaded += 1
        removed = 0
        for key in list(index):
            if key not in wanted:
                self.nvs.sticker_path(key).unlink(missing_ok=True)
                del index[key]
                removed += 1
        self.nvs.data.stk_index = index
        self.nvs.data.stk_rev = manifest["rev"]
        self.nvs.save()
        self.console.say(
            "info",
            f"stickers rev {manifest['rev']}: {downloaded} downloaded, {removed} removed, "
            f"{len(index)} cached",
        )

    # ------------------------------------------------------------ MQTT out
    async def _publish_event(self, event: dict[str, Any]) -> None:
        self.contracts.validate("event", event)
        if self._client is None:
            raise aiomqtt.MqttError("not connected")
        await self._client.publish(self._topic("event"), _dumps(event), qos=1)
        self.published.append((self._topic("event"), event))
        self.console.say("tx", f"event {event['type']} {event['msg_id']}")

    # ------------------------------------------------------------ helpers
    def _topic(self, leaf: str) -> str:
        return f"orb/{self.id}/{leaf}"

    def _set_state(self, state: State) -> None:
        self.state = state

    def _touch(self) -> None:
        self._changed.set()
        for cb in self.listeners:
            cb(self)

    def _arm_timer(self, seconds: float, only_if: State, back_to: State | None) -> None:
        if self._timer is not None:
            self._timer.cancel()

        async def fire() -> None:
            await asyncio.sleep(seconds)
            if self.state != only_if:
                return
            target = back_to if back_to is not None else self._prev_state
            if target in (State.SHOW, State.NOTHING):
                target = State.IDLE
            self._set_state(target)
            if target == State.IDLE:
                self._render_idle()
            elif target == State.UNREAD and self.message:
                self.console.screen(target, f"New from {self.message['from_name']}", "tap to open")
            self._touch()

        self._timer = asyncio.create_task(fire())

    def _render_idle(self) -> None:
        name = self.config.get("name", "") if self.config else ""
        self.console.screen(State.IDLE, f"eyes · {name}", led_text(self.led))

    def _render_message(self, msg: dict[str, Any]) -> None:
        kind = msg["type"]
        head = f"{msg['from_name']} · {time.strftime('%H:%M', time.localtime(msg['ts']))}"
        if kind == "text":
            body = f'"{msg["text"]}"'
        elif kind == "mood":
            body = f"[feels {msg['mood']}]"
            if msg.get("text"):
                body += f' "{msg["text"]}"'
        elif kind == "sticker":
            cached = self.nvs.sticker_path(msg["sticker"]).exists()
            body = f"[{msg['sticker']}{'' if cached else ' (not cached: caption only)'}]"
            if msg.get("text"):
                body += f' "{msg["text"]}"'
        else:
            size = len(self.photo) if self.photo else 0
            body = f"[photo {msg['media']} {size} bytes]"
            if msg.get("text"):
                body += f' "{msg["text"]}"'
        self.console.screen(State.SHOW, body, head)


def _dumps(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


def _short(data: Any, limit: int = 160) -> str:
    s = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[: limit - 1] + "…"
