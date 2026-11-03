#!/usr/bin/env python3
"""Generate a Mosquitto 2.x password file without the mosquitto_passwd binary.

Mosquitto 2.x stores `user:$7$101$<salt b64>$<pbkdf2-sha512 b64>` (hash type 7,
101 iterations, 12-byte salt, 64-byte key). This reproduces that format so
tests and deploys don't need mosquitto installed on the laptop.

Usage: mosquitto_passwd.py OUT_FILE user1=pw1 [user2=pw2 ...]
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

ITERATIONS = 101
SALT_LEN = 12
KEY_LEN = 64


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(SALT_LEN)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, ITERATIONS, dklen=KEY_LEN)
    return f"$7${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def render(users: dict[str, str]) -> str:
    return "".join(f"{user}:{hash_password(pw)}\n" for user, pw in users.items())


def write_passwd(path: Path, users: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(users))
    path.chmod(0o600)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or any("=" not in a for a in argv[1:]):
        print(__doc__, file=sys.stderr)
        return 2
    users = dict(a.split("=", 1) for a in argv[1:])
    write_passwd(Path(argv[0]), users)
    print(f"wrote {argv[0]} ({', '.join(users)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
