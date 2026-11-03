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
