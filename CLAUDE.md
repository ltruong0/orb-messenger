# CLAUDE.md: Orb Messenger

Small round ESP32-S3 "orbs" that show messages, stickers and photos sent from phones. Backend runs on a homelab k3s cluster. See `docs/` for the design and `docs/decisions.md` for the phase history.

## Source of truth

- **Contract:** `docs/mqtt-contract.md` and `contracts/schemas/`. Code follows the contract, never the other way round. Contract changes go in the same commit as the code that needs them, with examples updated.
- **Look and feel:** `design/png/`. Phone screens are 390×844; orb screens are 466×466 round with a 330 px safe square for text.
- **Decisions:** `docs/decisions.md`. Add an entry when you make a non-obvious choice.

## Repo layout (target)

```
contracts/      schemas/*.schema.json, examples/*.json, validate.py
hub/            msg-hub: app/ (FastAPI), web/ (PWA static), tests/, Dockerfile, pyproject.toml
tools/orb-sim/  MQTT orb simulator (Python)
firmware/       PlatformIO project for the Waveshare ESP32-S3-Touch-AMOLED-1.75
deploy/         k8s/base (incl. mosquitto/ conf + acl), k8s/overlays/homelab (+ gitignored secrets/)
design/         png/ and html/ exports of the mockups (read-only reference)
docs/           architecture, contract, API, firmware, hardware, deploy, decisions
```

## Commands (create these as you go and keep this list current)

```
make contract-test   # validate contracts/examples against schemas
make broker-test     # Mosquitto ACL/LWT/persistence tests against Docker (uses deploy/k8s/base/mosquitto/*)
make broker-check    # same tests against the deployed broker (restarts the pod once)
make sim-test        # orb-sim integration tests against Mosquitto in Docker
make fonts           # fetch NotoColorEmoji.ttf into ~/.cache for local sticker rendering (tests do it too)
make mosquitto-passwd # write the gitignored passwd file for the homelab overlay from .env
make lint / make fmt # ruff
make hub-test        # msg-hub unit + integration tests (uvicorn in-process, Mosquitto in Docker, two orb-sims)
make hub-run         # run msg-hub locally against MQTT_HOST from .env (state in hub/data)
make sim ID=orb-sim1 # run one orb-sim at the terminal
make sim-ui          # orb-sim --web: two orbs with mock 466 px screens at http://localhost:8090 (IDS=, PORT=)
make image           # docker build hub/Dockerfile from the repo root (TAG=dev)
make hub-secrets     # write the gitignored hub.env (HUB_PIN, SESSION_SECRET, MQTT_PASSWORD) from .env
make image-push      # build + push ghcr.io/ltruong0/orb-messenger/msg-hub:<hub version>
make set-image       # pin that tag in the homelab overlay
make deploy          # kubectl apply -k deploy/k8s/overlays/homelab (mosquitto + hub + ingress)
make deploy-status   # rollout status + pods/svc/ingress/pvc in ns orb
make fw-secrets      # firmware/secrets.ini (gitignored) from .env: broker password, optional WIFI_SSID/WIFI_PASS, ORB_LED_PIN
make fw-build        # compile the orb firmware (pioarduino; pio from `uv tool install --python 3.12 platformio`)
make fw-flash        # build + flash over USB (FW_PORT=/dev/cu.usbmodemXXX)
make fw-monitor      # serial log at 115200 (needs a real tty; from scripts use pyserial directly)
```

## Conventions

- Python 3.12 via `uv` (root `pyproject.toml` is the dev workspace; run things with `uv run`). `ruff` + `ruff format`, type hints, `pytest`. Async throughout the hub (FastAPI + aiomqtt + aiosqlite).
- Hostnames: `orb.marcellolab.com` (hub, HTTPS via Traefik's default mkcert wildcard) and `mqtt.marcellolab.com` (broker, ServiceLB on 192.168.40.129). Users: `lee` and `ashleigh`.
- IDs: messages use ULIDs. Orb ids are `orb-` + the last 2 bytes of the Wi-Fi MAC as 4 lowercase hex chars (e.g. `orb-3f2a`). That id is also the MQTT client id. Simulators use the same shape with any `[a-z0-9]{4,8}` suffix (`orb-sim1`); the hub accepts `^orb-[a-z0-9]{4,8}$`.
- Times: Unix seconds (UTC) on the wire. Quiet hours are local time, with the orb's POSIX TZ string sent in `config`.
- All MQTT payloads are compact JSON (UTF-8) except `orb/{id}/status`, which is the bare string `online` / `offline`.
- Retained topics are state; non-retained topics are events. Never retain an event.
- To forget an orb, publish zero-length retained payloads to all its retained topics.
- Secrets never go in git. Use k8s Secrets and a gitignored `.env` for local runs.
- Firmware: no `delay()` in the main loop, LVGL on one task, network on another, a queue between them. Big buffers in PSRAM. `-DORB_BOARD=143|175` picks the pin map; the orb in hand is the 1.43.

## Things that will bite

- **JPEGDEC on the ESP32 can't decode progressive JPEGs.** The hub must emit baseline JPEGs (`progressive=False`).
- **Pillow only renders Noto Color Emoji at size 109.** Render at 109, then resize to 160 px.
- **iOS may hand over HEIC.** Accept it (`pillow-heif`) or reject it with a clear error.
- **Service workers need HTTPS.** Plain-HTTP LAN access works for the page but not for "install".
- **SQLite means one hub replica**, with `Recreate` deployment strategy and the DB on a PVC.
- **Mosquitto 2.x refuses anonymous clients by default and is picky about password-file ownership/permissions.** The deployment's init container copies conf/acl/passwd into an emptyDir owned by uid 1883, mode 0600. Check the pod logs after changing it.
- **Mosquitto grants any SUBSCRIBE and enforces `read` ACLs at delivery.** Test "cannot read" as "never receives".
- **Docker Desktop on macOS doesn't share `/var/folders`.** Bind mounts from pytest's tmp dir show up empty; the broker fixture uses `.tmp/` under the repo.
- **aiomqtt has no unclean-disconnect API.** To make the broker fire an LWT from a test, `shutdown()` paho's socket; `close()` leaves aiomqtt's reader dangling and hangs.
- **`%c` in ACL patterns is the client id.** Orbs must connect with client id == orb id, or they can't touch their own topics.
- **No `from __future__ import annotations` in `hub/app/api.py`.** FastAPI resolves annotations at decoration time; a locally defined `Annotated[...]` alias becomes an unresolvable string and silently turns into a query parameter (422 instead of 401).
- **Retained `hello` from a dead orb is a pending orb.** Any test that leaves retained topics behind on the shared broker will see it registered by the next hub. The hub test fixtures clear them; do the same in new fixtures.
- **The hub tells a fresh `hello` from a replayed one by the MQTT retain flag.** Don't "fix" that by regenerating codes on every hello: a hub restart would change the code every orb is showing.
- **httpx's `ASGITransport` buffers whole responses.** SSE endpoints can't be tested through it; run uvicorn in-process (hub tests) or drive the generator directly (sim web tests).
- **The k3s node is amd64; this Mac is arm64.** `make image` passes `--platform linux/amd64`. An image built without it pulls fine and then dies with `exec format error`.
- **CSS `display` on a class beats the `hidden` attribute.** The PWA stylesheet has `[hidden]{display:none !important}`; keep it.
- **Stickers: `thumbnail()` never upscales.** The 109 px emoji glyph must be resized up to 160; use `resize`, not `thumbnail`.
- **The orb that arrived is the AMOLED-1.43, not the 1.75.** I2C is on GPIO 47/48, there is no PMU, and the panel + touch rail is off until GPIO 42 is driven high. The boot log's `i2c devices on sda=.. scl=..` line is the first thing to read on any new unit.
- **Arduino core 3.3's I2C driver reports a NACK as `ESP_ERR_INVALID_STATE` (259).** "i2c_master_transmit failed: [259]" means nobody answered at that address, not a driver bug.
- **pioarduino refuses Python 3.9.** Use the `pio` from `uv tool install --python 3.12 platformio` (`~/.local/bin/pio`), not the old system-Python one.
- **LittleFS looks for a partition labelled `spiffs`.** A custom `partitions.csv` with any other label fails with "partition spiffs could not be found".
- **`pio device monitor` needs a tty.** From a script, read the port with pyserial; toggling RTS resets the board (USB-JTAG/serial) so you get the boot log.
- **A green sliver along one screen edge is the CO5300 column offset.** Both boards need `LCD_COL_OFFSET 6`; the unwritten GRAM columns show up as green on the glass.
- **The CO5300 can't rotate.** Its MADCTL has only flip bits, so `setRotation` is useless and LVGL 9 doesn't rotate for you either; `flush_cb` does it (`ORB_ROTATION`, 90 on the 1.43).
- **Don't name a pyserial helper `serial.py`.** Python imports the script itself instead of pyserial.
- **`lv_image_cache_drop` isn't in `lvgl.h`.** Include `src/misc/cache/instance/lv_image_cache.h`. `lv_obj_remove_flag` wants a cast when OR-ing flags in C++.
