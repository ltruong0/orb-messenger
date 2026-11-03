# Deploying on k3s

Namespace `orb`. Everything single-replica. Assumptions to confirm in Phase 0 are marked ❓.

## Mosquitto

| Object | Spec |
|---|---|
| Deployment | `eclipse-mosquitto:2` (pin a digest), 1 replica, `strategy: Recreate`, non-root (uid 1883) |
| ConfigMap `mosquitto-config` | `mosquitto.conf` and `aclfile` from `deploy/mosquitto/` |
| Secret `mosquitto-passwd` | `passwd` generated with `mosquitto_passwd -c -b passwd msg-hub <pw>` and `mosquitto_passwd -b passwd orb <pw>` |
| PVC `mosquitto-data` | 1 Gi, ❓ StorageClass (`local-path`?) |
| Service `mqtt` | `type: LoadBalancer` on 1883. ❓ k3s ServiceLB vs MetalLB; give it a stable IP and a DNS name `mqtt.home.lan` |
| Probes | TCP 1883 |

Mosquitto 2.x can refuse or warn about password files it doesn't own or that are too permissive. If the logs complain, copy the Secret into an `emptyDir` with an initContainer and `chmod 0700`/`chown 1883` it there.

## msg-hub

| Object | Spec |
|---|---|
| Deployment | 1 replica, `strategy: Recreate` (SQLite is single-writer) |
| Image | Python 3.12-slim + `fonts-noto-color-emoji` (or ship `NotoColorEmoji.ttf`), ❓ registry |
| PVC `hub-data` | 5 Gi at `/data` (DB + media) |
| Secret `hub` | `HUB_PIN`, `SESSION_SECRET`, `MQTT_PASSWORD` |
| ConfigMap `hub` | `MQTT_HOST=mosquitto.orb.svc`, `MEDIA_BASE_URL=http://orb.home.lan`, `users.yaml` |
| Service `hub` | ClusterIP 8080 |
| Probes | `/healthz` liveness, `/readyz` readiness |
| Resources | requests 100m / 128Mi, limits 500m / 512Mi (Pillow spikes on big photos) |

## Ingress

- `https://orb.home.lan/` → `hub:8080` for phones. ❓ TLS source: cert-manager DNS-01 on a real domain, Tailscale certs, or a local CA.
- `http://orb.home.lan/media/*` → `hub:8080` **without** the HTTPS redirect, so orbs can fetch over plain HTTP. With Traefik: a second IngressRoute on the `web` entrypoint matching `PathPrefix(/media)`.
- `/api/events` (SSE): disable response buffering. Traefik passes SSE through, but check that no middleware compresses it.

## Layout

```
deploy/k8s/
  base/            namespace, mosquitto, hub, services, pvcs, kustomization.yaml
  overlays/homelab/ ingress, LB IP, storageClass, image tag, secretGenerator (from gitignored files)
```

## Backups

Snapshot the two PVCs. `hub.db` can be copied hot with `sqlite3 hub.db ".backup /data/backup.db"`. Optional CronJob.
