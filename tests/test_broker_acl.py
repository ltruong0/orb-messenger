"""Phase 1 acceptance: Mosquitto ACL scopes each orb to its own client id,
msg-hub owns orb/#, and retained state survives a broker restart."""

from __future__ import annotations

import queue
import threading

import paho.mqtt.client as mqtt
import pytest
from conftest import Broker

TIMEOUT = 3.0


class Client:
    """Thin paho wrapper: synchronous connect/subscribe, inbox queue of (topic, payload)."""

    def __init__(self, broker: Broker, client_id: str, user: str, password=None, will=None):
        password = password or (broker.hub_password if user == "msg-hub" else broker.orb_password)
        self._broker = broker
        self.inbox: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self._connected = threading.Event()
        self._suback: queue.Queue[list] = queue.Queue()
        self._c = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311
        )
        self._c.username_pw_set(user, password)
        if will:
            self._c.will_set(*will)
        self._c.on_connect = lambda c, u, f, rc, p=None: (
            self._connected.set() if not rc.is_failure else None
        )
        self._c.on_message = lambda c, u, m: self.inbox.put((m.topic, m.payload))
        self._c.on_subscribe = lambda c, u, mid, rcs, p=None: self._suback.put(rcs)
        self._c.connect(broker.host, broker.port, keepalive=10)
        self._c.loop_start()
        assert self._connected.wait(TIMEOUT), f"{client_id} did not connect"

    def subscribe(self, topic: str) -> bool:
        """Return True if the broker granted the subscription."""
        self._c.subscribe(topic, qos=1)
        rcs = self._suback.get(timeout=TIMEOUT)
        return not rcs[0].is_failure

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._c.publish(topic, payload, qos=1, retain=retain).wait_for_publish(TIMEOUT)

    def expect(self, topic: str, timeout: float = TIMEOUT) -> bytes:
        while True:
            t, p = self.inbox.get(timeout=timeout)
            if t == topic:
                return p

    def expect_nothing(self, timeout: float = 1.0) -> None:
        try:
            t, p = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return
        pytest.fail(f"unexpected message on {t}: {p!r}")

    def disconnect(self, clean: bool = True) -> None:
        if clean:
            self._c.disconnect()
        else:
            self._c._sock_close()  # drop the socket so the broker fires the LWT
        self._c.loop_stop()


@pytest.fixture
def hub(broker: Broker):
    c = Client(broker, "msg-hub-test", "msg-hub")
    assert c.subscribe("orb/#")
    yield c
    c.disconnect()


@pytest.fixture
def orb(broker: Broker):
    c = Client(broker, "orb-test", "orb")
    yield c
    c.disconnect()


def test_bad_password_is_refused(broker: Broker):
    with pytest.raises(AssertionError, match="did not connect"):
        Client(broker, "orb-test", "orb", "wrong")


def test_orb_can_write_its_own_topics(hub: Client, orb: Client):
    for t in ("hello", "status", "event"):
        orb.publish(f"orb/orb-test/{t}", f"payload-{t}")
        assert hub.expect(f"orb/orb-test/{t}") == f"payload-{t}".encode()


def test_orb_cannot_write_another_orbs_topics(hub: Client, orb: Client):
    orb.publish("orb/orb-other/hello", "spoof")
    orb.publish("orb/orb-other/inbox", "spoof")
    orb.publish("orb/orb-test/inbox", "spoof-own-inbox")  # inbox is hub -> orb only
    hub.expect_nothing()


def test_orb_can_read_only_its_own_topics_and_the_shared_manifest(hub: Client, orb: Client):
    for t in ("config", "inbox", "led", "notice"):
        assert orb.subscribe(f"orb/orb-test/{t}"), t
    assert orb.subscribe("orb/all/stickers")
    # Mosquitto 2.x grants these subscriptions (SUBACK ok) and enforces the
    # `read` ACL at delivery time, so the property to test is non-delivery.
    orb.subscribe("orb/orb-other/inbox")
    orb.subscribe("orb/#")
    orb.subscribe("orb/+/inbox")

    hub.publish("orb/orb-other/inbox", "not-for-me", retain=True)
    hub.publish("orb/orb-test/inbox", "for-me")
    hub.publish("orb/all/stickers", "manifest")
    assert orb.expect("orb/orb-test/inbox") == b"for-me"
    assert orb.expect("orb/all/stickers") == b"manifest"
    orb.expect_nothing()

    # a fresh session must not get the other orb's retained message either
    orb.disconnect()
    orb2 = Client(orb._broker, "orb-test", "orb")
    orb2.subscribe("orb/orb-other/inbox")
    orb2.expect_nothing()
    orb2.disconnect()
    hub.publish("orb/orb-other/inbox", "", retain=True)


def test_lwt_sets_status_offline(broker: Broker, hub: Client):
    orb = Client(
        broker,
        "orb-lwt",
        "orb",
        will=("orb/orb-lwt/status", "offline", 1, True),
    )
    orb.publish("orb/orb-lwt/status", "online", retain=True)
    assert hub.expect("orb/orb-lwt/status") == b"online"
    orb.disconnect(clean=False)
    assert hub.expect("orb/orb-lwt/status", timeout=15) == b"offline"


def test_retained_state_survives_broker_restart(broker: Broker, hub: Client):
    hub.publish("orb/orb-persist/config", '{"state":"pending","code":"4821"}', retain=True)
    hub.disconnect()
    broker.restart()

    orb = Client(broker, "orb-persist", "orb")
    assert orb.subscribe("orb/orb-persist/config")
    assert orb.expect("orb/orb-persist/config") == b'{"state":"pending","code":"4821"}'
    orb.disconnect()

    # forgetting an orb: a zero-length retained publish clears the topic
    hub2 = Client(broker, "msg-hub-test-2", "msg-hub")
    hub2.publish("orb/orb-persist/config", "", retain=True)
    hub2.disconnect()
    orb2 = Client(broker, "orb-persist", "orb")
    assert orb2.subscribe("orb/orb-persist/config")
    orb2.expect_nothing()
    orb2.disconnect()
