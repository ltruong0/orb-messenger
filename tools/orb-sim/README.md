# orb-sim

A simulated orb: everything in `docs/firmware.md` except the pixels. It is both a
CLI for poking at the broker/hub and an async library the hub's integration tests
drive directly.

```
make sim ID=orb-sim1            # broker + password from .env
uv run orb-sim --help
```

Commands at the prompt: `read` (tap), `react [sticker]` (long-press), `drop [seconds]`
(cut the socket without DISCONNECT so the LWT fires, reconnect later), `status`, `quit`.

What it does on its own:

- connects as client id = orb id, user `orb`, LWT `status=offline`, keepalive 30 s
- publishes `status online` + `hello` (retained), subscribes to its five topics
- validates **every** payload in and out against `contracts/schemas`. A violation prints
  the problems and exits with code 2 (`--lenient` logs and reconnects instead)
- state machine: unreachable → unconfigured → pairing → idle / unread / show / receipt,
  with the 60 s show and 30 s receipt timeouts
- `received` once per message id, `read` on first tap, both remembered in
  `~/.orb-sim/{id}/nvs.json` so a restart never re-alerts (`--fresh` wipes it)
- sticker cache under `~/.orb-sim/{id}/stk/`, synced on manifest `rev` change with
  sha256 verification; photos are fetched from `media_base` and must be baseline JPEG
- reconnects with 1 → 30 s backoff

Not simulated: quiet hours (they only affect the LED locally) and the Wi-Fi portal.

Library use:

```python
sim = OrbSim(Settings(host=..., password=..., orb_id="orb-ab12", state_dir=tmp))
sim.start()
await sim.wait_for(lambda s: s.state == State.PAIRING)
await sim.read()
await sim.react("purple-heart")
await sim.stop()
```
