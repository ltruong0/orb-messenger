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
