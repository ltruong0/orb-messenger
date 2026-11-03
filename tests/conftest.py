"""Root test fixtures. The broker lives in tests/orbtest so hub/tests can reuse it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from orbtest import Broker, start_broker


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    yield from start_broker()
