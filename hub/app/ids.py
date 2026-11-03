"""ULIDs for messages and media (CLAUDE.md "IDs")."""

from __future__ import annotations

import re

from ulid import ULID

ORB_ID_RE = re.compile(r"^orb-[a-z0-9]{4,8}$")
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
USER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
STICKER_KEY_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def new_ulid() -> str:
    return str(ULID())


def is_orb_id(s: str) -> bool:
    return bool(ORB_ID_RE.match(s))
