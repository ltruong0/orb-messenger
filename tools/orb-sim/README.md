# orb-sim

A simulated orb: everything in `docs/firmware.md` except the pixels. It is both a
CLI for poking at the broker/hub and an async library the hub's integration tests
drive directly.

```
make sim ID=orb-sim1            # one orb at the terminal (broker + password from .env)
make sim-ui                     # two orbs with mock screens in the browser: http://localhost:8090
uv run orb-sim --help
```

## Browser UI (`--web`)

`orb-sim --web 8090 --ids orb-sim1,orb-sim2` runs several orbs in one process and
serves a page that draws each one as a 466 px round screen, following
`design/png/20-orb-screen-states.png`: pairing code, idle eyes, "New from …",
text auto-sized, sticker + caption, full-bleed photo, read receipt, broker
unreachable. The enclosure ring glows with the LED (`led` payload colour and
pattern; amber while unreachable). Tap the screen (or **Tap**) to open, hold it
≥ 600 ms (or **Hold** + sticker picker) to react, **Drop 5 s** cuts the socket so
the LWT fires, **Reboot** clears RAM and rebuilds from the retained topics. The
log under each orb is the same output the terminal mode prints.

The browser never talks MQTT: the page gets a snapshot of every orb over SSE
(`/api/events`) and posts actions to `/api/orbs/{id}/tap|hold|drop|reboot`. The
stickers and photos it shows are served from the orb's own cache
(`/orbs/{id}/stickers/{key}.png`, `/orbs/{id}/photo.jpg`), i.e. the bytes the
orb downloaded and verified, not the hub's originals. Binds to `0.0.0.0` so you
can open it from a phone on the LAN (`--bind 127.0.0.1` to keep it local).

Commands at the terminal prompt: `read` (tap), `react [sticker]` (long-press), `drop [seconds]`
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
