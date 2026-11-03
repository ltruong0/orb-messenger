# Orb Messenger

Round ESP32-S3 screens for sending each other small messages, stickers and photos between home offices. Mosquitto and the web app run on the homelab k3s cluster.

Built in phases: contract + broker, orb simulator, `msg-hub` backend, phone PWA, k3s deploy, firmware. `CLAUDE.md` holds the conventions and the "things that will bite" list; `docs/decisions.md` says why things are the way they are.

## What's here

| Path | What |
|---|---|
| `CLAUDE.md` | Repo conventions and gotchas |
| `docs/architecture.md` | Components, message lifecycle, pairing, network paths |
| `docs/mqtt-contract.md` | Topics and payloads (normative) |
| `docs/hub-api.md` | REST + SSE for msg-hub |
| `docs/firmware.md` | Orb firmware stack, tasks, state machine |
| `docs/hardware.md` | Board facts (⚠ = verify), LED wiring and modes |
| `docs/deploy.md` | k3s objects, ingress, backups |
| `docs/decisions.md` | Why it's built this way |
| `contracts/` | JSON Schemas, valid and invalid examples, `validate.py` (`make contract-test`) |
| `tests/` | Broker acceptance tests against a real Mosquitto in Docker (`make broker-test`) or the cluster (`make broker-check`) |
| `hub/` | `msg-hub`: FastAPI + aiomqtt + SQLite. `app/` (registry, pairing, fan-out, receipts, reactions, stickers, photos, SSE, PIN sessions), `web/` (the phone PWA: Send, History, Orbs, Stickers), `tests/` (`make hub-test`), `Dockerfile` (`make image`), `users.yaml` |
| `tools/orb-sim/` | Orb simulator: CLI, async library and a browser UI with mock orb screens driven by the real broker (`make sim ID=orb-sim1`, `make sim-ui`, `make sim-test`) |
| `tools/` | `mosquitto_passwd.py` (pure-Python password file generator) |
| `deploy/k8s/` | Kustomize base (namespace, Mosquitto, conf + ACL) and the `homelab` overlay (ServiceLB, `local-path`, gitignored secrets) |
| `design/png/` | Mockups and diagrams as PNG |
| `design/html/` | The same boards as standalone HTML (fonts bundled) |

## Design boards

| File | Board |
|---|---|
| `01-architecture` | System architecture |
| `02-registration-pairing` | Self-registration and pairing sequence |
| `03-mqtt-topics-acl` | Topic table, inbox payload, Mosquitto ACL |
| `10-app-send` … `13-app-stickers` | Phone app: Send, History, Orbs, Stickers |
| `20-orb-screen-states` | Eight orb screen states with LED colors |
