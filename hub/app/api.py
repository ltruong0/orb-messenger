"""REST + SSE routes (docs/hub-api.md). Thin: validation of shapes here, rules in Hub.

No `from __future__ import annotations` here: FastAPI must evaluate the local
`Me` alias at decoration time, and string annotations hide it."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import COOKIE, MAX_AGE, Sessions
from .config import User
from .hub import Hub, HubError
from .photos import MAX_UPLOAD

MAX_FIRMWARE = 3 * 1024 * 1024  # app partition size (firmware/partitions.csv)


class LoginBody(BaseModel):
    user: str
    pin: str


class ApproveBody(BaseModel):
    code: str
    name: str
    owner: str | None = None


class OrbPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    brightness: int | None = None
    quiet: dict[str, str] | None = None
    model_config = {"extra": "forbid"}


class SendBody(BaseModel):
    to: list[str] = Field(default_factory=list)
    text: str | None = None
    sticker: str | None = None
    media_id: str | None = None


class EmojiStickerBody(BaseModel):
    emoji: str
    name: str


def build_router(hub: Hub, sessions: Sessions) -> APIRouter:
    api = APIRouter(prefix="/api")
    Me = Annotated[User, Depends(sessions.require)]

    # ---- session
    @api.post("/session")
    async def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        user = sessions.login(body.user, body.pin)
        response.set_cookie(
            COOKIE,
            sessions.cookie_value(user),
            max_age=MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        return await _me(hub, user)

    @api.delete("/session")
    async def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.get("/me")
    async def me(user: Me) -> dict[str, Any]:
        return await _me(hub, user)

    @api.get("/users")
    async def users() -> list[dict[str, str]]:
        """Public: the login screen needs the user picker before there is a session."""
        return [{"id": u.id, "name": u.name} for u in hub.s.users]

    # ---- orbs
    @api.get("/orbs")
    async def list_orbs(_: Me) -> list[dict[str, Any]]:
        return await hub.list_orbs()

    @api.post("/orbs/{orb_id}/approve")
    async def approve(orb_id: str, body: ApproveBody, _: Me) -> dict[str, Any]:
        return await hub.approve(orb_id, body.code, body.name, body.owner)

    @api.post("/orbs/{orb_id}/ignore", status_code=204, response_class=Response)
    async def ignore(orb_id: str, _: Me) -> Response:
        await hub.ignore(orb_id)
        return Response(status_code=204)

    @api.patch("/orbs/{orb_id}")
    async def patch_orb(orb_id: str, body: OrbPatch, _: Me) -> dict[str, Any]:
        return await hub.update_orb(orb_id, body.model_dump(exclude_unset=True))

    @api.delete("/orbs/{orb_id}", status_code=204, response_class=Response)
    async def forget(orb_id: str, _: Me) -> Response:
        await hub.forget(orb_id)
        return Response(status_code=204)

    # ---- messages
    @api.post("/messages", status_code=201)
    async def send(body: SendBody, user: Me) -> dict[str, Any]:
        return await hub.send(user, body.to, body.text, body.sticker, body.media_id)

    @api.get("/messages")
    async def history(_: Me, before: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return await hub.list_messages(before, limit)

    # ---- media
    @api.post("/media", status_code=201)
    async def upload(_: Me, file: Annotated[UploadFile, File()]) -> dict[str, str]:
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HubError(413, "too_large", "photos must be 15 MB or smaller")
        try:
            return await hub.store_photo(data)
        except ValueError as e:  # BadImage
            raise HubError(422, "bad_image", str(e)) from e

    # ---- firmware (OTA)
    @api.get("/firmware")
    async def firmware(_: Me) -> list[dict[str, Any]]:
        return await hub.list_firmware()

    @api.post("/firmware", status_code=201)
    async def add_firmware(
        _: Me,
        file: Annotated[UploadFile, File()],
        version: Annotated[str, Form()],
        model: Annotated[str, Form()],
    ) -> dict[str, Any]:
        data = await file.read(MAX_FIRMWARE + 1)
        if len(data) > MAX_FIRMWARE:
            raise HubError(413, "too_large", "firmware must fit a 3 MB app partition")
        return await hub.add_firmware(data, version.strip(), model.strip())

    @api.delete("/firmware/{model}", status_code=204, response_class=Response)
    async def delete_firmware(model: str, _: Me) -> None:
        await hub.delete_firmware(model)

    # ---- stickers & phrases
    @api.get("/stickers")
    async def stickers(_: Me) -> list[dict[str, Any]]:
        return await hub.list_stickers()

    @api.post("/stickers", status_code=201)
    async def add_sticker(
        request: Request,
        _: Me,
        file: Annotated[UploadFile | None, File()] = None,
        name: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = EmojiStickerBody.model_validate(await request.json())
            return await hub.add_emoji_sticker(body.emoji, body.name)
        if file is None or name is None:
            raise HubError(422, "validation", "send JSON {emoji,name} or multipart {file,name}")
        return await hub.add_upload_sticker(await file.read(6 * 1024 * 1024), name)

    @api.delete("/stickers/{key}", status_code=204, response_class=Response)
    async def delete_sticker(key: str, _: Me) -> Response:
        await hub.delete_sticker(key)
        return Response(status_code=204)

    @api.get("/phrases")
    async def phrases(_: Me) -> list[str]:
        return await hub.get_phrases()

    @api.put("/phrases")
    async def put_phrases(body: list[str], _: Me) -> list[str]:
        return await hub.set_phrases(body)

    # ---- live updates
    @api.get("/events")
    async def events(_: Me) -> StreamingResponse:
        return StreamingResponse(
            hub.bus.subscribe(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return api


async def _me(hub: Hub, user: User) -> dict[str, Any]:
    home = await hub.home_orb(user.id)
    return {"id": user.id, "name": user.name, "home_orb": home["id"] if home else None}


def error_response(status: int, code: str, detail: Any) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)
