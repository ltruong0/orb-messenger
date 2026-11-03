"""Root test fixtures. The broker lives in tests/orbtest so hub/tests can reuse it."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from orbtest import Broker, start_broker


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    yield from start_broker()


@pytest.fixture
def media_server(tmp_path: Path):
    """Plain HTTP server for /media/* like the hub's, serving tmp_path/www."""
    root = tmp_path / "www"
    (root / "media" / "stickers").mkdir(parents=True)
    (root / "media" / "photos").mkdir(parents=True)
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(root), **k
    )
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield root, f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
