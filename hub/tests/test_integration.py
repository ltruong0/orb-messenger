"""Phase 3 done check: a real Mosquitto, the hub, and two orb-sims driven through
register -> approve -> send -> received -> read -> receipt (-> react)."""

from __future__ import annotations

import asyncio
import io

import httpx
import pytest
from conftest import HubServer, make_settings, start_hub
from orb_sim import OrbSim, State
from orbtest import Broker, HubProbe, free_port
from PIL import Image


async def orb_view(client: httpx.AsyncClient, orb_id: str) -> dict:
    return next(o for o in (await client.get("/api/orbs")).json() if o["id"] == orb_id)


async def message_view(client: httpx.AsyncClient, msg_id: str) -> dict:
    return next(m for m in (await client.get("/api/messages")).json() if m["id"] == msg_id)


async def wait_delivery(
    client: httpx.AsyncClient, msg_id: str, orb_id: str, status: str, timeout: float = 5.0
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        m = await message_view(client, msg_id)
        d = next(d for d in m["deliveries"] if d["orb"] == orb_id)
        if d["status"] == status:
            return d
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{msg_id}/{orb_id} is {d['status']}, wanted {status}")
        await asyncio.sleep(0.1)


async def pair(client: httpx.AsyncClient, sim: OrbSim, name: str, owner: str | None) -> dict:
    """Register (the sim already said hello) and approve with the code the orb shows."""
    await sim.wait_for(lambda s: s.state == State.PAIRING, 10)
    view = await orb_view(client, sim.id)
    assert view["state"] == "pending" and view["online"] is True
    code = sim.config["code"]
    r = await client.post(
        f"/api/orbs/{sim.id}/approve", json={"code": code, "name": name, "owner": owner}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active" and r.json()["owner"] == owner
    await sim.wait_for(lambda s: s.state == State.IDLE, 10)
    assert sim.config["name"] == name and sim.config["media_base"] == client.base_url
    return r.json()


async def test_register_approve_send_received_read_receipt_react(
    client: httpx.AsyncClient, hub_server: HubServer, make_sim
):
    lee_orb = await make_sim()
    ash_orb = await make_sim()

    # --- register + pairing
    await pair(client, lee_orb, "Lee's desk", "lee")
    await pair(client, ash_orb, "Ashleigh's office", "ashleigh")
    assert (await client.get("/api/me")).json()["home_orb"] == lee_orb.id
    await lee_orb.wait_for(lambda s: s.led["mode"] == "off", 5)
    names = {e.name for e in hub_server.hub.bus.history}
    assert {"orb.pending", "orb.updated"} <= names

    # --- lee sends text to ashleigh's orb
    r = await client.post("/api/messages", json={"to": [ash_orb.id], "text": "proud of you"})
    assert r.status_code == 201, r.text
    msg_id = r.json()["id"]
    assert r.json()["deliveries"] == [{"orb": ash_orb.id, "status": "queued"}]

    await ash_orb.wait_for(lambda s: s.state == State.UNREAD, 10)
    assert ash_orb.message["text"] == "proud of you" and ash_orb.message["from_name"] == "Lee"
    await ash_orb.wait_for(lambda s: s.led["mode"] == "unread", 5)
    await wait_delivery(client, msg_id, ash_orb.id, "delivered")
    assert (await orb_view(client, ash_orb.id))["unread"] is True

    # --- she taps: read -> receipt on lee's home orb
    assert await ash_orb.read() is True
    d = await wait_delivery(client, msg_id, ash_orb.id, "read")
    assert d["read_at"] >= d["delivered_at"]
    await ash_orb.wait_for(lambda s: s.led["mode"] == "off", 5)
    await lee_orb.wait_for(lambda s: s.state == State.RECEIPT, 5)
    assert lee_orb.notice == {"type": "read_receipt", "by": "Ashleigh", "msg_id": msg_id}
    await lee_orb.wait_for(lambda s: s.led["mode"] == "read", 5)
    await lee_orb.wait_for(lambda s: s.led["mode"] == "off", 5)  # receipt_led_seconds=1
    assert (await orb_view(client, ash_orb.id))["unread"] is False
    events = [e.name for e in hub_server.hub.bus.history]
    assert events.index("message.delivered") < events.index("message.read")

    # --- she long-presses: reaction lands on lee's orb as a sticker message from ashleigh
    await ash_orb.react("purple-heart")
    await lee_orb.wait_for(lambda s: s.state == State.UNREAD, 10)
    assert lee_orb.message["type"] == "sticker"
    assert lee_orb.message["from"] == "ashleigh" and lee_orb.message["sticker"] == "purple-heart"
    history = (await client.get("/api/messages")).json()
    assert history[0]["reaction_to"] == msg_id and history[0]["from"] == "ashleigh"
    assert history[1]["id"] == msg_id
    await wait_delivery(client, history[0]["id"], lee_orb.id, "delivered")

    # --- duplicate events are ignored (QoS 1 redelivery); re-reading is a no-op
    assert await ash_orb.read() is False
    assert ash_orb.state == State.SHOW

    # --- stickers synced to both orbs from the hub's /media
    assert lee_orb.stickers["rev"] == 1 and len(lee_orb.nvs.data.stk_index) == 7


async def test_bad_code_and_ignore(client: httpx.AsyncClient, make_sim):
    sim = await make_sim()
    await sim.wait_for(lambda s: s.state == State.PAIRING, 10)
    wrong = "0000" if sim.config["code"] != "0000" else "0001"
    r = await client.post(f"/api/orbs/{sim.id}/approve", json={"code": wrong, "name": "x"})
    assert r.status_code == 409 and r.json()["error"] == "bad_code"
    r = await client.post(
        f"/api/orbs/{sim.id}/approve",
        json={"code": sim.config["code"], "name": "x", "owner": "nobody"},
    )
    assert r.status_code == 422 and r.json()["error"] == "unknown_user"
    r = await client.post("/api/messages", json={"to": [sim.id], "text": "too early"})
    assert r.status_code == 422 and r.json()["error"] == "no_active_recipients"

    assert (await client.post(f"/api/orbs/{sim.id}/ignore")).status_code == 204
    await sim.wait_for(lambda s: s.state == State.UNCONFIGURED, 5)
    assert (await client.get("/api/orbs")).json() == []


async def test_expired_code_is_regenerated(broker: Broker, tmp_path, emoji_font, make_sim):
    port = free_port()
    settings = make_settings(broker, tmp_path / "data2", port, emoji_font, code_ttl=0)
    hs = await start_hub(settings, port)
    try:
        async with httpx.AsyncClient(base_url=hs.base_url, timeout=10) as client:
            await client.post("/api/session", json={"user": "lee", "pin": "2468"})
            sim = await make_sim()
            await sim.wait_for(lambda s: s.state == State.PAIRING, 10)
            first = sim.config["code"]
            r = await client.post(f"/api/orbs/{sim.id}/approve", json={"code": first, "name": "x"})
            assert r.status_code == 409 and r.json()["error"] == "code_expired"
            await sim.wait_for(lambda s: s.config["code"] != first, 5)  # orb shows a new code
    finally:
        await hs.stop()


async def test_reboot_gets_new_code_and_keeps_state_after_approval(
    client: httpx.AsyncClient, make_sim
):
    sim = await make_sim()
    await sim.wait_for(lambda s: s.state == State.PAIRING, 10)
    first = sim.config["code"]
    await sim.drop(0.5)  # LWT offline, reconnect -> fresh hello -> new code
    await sim.wait_for(lambda s: s.config["code"] != first, 10)
    await pair(client, sim, "Kitchen", None)
    await sim.drop(0.5)
    await sim.wait_for(lambda s: not s.connected, 5)
    await sim.wait_for(lambda s: s.connected and s.state == State.IDLE, 10)
    assert (await orb_view(client, sim.id))["state"] == "active"


async def test_photo_message_and_offline_delivery(client: httpx.AsyncClient, make_sim):
    sim = await make_sim()
    await pair(client, sim, "Hall", None)
    out = io.BytesIO()
    Image.new("RGB", (1200, 900), (0, 120, 0)).save(out, "JPEG", progressive=True)
    media = (await client.post("/api/media", files={"file": ("p.jpg", out.getvalue())})).json()

    # orb goes offline; message is queued; it arrives via retained inbox on reconnect
    await sim.drop(1.5)
    await sim.wait_for(lambda s: not s.connected, 5)
    await asyncio.sleep(0.3)
    assert (await orb_view(client, sim.id))["online"] is False
    r = await client.post(
        "/api/messages", json={"to": [sim.id], "media_id": media["id"], "text": "look"}
    )
    assert r.status_code == 201, r.text
    msg_id = r.json()["id"]
    await sim.wait_for(lambda s: s.state == State.UNREAD, 15)
    assert sim.message["type"] == "photo" and sim.message["media"] == media["url"]
    assert sim.photo and len(sim.photo) > 1000  # fetched and baseline-checked by the sim
    await wait_delivery(client, msg_id, sim.id, "delivered")
    assert (await orb_view(client, sim.id))["online"] is True


async def test_only_latest_message_retained_and_superseded_events_ignored(
    client: httpx.AsyncClient, make_sim
):
    sim = await make_sim()
    await pair(client, sim, "Hall", None)
    await sim.drop(1.5)
    await sim.wait_for(lambda s: not s.connected, 5)
    a = (await client.post("/api/messages", json={"to": [sim.id], "text": "first"})).json()["id"]
    b = (await client.post("/api/messages", json={"to": [sim.id], "text": "second"})).json()["id"]
    await sim.wait_for(lambda s: s.state == State.UNREAD, 15)
    assert sim.message["id"] == b
    await wait_delivery(client, b, sim.id, "delivered")
    assert (await message_view(client, a))["deliveries"][0]["status"] == "queued"
    history = (await client.get("/api/messages?limit=1")).json()
    assert [m["id"] for m in history] == [b]
    assert [m["id"] for m in (await client.get(f"/api/messages?before={b}")).json()] == [a]


async def test_patch_and_forget(client: httpx.AsyncClient, make_sim, broker: Broker):
    sim = await make_sim()
    await pair(client, sim, "Hall", None)
    r = await client.patch(
        f"/api/orbs/{sim.id}",
        json={
            "name": "Hallway",
            "brightness": 40,
            "quiet": {"start": "22:00", "end": "07:00"},
            "owner": "ashleigh",
        },
    )
    assert r.status_code == 200, r.text
    await sim.wait_for(lambda s: s.config.get("quiet") is not None, 5)
    assert sim.config["name"] == "Hallway" and sim.config["brightness"] == 40
    assert sim.config["owner"] == "ashleigh" and sim.config["tz"]
    r = await client.patch(f"/api/orbs/{sim.id}", json={"quiet": {"start": "25:00", "end": "x"}})
    assert r.status_code == 422
    r = await client.patch(f"/api/orbs/{sim.id}", json={"bogus": 1})
    assert r.status_code == 422

    assert (await client.delete(f"/api/orbs/{sim.id}")).status_code == 204
    await sim.wait_for(lambda s: s.state == State.UNCONFIGURED, 5)
    async with HubProbe(broker, client_id="probe-forget") as probe:
        await probe.expect_nothing_on(f"orb/{sim.id}/config", 1.0)  # retained state is gone
        await probe.expect_nothing_on(f"orb/{sim.id}/hello", 0.5)


async def test_hub_restart_reconciles_retained_state(
    broker: Broker, tmp_path, emoji_font, make_sim
):
    port = free_port()
    settings = make_settings(broker, tmp_path / "data3", port, emoji_font)
    hs = await start_hub(settings, port)
    sim = await make_sim()
    async with httpx.AsyncClient(base_url=hs.base_url, timeout=10) as client:
        await client.post("/api/session", json={"user": "lee", "pin": "2468"})
        await pair(client, sim, "Hall", "lee")
        msg_id = (await client.post("/api/messages", json={"to": [sim.id], "text": "hi"})).json()[
            "id"
        ]
        await sim.wait_for(lambda s: s.state == State.UNREAD, 10)
        await wait_delivery(client, msg_id, sim.id, "delivered")
    await hs.stop()

    # wipe what the broker holds for this orb, then restart the hub on the same DB
    async with HubProbe(broker, client_id="probe-wipe") as probe:
        await probe.publish(f"orb/{sim.id}/config", b"", retain=True)
        await probe.publish(f"orb/{sim.id}/inbox", b"", retain=True)
    await sim.wait_for(lambda s: s.state == State.UNCONFIGURED, 5)
    hs = await start_hub(settings, free_port())
    try:
        await sim.wait_for(lambda s: s.state == State.UNREAD and s.message["id"] == msg_id, 10)
        assert sim.config["state"] == "active" and sim.led["mode"] == "unread"
        async with httpx.AsyncClient(base_url=hs.base_url, timeout=10) as client:
            await client.post("/api/session", json={"user": "lee", "pin": "2468"})
            assert (await orb_view(client, sim.id))["state"] == "active"
            # the replayed `received` is a duplicate and must not flip state back
            assert (await message_view(client, msg_id))["deliveries"][0]["status"] == "delivered"
    finally:
        await hs.stop()


async def test_hub_tolerates_bad_payloads(
    client: httpx.AsyncClient, broker: Broker, hub_server: HubServer
):
    async with HubProbe(broker, client_id="probe-bad") as probe:
        await probe.publish("orb/orb-bad1/hello", b"{not json", retain=False)
        await probe.publish("orb/orb-bad1/event", {"type": "read"}, retain=False)
        await probe.publish("orb/orb-bad1/status", "maybe", retain=False)
        await probe.publish("orb/BAD/hello", {"fw": "1.0.0", "w": 1, "h": 1, "caps": []})
        await asyncio.sleep(0.5)
    assert (await client.get("/api/orbs")).json() == []
    assert (await client.get("/readyz")).status_code == 200


@pytest.mark.parametrize("payload", [{"fw": "1.0.0", "w": 466, "h": 466, "caps": ["touch"]}])
async def test_hand_published_hello_creates_pending(
    client: httpx.AsyncClient, broker: Broker, payload
):
    async with HubProbe(broker, client_id="probe-hello") as probe:
        await probe.publish("orb/orb-hand", "online", retain=True)
        await probe.publish("orb/orb-hand/status", "online", retain=True)
        await probe.publish("orb/orb-hand/hello", payload, retain=True)
        cfg = await probe.expect_json("orb/orb-hand/config")
        assert cfg["state"] == "pending" and len(cfg["code"]) == 4
        led = await probe.expect_json("orb/orb-hand/led")
        assert led["mode"] == "pair"
        view = await orb_view(client, "orb-hand")
        assert view["state"] == "pending" and view["online"] is True and view["fw"] == "1.0.0"
        assert (await client.delete("/api/orbs/orb-hand")).status_code == 204
        await probe.clear_retained("orb-hand")
