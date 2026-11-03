"""Household PIN -> signed session cookie (decisions 11)."""

from __future__ import annotations

import secrets

from fastapi import Request
from itsdangerous import BadSignature, TimestampSigner

from .config import Settings, User
from .hub import HubError

COOKIE = "orb_session"
MAX_AGE = 365 * 24 * 3600


class Sessions:
    def __init__(self, settings: Settings):
        self.s = settings
        self.signer = TimestampSigner(settings.session_secret, salt="orb-session")

    def login(self, user_id: str, pin: str) -> User:
        user = self.s.user(user_id)
        ok = secrets.compare_digest(pin.encode(), self.s.hub_pin.encode())
        if user is None or not ok:
            raise HubError(401, "bad_login", "unknown user or wrong PIN")
        return user

    def cookie_value(self, user: User) -> str:
        return self.signer.sign(user.id.encode()).decode()

    def user_from_cookie(self, value: str | None) -> User | None:
        if not value:
            return None
        try:
            user_id = self.signer.unsign(value.encode(), max_age=MAX_AGE).decode()
        except BadSignature:
            return None
        return self.s.user(user_id)

    def require(self, request: Request) -> User:
        user = self.user_from_cookie(request.cookies.get(COOKIE))
        if user is None:
            raise HubError(401, "unauthorized", "sign in with your PIN")
        return user
