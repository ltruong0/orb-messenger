"""Web UI for simulated orbs: real OrbSim instances against the real broker,
rendered as 466x466 round screens in the browser (design/png/20-orb-screen-states.png).

The browser never speaks MQTT. It gets a JSON snapshot of every orb over SSE
and sends taps / long-presses / drops / reboots back over REST. Stickers and
photos shown on screen are the bytes the orb actually downloaded and verified.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .contracts import Contracts
from .render import Console
from .sim import FW_VERSION, OrbSim, Settings

log = logging.getLogger("orb-sim.web")
WEB_DIR = Path(__file__).resolve().parent / "web"
LOG_LINES = 60


class Fleet:
    """A handful of orbs sharing one event loop and one 'something changed' signal."""

    def __init__(self, base: Settings, ids: list[str], contracts: Contracts | None = None):
        self.base = base
        self.contracts = contracts or Contracts()
        self.sims: dict[str, OrbSim] = {}
        self._changed = asyncio.Event()
        for orb_id in ids:
            console = Console(prefix=f"[{orb_id}] ", quiet=False, on_line=self._touch)
            sim = OrbSim(replace(base, orb_id=orb_id), self.contracts, console)
            sim.listeners.append(lambda _s: self._touch())
            self.sims[orb_id] = sim

    def _touch(self) -> None:
        self._changed.set()

    def start(self) -> None:
        for sim in self.sims.values():
            sim.start()

    async def stop(self) -> None:
        for sim in self.sims.values():
            await sim.stop()

    def get(self, orb_id: str) -> OrbSim:
        sim = self.sims.get(orb_id)
        if sim is None:
            raise KeyError(orb_id)
        return sim

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> dict[str, Any]:
        return {
            "broker": f"{self.base.host}:{self.base.port}",
            "ts": time.time(),
            "orbs": [self._orb_snapshot(sim) for sim in self.sims.values()],
        }

    def _orb_snapshot(self, sim: OrbSim) -> dict[str, Any]:
        msg = dict(sim.message) if sim.message else None
        if msg:
            if msg["type"] == "sticker":
                cached = sim.nvs.sticker_path(msg["sticker"]).exists()
                msg["sticker_url"] = (
                    f"/orbs/{sim.id}/stickers/{msg['sticker']}.png" if cached else None
                )
            elif msg["type"] == "photo":
                msg["photo_url"] = (
                    f"/orbs/{sim.id}/photo.jpg?msg={msg['id']}" if sim.photo else None
                )
        task_done = sim._task is not None and sim._task.done()
        return {
            "id": sim.id,
            "state": str(sim.state),
            "connected": sim.connected,
            "stopped": task_done,
            "config": sim.config,
            "message": msg,
            "led": sim.led,
            "notice": sim.notice,
            "stickers": sorted(sim.nvs.data.stk_index),
            "stickers_rev": sim.nvs.data.stk_rev,
            "nvs": {
                "last_read_id": sim.nvs.data.last_read_id,
                "last_received_id": sim.nvs.data.last_received_id,
            },
            "fw": FW_VERSION,
            "error": (
                {"kind": sim.error.kind, "problems": sim.error.problems} if sim.error else None
            ),
            "log": [
                {"ts": ts, "kind": kind, "text": text}
                for ts, kind, text in list(sim.console.entries)[-LOG_LINES:]
            ],
        }

    async def events(self, heartbeat: float = 15.0) -> AsyncIterator[str]:
        yield f"event: state\ndata: {json.dumps(self.snapshot())}\n\n"
        while True:
            try:
                await asyncio.wait_for(self._changed.wait(), heartbeat)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            # claim the change *before* coalescing: anything that lands during the
            # sleep or while the consumer is busy sets the flag again and is not lost
            self._changed.clear()
            await asyncio.sleep(0.03)  # coalesce bursts (inbox + led + log lines)
            yield f"event: state\ndata: {json.dumps(self.snapshot())}\n\n"


# ------------------------------------------------------------ routes
def build_app(fleet: Fleet) -> Starlette:
    async def index(_: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html")

    async def state(_: Request) -> Response:
        return JSONResponse(fleet.snapshot())

    async def events(_: Request) -> Response:
        return StreamingResponse(
            fleet.events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def _sim(request: Request) -> OrbSim:
        return fleet.get(request.path_params["orb_id"])

    async def _body(request: Request) -> dict[str, Any]:
        if request.headers.get("content-length", "0") == "0":
            return {}
        with contextlib.suppress(ValueError):
            return await request.json()
        return {}

    async def tap(request: Request) -> Response:
        sim = _sim(request)
        before = sim.nvs.data.last_read_id
        action = await sim.tap()
        first = action == "read" and sim.nvs.data.last_read_id != before
        return JSONResponse({"ok": True, "action": action, "first": first})

    async def hold(request: Request) -> Response:
        sim = _sim(request)
        body = await _body(request)
        sent = await sim.react(body.get("sticker") or "purple-heart")
        return JSONResponse({"ok": True, "action": "react", "sent": sent})

    async def drop(request: Request) -> Response:
        sim = _sim(request)
        body = await _body(request)
        await sim.drop(float(body.get("seconds", 5)))
        return JSONResponse({"ok": True, "action": "drop"})

    async def reboot(request: Request) -> Response:
        await _sim(request).reboot()
        return JSONResponse({"ok": True, "action": "reboot"})

    async def sticker(request: Request) -> Response:
        sim = _sim(request)
        path = sim.nvs.sticker_path(request.path_params["key"])
        if not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})

    async def photo(request: Request) -> Response:
        sim = _sim(request)
        if sim.photo is None or not sim.message:
            return Response(status_code=404)
        if request.query_params.get("msg") not in (None, sim.message["id"]):
            return Response(status_code=404)
        return Response(sim.photo, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/state", state),
            Route("/api/events", events),
            Route("/api/orbs/{orb_id}/tap", tap, methods=["POST"]),
            Route("/api/orbs/{orb_id}/hold", hold, methods=["POST"]),
            Route("/api/orbs/{orb_id}/drop", drop, methods=["POST"]),
            Route("/api/orbs/{orb_id}/reboot", reboot, methods=["POST"]),
            Route("/orbs/{orb_id}/stickers/{key}.png", sticker),
            Route("/orbs/{orb_id}/photo.jpg", photo),
            Mount("/fonts", StaticFiles(directory=WEB_DIR / "fonts"), name="fonts"),
        ],
        exception_handlers={KeyError: lambda _r, _e: JSONResponse({"error": "no such orb"}, 404)},
    )
    app.state.fleet = fleet
    return app


async def serve(fleet: Fleet, host: str, port: int) -> int:
    """Run the orbs and the UI until Ctrl-C. Exit 2 if an orb hit a contract violation."""
    fleet.start()
    server = uvicorn.Server(
        uvicorn.Config(build_app(fleet), host=host, port=port, log_level="warning")
    )
    print(f"orb-sim web UI on http://{host}:{port}  (orbs: {', '.join(fleet.sims)})", flush=True)
    try:
        await server.serve()
    finally:
        await fleet.stop()
    return 2 if any(sim.error for sim in fleet.sims.values()) else 0
