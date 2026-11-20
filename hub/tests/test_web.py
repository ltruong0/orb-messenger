"""The PWA shell is served by the hub itself (hub/web)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

WEB = Path(__file__).resolve().parents[1] / "web"


async def test_shell_manifest_and_worker_are_served(anon: httpx.AsyncClient):
    r = await anon.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in r.text
    for path, ctype in (
        ("/app.js", "text/javascript"),
        ("/app.css", "text/css"),
        ("/sw.js", "text/javascript"),
        ("/manifest.webmanifest", "application/manifest+json"),
        ("/icons/icon-192.png", "image/png"),
        ("/fonts/fredoka-latin-wght-normal.woff2", "font/woff2"),
    ):
        r = await anon.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith(ctype), (path, r.headers["content-type"])


async def test_shell_is_revalidated_not_heuristically_cached(anon: httpx.AsyncClient):
    for path in ("/", "/app.js", "/sw.js"):
        r = await anon.get(path)
        assert r.headers["cache-control"] == "no-cache", path
        assert "etag" in r.headers, path
    r = await anon.get("/media/stickers/purple-heart.png")
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("public, max-age="), r.headers


async def test_manifest_icons_exist(anon: httpx.AsyncClient):
    manifest = json.loads((WEB / "manifest.webmanifest").read_text())
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert (await anon.get(icon["src"])).status_code == 200, icon


async def test_worker_precache_list_matches_served_files(anon: httpx.AsyncClient):
    sw = (WEB / "sw.js").read_text()
    shell = re.search(r"const SHELL = \[(.*?)\];", sw, re.S).group(1)
    for path in re.findall(r"'([^']+)'", shell):
        assert (await anon.get(path)).status_code == 200, path


async def test_users_is_public_for_the_login_screen(anon: httpx.AsyncClient):
    r = await anon.get("/api/users")
    assert r.status_code == 200 and [u["id"] for u in r.json()] == ["lee", "ashleigh"]
    assert (await anon.get("/api/orbs")).status_code == 401
