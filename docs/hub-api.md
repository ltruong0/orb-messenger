# msg-hub API (v1 draft)

FastAPI. JSON in/out. All `/api/*` routes need a session cookie except `POST /api/session`. Error shape: `{"error":"<code>","detail":"..."}`.

## Auth

Two-person household, LAN-only. Keep it simple:

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/session` | `{user, pin}` | `pin` = `HUB_PIN` secret. Sets an HttpOnly, SameSite=Lax signed cookie (`SESSION_SECRET`), 1-year expiry. |
| GET | `/api/me` | | `{id, name, home_orb}` |
| DELETE | `/api/session` | | Log out |

Users come from config (env or a small YAML), e.g. `lee` → "Lee", home orb `orb-1a2b`. No sign-up.

## Orbs

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/orbs` | | `[{id, name, owner, state: pending\|active, online, last_seen, fw, unread}]` |
| POST | `/api/orbs/{id}/approve` | `{code, name, owner?}` | 200, or 409 `bad_code` / `code_expired` |
| POST | `/api/orbs/{id}/ignore` | | Drops the pending orb and clears its retained topics |
| PATCH | `/api/orbs/{id}` | `{name?, owner?, brightness?, quiet?}` | Republishes `config` |
| DELETE | `/api/orbs/{id}` | | Forget (clear retained topics) |

## Messages

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/api/messages` | `{to:[orb ids], text?, sticker?, media_id?}` | 201 `{id, deliveries:[{orb, status}]}`. 422 if empty, over 80 chars, or no active recipients. |
| GET | `/api/messages?before=<id>&limit=50` | | Newest first, with per-orb delivery status and timestamps |

Delivery status: `queued` → `delivered` (orb sent `received`) → `read`. Offline orbs stay `queued` and get the message from the retained `inbox` when they reconnect. Only the **latest** message per orb is retained. An orb that was offline for several messages shows only the newest; History on the phone still lists all of them.

## Media

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/api/media` | multipart `file` (≤ 15 MB, JPEG/PNG/HEIC/WebP) | `{id, url}` |
| GET | `/media/photos/{id}.jpg` | | Public on the LAN (orbs fetch without auth). Unguessable ULID names. |
| GET | `/media/stickers/{key}.png` | | Same |

Photo pipeline: `ImageOps.exif_transpose` → center-crop square → 466×466 LANCZOS → RGB → JPEG quality 85, `progressive=False`, `optimize=True`, no EXIF.

## Stickers and phrases

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/stickers` | | `[{key, name, url}]` |
| POST | `/api/stickers` | `{emoji, name}` or multipart `{file, name}` | Renders a 160×160 RGBA PNG, bumps `rev`, republishes `orb/all/stickers` |
| DELETE | `/api/stickers/{key}` | | Same republish |
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

`GET /healthz` (process up), `GET /readyz` (DB open and MQTT connected). Structured JSON logs to stdout. Config via env: `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`, `HUB_PIN`, `SESSION_SECRET`, `DATA_DIR`, `MEDIA_BASE_URL`, `USERS_FILE`.
