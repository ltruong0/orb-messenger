"""A real Mosquitto in Docker using the repo's conf + ACL (or the cluster)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from mosquitto_passwd import write_passwd

REPO = Path(__file__).resolve().parents[2]
MOSQUITTO_IMAGE = os.environ.get("MOSQUITTO_IMAGE", "eclipse-mosquitto:2.0.22")

HUB_PASSWORD = "hub-test-pw"
ORB_PASSWORD = "orb-test-pw"


@dataclass
class Broker:
    host: str
    port: int
    hub_password: str
    orb_password: str
    container: str | None = None  # docker mode
    kube_namespace: str | None = None  # cluster mode (ORB_BROKER_HOST set)

    def restart(self) -> None:
        if self.container:
            subprocess.run(
                ["docker", "restart", "-t", "5", self.container], check=True, capture_output=True
            )
        else:
            ns = ["-n", self.kube_namespace or "orb"]
            subprocess.run(["kubectl", *ns, "rollout", "restart", "deploy/mosquitto"], check=True)
            subprocess.run(
                ["kubectl", *ns, "rollout", "status", "deploy/mosquitto", "--timeout=120s"],
                check=True,
            )
        wait_for_port(self.host, self.port, timeout=60)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(host: str, port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"mosquitto did not listen on {host}:{port} within {timeout}s")


def start_broker() -> Iterator[Broker]:
    """Mosquitto in Docker, or an existing broker when ORB_BROKER_HOST is set
    (e.g. `make broker-check` against the cluster; passwords from .env).
    A generator so conftests can wrap it in a fixture of any scope."""
    if host := os.environ.get("ORB_BROKER_HOST"):
        yield Broker(
            host=host,
            port=int(os.environ.get("ORB_BROKER_PORT", "1883")),
            hub_password=os.environ["MQTT_HUB_PASSWORD"],
            orb_password=os.environ["MQTT_ORB_PASSWORD"],
            kube_namespace=os.environ.get("ORB_NAMESPACE", "orb"),
        )
        return

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker is not available")

    # Under the repo, not the OS temp dir: Docker Desktop on macOS only
    # bind-mounts shared paths (/Users is shared, /var/folders is not).
    name = f"orb-test-mosquitto-{uuid.uuid4().hex[:8]}"
    work = REPO / ".tmp" / name
    work.mkdir(parents=True)
    secret_dir = work / "secret"
    data_dir = work / "data"
    data_dir.mkdir()
    data_dir.chmod(0o777)  # mosquitto runs as uid 1883 inside the container
    write_passwd(secret_dir / "passwd", {"msg-hub": HUB_PASSWORD, "orb": ORB_PASSWORD})
    (secret_dir / "passwd").chmod(0o644)

    conf_dir = REPO / "deploy/k8s/base/mosquitto"
    # fixed host port: `docker restart` would re-roll an ephemeral one
    port = free_port()
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:1883",
            "-v",
            f"{conf_dir / 'mosquitto.conf'}:/mosquitto/config/mosquitto.conf:ro",
            "-v",
            f"{conf_dir / 'aclfile'}:/mosquitto/config/aclfile:ro",
            "-v",
            f"{secret_dir}:/mosquitto/secret:ro",
            "-v",
            f"{data_dir}:/mosquitto/data",
            MOSQUITTO_IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        wait_for_port("127.0.0.1", port)
        yield Broker(
            host="127.0.0.1",
            port=port,
            hub_password=HUB_PASSWORD,
            orb_password=ORB_PASSWORD,
            container=name,
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
