# Decisions

Short records. Add new ones at the bottom.

1. **Broker and hub on the homelab k3s cluster, not on an orb.** An orb-hosted broker (the "master orb" option) was considered. It was rejected because one unplugged orb takes down delivery, it has no real ACLs, and the ESP32 would have to serve the web app and store media. k3s gives history, timed messages and backups for free.

2. **MQTT retained topics carry orb state.** `config`, `inbox` and `led` are retained, so a rebooted or reconnecting orb converges with no request/response protocol. Events (`event`, `notice`) are never retained.

3. **Only the latest message per orb is delivered.** No on-device queue. An orb that was offline sees the newest message; history lives on the phone. This keeps the firmware simple.

4. **The hub owns all logic; orbs are thin.** Read receipts, reactions, LED decisions and sticker manifests are computed by the hub. Orbs render state and report taps.

5. **The hub renders emoji to PNG.** That gives the same art on every orb regardless of the sender's phone (Apple vs Google emoji), and no emoji fonts on the ESP32.

6. **Photos are processed server-side into 466×466 baseline JPEGs.** JPEGDEC can't do progressive JPEGs, and the orb shouldn't decode a 12 MP image.

7. **Orbs fetch media over plain HTTP on the LAN.** This avoids TLS and certificate handling on the ESP32 for v1. Media names are unguessable ULIDs. Phones still use HTTPS.

8. **Shared `orb` MQTT login, scoped by `%c` ACLs.** Simple provisioning (one password typed in the portal). Upgrade path: the hub issues per-orb credentials at approval time and rewrites the password file. Revisit if the household grows or the network gets less trusted.

9. **Physical pairing code.** The orb displays a code the hub generated, and approving requires typing it. That proves someone can see the orb, and it stops a stray device that knows the `orb` password from silently joining.

10. **SQLite, one replica.** The load is two people. `Recreate` strategy and a PVC. Postgres only if there's a real need.

11. **Household PIN auth for the PWA.** LAN-only plus Tailscale, two users. No accounts, no OAuth.

12. **Hostnames `orb.marcellolab.com` / `mqtt.marcellolab.com`, not `*.home.lan`.** (2026-11-02) The lab already has Technitium DNS, Traefik with a default mkcert wildcard cert for `*.marcellolab.com`, and k3s ServiceLB on the single node's IP. Reusing them gives HTTPS for the PWA with zero new infrastructure. No Tailscale: the orbs are only ever used at home.

13. **Mosquitto conf and ACL live under `deploy/k8s/base/mosquitto/`.** Kustomize refuses `configMapGenerator` sources outside the kustomization directory, so the files moved from `deploy/mosquitto/` into the base. The base is the only copy; tests bind-mount it into Docker.

14. **Password file generated in pure Python.** `tools/mosquitto_passwd.py` reproduces `mosquitto_passwd`'s `$7$101$salt$hash` (PBKDF2-SHA512) format so neither the laptop nor CI needs Mosquitto installed. Verified against `eclipse-mosquitto:2.0.22`.

15. **Broker tests run against a real Mosquitto in Docker, with the repo's conf.** Not a mock. The same pytest file runs against the cluster with `ORB_BROKER_HOST` set (`make broker-check`), where "restart" becomes `kubectl rollout restart`. Docker Desktop on macOS only shares `/Users`, so the fixture keeps its bind-mount dir under the repo (`.tmp/`, gitignored), not in the OS temp dir.

16. **ACL "cannot read" is asserted as non-delivery.** Mosquitto 2.x grants subscriptions (even `orb/#`) and filters on delivery. Asserting on SUBACK would be wrong; the tests subscribe broadly and assert nothing leaks.

17. **Secrets stay out of git from day one.** `.env`, `deploy/k8s/overlays/*/secrets/` and key files are gitignored; `secretGenerator` reads the gitignored files. The repo is meant to be published later.

18. **`orb-sim` is an async library first, a CLI second.** (2026-11-03) `OrbSim` exposes `start/stop/read/react/drop/wait_for`, so the Phase 3 hub integration test drives real simulated orbs instead of mocking MQTT. The CLI is a thin stdin loop over the same object. It uses aiomqtt (same client family as the hub).

19. **The sim is strict by default.** Any payload that fails its schema, a sticker whose sha256 doesn't match the manifest, or a photo that isn't a baseline JPEG stops the sim with exit code 2. A real orb would misrender silently; the sim's job is to catch hub bugs. `--lenient` logs and reconnects for exploratory use.

20. **Orb id pattern relaxed to `^orb-[a-z0-9]{4,8}$` for the hub.** Real orbs still derive 4 hex chars from the MAC; the suffix just can't be constrained to hex without breaking `orb-sim1`. The ACL doesn't care (`%c`).

21. **Sim persists `last_received_id` as well as `last_read_id`.** The contract says `received` is sent at most once per message id. A rebooted orb gets the retained inbox again; with only `last_read_id` it would re-send `received` for an unread message. Firmware should keep both in NVS too.

22. **Quiet hours and the Wi-Fi portal are not simulated.** Both are local-only behaviour with no MQTT side effects; the sim prints the quiet config and moves on.

23. **The hub republishes every retained topic on each MQTT connect ("reconcile").** (2026-11-03) `config`, `led`, the latest `inbox` per orb and the sticker manifest. Cheap for a household and it makes the broker's persistence a cache rather than the source of truth: a wiped broker or a failed publish converges on the next connect. Orbs must treat a replayed inbox as a duplicate (they do: `last_received_id`).

24. **Pairing codes: new on a fresh `hello`, kept on a retained replay.** MQTT delivers retained messages with the retain flag set, so the hub can tell "the orb just connected" from "I just subscribed". A hub restart therefore doesn't change the code the orb is showing; a reboot of the orb does. An expired code is regenerated when someone tries to use it (409 `code_expired`) so the orb immediately shows a usable one.

25. **Orb events are deduplicated in a table, and events for superseded messages are ignored.** `orb_events(orb, type, msg_id, sticker)` is the idempotency key, which covers QoS 1 redelivery and a sim/orb that replays after a reboot. Only the orb's latest delivery can change state; the contract says older messages are superseded.

26. **A reaction is a sticker message, not a `notice`.** `architecture.md` step 6 wins over the `reaction` notice type: a message shows up in History and survives the home orb being offline, a notice does neither. The `reaction` notice stays in the contract for a future "lightweight" path. The reaction's `from` is the reacting orb's owner, or the orb id itself for a shared orb (the `from` pattern allows it and it's honest).

27. **Read receipts say who, by name.** `notice.by` is the reading orb's owner's display name, or the orb's name for a shared orb, truncated to 24 chars. The receipt goes to the sender's home orb only if it is a different orb.

28. **Hub tests run uvicorn in-process on a free port.** The orb-sims fetch stickers and photos over real HTTP from the same server the API is on, which is what the firmware will do. The sim fixture clears each orb's retained topics on teardown because the broker is shared across tests and a leftover retained `hello` is, correctly, a new pending orb to the next hub.

29. **Emoji font comes from Debian in the image, from `~/.cache` on laptops.** `fonts-noto-color-emoji` in the container; `make fonts` (or the test fixture) downloads the same `NotoColorEmoji.ttf` once for local runs. A hub without the font still starts (empty sticker library, warning in the log) rather than crashing at seed time.

30. **Pending orbs in `to` are skipped, unknown ones are an error.** Sending to an orb that hasn't been approved yet is a UI race, not a bug; sending to an id that doesn't exist is. 422 `no_active_recipients` only when nothing is left.

31. **The browser orb UI is a view on `OrbSim`, not a second orb implementation.** (2026-11-03) `orb-sim --web` runs real `OrbSim` instances (real MQTT clients, LWT, strict schema checks, NVS, sticker cache) and the page only renders their snapshot over SSE and posts taps back. Doing it in the browser with MQTT-over-WebSockets would have meant a websockets listener on Mosquitto, the orb password in the page, and a JavaScript copy of the state machine that could drift from the Python one. The screens follow `design/png/20-orb-screen-states.png`; the stickers and photos shown are the bytes the orb fetched and verified, so a hub bug in media would show up on the mock screen just like on a real one. Quiet hours (NIGHT) are still not simulated.

32. **The PWA is one HTML file, one stylesheet and one script, no framework.** (2026-11-03) Four screens, a login overlay and two bottom sheets is ~350 lines of plain JS. A bundler or framework would add a build step to a repo whose other half is firmware, for no gain at this size. State lives in one object; each screen has a `render*()` that rebuilds its DOM from it.

33. **Live updates refetch, they don't patch.** An SSE event tells the app *that* orbs or messages changed; the app then re-GETs the list. The lists are tiny (two people, a handful of orbs), it keeps the event payloads minimal, and it can't drift from the server.

34. **`GET /api/users` is public.** The login screen needs the picker before a session exists, and the only thing it reveals is two first names on a LAN-only app. Everything else under `/api` still needs the cookie.

35. **Service worker caches the shell and sticker PNGs only.** `/api/*` is network-only (SSE, auth), photos are fetched normally. Stickers are content-addressed by the manifest's sha256 anyway. It registers only in a secure context, which on the LAN means `https://orb.marcellolab.com` with the mkcert root trusted on the phone (deploy.md).

36. **Image tag = hub version, pinned in the overlay.** (2026-11-03) `make image-push` tags with `version` from `hub/pyproject.toml`; `make set-image` writes that into `overlays/homelab/kustomization.yaml`, which is committed. No `:latest`, no mutable tags, and `git log` of the overlay is the deploy history. The GHCR pull secret is created by hand (one PAT, one namespace) rather than generated from a file, so no token ever sits in the working tree.

37. **`users.yaml` ships in the image.** Two names that change once a year don't justify a ConfigMap mount in v1; `USERS_FILE` is there if that changes.

38. **Images are built with BuildKit for `linux/amd64`.** (2026-11-03) The node is amd64, the laptop is an arm64 Mac on Colima. The legacy `docker build` can't cross-build (it chokes on `--platform` after the first `RUN`), so `make image` uses `docker buildx build --platform linux/amd64 --load`, and `uv` is installed with `pip` instead of `COPY --from=ghcr.io/astral-sh/uv` for the same reason. `imagePullPolicy: Always` on the hub so a re-pushed tag beats the node's cache.

39. **Firmware builds with pioarduino, not the stock `espressif32` platform.** (2026-11-03) The stock platform ships Arduino core 2.x; we want core 3.x for the IDF 5 `esp-mqtt` client (QoS 1, LWT, outbox) and current LittleFS/HTTPClient. pioarduino needs Python ≥ 3.10, so PlatformIO is installed with `uv tool install --python 3.12 platformio` instead of the system Python 3.9 copy.

40. **Board variants are a compile-time switch, and the first orb is a 1.43, not a 1.75.** The board that arrived answers I2C on GPIO 47/48 with FT3168 + PCF85063 + QMI8658 and no AXP2101, which is the ESP32-S3-Touch-AMOLED-1.43's map. `-DORB_BOARD=143|175` selects `include/pins.h`; the touch and PMU code is `#if`-ed per board, everything above `board.cpp` is shared, and `hello.model` reports which board an orb is. A boot-time I2C scan is logged on every start because it is what found this.

41. **LVGL decodes photos and stickers itself from LittleFS.** TJpgd + LodePNG + the Arduino LittleFS driver (`L:` drive) replace JPEGDEC/PNGdec. Media are plain files (`/p0.jpg`/`/p1.jpg` alternate so a new photo never overwrites the one being drawn; `/stk/<key>.png`), the decoded image lives in LVGL's 1 MB cache in PSRAM, and there is no second image pipeline to keep in sync with LVGL's.

42. **Build-time secrets seed NVS; the portal stays the fallback.** `make fw-secrets` writes the gitignored `firmware/secrets.ini` from `.env` (broker password, optional Wi-Fi) as `-D` flags; `settings.load()` uses them as defaults when NVS is empty. Without Wi-Fi in `.env` the orb boots into the WiFiManager portal and the broker fields are prefilled, so only the Wi-Fi password has to be typed on a phone.

43. **MQTT and UI never share a task.** esp-mqtt events are copied into a queue and handled on `net_task`, so photo/sticker downloads can't stall keepalives; `net_task` and the LVGL loop talk only through two queues of heap-allocated commands. NVS is written by `net_task` only.

44. **Screen rotation is done in software, per board.** (2026-11-16) The 1.43's panel is mounted a quarter turn off, and the CO5300 only has axis-flip bits in MADCTL, no row/column exchange, so Arduino_GFX can't rotate it. LVGL 9 leaves rotation to the driver anyway, so `flush_cb` rotates each 40-line chunk into a third internal buffer in the same pass that byte-swaps it. `ORB_ROTATION` (degrees clockwise) defaults per board in `pins.h` and can be overridden with `-D`; touch coordinates go through the same mapping. The rounder still holds because 466 is even. The same day the 1.43 turned out to need the CO5300's 6-column GRAM offset too (the port the pins came from used 0): the symptom is a green sliver along one edge, which the rotation moves to the top.

45. **Sleep after 3 h, re-show only recent messages, moods instead of a blink.** (2026-11-20, Lee's asks) The AMOLED burns in, so after three hours without a tap, a message or a receipt the panel goes dark (`SLEEP`); a tap or an unread message wakes it, quiet hours still win. A tap in idle re-opens the last message only if it was read less than ten minutes ago, otherwise a short "no new messages" screen: history lives on the phone. The idle eyes play a weighted set of go-and-return animations (blink, double blink, wink, look, happy, sleepy, surprise, curious, bounce) and the whole face drifts a few pixels now and then. All three are local; the hub knows nothing about them.

46. **Inline emoji are hub-rendered PNGs behind an LVGL image font.** Montserrat has no emoji, and a colour emoji font won't fit the ESP32. The hub already renders Noto Color Emoji for stickers, so it also serves any grapheme as a 40 px PNG at `/media/emoji/{seq}.png` (`seq` = codepoints in hex joined by `-`, U+FE0F dropped), rendered on first request and cached. The orb scans each message for emoji graphemes (ZWJ sequences, skin tones, flags, keycaps), fetches the missing files into `/emo/`, swaps each grapheme for a private-use codepoint and lets an `lv_imgfont` fallback draw the file. No contract field is needed; the URL is derived from the text. `LV_USE_FONT_PLACEHOLDER` is off so anything still unknown draws nothing rather than a box.

47. **OTA through `config.fw`, binaries on the hub.** Orbs shouldn't need the laptop. `make fw-release` uploads the build to `POST /api/firmware` with its version and board model; the hub stores it under `media/fw/` and adds `fw {version, model, url, sha256, size}` to the retained `config` of every active orb of that model. An orb whose running version differs (either direction, so a rollback is just uploading the older build) streams the image into the other app slot, checks the sha256, shows an "updating" screen with progress so nobody wonders why it's unresponsive, and reboots; its next `hello` reports the new version. A failed update is shown for 15 s and not retried until the next boot. The partition table now has two 3 MB app slots and a 9.9 MB LittleFS; changing it needed one last USB flash, and LittleFS was wiped (stickers and emoji re-sync, NVS survives). The LED was dropped from v1 (Lee, 2026-11-20).

48. **Moods are a message type, and the orb draws the faces.** (2026-11-20, Lee's ask) `inbox.type = mood` with `mood` from a fixed enum (happy, sad, confused, cold, hot, scared, surprised, angry, sleepy, love, laughing, cool, sick, excited) and an optional caption. Sending a PNG per mood would have been simpler for the hub, but the faces are two eyes and a mouth built from LVGL primitives with a small looping animation each, which keeps them crisp, themeable and alive on the orb, and the same drawings double as idle "moments": every 30–90 s the plain eyes become a random full face for five seconds. The list lives in three places on purpose (schema enum, `hub/app/moods.py` with the phone emoji, `ui.cpp` with the drawings); the contract test and `GET /api/moods` keep hub and app aligned, and an orb that gets a key it doesn't know draws plain eyes. The SQLite `messages.type` CHECK had to change, which SQLite can't ALTER, so `Database._migrate()` rebuilds the table once on an old file.
