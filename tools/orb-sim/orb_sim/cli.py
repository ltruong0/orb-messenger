"""orb-sim CLI: run one simulated orb and drive it from the keyboard,
or `--web PORT` to run several and drive them from a browser.

read            tap: open the current message
react [sticker] long-press: send a sticker back (default purple-heart)
drop [seconds]  cut the connection without DISCONNECT (LWT fires), reconnect later
status          print state / config / message / led
quit            publish status offline and exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from .contracts import ContractError, Contracts
from .sim import OrbSim, Settings, default_orb_id

EXIT_CONTRACT = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="orb-sim", description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default=env("MQTT_HOST", "mqtt.marcellolab.com"))
    p.add_argument("--port", type=int, default=int(env("MQTT_PORT", "1883")))
    p.add_argument("--user", default=env("MQTT_USER", "orb"))
    p.add_argument(
        "--password",
        default=env("MQTT_ORB_PASSWORD") or env("MQTT_PASSWORD", ""),
        help="default: $MQTT_ORB_PASSWORD or $MQTT_PASSWORD",
    )
    p.add_argument("--id", default=env("ORB_ID") or default_orb_id(), help="orb id = client id")
    p.add_argument("--state-dir", type=Path, default=Path(env("ORB_SIM_STATE", "~/.orb-sim")))
    p.add_argument("--fresh", action="store_true", help="wipe this orb's saved state first")
    p.add_argument(
        "--lenient", action="store_true", help="log contract violations instead of exiting"
    )
    p.add_argument("--no-color", action="store_true")
    p.add_argument(
        "--web",
        type=int,
        metavar="PORT",
        help="serve the browser UI (mock orb screens) on this port instead of the REPL",
    )
    p.add_argument("--bind", default=env("ORB_SIM_BIND", "0.0.0.0"), help="--web bind address")
    p.add_argument(
        "--ids",
        default=env("ORB_SIM_IDS", ""),
        help="--web: comma-separated orb ids (default: --id)",
    )
    return p.parse_args(argv)


def _stdin_lines(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str | None]) -> None:
    for line in sys.stdin:
        loop.call_soon_threadsafe(queue.put_nowait, line.strip())
    loop.call_soon_threadsafe(queue.put_nowait, None)


async def repl(sim: OrbSim) -> int:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    threading.Thread(target=_stdin_lines, args=(loop, queue), daemon=True).start()
    task = sim.start()
    sim.console.say("info", "commands: read | react [sticker] | drop [seconds] | status | quit")
    getter = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                task.result()  # raises ContractError in strict mode
                return 0
            line = getter.result()
            getter = asyncio.ensure_future(queue.get())
            if line is None or line in ("quit", "exit", "q"):
                await sim.stop()
                return 0
            await dispatch(sim, line)
    except ContractError:
        return EXIT_CONTRACT
    finally:
        getter.cancel()


async def dispatch(sim: OrbSim, line: str) -> None:
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd == "":
        return
    if cmd == "read":
        await sim.read()
    elif cmd == "react":
        await sim.react(arg or "purple-heart")
    elif cmd == "drop":
        await sim.drop(float(arg) if arg else 5.0)
    elif cmd == "status":
        sim.console.say("info", f"state={sim.state} connected={sim.connected}")
        sim.console.say("info", f"config={json.dumps(sim.config)}")
        sim.console.say("info", f"message={json.dumps(sim.message)}")
        sim.console.say("info", f"led={json.dumps(sim.led)} last_read={sim.nvs.data.last_read_id}")
    elif cmd in ("help", "?"):
        sim.console.say("info", __doc__.strip())
    else:
        sim.console.say("warn", f"unknown command {cmd!r} (try: help)")


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    if not a.password:
        print("orb-sim: no password (set MQTT_ORB_PASSWORD or --password)", file=sys.stderr)
        return 1
    settings = Settings(
        host=a.host,
        port=a.port,
        user=a.user,
        password=a.password,
        orb_id=a.id,
        state_dir=a.state_dir,
        fresh=a.fresh,
        strict=not a.lenient,
    )
    from .render import Console

    try:
        contracts = Contracts()
        if a.web:
            from .web import Fleet, serve

            ids = [i.strip() for i in a.ids.split(",") if i.strip()] or [a.id]
            fleet = Fleet(settings, ids, contracts)
            return asyncio.run(serve(fleet, a.bind, a.web))
        console = Console(prefix=f"[{a.id}] ", color=False if a.no_color else None)
        sim = OrbSim(settings, contracts, console)
    except FileNotFoundError as e:
        print(f"orb-sim: {e}", file=sys.stderr)
        return 1
    try:
        return asyncio.run(repl(sim))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
