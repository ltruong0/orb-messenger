"""orb-sim --web: real OrbSim instances behind a Starlette app. The browser side
is plain HTML; here we check the JSON/SSE/media endpoints and the actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from orb_sim import Contracts, Settings, State
from orb_sim.web import Fleet, build_app
from orbtest import Broker, HubProbe
from PIL import Image

contracts = Contracts()
MSG = "01JBX7Q2M3N4P5Q6R7S8T9V0W1"


@pytest.fixture
async def fleet(broker: Broker, tmp_path: Path) -> AsyncIterator[Fleet]:
    ids = [f"orb-{uuid.uuid4().hex[:4]}" for _ in range(2)]
    base = Settings(
        host=broker.host,
        port=broker.port,
        password=broker.orb_password,
        state_dir=tmp_path / "state",
        max_backoff=1.0,
    )
    fl = Fleet(base, ids, contracts)
    fl.start()
    for sim in fl.sims.values():
        await sim.wait_for(lambda s: s.connected, 10)
    try:
        yield fl
    finally:
        await fl.stop()
        async with HubProbe(broker, client_id=f"probe-clean-{ids[0]}") as probe:
            for orb_id in ids:
                await probe.clear_retained(orb_id)


@pytest.fixture
async def web(fleet: Fleet) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(fleet)), base_url="http://sim"
    ) as c:
        yield c


async def test_state_and_pairing_code_visible(web: httpx.AsyncClient, fleet: Fleet, broker):
    a, b = fleet.sims
    async with HubProbe(broker, client_id=f"probe-{a}") as probe:
        await probe.publish(f"orb/{a}/config", {"state": "pending", "code": "4821"}, retain=True)
        await fleet.sims[a].wait_for(lambda s: s.state == State.PAIRING, 5)
    snap = (await web.get("/api/state")).json()
    assert snap["broker"].endswith(str(broker.port))
    by_id = {o["id"]: o for o in snap["orbs"]}
    assert by_id[a]["state"] == "pairing" and by_id[a]["config"]["code"] == "4821"
    assert by_id[a]["led"]["mode"] == "off" and by_id[a]["connected"] is True
    assert by_id[b]["state"] == "unconfigured"
    assert any("pending, code 4821" in line["text"] for line in by_id[a]["log"])
    assert (await web.get("/")).headers["content-type"].startswith("text/html")
    assert (await web.post("/api/orbs/orb-nope/tap")).status_code == 404


async def test_tap_and_hold_publish_events(web: httpx.AsyncClient, fleet: Fleet, broker):
    a = next(iter(fleet.sims))
    sim = fleet.sims[a]
    async with HubProbe(broker, client_id=f"probe-{a}") as probe:
        await probe.publish(
            f"orb/{a}/config",
            {"state": "active", "name": "Hall", "media_base": "http://127.0.0.1:1"},
            retain=True,
        )
        await probe.publish(
            f"orb/{a}/inbox",
            {
                "id": MSG,
                "from": "lee",
                "from_name": "Lee",
                "type": "text",
                "text": "hi",
                "ts": 1761834120,
            },
            retain=True,
        )
        assert (await probe.expect_json(f"orb/{a}/event"))["type"] == "received"
        await sim.wait_for(lambda s: s.state == State.UNREAD, 5)

        r = await web.post(f"/api/orbs/{a}/tap")
        assert r.json() == {"ok": True, "action": "read", "first": True}
        assert (await probe.expect_json(f"orb/{a}/event")) == {"type": "read", "msg_id": MSG}
        assert sim.state == State.SHOW

        r = await web.post(f"/api/orbs/{a}/hold", json={"sticker": "cat"})
        assert r.json()["sent"] is True
        ev = await probe.expect_json(f"orb/{a}/event")
        assert ev == {"type": "react", "msg_id": MSG, "sticker": "cat"}

        # a tap during a receipt overlay dismisses it instead of re-reading
        await probe.publish(
            f"orb/{a}/notice", {"type": "read_receipt", "by": "Ashleigh", "msg_id": MSG}
        )
        await sim.wait_for(lambda s: s.state == State.RECEIPT, 5)
        assert (await web.post(f"/api/orbs/{a}/tap")).json()["action"] == "dismiss"
        await sim.wait_for(lambda s: s.state != State.RECEIPT, 5)
        snap = (await web.get("/api/state")).json()
        me = next(o for o in snap["orbs"] if o["id"] == a)
        assert me["nvs"]["last_read_id"] == MSG and me["message"]["text"] == "hi"


async def test_drop_and_reboot(web: httpx.AsyncClient, fleet: Fleet, broker):
    a = next(iter(fleet.sims))
    sim = fleet.sims[a]
    async with HubProbe(broker, client_id=f"probe-{a}") as probe:
        await probe.publish(
            f"orb/{a}/config",
            {"state": "active", "name": "Hall", "media_base": "http://127.0.0.1:1"},
            retain=True,
        )
        await sim.wait_for(lambda s: s.state == State.IDLE, 5)
        await web.post(f"/api/orbs/{a}/drop", json={"seconds": 1})
        while (await probe.expect(f"orb/{a}/status")) != b"offline":  # LWT (after replays)
            pass
        await sim.wait_for(lambda s: not s.connected, 5)
        await sim.wait_for(lambda s: s.connected and s.state == State.IDLE, 10)

        await web.post(f"/api/orbs/{a}/reboot")
        snap = (await web.get("/api/state")).json()
        me = next(o for o in snap["orbs"] if o["id"] == a)
        assert me["config"] is None and me["connected"] is False  # RAM cleared
        await sim.wait_for(lambda s: s.connected and s.config is not None, 10)
        assert sim.state == State.IDLE  # rebuilt from retained config


async def test_media_proxies_serve_what_the_orb_fetched(
    web: httpx.AsyncClient, fleet: Fleet, broker, tmp_path: Path, media_server
):
    root, base = media_server
    a = next(iter(fleet.sims))
    sim = fleet.sims[a]
    png = Image.new("RGBA", (160, 160), (255, 0, 255, 255))
    png.save(root / "media/stickers/cat.png")
    sha = hashlib.sha256((root / "media/stickers/cat.png").read_bytes()).hexdigest()
    Image.new("RGB", (466, 466), (0, 200, 0)).save(root / "media/photos/p1.jpg", "JPEG")
    async with HubProbe(broker, client_id=f"probe-{a}") as probe:
        await probe.publish(
            "orb/all/stickers",
            {"rev": 1, "items": [{"key": "cat", "url": "/media/stickers/cat.png", "sha256": sha}]},
            retain=True,
        )
        await probe.publish(
            f"orb/{a}/config", {"state": "active", "name": "Hall", "media_base": base}, retain=True
        )
        await sim.wait_for(lambda s: s.nvs.data.stk_rev == 1, 10)
        r = await web.get(f"/orbs/{a}/stickers/cat.png")
        assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == sha
        assert (await web.get(f"/orbs/{a}/stickers/nope.png")).status_code == 404
        assert (await web.get(f"/orbs/{a}/photo.jpg")).status_code == 404

        await probe.publish(
            f"orb/{a}/inbox",
            {
                "id": MSG,
                "from": "lee",
                "from_name": "Lee",
                "type": "photo",
                "media": "/media/photos/p1.jpg",
                "ts": 1761834120,
            },
            retain=True,
        )
        await sim.wait_for(lambda s: s.photo is not None, 10)
        snap = (await web.get("/api/state")).json()
        me = next(o for o in snap["orbs"] if o["id"] == a)
        assert me["message"]["photo_url"] == f"/orbs/{a}/photo.jpg?msg={MSG}"
        r = await web.get(me["message"]["photo_url"])
        assert r.status_code == 200 and r.content == sim.photo
        assert "cat" in me["stickers"]
        await probe.publish("orb/all/stickers", b"", retain=True)


async def test_sse_pushes_snapshots_on_change(fleet: Fleet, broker):
    """(httpx's ASGITransport buffers whole bodies, so drive the generator directly.)"""
    a = next(iter(fleet.sims))
    stream = fleet.events(heartbeat=0.5)
    first = await anext(stream)
    assert first.startswith("event: state\ndata: ")
    assert (
        next(o for o in json.loads(first.split("data: ", 1)[1])["orbs"] if o["id"] == a)["state"]
        == "unconfigured"
    )
    # heartbeat while idle (a snapshot or two may still drain from the connect burst)
    while (chunk := await asyncio.wait_for(anext(stream), 2)) != ": ping\n\n":
        assert chunk.startswith("event: state")
    async with HubProbe(broker, client_id=f"probe-{a}") as probe:
        await probe.publish(f"orb/{a}/config", {"state": "pending", "code": "1111"}, retain=True)
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        chunk = await asyncio.wait_for(anext(stream), 5)
        if chunk.startswith("event: state"):
            snap = json.loads(chunk.split("data: ", 1)[1])
            if next(o for o in snap["orbs"] if o["id"] == a)["state"] == "pairing":
                break
        assert asyncio.get_running_loop().time() < deadline
    await stream.aclose()
