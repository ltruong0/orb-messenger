"""The hub's domain logic. Owns all state; everything else (API, MQTT) is plumbing.

Message lifecycle, pairing and the retained-topic rules are in
docs/architecture.md and docs/mqtt-contract.md. The hub only ever publishes
payloads that validate against contracts/schemas."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from .bus import EventBus
from .config import Settings, User
from .contract import ContractError, Contracts, dumps
from .db import Database
from .ids import ORB_ID_RE, STICKER_KEY_RE, ULID_RE, new_ulid
from .moods import MOOD_KEYS
from .mqtt import Mqtt, MqttUnavailable
from .photos import process_photo
from .stickers import (
    EMOJI_INLINE_SIZE,
    SEED_STICKERS,
    BadSticker,
    render_emoji,
    render_upload,
    sha256_hex,
    slugify,
)

log = logging.getLogger("hub")

RETAINED_LEAVES = ("hello", "status", "config", "inbox", "led")
DEFAULT_PHRASES = ["thinking of you", "miss you", "lunch?", "love you", "on my way", "coffee?"]
MAX_TEXT = 80
MAX_PHRASES = 12


class HubError(Exception):
    """Maps to the API error shape {"error": code, "detail": ...}."""

    def __init__(self, status: int, code: str, detail: str = ""):
        self.status, self.code, self.detail = status, code, detail
        super().__init__(f"{code}: {detail}")


def now() -> int:
    return int(time.time())


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


class Hub:
    def __init__(self, settings: Settings, bus: EventBus | None = None):
        self.s = settings
        self.db = Database(settings.db_path)
        self.bus = bus or EventBus()
        self.contracts = Contracts(settings.contracts_dir)
        self.mqtt = Mqtt(
            settings.mqtt_host,
            settings.mqtt_port,
            settings.mqtt_user,
            settings.mqtt_password,
            handler=self.on_mqtt,
            on_connect=self.reconcile,
        )
        self.emoji_font: Path | None = settings.find_emoji_font()
        self.firmware: dict[str, dict[str, Any]] = {}  # model -> build to run (meta 'firmware')
        self._presence: dict[str, bool] = {}  # status seen before hello for unknown orbs
        self._led_timers: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        for sub in ("photos", "stickers", "emoji", "fw"):
            (self.s.media_dir / sub).mkdir(parents=True, exist_ok=True)
        await self.db.open()
        self.firmware = await self.db.get_meta("firmware", {})
        await self._seed_stickers()
        if await self.db.get_meta("phrases") is None:
            await self.db.set_meta("phrases", DEFAULT_PHRASES)
        await self.mqtt.start()

    async def stop(self) -> None:
        for t in self._led_timers.values():
            t.cancel()
        await self.mqtt.stop()
        await self.db.close()

    @property
    def ready(self) -> bool:
        return self.db.is_open and self.mqtt.connected.is_set()

    async def reconcile(self) -> None:
        """On every MQTT (re)connect: republish all retained hub state so orbs
        converge even if the broker lost its persistence or a publish failed."""
        await self.publish_manifest()
        for orb in await self.db.fetchall("SELECT * FROM orbs"):
            await self._publish_orb_state(dict(orb))
        log.info("reconciled retained state")

    # ------------------------------------------------------------ MQTT in
    async def on_mqtt(self, topic: str, payload: bytes, retained: bool) -> None:
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "orb" or parts[2] not in ("hello", "status", "event"):
            return
        orb_id, leaf = parts[1], parts[2]
        if not ORB_ID_RE.match(orb_id):
            log.warning("ignoring %s: bad orb id", topic)
            return
        if not payload:
            return  # retained clear (our own forget)
        if leaf == "status":
            await self._on_status(orb_id, payload.decode("utf-8", "replace"))
            return
        try:
            data = self.contracts.parse(leaf, payload)
        except ContractError as e:
            log.warning("ignoring %s: %s", topic, e)
            return
        if leaf == "hello":
            await self._on_hello(orb_id, data, retained)
        else:
            await self._on_event(orb_id, data)

    async def _on_status(self, orb_id: str, text: str) -> None:
        if text not in ("online", "offline"):
            log.warning("ignoring status %r from %s", text, orb_id)
            return
        online = text == "online"
        orb = _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
        if orb is None:
            self._presence[orb_id] = online
            return
        await self.db.execute(
            "UPDATE orbs SET online=?, last_seen=? WHERE id=?", (int(online), now(), orb_id)
        )
        if bool(orb["online"]) != online:
            self.bus.publish("orb.status", id=orb_id, online=online)

    async def _on_hello(self, orb_id: str, hello: dict[str, Any], retained: bool) -> None:
        ts = now()
        orb = _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
        caps = json.dumps(hello.get("caps", []))
        if orb is None:
            online = self._presence.pop(orb_id, not retained)
            code, expires = self._new_code()
            await self.db.execute(
                "INSERT INTO orbs(id,state,code,code_expires,online,last_seen,fw,model,caps,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    orb_id,
                    "pending",
                    code,
                    expires,
                    int(online),
                    ts,
                    hello["fw"],
                    hello.get("model"),
                    caps,
                    ts,
                ),
            )
            orb = _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
            assert orb is not None
            log.info("new orb %s (fw %s): pending, code %s", orb_id, hello["fw"], code)
            await self._publish_orb_state(orb)
            self.bus.publish("orb.pending", id=orb_id, fw=hello["fw"])
            return

        await self.db.execute(
            "UPDATE orbs SET fw=?, model=?, caps=?, last_seen=?, online=? WHERE id=?",
            (
                hello["fw"],
                hello.get("model"),
                caps,
                ts,
                int(online)
                if (online := self._presence.pop(orb_id, None)) is not None
                else (1 if not retained else orb["online"]),
                orb_id,
            ),
        )
        orb.update(fw=hello["fw"], model=hello.get("model"), caps=caps)
        if orb["state"] == "pending":
            # a fresh hello (reboot) gets a fresh code; a retained replay at hub
            # start keeps the code the orb is already showing unless it expired
            if not retained or (orb["code_expires"] or 0) <= ts:
                code, expires = self._new_code()
                await self.db.execute(
                    "UPDATE orbs SET code=?, code_expires=? WHERE id=?", (code, expires, orb_id)
                )
                orb.update(code=code, code_expires=expires)
                log.info("orb %s said hello again: new code %s", orb_id, code)
            await self._publish_orb_state(orb)
            self.bus.publish("orb.pending", id=orb_id, fw=hello["fw"])
        else:
            if not retained:
                await self._publish_orb_state(orb)
            self.bus.publish("orb.updated", id=orb_id)

    async def _on_event(self, orb_id: str, ev: dict[str, Any]) -> None:
        orb = _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
        if orb is None or orb["state"] != "active":
            log.info(
                "ignoring event from %s orb %s", "unknown" if orb is None else "pending", orb_id
            )
            return
        kind, msg_id, sticker = ev["type"], ev["msg_id"], ev.get("sticker", "")
        inserted = await self.db.execute(
            "INSERT OR IGNORE INTO orb_events(orb_id,type,msg_id,sticker,at) VALUES(?,?,?,?,?)",
            (orb_id, kind, msg_id, sticker, now()),
        )
        if not inserted:
            log.debug("duplicate %s event from %s for %s", kind, orb_id, msg_id)
            return
        delivery = _row(
            await self.db.fetchone(
                "SELECT * FROM deliveries WHERE msg_id=? AND orb_id=?", (msg_id, orb_id)
            )
        )
        if delivery is None:
            log.info("ignoring %s event from %s for unknown message %s", kind, orb_id, msg_id)
            return
        latest = await self._latest_delivery(orb_id)
        if latest is None or latest["msg_id"] != msg_id:
            log.info("ignoring %s event from %s for superseded message %s", kind, orb_id, msg_id)
            return
        if kind == "received":
            await self._mark_delivered(delivery)
        elif kind == "read":
            await self._mark_read(orb, delivery)
        elif kind == "react":
            await self._react(orb, delivery, sticker)

    async def _mark_delivered(self, d: dict[str, Any]) -> None:
        if d["status"] != "queued":
            return
        ts = now()
        await self.db.execute(
            "UPDATE deliveries SET status='delivered', delivered_at=? WHERE msg_id=? AND orb_id=?",
            (ts, d["msg_id"], d["orb_id"]),
        )
        self.bus.publish("message.delivered", id=d["msg_id"], orb=d["orb_id"])

    async def _mark_read(self, orb: dict[str, Any], d: dict[str, Any]) -> None:
        if d["status"] == "read":
            return
        ts = now()
        await self.db.execute(
            "UPDATE deliveries SET status='read', read_at=?, delivered_at=COALESCE(delivered_at,?)"
            " WHERE msg_id=? AND orb_id=?",
            (ts, ts, d["msg_id"], d["orb_id"]),
        )
        self.bus.publish("message.read", id=d["msg_id"], orb=d["orb_id"], at=ts)
        await self._publish_led(orb["id"])
        # read receipt to the sender's home orb (architecture.md step 5)
        msg = _row(await self.db.fetchone("SELECT * FROM messages WHERE id=?", (d["msg_id"],)))
        if msg is None:
            return
        home = await self.home_orb(msg["sender"])
        if home is None or home["id"] == orb["id"]:
            return
        notice = {"type": "read_receipt", "by": self._reader_name(orb), "msg_id": msg["id"]}
        await self._publish(f"orb/{home['id']}/notice", "notice", notice, retain=False)
        await self._publish_led(home["id"], override="read")
        self._schedule_led_reset(home["id"])

    async def _react(self, orb: dict[str, Any], d: dict[str, Any], sticker: str) -> None:
        if not await self.db.fetchone("SELECT 1 FROM stickers WHERE key=?", (sticker,)):
            log.info("ignoring reaction with unknown sticker %r from %s", sticker, orb["id"])
            return
        msg = _row(await self.db.fetchone("SELECT * FROM messages WHERE id=?", (d["msg_id"],)))
        if msg is None:
            return
        home = await self.home_orb(msg["sender"])
        if home is None or home["id"] == orb["id"]:
            log.info(
                "reaction from %s has nowhere to go (sender %s has no other home orb)",
                orb["id"],
                msg["sender"],
            )
            return
        sender = self._orb_sender(orb)
        await self.send(sender, [home["id"]], sticker=sticker, reaction_to=msg["id"])

    # ------------------------------------------------------------ orbs
    async def list_orbs(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT o.*, (SELECT status FROM deliveries d WHERE d.orb_id=o.id"
            " ORDER BY d.msg_id DESC LIMIT 1) AS latest_status FROM orbs o ORDER BY created_at"
        )
        return [self._orb_view(dict(r)) for r in rows]

    async def get_orb(self, orb_id: str) -> dict[str, Any]:
        orb = _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
        if orb is None:
            raise HubError(404, "not_found", f"no orb {orb_id}")
        return orb

    async def approve(self, orb_id: str, code: str, name: str, owner: str | None) -> dict:
        orb = await self.get_orb(orb_id)
        if orb["state"] != "pending":
            raise HubError(409, "already_active", f"{orb_id} is already approved")
        name = self._check_name(name)
        self._check_owner(owner)
        ts = now()
        if (orb["code_expires"] or 0) <= ts:
            new_code, expires = self._new_code()
            await self.db.execute(
                "UPDATE orbs SET code=?, code_expires=? WHERE id=?", (new_code, expires, orb_id)
            )
            orb.update(code=new_code, code_expires=expires)
            await self._publish_orb_state(orb)
            raise HubError(409, "code_expired", "pairing code expired; the orb shows a new one")
        if not secrets.compare_digest(str(code), str(orb["code"])):
            raise HubError(409, "bad_code", "that is not the code on the orb")
        await self.db.execute(
            "UPDATE orbs SET state='active', name=?, owner=?, code=NULL, code_expires=NULL,"
            " approved_at=? WHERE id=?",
            (name, owner, ts, orb_id),
        )
        orb = await self.get_orb(orb_id)
        await self._publish_orb_state(orb)
        self.bus.publish("orb.updated", id=orb_id)
        log.info("orb %s approved as %r (owner %s)", orb_id, name, owner)
        return self._orb_view(orb)

    async def update_orb(self, orb_id: str, patch: dict[str, Any]) -> dict:
        orb = await self.get_orb(orb_id)
        if orb["state"] != "active":
            raise HubError(409, "not_active", "approve the orb first")
        sets: dict[str, Any] = {}
        if "name" in patch:
            sets["name"] = self._check_name(patch["name"])
        if "owner" in patch:
            self._check_owner(patch["owner"])
            sets["owner"] = patch["owner"]
        if "brightness" in patch:
            b = patch["brightness"]
            if not isinstance(b, int) or not 0 <= b <= 100:
                raise HubError(422, "validation", "brightness must be 0..100")
            sets["brightness"] = b
        if "quiet" in patch:
            q = patch["quiet"]
            if q is not None:
                try:
                    self.contracts.validate(
                        "config", {"state": "pending", "code": "0000", "quiet": q}
                    )
                except ContractError as e:
                    raise HubError(422, "validation", f"quiet: {e.problems[0]}") from e
            sets["quiet"] = json.dumps(q) if q is not None else None
        if sets:
            cols = ", ".join(f"{k}=?" for k in sets)
            await self.db.execute(f"UPDATE orbs SET {cols} WHERE id=?", (*sets.values(), orb_id))
        orb = await self.get_orb(orb_id)
        await self._publish_orb_state(orb)
        self.bus.publish("orb.updated", id=orb_id)
        return self._orb_view(orb)

    async def ignore(self, orb_id: str) -> None:
        orb = await self.get_orb(orb_id)
        if orb["state"] != "pending":
            raise HubError(409, "already_active", "use DELETE to forget an approved orb")
        await self.forget(orb_id)

    async def forget(self, orb_id: str) -> None:
        await self.get_orb(orb_id)
        await self.db.execute("DELETE FROM orbs WHERE id=?", (orb_id,))
        await self.db.execute("DELETE FROM orb_events WHERE orb_id=?", (orb_id,))
        self._presence.pop(orb_id, None)
        if t := self._led_timers.pop(orb_id, None):
            t.cancel()
        for leaf in RETAINED_LEAVES:
            await self.mqtt.publish(f"orb/{orb_id}/{leaf}", b"", retain=True)
        self.bus.publish("orb.updated", id=orb_id)
        log.info("orb %s forgotten", orb_id)

    async def home_orb(self, user_id: str) -> dict[str, Any] | None:
        return _row(
            await self.db.fetchone(
                "SELECT * FROM orbs WHERE state='active' AND owner=? ORDER BY approved_at LIMIT 1",
                (user_id,),
            )
        )

    def _orb_view(self, orb: dict[str, Any]) -> dict[str, Any]:
        latest = orb.get("latest_status")
        return {
            "id": orb["id"],
            "name": orb["name"],
            "owner": orb["owner"],
            "state": orb["state"],
            "online": bool(orb["online"]),
            "last_seen": orb["last_seen"],
            "fw": orb["fw"],
            "model": orb["model"],
            "caps": json.loads(orb["caps"] or "[]"),
            "brightness": orb["brightness"],
            "quiet": json.loads(orb["quiet"]) if orb["quiet"] else None,
            "unread": latest is not None and latest != "read",
            "code_expires": orb["code_expires"] if orb["state"] == "pending" else None,
        }

    def _new_code(self) -> tuple[str, int]:
        return f"{secrets.randbelow(10000):04d}", now() + self.s.code_ttl

    def _check_name(self, name: Any) -> str:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32:
            raise HubError(422, "validation", "name must be 1..32 characters")
        return name.strip()

    def _check_owner(self, owner: Any) -> None:
        if owner is not None and self.s.user(owner) is None:
            raise HubError(422, "unknown_user", f"no user {owner!r}")

    def _reader_name(self, orb: dict[str, Any]) -> str:
        user = self.s.user(orb["owner"]) if orb["owner"] else None
        return (user.name if user else orb["name"] or orb["id"])[:24]

    def _orb_sender(self, orb: dict[str, Any]) -> User:
        """Who a reaction is from: the orb's owner, or the orb itself if shared."""
        if orb["owner"] and (user := self.s.user(orb["owner"])):
            return user
        return User(id=orb["id"], name=(orb["name"] or orb["id"])[:24])

    # ------------------------------------------------------------ publishing
    async def _publish(self, topic: str, kind: str, data: dict[str, Any], retain: bool) -> None:
        self.contracts.validate(kind, data)
        await self.mqtt.publish(topic, dumps(data), retain=retain)

    def config_payload(self, orb: dict[str, Any]) -> dict[str, Any]:
        if orb["state"] == "pending":
            return {"state": "pending", "code": orb["code"]}
        payload = {
            "state": "active",
            "name": orb["name"],
            "owner": orb["owner"],
            "brightness": orb["brightness"],
            "quiet": json.loads(orb["quiet"]) if orb["quiet"] else None,
            "tz": self.s.tz_posix,
            "media_base": self.s.media_base_url,
        }
        fw = self.firmware.get(orb.get("model") or "")
        if fw:
            payload["fw"] = {k: fw[k] for k in ("version", "model", "url", "sha256", "size")}
        return payload

    async def _publish_orb_state(self, orb: dict[str, Any]) -> None:
        """config + led (+ latest inbox for active orbs): the orb's whole retained state."""
        await self._publish(f"orb/{orb['id']}/config", "config", self.config_payload(orb), True)
        if orb["state"] == "active" and (latest := await self._latest_delivery(orb["id"])):
            msg = _row(
                await self.db.fetchone("SELECT * FROM messages WHERE id=?", (latest["msg_id"],))
            )
            if msg:
                await self._publish(
                    f"orb/{orb['id']}/inbox", "inbox", self.inbox_payload(msg), True
                )
        await self._publish_led(orb["id"], orb=orb)

    async def _publish_led(
        self, orb_id: str, override: str | None = None, orb: dict[str, Any] | None = None
    ) -> None:
        mode = override or await self._led_mode(orb_id, orb)
        await self._publish(f"orb/{orb_id}/led", "led", {"mode": mode}, retain=True)

    async def _led_mode(self, orb_id: str, orb: dict[str, Any] | None = None) -> str:
        orb = orb or _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
        if orb is None:
            return "off"
        if orb["state"] == "pending":
            return "pair"
        latest = await self._latest_delivery(orb_id)
        return "unread" if latest and latest["status"] != "read" else "off"

    def _schedule_led_reset(self, orb_id: str) -> None:
        if t := self._led_timers.pop(orb_id, None):
            t.cancel()

        async def reset() -> None:
            await asyncio.sleep(self.s.receipt_led_seconds)
            self._led_timers.pop(orb_id, None)
            try:
                await self._publish_led(orb_id)
            except (MqttUnavailable, HubError) as e:
                log.warning("led reset for %s failed: %s", orb_id, e)

        self._led_timers[orb_id] = asyncio.create_task(reset(), name=f"led-reset:{orb_id}")

    async def _latest_delivery(self, orb_id: str) -> dict[str, Any] | None:
        return _row(
            await self.db.fetchone(
                "SELECT * FROM deliveries WHERE orb_id=? ORDER BY msg_id DESC LIMIT 1", (orb_id,)
            )
        )

    # ------------------------------------------------------------ messages
    def inbox_payload(self, msg: dict[str, Any]) -> dict[str, Any]:
        p: dict[str, Any] = {
            "id": msg["id"],
            "from": msg["sender"],
            "from_name": msg["sender_name"][:24],
            "type": msg["type"],
            "ts": msg["created_at"],
        }
        if msg["text"]:
            p["text"] = msg["text"]
        if msg["type"] == "sticker":
            p["sticker"] = msg["sticker"]
            p["media"] = f"/media/stickers/{msg['sticker']}.png"
        elif msg["type"] == "photo":
            p["media"] = f"/media/photos/{msg['media_id']}.jpg"
        elif msg["type"] == "mood":
            p["mood"] = msg["mood"]
        return p

    async def send(
        self,
        sender: User,
        to: list[str],
        text: str | None = None,
        sticker: str | None = None,
        media_id: str | None = None,
        reaction_to: str | None = None,
        mood: str | None = None,
    ) -> dict[str, Any]:
        text = (text or "").strip() or None
        if text is not None and len(text) > MAX_TEXT:
            raise HubError(422, "validation", f"text must be at most {MAX_TEXT} characters")
        if media_id:
            if not ULID_RE.match(media_id) or not await self.db.fetchone(
                "SELECT 1 FROM media WHERE id=?", (media_id,)
            ):
                raise HubError(422, "unknown_media", f"no uploaded media {media_id}")
            kind = "photo"
        elif sticker:
            if not STICKER_KEY_RE.match(sticker) or not await self.db.fetchone(
                "SELECT 1 FROM stickers WHERE key=?", (sticker,)
            ):
                raise HubError(422, "unknown_sticker", f"no sticker {sticker!r}")
            kind = "sticker"
        elif mood:
            if mood not in MOOD_KEYS:
                raise HubError(422, "unknown_mood", f"no mood {mood!r}")
            kind = "mood"
        elif text:
            kind = "text"
        else:
            raise HubError(422, "validation", "message needs text, a sticker, a mood or a photo")
        if not to:
            raise HubError(422, "validation", "pick at least one orb")
        recipients: list[dict[str, Any]] = []
        for orb_id in dict.fromkeys(to):
            orb = _row(await self.db.fetchone("SELECT * FROM orbs WHERE id=?", (orb_id,)))
            if orb is None:
                raise HubError(422, "unknown_orb", f"no orb {orb_id}")
            if orb["state"] == "active":
                recipients.append(orb)
        if not recipients:
            raise HubError(422, "no_active_recipients", "none of those orbs is approved")
        if not self.mqtt.connected.is_set():
            raise HubError(503, "mqtt_unavailable", "the broker is not reachable right now")

        msg = {
            "id": new_ulid(),
            "sender": sender.id,
            "sender_name": sender.name,
            "type": kind,
            "text": text,
            "sticker": sticker if kind == "sticker" else None,
            "mood": mood if kind == "mood" else None,
            "media_id": media_id if kind == "photo" else None,
            "reaction_to": reaction_to,
            "created_at": now(),
        }
        payload = self.inbox_payload(msg)  # validates shape before we store anything
        self.contracts.validate("inbox", payload)
        await self.db.execute(
            "INSERT INTO messages(id,sender,sender_name,type,text,sticker,mood,media_id,"
            "reaction_to,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            tuple(msg.values()),
        )
        deliveries = []
        for orb in recipients:
            await self.db.execute(
                "INSERT INTO deliveries(msg_id,orb_id,status) VALUES(?,?,'queued')",
                (msg["id"], orb["id"]),
            )
            try:
                await self.mqtt.publish(f"orb/{orb['id']}/inbox", dumps(payload), retain=True)
                await self._publish_led(orb["id"], override="unread")
            except MqttUnavailable as e:  # stays queued; reconcile() republishes on reconnect
                log.warning("publish to %s failed: %s", orb["id"], e)
            deliveries.append({"orb": orb["id"], "status": "queued"})
        self.bus.publish("message.created", id=msg["id"])
        log.info(
            "message %s (%s) from %s to %s",
            msg["id"],
            kind,
            sender.id,
            [d["orb"] for d in deliveries],
        )
        return {"id": msg["id"], "deliveries": deliveries}

    async def list_messages(self, before: str | None, limit: int) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        if before and not ULID_RE.match(before):
            raise HubError(422, "validation", "before must be a message id")
        rows = await self.db.fetchall(
            "SELECT * FROM messages WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
            (before, before, limit),
        )
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        drows = await self.db.fetchall(
            f"SELECT d.*, o.name AS orb_name FROM deliveries d LEFT JOIN orbs o ON o.id=d.orb_id"
            f" WHERE d.msg_id IN ({marks})",
            ids,
        )
        by_msg: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
        for d in drows:
            by_msg[d["msg_id"]].append(
                {
                    "orb": d["orb_id"],
                    "orb_name": d["orb_name"],
                    "status": d["status"],
                    "delivered_at": d["delivered_at"],
                    "read_at": d["read_at"],
                }
            )
        out = []
        for r in rows:
            m = dict(r)
            out.append(
                {
                    "id": m["id"],
                    "from": m["sender"],
                    "from_name": m["sender_name"],
                    "type": m["type"],
                    "text": m["text"],
                    "sticker": m["sticker"],
                    "mood": m.get("mood"),
                    "media_url": self.inbox_payload(m).get("media"),
                    "reaction_to": m["reaction_to"],
                    "ts": m["created_at"],
                    "deliveries": by_msg[m["id"]],
                }
            )
        return out

    # ------------------------------------------------------------ media
    async def store_photo(self, data: bytes) -> dict[str, str]:
        jpeg = await asyncio.to_thread(process_photo, data)
        media_id = new_ulid()
        (self.s.media_dir / "photos" / f"{media_id}.jpg").write_bytes(jpeg)
        await self.db.execute(
            "INSERT INTO media(id,kind,created_at) VALUES(?,?,?)", (media_id, "photo", now())
        )
        return {"id": media_id, "url": f"/media/photos/{media_id}.jpg"}

    EMOJI_SEQ = re.compile(r"^[0-9a-f]{2,6}(-[0-9a-f]{2,6}){0,8}$")

    async def emoji_png(self, seq: str) -> Path:
        """Small PNG of one emoji grapheme for inline text on orbs (decisions 46).

        `seq` is the grapheme's codepoints in lowercase hex joined by '-'. Rendered on
        first request and kept under media/emoji; the orb builds the URL itself.
        """
        if not self.EMOJI_SEQ.match(seq):
            raise HubError(404, "unknown_emoji", "not an emoji codepoint sequence")
        path = self.s.media_dir / "emoji" / f"{seq}.png"
        if path.exists():
            return path
        if self.emoji_font is None:
            raise HubError(500, "no_emoji_font", "NotoColorEmoji.ttf not found on the hub")
        text = "".join(chr(int(h, 16)) for h in seq.split("-"))
        try:
            png = await asyncio.to_thread(
                render_emoji, text, self.emoji_font, EMOJI_INLINE_SIZE, False
            )
        except (BadSticker, OSError, ValueError) as e:
            raise HubError(404, "unknown_emoji", str(e)) from e
        path.write_bytes(png)
        return path

    # ------------------------------------------------------------ firmware (OTA, decisions 47)
    FW_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$")
    FW_MODEL = re.compile(r"^[a-z0-9][a-z0-9.-]{1,31}$")

    async def list_firmware(self) -> list[dict[str, Any]]:
        return [{"model": m, **v} for m, v in sorted(self.firmware.items())]

    async def add_firmware(self, data: bytes, version: str, model: str) -> dict[str, Any]:
        """Store a build for one board model and tell every active orb of that model."""
        if not self.FW_VERSION.match(version):
            raise HubError(422, "validation", "version must look like 1.2.3")
        if not self.FW_MODEL.match(model):
            raise HubError(422, "validation", "model must be the orb's hello.model")
        if len(data) < 64 * 1024 or data[0] != 0xE9:
            raise HubError(422, "bad_firmware", "not an ESP32 application image")
        name = f"{model}-{version}.bin"
        (self.s.media_dir / "fw" / name).write_bytes(data)
        entry = {
            "version": version,
            "model": model,
            "url": f"/media/fw/{name}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "uploaded_at": now(),
        }
        self.firmware[model] = entry
        await self.db.set_meta("firmware", self.firmware)
        await self._republish_configs(model)
        self.bus.publish("firmware.changed", model=model)
        return {"model": model, **entry}

    async def delete_firmware(self, model: str) -> None:
        if model not in self.firmware:
            raise HubError(404, "unknown_firmware", "no firmware for that model")
        del self.firmware[model]
        await self.db.set_meta("firmware", self.firmware)
        await self._republish_configs(model)
        self.bus.publish("firmware.changed", model=model)

    async def _republish_configs(self, model: str) -> None:
        for orb in await self.db.fetchall(
            "SELECT * FROM orbs WHERE state='active' AND model=?", (model,)
        ):
            await self._publish(
                f"orb/{orb['id']}/config", "config", self.config_payload(_row(orb)), True
            )

    # ------------------------------------------------------------ stickers
    async def list_stickers(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM stickers ORDER BY sort, created_at")
        return [
            {
                "key": r["key"],
                "name": r["name"],
                "url": f"/media/stickers/{r['key']}.png",
                "sha256": r["sha256"],
            }
            for r in rows
        ]

    async def manifest(self) -> dict[str, Any]:
        rows = await self.db.fetchall("SELECT * FROM stickers ORDER BY sort, created_at")
        return {
            "rev": await self.db.get_meta("stickers_rev", 0),
            "items": [
                {"key": r["key"], "url": f"/media/stickers/{r['key']}.png", "sha256": r["sha256"]}
                for r in rows
            ],
        }

    async def publish_manifest(self) -> None:
        await self._publish("orb/all/stickers", "stickers", await self.manifest(), retain=True)

    async def add_emoji_sticker(self, emoji: str, name: str) -> dict[str, Any]:
        if self.emoji_font is None:
            raise HubError(500, "no_emoji_font", "NotoColorEmoji.ttf not found on the hub")
        try:
            png = await asyncio.to_thread(render_emoji, emoji, self.emoji_font)
        except BadSticker as e:
            raise HubError(422, "bad_sticker", str(e)) from e
        return await self._add_sticker(name, png)

    async def add_upload_sticker(self, data: bytes, name: str) -> dict[str, Any]:
        try:
            png = await asyncio.to_thread(render_upload, data)
        except BadSticker as e:
            raise HubError(422, "bad_sticker", str(e)) from e
        return await self._add_sticker(name, png)

    async def _add_sticker(self, name: str, png: bytes, *, publish: bool = True) -> dict[str, Any]:
        name = (name or "").strip()
        if not 1 <= len(name) <= 32:
            raise HubError(422, "validation", "name must be 1..32 characters")
        try:
            key = slugify(name)
        except BadSticker as e:
            raise HubError(422, "validation", str(e)) from e
        if await self.db.fetchone("SELECT 1 FROM stickers WHERE key=?", (key,)):
            raise HubError(409, "sticker_exists", f"a sticker with key {key!r} already exists")
        count = (await self.db.fetchone("SELECT COUNT(*) AS n FROM stickers"))["n"]
        if count >= 64:
            raise HubError(422, "validation", "sticker library is full (64)")
        (self.s.media_dir / "stickers" / f"{key}.png").write_bytes(png)
        await self.db.execute(
            "INSERT INTO stickers(key,name,sha256,sort,created_at) VALUES(?,?,?,?,?)",
            (key, name, sha256_hex(png), count, now()),
        )
        if publish:
            await self._bump_manifest()
        return {"key": key, "name": name, "url": f"/media/stickers/{key}.png"}

    async def delete_sticker(self, key: str) -> None:
        if not await self.db.execute("DELETE FROM stickers WHERE key=?", (key,)):
            raise HubError(404, "not_found", f"no sticker {key!r}")
        (self.s.media_dir / "stickers" / f"{key}.png").unlink(missing_ok=True)
        await self._bump_manifest()

    async def _bump_manifest(self) -> None:
        rev = await self.db.get_meta("stickers_rev", 0) + 1
        await self.db.set_meta("stickers_rev", rev)
        try:
            await self.publish_manifest()
        except MqttUnavailable as e:  # reconcile() republishes on reconnect
            log.warning("manifest publish failed: %s", e)

    async def _seed_stickers(self) -> None:
        if await self.db.fetchone("SELECT 1 FROM stickers LIMIT 1"):
            return
        if self.emoji_font is None:
            log.warning(
                "NotoColorEmoji.ttf not found: sticker library starts empty "
                "(set EMOJI_FONT or run `make fonts`)"
            )
            return
        for emoji, key, name in SEED_STICKERS:
            png = await asyncio.to_thread(render_emoji, emoji, self.emoji_font)
            await self._add_sticker(name, png, publish=False)
            assert slugify(name) == key, (name, key)
        await self.db.set_meta("stickers_rev", 1)
        log.info("seeded %d stickers", len(SEED_STICKERS))

    # ------------------------------------------------------------ phrases
    async def get_phrases(self) -> list[str]:
        return await self.db.get_meta("phrases", DEFAULT_PHRASES)

    async def set_phrases(self, phrases: Any) -> list[str]:
        if (
            not isinstance(phrases, list)
            or len(phrases) > MAX_PHRASES
            or not all(isinstance(p, str) and 1 <= len(p.strip()) <= MAX_TEXT for p in phrases)
        ):
            raise HubError(422, "validation", f"up to {MAX_PHRASES} phrases of 1..{MAX_TEXT} chars")
        cleaned = [p.strip() for p in phrases]
        await self.db.set_meta("phrases", cleaned)
        return cleaned
