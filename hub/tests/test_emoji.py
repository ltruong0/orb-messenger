"""Inline emoji PNGs for orb message text (GET /media/emoji/{seq}.png)."""

from __future__ import annotations

import io

import httpx
from PIL import Image


async def test_emoji_png_rendered_on_demand(anon: httpx.AsyncClient):
    r = await anon.get("/media/emoji/1f600.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"].startswith("public, max-age=")
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (40, 40) and img.mode == "RGBA"
    assert img.getchannel("A").getbbox() is not None
    # second request is served from disk and identical
    assert (await anon.get("/media/emoji/1f600.png")).content == r.content


async def test_emoji_sequences_render_as_one_glyph(anon: httpx.AsyncClient):
    for seq in ("1f468-200d-1f469-200d-1f467", "1f44d-1f3fd", "1f1e8-1f1e6", "2764-fe0f"):
        r = await anon.get(f"/media/emoji/{seq}.png")
        assert r.status_code == 200, (seq, r.text)
        assert Image.open(io.BytesIO(r.content)).size == (40, 40)


async def test_bad_sequences_are_404(anon: httpx.AsyncClient):
    for seq in ("zz", "1F600", "1f600-", "../x", "41"):
        r = await anon.get(f"/media/emoji/{seq}.png")
        assert r.status_code == 404, (seq, r.status_code)
