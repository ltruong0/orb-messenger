"""msg-hub test fixtures: real Mosquitto (tests/orbtest), the hub served by
uvicorn in-process on a free port (orbs fetch /media over real HTTP), and
orb-sim instances as the orbs."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from app.config import Settings, load_users
from app.hub import Hub
from app.main import create_app
from orb_sim import Contracts, OrbSim
from orb_sim import Settings as SimSettings
from orb_sim.render import Console
from orbtest import Broker, HubProbe, free_port, start_broker

REPO = Path(__file__).resolve().parents[2]
FONT_CACHE = Path("~/.cache/orb-messenger/fonts/NotoColorEmoji.ttf").expanduser()
FONT_URL = "https://github.com/googlefonts/noto-emoji/raw/v2.047/fonts/NotoColorEmoji.ttf"
PIN = "2468"
SECRET = "test-session-secret-0123456789"

contracts = Contracts()


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    yield from start_broker()


@pytest.fixture(scope="session")
def emoji_font() -> Path:
    """Noto Color Emoji for sticker rendering. Downloaded once into ~/.cache (10 MB)."""
    if env := os.environ.get("EMOJI_FONT"):
        return Path(env).expanduser()
    if not FONT_CACHE.is_file() or FONT_CACHE.stat().st_size < 1_000_000:
        FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sSL", "-o", str(FONT_CACHE), FONT_URL], check=True)
    return FONT_CACHE


@dataclass
class HubServer:
    settings: Settings
    hub: Hub
    base_url: str
    server: uvicorn.Server
    task: asyncio.Task

    async def stop(self) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 15)


def make_settings(broker: Broker, data_dir: Path, port: int, font: Path, **kw) -> Settings:
    s = Settings(
        mqtt_host=broker.host,
        mqtt_port=broker.port,
        mqtt_password=broker.hub_password,
        hub_pin=PIN,
        session_secret=SECRET,
        data_dir=data_dir,
        media_base_url=f"http://127.0.0.1:{port}",
        emoji_font=font,
        receipt_led_seconds=1.0,
        users=load_users(REPO / "hub" / "users.yaml"),
        **kw,
    )
    s.validate()
    return s


async def start_hub(settings: Settings, port: int) -> HubServer:
    hub = Hub(settings)
    app = create_app(settings, hub)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve(), name="hub-uvicorn")
    deadline = asyncio.get_running_loop().time() + 15
    while not server.started:
        if task.done():
            task.result()
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("hub did not start")
        await asyncio.sleep(0.05)
    await asyncio.wait_for(hub.mqtt.connected.wait(), 10)
    return HubServer(settings, hub, f"http://127.0.0.1:{port}", server, task)


@pytest.fixture
async def hub_server(broker: Broker, tmp_path: Path, emoji_font: Path) -> AsyncIterator[HubServer]:
    port = free_port()
    settings = make_settings(broker, tmp_path / "data", port, emoji_font)
    hs = await start_hub(settings, port)
    try:
        yield hs
    finally:
        await hs.stop()


@pytest.fixture
async def client(hub_server: HubServer) -> AsyncIterator[httpx.AsyncClient]:
    """Logged in as lee."""
    async with httpx.AsyncClient(base_url=hub_server.base_url, timeout=10) as c:
        r = await c.post("/api/session", json={"user": "lee", "pin": PIN})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture
async def anon(hub_server: HubServer) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=hub_server.base_url, timeout=10) as c:
        yield c


@pytest.fixture
async def make_sim(broker: Broker, tmp_path: Path):
    """Factory for simulated orbs; each gets a random id unless given one."""
    sims: list[OrbSim] = []

    async def _make(orb_id: str | None = None, **overrides) -> OrbSim:
        orb_id = orb_id or f"orb-{uuid.uuid4().hex[:4]}"
        kw = dict(
            host=broker.host,
            port=broker.port,
            password=broker.orb_password,
            orb_id=orb_id,
            state_dir=tmp_path / "sims",
            max_backoff=1.0,
            show_timeout=5.0,
            receipt_timeout=2.0,
        )
        kw.update(overrides)
        sim = OrbSim(SimSettings(**kw), contracts, Console(prefix=f"[{orb_id}] ", color=False))
        sim.start()
        sims.append(sim)
        await sim.wait_for(lambda s: s.connected, 10)
        return sim

    yield _make
    for sim in sims:
        await sim.stop()
    # the broker is shared across tests: drop these orbs' retained state so the
    # next test's hub doesn't register them as pending from the retained hello
    if sims:
        async with HubProbe(broker, client_id=f"probe-clean-{uuid.uuid4().hex[:6]}") as probe:
            for sim in sims:
                await probe.clear_retained(sim.id)
