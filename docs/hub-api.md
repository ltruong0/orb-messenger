# msg-hub API (v1)

FastAPI. JSON in/out. All `/api/*` routes need a session cookie except `POST /api/session` and `GET /api/users`. Error shape: `{"error":"<code>","detail":"..."}`.

## Auth

Two-person household, LAN-only. Keep it simple:

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/session` | `{user, pin}` | `pin` = `HUB_PIN` secret. Sets an HttpOnly, SameSite=Lax signed cookie `orb_session` (`SESSION_SECRET`, itsdangerous timestamp signer), 1-year expiry, `Secure` when the request came over HTTPS. 401 `bad_login`. Returns the same body as `/api/me`. |
| GET | `/api/me` | | `{id, name, home_orb}`; `home_orb` is the first active orb whose `owner` is this user, or `null` |
| GET | `/api/users` | | `[{id, name}]` for the login and owner pickers. The one `/api/*` route without auth. |
| DELETE | `/api/session` | | Log out |

Missing or bad cookie → 401 `unauthorized`.

Users come from config (`users.yaml`): `lee` → "Lee" and `ashleigh` → "Ashleigh". Display names are free text, so nicknames (Floof / Quokka) are a one-line change. Home orbs are assigned at approval time (the `owner` field), not in config. No sign-up.

## Orbs

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/orbs` | | `[{id, name, owner, state: pending\|active, online, last_seen, fw, model, caps, brightness, quiet, unread, code_expires}]`. `unread` = the orb's latest delivery isn't `read`. The pairing code itself is never returned: the orb shows it. |
| POST | `/api/orbs/{id}/approve` | `{code, name, owner?}` | 200 with the orb, or 404, 409 `bad_code` / `code_expired` (a fresh code is published to the orb) / `already_active`, 422 `unknown_user` |
| POST | `/api/orbs/{id}/ignore` | | 204. Drops the pending orb and clears its retained topics. 409 `already_active` for approved orbs. |
| PATCH | `/api/orbs/{id}` | `{name?, owner?, brightness?, quiet?}` | Republishes `config`. `quiet` is `{start, end}` (HH:MM) or `null`. 409 `not_active` for pending orbs. |
| DELETE | `/api/orbs/{id}` | | 204. Forget: delete the row, clear retained topics. History keeps its deliveries. |

Pairing: the first `hello` from an unknown orb creates it as `pending` with a 4-digit code (10-minute expiry). A fresh `hello` (reboot) issues a new code; a retained `hello` replayed when the hub restarts keeps the code the orb is already showing unless it has expired. On every MQTT connect the hub republishes `config`, `led`, the latest `inbox` for every orb and the sticker manifest ("reconcile"), so retained state converges even if the broker lost it.

## Messages

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/api/messages` | `{to:[orb ids], text?, sticker?, media_id?}` | 201 `{id, deliveries:[{orb, status}]}`. Type is `photo` if `media_id`, else `sticker` if `sticker`, else `text`; `text` is the caption for the other two. 422 `validation` (empty, over 80 chars, no `to`), `unknown_orb`, `unknown_sticker`, `unknown_media`, `no_active_recipients` (pending orbs in `to` are skipped). 503 `mqtt_unavailable`. |
| GET | `/api/messages?before=<id>&limit=50` | | Newest first (ULID order): `[{id, from, from_name, type, text, sticker, media_url, reaction_to, ts, deliveries:[{orb, orb_name, status, delivered_at, read_at}]}]`. `limit` ≤ 100. |

Reactions arrive here too: a long-press on an orb becomes a `sticker` message from that orb's owner (or from the orb id itself for a shared orb) to the original sender's home orb, with `reaction_to` set.

Delivery status: `queued` → `delivered` (orb sent `received`) → `read`. Offline orbs stay `queued` and get the message from the retained `inbox` when they reconnect. Only the **latest** message per orb is retained. An orb that was offline for several messages shows only the newest; History on the phone still lists all of them.

## Media

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/api/media` | multipart `file` (≤ 15 MB, JPEG/PNG/HEIC/WebP) | 201 `{id, url}`; 413 `too_large`, 422 `bad_image`. HEIC via `pillow-heif`. |
| GET | `/media/photos/{id}.jpg` | | Public on the LAN (orbs fetch without auth). Unguessable ULID names. |
| GET | `/media/stickers/{key}.png` | | Same |
| GET | `/media/emoji/{seq}.png` | | 40×40 RGBA PNG of one emoji grapheme for orb text, rendered on first request (`seq` = hex codepoints joined by `-`). 404 `unknown_emoji`. Public. |
| GET | `/media/fw/{model}-{version}.bin` | | Firmware images uploaded below. Public. |

Photo pipeline: `ImageOps.exif_transpose` → center-crop square → 466×466 LANCZOS → RGB → JPEG quality 85, `progressive=False`, `optimize=True`, no EXIF.

## Firmware (OTA)

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/firmware` | | `[{model, version, url, sha256, size, uploaded_at}]`, one per board model |
| POST | `/api/firmware` | multipart `file` (ESP32 app image ≤ 3 MB), `version` (semver), `model` (the orb's `hello.model`) | 201 with the entry; republishes `config.fw` to every active orb of that model. 413 `too_large`, 422 `validation` / `bad_firmware` |
| DELETE | `/api/firmware/{model}` | | 204; drops `fw` from those configs (orbs keep what they run). 404 `unknown_firmware` |

`make fw-release` does the POST from the laptop. SSE event `firmware.changed`.

## Stickers and phrases

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/stickers` | | `[{key, name, url, sha256}]` in library order |
| POST | `/api/stickers` | `{emoji, name}` or multipart `{file, name}` | 201 `{key, name, url}`. `key` is the slug of `name` (`^[a-z0-9-]{1,32}$`). Renders a 160×160 RGBA PNG, bumps `rev`, republishes `orb/all/stickers`. 422 `bad_sticker` (not a single grapheme / not an image), 409 `sticker_exists`, 500 `no_emoji_font`. Max 64. |
| DELETE | `/api/stickers/{key}` | | 204, same republish |
| GET / PUT | `/api/phrases` | `["thinking of you", …]` | Quick phrases on the Send screen (max 12) |

Emoji rendering: `ImageFont.truetype("NotoColorEmoji.ttf", 109)` + `draw.text(..., embedded_color=True)` on a transparent canvas, trim, then fit into 160×160. Validate that the input is a single grapheme cluster.

Seed stickers: 💜 purple-heart, 🥰 heart-eyes, ☕ coffee, 🌞 sun, 🫶 heart-hands, ✨ sparkles, 🐱 cat.

## Live updates (SSE)

`GET /api/events` → `text/event-stream`, heartbeat comment every 15 s.

| Event | Data |
|---|---|
| `orb.pending` | `{id, fw}` |
| `orb.status` | `{id, online}` |
| `orb.updated` | `{id}` |
| `message.created` | `{id}` |
| `message.delivered` | `{id, orb}` |
| `message.read` | `{id, orb, at}` |

## Ops

`GET /healthz` (process up), `GET /readyz` (200 when the DB is open and MQTT is connected, else 503). Structured JSON logs to stdout. Every payload the hub publishes is validated against `contracts/schemas` first; a failure is a 500 `contract_violation` and a log line, never a bad message on the wire.

Config via env:

| Var | Default | |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD` | `mosquitto.orb.svc`, 1883, `msg-hub`, required | |
| `HUB_PIN`, `SESSION_SECRET` | required (secret ≥ 16 chars) | |
| `DATA_DIR` | `/data` | `hub.db` + `media/{photos,stickers}` |
| `MEDIA_BASE_URL` | `http://orb.marcellolab.com` | sent to orbs as `config.media_base` |
| `USERS_FILE` | `hub/users.yaml` | |
| `HUB_TZ` | `CST6CDT,M3.2.0,M11.1.0` | POSIX TZ sent as `config.tz` |
| `EMOJI_FONT` | Debian's `NotoColorEmoji.ttf`, then `~/.cache/orb-messenger/fonts/` | without it the sticker library starts empty and emoji stickers fail with `no_emoji_font` |
| `CONTRACTS_DIR` | found by walking up to `contracts/schemas` | |
| `LISTEN_HOST`, `LISTEN_PORT` | `0.0.0.0`, 8080 | |
