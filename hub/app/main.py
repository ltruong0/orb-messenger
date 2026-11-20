"""FastAPI app factory + `msg-hub` entry point."""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import build_router, error_response
from .auth import Sessions
from .bus import EventBus
from .config import Settings
from .contract import ContractError
from .hub import Hub, HubError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class CachedStaticFiles(StaticFiles):
    """StaticFiles with an explicit Cache-Control.

    Starlette sends ETag/Last-Modified but no Cache-Control, so browsers apply
    heuristic freshness and can keep a stale app.js for hours. The shell is
    `no-cache` (always revalidated, cheap thanks to the ETag); media files are
    immutable by name (ULIDs, sha-addressed stickers) so they cache for a week.
    """

    def __init__(self, *args, cache_control: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cache_control = cache_control

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = self.cache_control
        return response


def create_app(settings: Settings, hub: Hub | None = None) -> FastAPI:
    hub = hub or Hub(settings, EventBus())
    sessions = Sessions(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(
        title="msg-hub",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.hub = hub
    app.state.settings = settings

    @app.exception_handler(HubError)
    async def _hub_error(_: Request, e: HubError) -> JSONResponse:
        return error_response(e.status, e.code, e.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError) -> JSONResponse:
        return error_response(422, "validation", json.loads(json.dumps(e.errors(), default=str)))

    @app.exception_handler(ContractError)
    async def _contract(_: Request, e: ContractError) -> JSONResponse:
        logging.getLogger("hub").error("contract violation: %s", e)
        return error_response(500, "contract_violation", e.problems)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ok = hub.ready
        return JSONResponse(
            {
                "status": "ok" if ok else "not ready",
                "db": hub.db.is_open,
                "mqtt": hub.mqtt.connected.is_set(),
            },
            status_code=200 if ok else 503,
        )

    app.include_router(build_router(hub, sessions))

    @app.get("/media/emoji/{seq}.png")
    async def emoji(seq: str) -> FileResponse:
        """Inline emoji for orb text, rendered on first request. Public like the rest of /media."""
        return FileResponse(
            await hub.emoji_png(seq),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    settings.media_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/media",
        CachedStaticFiles(directory=settings.media_dir, cache_control="public, max-age=604800"),
        name="media",
    )
    if WEB_DIR.is_dir():
        web = CachedStaticFiles(directory=WEB_DIR, html=True, cache_control="no-cache")
        app.mount("/", web, name="web")
    return app


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
