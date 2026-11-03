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
