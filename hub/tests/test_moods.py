"""Mood messages (decisions 48): a new inbox type the orb draws itself."""

from __future__ import annotations

import httpx


async def test_moods_listed(client: httpx.AsyncClient):
    moods = (await client.get("/api/moods")).json()
    keys = [m["key"] for m in moods]
    assert "cold" in keys and "happy" in keys and len(keys) >= 12
    assert all(m["emoji"] and m["label"] for m in moods)


async def test_unknown_mood_rejected(client: httpx.AsyncClient):
    r = await client.post("/api/messages", json={"to": ["orb-sim1"], "mood": "grumpy"})
    assert r.status_code == 422 and r.json()["error"] in ("unknown_mood", "unknown_orb")
