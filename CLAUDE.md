# CLAUDE.md: Orb Messenger

Small round ESP32-S3 "orbs" that show messages, stickers and photos sent from phones. Backend runs on a homelab k3s cluster. See `PROMPT.md` for the build plan and `docs/` for the design.

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
make mosquitto-passwd # write the gitignored passwd file for the homelab overlay from .env
make lint / make fmt # ruff
make hub-test        # pytest for msg-hub (unit + integration with a real mosquitto)
make hub-run         # run msg-hub locally against MQTT_HOST
make sim ID=orb-sim1 # run orb-sim
make image           # build the msg-hub container image
make deploy          # kubectl apply -k deploy/k8s/overlays/homelab
pio run -d firmware -t upload   # flash an orb
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
- Firmware: no `delay()` in the main loop, LVGL on one task, network on another, a queue between them. Big buffers in PSRAM.

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
