"""SQLite via aiosqlite. One writer (one replica), WAL, schema created on open."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS orbs (
  id            TEXT PRIMARY KEY,
  state         TEXT NOT NULL CHECK (state IN ('pending','active')),
  name          TEXT,
  owner         TEXT,
  code          TEXT,
  code_expires  INTEGER,
  online        INTEGER NOT NULL DEFAULT 0,
  last_seen     INTEGER,
  fw            TEXT,
  model         TEXT,
  caps          TEXT NOT NULL DEFAULT '[]',
  brightness    INTEGER NOT NULL DEFAULT 70,
  quiet         TEXT,
  created_at    INTEGER NOT NULL,
  approved_at   INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
  id            TEXT PRIMARY KEY,
  sender        TEXT NOT NULL,
  sender_name   TEXT NOT NULL,
  type          TEXT NOT NULL CHECK (type IN ('text','sticker','photo','mood')),
  text          TEXT,
  sticker       TEXT,
  mood          TEXT,
  media_id      TEXT,
  reaction_to   TEXT,
  auto_open     INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
  msg_id        TEXT NOT NULL REFERENCES messages(id),
  orb_id        TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('queued','delivered','read')),
  delivered_at  INTEGER,
  read_at       INTEGER,
  PRIMARY KEY (msg_id, orb_id)
);
CREATE INDEX IF NOT EXISTS deliveries_orb ON deliveries(orb_id, msg_id DESC);
CREATE TABLE IF NOT EXISTS media (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stickers (
  key           TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  sort          INTEGER NOT NULL,
  created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orb_events (
  orb_id        TEXT NOT NULL,
  type          TEXT NOT NULL,
  msg_id        TEXT NOT NULL,
  sticker       TEXT NOT NULL DEFAULT '',
  at            INTEGER NOT NULL,
  PRIMARY KEY (orb_id, type, msg_id, sticker)
);
CREATE TABLE IF NOT EXISTS meta (
  key           TEXT PRIMARY KEY,
  value         TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None)  # autocommit
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.executescript(SCHEMA)
        self._conn = conn
        await self._migrate()

    async def _migrate(self) -> None:
        """Bring a database from before 'mood' messages up to date (SQLite can't ALTER a CHECK)."""
        cols = [r["name"] for r in await self.fetchall("PRAGMA table_info(messages)")]
        if "mood" in cols:
            if "auto_open" not in cols:
                await self.execute(
                    "ALTER TABLE messages ADD COLUMN auto_open INTEGER NOT NULL DEFAULT 0"
                )
            return
        await self.execute("PRAGMA foreign_keys=OFF")
        await self.conn.executescript(
            "BEGIN;"
            "CREATE TABLE messages_v2 ("
            "  id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_name TEXT NOT NULL,"
            "  type TEXT NOT NULL CHECK (type IN ('text','sticker','photo','mood')),"
            "  text TEXT, sticker TEXT, mood TEXT, media_id TEXT, reaction_to TEXT,"
            "  auto_open INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);"
            "INSERT INTO messages_v2"
            "  (id,sender,sender_name,type,text,sticker,media_id,reaction_to,created_at)"
            "  SELECT id,sender,sender_name,type,text,sticker,media_id,reaction_to,created_at"
            "  FROM messages;"
            "DROP TABLE messages;"
            "ALTER TABLE messages_v2 RENAME TO messages;"
            "COMMIT;"
        )
        await self.execute("PRAGMA foreign_keys=ON")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "database not open"
        return self._conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self.conn.execute(sql, tuple(params))
        return cur.rowcount

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cur = await self.conn.execute(sql, tuple(params))
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cur = await self.conn.execute(sql, tuple(params))
        return list(await cur.fetchall())

    # meta (small key/value: sticker manifest rev, quick phrases)
    async def get_meta(self, key: str, default: Any = None) -> Any:
        row = await self.fetchone("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    async def set_meta(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO meta(key,value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
