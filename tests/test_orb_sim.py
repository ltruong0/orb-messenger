"""Phase 2 acceptance: orb-sim behaves like an orb over MQTT (docs/firmware.md)
and refuses payloads that break the contract."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from orb_sim import ContractError, Contracts, OrbSim, Settings, State
from orb_sim.render import Console
from orb_sim.sim import jpeg_is_baseline
from orbtest import Broker, HubProbe
from PIL import Image

MSG_A = "01JBX7Q2M3N4P5Q6R7S8T9V0W1"
MSG_B = "01JBX7R0A1B2C3D4E5F6G7H8J9"
contracts = Contracts()


@pytest.fixture
def orb_id() -> str:
    return f"orb-{uuid.uuid4().hex[:4]}"


@pytest.fixture
async def hub(broker: Broker, orb_id: str) -> AsyncIterator[HubProbe]:
    async with HubProbe(broker, client_id=f"probe-{orb_id}") as probe:
        yield probe
        await probe.clear_retained(orb_id)


@pytest.fixture
async def make_sim(broker: Broker, orb_id: str, tmp_path: Path):
    sims: list[OrbSim] = []

    async def _make(**overrides) -> OrbSim:
        kw = dict(
            host=broker.host,
            port=broker.port,
            password=broker.orb_password,
            orb_id=orb_id,
            state_dir=tmp_path / "state",
            max_backoff=1.0,
        )
        kw.update(overrides)
        sim = OrbSim(Settings(**kw), contracts, Console(prefix=f"[{orb_id}] ", color=False))
        sim.start()
        sims.append(sim)
        await sim.wait_for(lambda s: s.connected, 10)
        return sim

    yield _make
    for sim in sims:
        await sim.stop()


def active_config(media_base: str = "http://orb.marcellolab.com", **extra) -> dict:
    cfg = {"state": "active", "name": "Test orb", "owner": "lee", "media_base": media_base}
    cfg.update(extra)
    return cfg


def jpeg_bytes(progressive: bool) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (466, 466), (40, 20, 90)).save(
        buf, "JPEG", quality=80, progressive=progressive, optimize=True
    )
    return buf.getvalue()


# ----------------------------------------------------------------- connect


async def test_connect_publishes_status_and_valid_hello(hub: HubProbe, make_sim, orb_id: str):
    sim = await make_sim()
    assert await hub.expect(f"orb/{orb_id}/status") == b"online"
    hello = await hub.expect_json(f"orb/{orb_id}/hello")
    contracts.validate("hello", hello)
    assert hello["w"] == hello["h"] == 466
    assert sim.state == State.UNCONFIGURED


async def test_pending_config_shows_pairing_code(hub: HubProbe, make_sim, orb_id: str):
    sim = await make_sim()
    await hub.publish(f"orb/{orb_id}/config", {"state": "pending", "code": "4821"}, retain=True)
    await sim.wait_for(lambda s: s.state == State.PAIRING)
    assert sim.config["code"] == "4821"
    assert any("pending, code 4821" in text for _, text in sim.console.lines)

    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    await sim.wait_for(lambda s: s.state == State.IDLE)


async def test_retained_config_applies_on_reconnect(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim()
    await sim.wait_for(lambda s: s.state == State.IDLE)


# ----------------------------------------------------------------- messages


async def test_text_message_received_read_react(hub: HubProbe, make_sim, orb_id: str, tmp_path):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim()
    await sim.wait_for(lambda s: s.state == State.IDLE)
    hub.drain()

    inbox = {
        "id": MSG_A, "from": "lee", "from_name": "Lee", "type": "text",
        "text": "proud of you today", "ts": 1761834120,
    }  # fmt: skip
    await hub.publish(f"orb/{orb_id}/inbox", inbox, retain=True)
    await hub.publish(f"orb/{orb_id}/led", {"mode": "unread"}, retain=True)

    ev = await hub.expect_json(f"orb/{orb_id}/event")
    assert ev == {"type": "received", "msg_id": MSG_A}
    await sim.wait_for(lambda s: s.state == State.UNREAD and s.led["mode"] == "unread")
    assert sim.led["color"] == "#FF2D2D"  # local default filled in

    assert await sim.read() is True
    assert sim.state == State.SHOW
    assert await hub.expect_json(f"orb/{orb_id}/event") == {"type": "read", "msg_id": MSG_A}
    nvs = json.loads((tmp_path / "state" / orb_id / "nvs.json").read_text())
    assert nvs["last_read_id"] == MSG_A

    assert await sim.read() is False  # re-show: no second read event
    await sim.react("purple-heart")
    assert await hub.expect_json(f"orb/{orb_id}/event") == {
        "type": "react", "msg_id": MSG_A, "sticker": "purple-heart",
    }  # fmt: skip
    await hub.expect_nothing_on(f"orb/{orb_id}/event")

    await hub.publish(f"orb/{orb_id}/led", {"mode": "off"}, retain=True)
    await sim.wait_for(lambda s: s.led["mode"] == "off")


async def test_reboot_does_not_re_alert_on_read_message(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim()
    inbox = {
        "id": MSG_A,
        "from": "lee",
        "from_name": "Lee",
        "type": "text",
        "text": "hi",
        "ts": 1761834120,
    }
    await hub.publish(f"orb/{orb_id}/inbox", inbox, retain=True)
    await sim.wait_for(lambda s: s.state == State.UNREAD)
    await sim.read()
    await sim.stop()
    assert await hub.expect(f"orb/{orb_id}/status", 5) == b"online"
    assert await hub.expect(f"orb/{orb_id}/status", 5) == b"offline"
    hub.drain()

    sim2 = await make_sim()  # same id + state dir: like a power cycle
    await sim2.wait_for(lambda s: s.message is not None and s.state == State.IDLE)
    await hub.expect_nothing_on(f"orb/{orb_id}/event")

    # a newer message still alerts
    inbox2 = dict(inbox, id=MSG_B, text="lunch?")
    await hub.publish(f"orb/{orb_id}/inbox", inbox2, retain=True)
    assert await hub.expect_json(f"orb/{orb_id}/event") == {"type": "received", "msg_id": MSG_B}
    await sim2.wait_for(lambda s: s.state == State.UNREAD)


async def test_show_times_out_back_to_idle(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim(show_timeout=0.3)
    inbox = {
        "id": MSG_A,
        "from": "lee",
        "from_name": "Lee",
        "type": "text",
        "text": "hi",
        "ts": 1761834120,
    }
    await hub.publish(f"orb/{orb_id}/inbox", inbox, retain=True)
    await sim.wait_for(lambda s: s.state == State.UNREAD)
    await sim.read()
    await sim.wait_for(lambda s: s.state == State.IDLE, 3)


async def test_notice_overlay_then_back(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim(receipt_timeout=0.3)
    await sim.wait_for(lambda s: s.state == State.IDLE)
    await hub.publish(
        f"orb/{orb_id}/notice", {"type": "read_receipt", "by": "Ashleigh", "msg_id": MSG_A}
    )
    await sim.wait_for(lambda s: s.state == State.RECEIPT)
    assert any("Ashleigh read it" in text for _, text in sim.console.lines)
    await sim.wait_for(lambda s: s.state == State.IDLE, 3)


# ----------------------------------------------------------------- contract


async def test_invalid_inbox_fails_loudly(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim()
    bad = {
        "id": MSG_A,
        "from": "lee",
        "from_name": "Lee",
        "type": "text",
        "text": "x" * 81,
        "ts": 1761834120,
    }
    await hub.publish(f"orb/{orb_id}/inbox", bad, retain=True)
    with pytest.raises(ContractError) as ei:
        await sim.wait_for(lambda s: False, 5)
    assert ei.value.kind == "inbox"
    assert "text" in ei.value.problems[0]
    await asyncio.sleep(0.1)
    assert sim._task.done()  # strict mode: the sim is dead, not limping
    await hub.expect_nothing_on(f"orb/{orb_id}/event")
    await hub.publish(f"orb/{orb_id}/inbox", b"", retain=True)


async def test_lenient_mode_logs_and_keeps_running(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim(strict=False)
    await hub.publish(f"orb/{orb_id}/led", {"mode": "disco"}, retain=True)
    await sim.wait_for(lambda s: s.error is not None, 5)
    await sim.wait_for(lambda s: s.connected, 5)  # reconnected after the error
    await hub.publish(f"orb/{orb_id}/led", {"mode": "off"}, retain=True)


# ----------------------------------------------------------------- network


async def test_drop_fires_lwt_then_reconnects(hub: HubProbe, make_sim, orb_id: str):
    sim = await make_sim()
    assert await hub.expect(f"orb/{orb_id}/status") == b"online"
    await sim.drop(seconds=1.0)
    assert await hub.expect(f"orb/{orb_id}/status", 10) == b"offline"
    assert await hub.expect(f"orb/{orb_id}/status", 10) == b"online"
    await sim.wait_for(lambda s: s.connected)


# ----------------------------------------------------------------- media


async def test_sticker_sync_downloads_verifies_and_prunes(
    hub: HubProbe, make_sim, orb_id: str, media_server, tmp_path
):
    root, base = media_server
    png = b"\x89PNG\r\n\x1a\n" + b"fake-heart-eyes"
    (root / "media/stickers/heart-eyes.png").write_bytes(png)
    sha = hashlib.sha256(png).hexdigest()
    manifest = {
        "rev": 1,
        "items": [{"key": "heart-eyes", "url": "/media/stickers/heart-eyes.png", "sha256": sha}],
    }

    sim = await make_sim()
    await hub.publish("orb/all/stickers", manifest, retain=True)
    await sim.wait_for(lambda s: s.stickers is not None)
    assert sim.nvs.data.stk_rev is None  # deferred: no media_base yet

    await hub.publish(f"orb/{orb_id}/config", active_config(media_base=base), retain=True)
    await sim.wait_for(lambda s: s.nvs.data.stk_rev == 1)
    cached = tmp_path / "state" / orb_id / "stk" / "heart-eyes.png"
    assert cached.read_bytes() == png

    await hub.publish("orb/all/stickers", {"rev": 2, "items": []}, retain=True)
    await sim.wait_for(lambda s: s.nvs.data.stk_rev == 2)
    assert not cached.exists()

    bad = dict(manifest, rev=3, items=[dict(manifest["items"][0], sha256="0" * 64)])
    await hub.publish("orb/all/stickers", bad, retain=True)
    with pytest.raises(ContractError, match="sha256"):
        await sim.wait_for(lambda s: False, 5)
    await hub.publish("orb/all/stickers", b"", retain=True)


async def test_photo_must_be_baseline_jpeg(hub: HubProbe, make_sim, orb_id: str, media_server):
    root, base = media_server
    (root / "media/photos/good.jpg").write_bytes(jpeg_bytes(progressive=False))
    (root / "media/photos/bad.jpg").write_bytes(jpeg_bytes(progressive=True))
    assert jpeg_is_baseline(jpeg_bytes(progressive=False))[0]
    assert not jpeg_is_baseline(jpeg_bytes(progressive=True))[0]

    await hub.publish(f"orb/{orb_id}/config", active_config(media_base=base), retain=True)
    sim = await make_sim()
    photo = {
        "id": MSG_A,
        "from": "ashleigh",
        "from_name": "Ashleigh",
        "type": "photo",
        "media": "/media/photos/good.jpg",
        "ts": 1761835200,
    }
    await hub.publish(f"orb/{orb_id}/inbox", photo, retain=True)
    await sim.wait_for(lambda s: s.state == State.UNREAD)
    assert sim.photo and sim.photo[:2] == b"\xff\xd8"

    await hub.publish(
        f"orb/{orb_id}/inbox", dict(photo, id=MSG_B, media="/media/photos/bad.jpg"), retain=True
    )
    with pytest.raises(ContractError, match="progressive"):
        await sim.wait_for(lambda s: False, 5)
    await hub.publish(f"orb/{orb_id}/inbox", b"", retain=True)


# ----------------------------------------------------------------- forget


async def test_forget_clears_state(hub: HubProbe, make_sim, orb_id: str):
    await hub.publish(f"orb/{orb_id}/config", active_config(), retain=True)
    sim = await make_sim()
    await sim.wait_for(lambda s: s.state == State.IDLE)
    await hub.clear_retained(orb_id)
    await sim.wait_for(lambda s: s.state == State.UNCONFIGURED and s.config is None)
