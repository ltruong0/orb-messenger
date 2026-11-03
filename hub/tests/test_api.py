"""REST surface (docs/hub-api.md) against the in-process hub: auth, stickers,
phrases, media, validation. Orb and message flows live in test_integration.py."""

from __future__ import annotations

import asyncio
import io
import json

import httpx
from app.photos import jpeg_is_baseline
from conftest import PIN, HubServer
from orbtest import Broker, HubProbe
from PIL import Image


def png(w: int = 800, h: int = 600) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (30, 60, 200)).save(out, "PNG")
    return out.getvalue()


async def test_ops_endpoints(anon: httpx.AsyncClient):
    assert (await anon.get("/healthz")).json()["status"] == "ok"
    r = await anon.get("/readyz")
    assert r.status_code == 200 and r.json()["mqtt"] is True


async def test_auth_required_and_login(anon: httpx.AsyncClient):
    r = await anon.get("/api/me")
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"
    r = await anon.post("/api/session", json={"user": "lee", "pin": "0000"})
    assert r.status_code == 401 and r.json()["error"] == "bad_login"
    r = await anon.post("/api/session", json={"user": "nobody", "pin": PIN})
    assert r.status_code == 401
    r = await anon.post("/api/session", json={"user": "ashleigh", "pin": PIN})
    assert r.status_code == 200
    assert r.json() == {"id": "ashleigh", "name": "Ashleigh", "home_orb": None}
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    assert (await anon.get("/api/me")).json()["name"] == "Ashleigh"
    assert (await anon.get("/api/users")).json() == [
        {"id": "lee", "name": "Lee"},
        {"id": "ashleigh", "name": "Ashleigh"},
    ]
    await anon.delete("/api/session")
    assert (await anon.get("/api/me")).status_code == 401


async def test_tampered_cookie_is_rejected(anon: httpx.AsyncClient):
    anon.cookies.set("orb_session", "lee.forged.signature")
    assert (await anon.get("/api/me")).status_code == 401


async def test_seed_stickers_and_manifest(
    client: httpx.AsyncClient, hub_server: HubServer, broker: Broker
):
    stickers = (await client.get("/api/stickers")).json()
    assert [s["key"] for s in stickers] == [
        "purple-heart",
        "heart-eyes",
        "coffee",
        "sun",
        "heart-hands",
        "sparkles",
        "cat",
    ]
    # the PNGs are served publicly and match the manifest's sha256
    async with HubProbe(broker, client_id="probe-manifest") as probe:
        manifest = await probe.expect_json("orb/all/stickers")  # retained replay
    assert manifest["rev"] == 1
    assert [i["key"] for i in manifest["items"]] == [s["key"] for s in stickers]
    async with httpx.AsyncClient(base_url=hub_server.base_url) as anon:
        r = await anon.get(manifest["items"][0]["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    import hashlib

    assert hashlib.sha256(r.content).hexdigest() == manifest["items"][0]["sha256"]


async def test_add_and_delete_sticker(client: httpx.AsyncClient, broker: Broker):
    async with HubProbe(broker, client_id="probe-stk") as probe:
        await probe.expect_json("orb/all/stickers")  # retained rev 1
        r = await client.post("/api/stickers", json={"emoji": "🐶", "name": "Dog"})
        assert r.status_code == 201, r.text
        assert r.json() == {"key": "dog", "name": "Dog", "url": "/media/stickers/dog.png"}
        manifest = await probe.expect_json("orb/all/stickers")
        assert manifest["rev"] == 2 and any(i["key"] == "dog" for i in manifest["items"])

        r = await client.post("/api/stickers", json={"emoji": "🐶", "name": "dog"})
        assert r.status_code == 409 and r.json()["error"] == "sticker_exists"
        r = await client.post("/api/stickers", json={"emoji": "ab", "name": "Letters"})
        assert r.status_code == 422 and r.json()["error"] == "bad_sticker"
        r = await client.post("/api/stickers", json={"emoji": "🐶🐶", "name": "Two"})
        assert r.status_code == 422

        # multipart upload
        out = io.BytesIO()
        Image.new("RGBA", (300, 300), (255, 0, 0, 255)).save(out, "PNG")
        r = await client.post(
            "/api/stickers", data={"name": "Red Square"}, files={"file": ("x.png", out.getvalue())}
        )
        assert r.status_code == 201 and r.json()["key"] == "red-square"
        assert (await probe.expect_json("orb/all/stickers"))["rev"] == 3

        assert (await client.delete("/api/stickers/dog")).status_code == 204
        manifest = await probe.expect_json("orb/all/stickers")
        assert manifest["rev"] == 4 and not any(i["key"] == "dog" for i in manifest["items"])
        assert (await client.delete("/api/stickers/dog")).status_code == 404


async def test_phrases(client: httpx.AsyncClient):
    assert "thinking of you" in (await client.get("/api/phrases")).json()
    r = await client.put("/api/phrases", json=[" lunch? ", "love you"])
    assert r.status_code == 200 and r.json() == ["lunch?", "love you"]
    assert (await client.get("/api/phrases")).json() == ["lunch?", "love you"]
    r = await client.put("/api/phrases", json=["x" * 81])
    assert r.status_code == 422
    r = await client.put("/api/phrases", json=["a"] * 13)
    assert r.status_code == 422


async def test_media_upload_pipeline(client: httpx.AsyncClient, hub_server: HubServer):
    r = await client.post("/api/media", files={"file": ("pic.png", png(), "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["url"] == f"/media/photos/{body['id']}.jpg"
    async with httpx.AsyncClient(base_url=hub_server.base_url) as anon:  # orbs fetch without auth
        r = await anon.get(body["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert jpeg_is_baseline(r.content)
    assert Image.open(io.BytesIO(r.content)).size == (466, 466)

    r = await client.post("/api/media", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 422 and r.json()["error"] == "bad_image"


async def test_send_validation(client: httpx.AsyncClient):
    r = await client.post("/api/messages", json={"to": [], "text": "hi"})
    assert r.status_code == 422
    r = await client.post("/api/messages", json={"to": ["orb-nope"], "text": "hi"})
    assert r.status_code == 422 and r.json()["error"] == "unknown_orb"
    r = await client.post("/api/messages", json={"to": ["orb-nope"], "text": "x" * 81})
    assert r.status_code == 422 and r.json()["error"] == "validation"
    r = await client.post("/api/messages", json={"to": ["orb-nope"]})
    assert r.status_code == 422 and r.json()["error"] == "validation"
    r = await client.post("/api/messages", json={"to": ["orb-nope"], "sticker": "nope"})
    assert r.status_code == 422 and r.json()["error"] == "unknown_sticker"
    r = await client.post("/api/messages", json={"to": ["orb-nope"], "media_id": "nope"})
    assert r.status_code == 422 and r.json()["error"] == "unknown_media"
    assert (await client.get("/api/messages")).json() == []
    assert (await client.get("/api/orbs")).json() == []
    assert (await client.post("/api/orbs/orb-nope/ignore")).status_code == 404
    r = await client.post("/api/orbs/orb-nope/approve", json={"code": "1234", "name": "x"})
    assert r.status_code == 404


async def test_sse_stream_heartbeat_and_event(client: httpx.AsyncClient, hub_server: HubServer):
    got: list[str] = []

    async def reader() -> None:
        async with client.stream("GET", "/api/events") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                got.append(line)
                if line.startswith("data:"):
                    return

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.3)
    hub_server.hub.bus.publish("orb.updated", id="orb-test")
    await asyncio.wait_for(task, 5)
    assert got[0] == ": connected"
    assert "event: orb.updated" in got
    assert json.loads(next(x for x in got if x.startswith("data:"))[5:]) == {"id": "orb-test"}
