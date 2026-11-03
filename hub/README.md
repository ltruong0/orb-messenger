# msg-hub

The Orb Messenger backend: FastAPI + aiomqtt + SQLite (see `docs/hub-api.md`, `docs/architecture.md`).

```
hub/app/
  main.py      app factory, JSON logging, `msg-hub` entry point
  api.py       REST + SSE routes (thin; shapes only)
  hub.py       all the rules: pairing, fan-out, delivery state, receipts, reactions, stickers, phrases
  mqtt.py      one aiomqtt connection as msg-hub, subscribed to orb/#, reconnects, reconcile on connect
  db.py        aiosqlite + schema
  photos.py    EXIF-rotate, centre-crop, 466x466 baseline JPEG
  stickers.py  emoji -> 160x160 RGBA PNG (Noto Color Emoji at 109 px, then resized)
  auth.py      household PIN -> signed cookie
  bus.py       in-process event bus for /api/events
  contract.py  validates every outbound payload against contracts/schemas
hub/tests/     unit tests + integration test (real Mosquitto, uvicorn in-process, two orb-sims)
hub/web/       the PWA: index.html + app.css + app.js (no build step), manifest, sw.js, icons, fonts
```

Run locally: `make hub-run` (needs `.env` with `MQTT_HUB_PASSWORD`, `HUB_PIN`, `SESSION_SECRET`; `make fonts` once for emoji stickers).
Tests: `make hub-test`. Image: `make image`.

## PWA

`hub/web` is served at `/` by the hub (StaticFiles, `html=True`). Sign in with the household
PIN, then: **Send** (orb chips, 80-char text with quick phrases, one sticker, one photo),
**History** (bubbles with delivered/read state, live over SSE), **Orbs** (pending cards with
the 4-digit code, paired list, tap to rename / change owner / brightness / quiet hours /
forget), **Stickers** (emoji or image upload, library, quick phrases). The service worker
only registers over HTTPS; on plain HTTP the page works but can't be installed.
