"""Firmware uploads for OTA (decisions 47): stored per model, advertised in config."""

from __future__ import annotations

import hashlib

import httpx

MODEL = "ws-s3-amoled-1.43"
IMAGE = b"\xe9" + bytes(range(256)) * 400  # looks like an ESP32 app image, 100 KB


async def test_upload_list_delete(client: httpx.AsyncClient, hub_server):
    r = await client.post(
        "/api/firmware",
        files={"file": ("firmware.bin", IMAGE, "application/octet-stream")},
        data={"version": "0.2.0", "model": MODEL},
    )
    assert r.status_code == 201, r.text
    fw = r.json()
    assert fw["url"] == f"/media/fw/{MODEL}-0.2.0.bin"
    assert fw["sha256"] == hashlib.sha256(IMAGE).hexdigest() and fw["size"] == len(IMAGE)
    served = await client.get(fw["url"])
    assert served.status_code == 200 and served.content == IMAGE

    assert [f["version"] for f in (await client.get("/api/firmware")).json()] == ["0.2.0"]

    # an active orb of that model gets the fw block in its config; other models don't
    hub = hub_server.hub
    orb = {
        "state": "active",
        "name": "x",
        "owner": None,
        "brightness": 50,
        "quiet": None,
        "model": MODEL,
    }
    cfg = hub.config_payload(orb)
    assert cfg["fw"] == {k: fw[k] for k in ("version", "model", "url", "sha256", "size")}
    hub.contracts.validate("config", cfg)
    assert "fw" not in hub.config_payload({**orb, "model": "other"})

    assert (await client.delete(f"/api/firmware/{MODEL}")).status_code == 204
    assert (await client.get("/api/firmware")).json() == []
    assert "fw" not in hub.config_payload(orb)


async def test_bad_uploads_rejected(client: httpx.AsyncClient):
    files = {"file": ("firmware.bin", IMAGE, "application/octet-stream")}
    r = await client.post("/api/firmware", files=files, data={"version": "nope", "model": MODEL})
    assert r.status_code == 422
    r = await client.post(
        "/api/firmware", files=files, data={"version": "0.2.0", "model": "Bad Model"}
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/firmware",
        files={"file": ("x.bin", b"not an image" * 10000, "application/octet-stream")},
        data={"version": "0.2.0", "model": MODEL},
    )
    assert r.status_code == 422 and r.json()["error"] == "bad_firmware"
    assert (await client.delete("/api/firmware/unknown")).status_code == 404
