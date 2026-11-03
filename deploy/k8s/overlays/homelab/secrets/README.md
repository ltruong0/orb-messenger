Gitignored. Generated from `.env`:

- `make mosquitto-passwd` → `passwd` (Mosquitto password file)
- `make hub-secrets` → `hub.env` (HUB_PIN, SESSION_SECRET, MQTT_PASSWORD for the hub)

The GHCR pull secret `ghcr-secret` is not generated here; see docs/deploy.md.
