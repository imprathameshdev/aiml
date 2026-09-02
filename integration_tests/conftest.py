"""Shared fixtures for the alerting end-to-end integration tests.

Sets up an in-process TestClient against the FastAPI app, a fresh
in-memory SQLite database, and the three capture servers (webhook
HTTP, SMTP, MQTT broker) that the per-channel tests bind to and
verify against.

The engine and dispatcher background tasks are started by the
app's lifespan (via TestClient entering the context). A small
``_patch_session_local`` helper monkeypatches
``backend.database.SessionLocal`` (and the ``database`` module
references inside ``engine`` and ``dispatcher``) so the engine
and dispatcher see the test session — without this they query a
different ``SessionLocal`` and the test event isn't picked up.

The capture servers run as async tasks in the test's event loop.
They bind to port 0 so a free localhost port is allocated by the
kernel; the bound port is returned via the fixture so per-test
code can configure channels to point at it.

Tests should be ``async def`` (asyncio_mode = auto in pytest.ini).
"""
import asyncio
import datetime
import json
import os
import pathlib
import sys
import uuid
from typing import Any

# Make the main app importable.  integration_tests/ is a sibling of
# the project root, so we add the parent to sys.path.
_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Force a deterministic LV_HOME for the test process so the secret
# key file and any DB files are scoped to this run.
os.environ.setdefault("VULTURE_LV_HOME", str(_HERE / ".test_lv_home"))
os.environ.setdefault("VULTURE_OTEL_ENABLED", "false")

import pytest  # noqa: E402  (import after sys.path tweak)

# Now we can import the app.
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend import database as backend_db  # noqa: E402
from backend import models  # noqa: E402
from backend.alerts import dispatcher as alerts_dispatcher  # noqa: E402
from backend.alerts import engine as alerts_engine  # noqa: E402
from backend.alerts.channels import email as email_channel  # noqa: E402
from backend.alerts.channels import mqtt as mqtt_channel  # noqa: E402
from backend.alerts.channels import webhook as webhook_channel  # noqa: E402
from backend.app import app  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory SQLite + module-level DB patches
# ---------------------------------------------------------------------------

_TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=_TEST_ENGINE, expire_on_commit=False
)


def _patch_session_local(test_session):
    """Redirect all SessionLocal callers in the app to fresh test sessions.

    Returns a new session each time (NOT the shared test_session
    fixture).  Channels like ``email.py`` open a fresh SessionLocal
    per call and ``db.close()`` it — if we returned the same
    session, closing it inside the channel would invalidate the
    shared session for the rest of the test.

    Because ``_TestingSessionLocal`` is bound to a StaticPool
    engine, every session sees the same underlying data.  The
    test's own ``test_session`` (the fixture) is a separate
    session used for assertions — it can query while the channel
    is in the middle of its own session work.
    """
    backend_db.SessionLocal = lambda: _TestingSessionLocal()
    alerts_engine.database.SessionLocal = lambda: _TestingSessionLocal()
    alerts_dispatcher.database.SessionLocal = lambda: _TestingSessionLocal()
    email_channel.database.SessionLocal = lambda: _TestingSessionLocal()


# ---------------------------------------------------------------------------
# Core app fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client():
    """Yield a TestClient with the in-memory test DB wired in.

    The TestClient enters the app's lifespan, which starts the
    engine and dispatcher background tasks.  We patch SessionLocal
    BEFORE entering the context so the first ``database.SessionLocal()``
    call (made during init_db) returns the test session.
    """
    # Recreate the schema for each test (function scope).
    models.Base.metadata.drop_all(bind=_TEST_ENGINE)
    models.Base.metadata.create_all(bind=_TEST_ENGINE)

    test_session = _TestingSessionLocal()
    _patch_session_local(test_session)

    # Reset module-level state so a previous test's tasks are gone.
    alerts_dispatcher.reset_events()
    alerts_engine._last_seen_occupancy_id = -1

    try:
        with TestClient(app) as client:
            yield {"client": client, "session": test_session}
    finally:
        # Stop background tasks cleanly.
        alerts_dispatcher.stop_background()
        alerts_engine.stop_background()
        test_session.close()
        # Reset state so the next test starts clean.
        alerts_dispatcher.reset_events()
        alerts_engine.reset_events()
        # Wipe the schema for the next test.
        models.Base.metadata.drop_all(bind=_TEST_ENGINE)


@pytest.fixture
def session(app_client):
    """The SQLAlchemy session for the current test (shorter alias)."""
    return app_client["session"]


@pytest.fixture
def client(app_client):
    """The TestClient for the current test (shorter alias)."""
    return app_client["client"]


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def seed_alert(session):
    """Factory: create a camera, space, channel, rule and return their IDs.

    Usage::

        def test_foo(seed_alert):
            ids = seed_alert(channel_type="webhook", config={"url": "..."})
            ids["event_id"]  # the AlertEvent ID once the rule fires
    """
    def _seed(channel_type: str, config: dict, **rule_overrides) -> dict:
        camera = models.Camera(
            name=f"Cam-{_uid()}", snapshot_url="http://x", is_enabled=True
        )
        session.add(camera)
        session.flush()

        space = models.Space(camera_id=camera.id, name=f"S-{_uid()}", points="[]")
        session.add(space)
        session.flush()

        channel = models.AlertChannel(
            name=f"Ch-{_uid()}",
            channel_type=channel_type,
            config=json.dumps(config),
            enabled=True,
        )
        session.add(channel)
        session.flush()

        rule_defaults = {
            "name": f"R-{_uid()}",
            "trigger_type": "space_occupied",
            "condition": json.dumps({"space_ids": [space.id]}),
            "channel_id": channel.id,
            "enabled": True,
            "cooldown_seconds": 0,
        }
        rule_defaults.update(rule_overrides)
        rule = models.AlertRule(**rule_defaults)
        session.add(rule)
        session.commit()

        return {
            "camera_id": camera.id,
            "space_id": space.id,
            "channel_id": channel.id,
            "rule_id": rule.id,
        }

    return _seed


@pytest.fixture
def clear_smtp_settings(session):
    """Remove all alerting_smtp_* and alerting_enabled rows after each test."""
    yield
    session.query(models.Setting).filter(
        models.Setting.key.in_([
            "alerting_enabled",
            "alerting_smtp_host",
            "alerting_smtp_port",
            "alerting_smtp_user",
            "alerting_smtp_pass",
            "alerting_smtp_from",
            "alerting_smtp_use_tls",
        ])
    ).delete(synchronize_session=False)
    session.commit()


# ---------------------------------------------------------------------------
# Trigger + wait helpers
# ---------------------------------------------------------------------------


async def wait_for_status(
    session,
    event_id: int,
    target: set[str],
    timeout: float = 5.0,
    poll_interval: float = 0.1,
):
    """Poll the event row until its status is in ``target`` or timeout.

    Uses ``asyncio.sleep`` to yield to the event loop, so the
    dispatcher task can make progress between polls.

    The dispatcher commits through its own session (the one
    patched into ``alerts_dispatcher.database.SessionLocal``).
    Because the in-memory engine uses a StaticPool — one shared
    connection — the test session and the dispatcher's session
    share the underlying connection but have separate transactions.
    The test session won't see the dispatcher's commit until its
    current transaction ends.  We force a refresh with
    ``session.expire_all()`` before each poll.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        session.expire_all()
        ev = session.query(models.AlertEvent).filter_by(id=event_id).first()
        if ev is not None and ev.status in target:
            return ev
        await asyncio.sleep(poll_interval)
    session.expire_all()
    ev = session.query(models.AlertEvent).filter_by(id=event_id).first()
    raise AssertionError(
        f"Event {event_id} did not reach status {target!r} within {timeout}s. "
        f"Last status: {ev.status if ev else 'event missing'}; "
        f"last_error: {ev.last_error if ev else 'n/a'}"
    )


@pytest.fixture
def trigger_alert(session):
    """Factory: insert an Occupancy, run the engine tick, return event id.

    The engine tick is run synchronously (it does sync DB work); it
    inserts an AlertEvent with status='fired'.  The dispatcher (running
    in the app's lifespan) then picks it up on its next 2s poll.
    """
    def _trigger(rule_id: int, space_id: int, occupied: bool = True) -> int:
        occ = models.Occupancy(space_id=space_id, scan_id=None, occupied=occupied)
        session.add(occ)
        session.commit()

        # Run one engine tick directly.  _last_seen_occupancy_id is
        # already at -1 (the "never hydrated" sentinel), so the
        # engine will process all rows including the one we just
        # inserted.
        original = alerts_engine.database.SessionLocal
        alerts_engine.database.SessionLocal = lambda: session
        try:
            alerts_engine._tick()
        finally:
            alerts_engine.database.SessionLocal = original

        # Force the test session to see the new event (separate
        # transaction from the engine's session).
        session.expire_all()
        ev = (
            session.query(models.AlertEvent)
            .filter_by(rule_id=rule_id)
            .order_by(models.AlertEvent.id.desc())
            .first()
        )
        if ev is None:
            raise AssertionError(
                "Engine tick did not create an AlertEvent.  Check the rule's "
                "condition, trigger_type, and that the Occupancy was inserted."
            )
        return ev.id

    return _trigger


# ---------------------------------------------------------------------------
# Capture server fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def webhook_capture_server():
    """aiohttp server on 127.0.0.1:0.  Records every POST in a list.

    The default handler returns 200.  Tests can override the response
    by replacing ``server.set_response(status, body)``.

    Yields: ``(port, captured_list, server)`` where ``captured_list``
    is a list of dicts with keys ``method``, ``headers``, ``body``.
    """
    from aiohttp import web

    captured: list[dict[str, Any]] = []
    response_status = [200]
    response_body = [b'{"ok": true}']
    response_ctype = ["application/json"]

    async def handler(request: web.Request) -> web.Response:
        body = await request.read()
        captured.append({
            "method": request.method,
            "headers": dict(request.headers),
            "body": body,
        })
        return web.Response(
            body=response_body[0],
            status=response_status[0],
            content_type=response_ctype[0],
        )

    app = web.Application()
    app.router.add_post("/hook", handler)
    app.router.add_post("/{tail:.*}", handler)  # catch-all for test URLs

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    # Find the actual port
    port = site._server.sockets[0].getsockname()[1]

    class _Server:
        def set_response(self, status: int, body: bytes, content_type: str = "application/json"):
            response_status[0] = status
            response_body[0] = body
            response_ctype[0] = content_type

    try:
        yield (port, captured, _Server())
    finally:
        await runner.cleanup()


@pytest.fixture
async def smtp_capture_server():
    """aiosmtpd Controller on 127.0.0.1:<random-free-port>.

    aiosmtpd's ``Controller(port=0)`` has a bug — its own readiness
    probe uses ``self.port`` (the requested value, 0) instead of
    the actually-bound port, so the probe always fails on
    ``port=0``.  We work around that by allocating a free port
    ourselves and passing it in.

    Records every DATA in a list.  Yields ``(port, captured_list)``.
    """
    import socket

    from aiosmtpd.controller import Controller

    captured: list[dict[str, Any]] = []

    class Capture:
        async def handle_DATA(self, server, session, envelope):
            captured.append({
                "from": envelope.mail_from,
                "to": list(envelope.rcpt_tos),
                "data": envelope.content,
            })
            return "250 OK"

    # Find a free port.  Bind to 0 to let the kernel assign, then
    # close and use the port number — TOCTOU race but acceptable
    # in a single-test-process environment.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    controller = Controller(Capture(), hostname="127.0.0.1", port=port)
    controller.start()
    # Give the socket a moment to bind (it should be instant since
    # the port is reserved, but a small sleep avoids flakes on slow
    # CI).
    await asyncio.sleep(0.2)

    try:
        yield (port, captured)
    finally:
        controller.stop()


@pytest.fixture
async def mqtt_capture_broker():
    """amqtt Broker on 127.0.0.1:1883 (default MQTT port).

    The amqtt config schema doesn't honor ``port: 0`` in the
    listener dict — it ignores the field and binds to the MQTT
    default (1883).  Rather than fight the library, we hard-code
    1883 and document the constraint.

    If port 1883 is already in use (e.g. a real broker running on
    the host), the broker will fail to start and the test will
    fail with a clear error.  Skip the test in that case so
    parallel CI runs don't break the suite.

    amqtt's broker.start() returns before the listener is bound to
    a port (it spawns internal coroutines and yields).  We
    wait until a TCP connect succeeds, with a timeout.
    """
    import asyncio
    import socket

    from amqtt.broker import Broker

    # Check if 1883 is free before spending time starting a broker
    # that will fail to bind.
    def _is_port_free(p: int) -> bool:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return True
            except OSError:
                return False

    if not _is_port_free(1883):
        pytest.skip("port 1883 is in use; cannot start in-process MQTT broker")

    broker = Broker({
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": "127.0.0.1",
                "max_connections": 5,
            }
        }
    })
    await broker.start()

    # Wait for the listener to actually bind (amqtt can take a few
    # seconds after start() returns).  Poll with a short connect
    # attempt until it succeeds or we hit the timeout.
    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        if _is_port_free(1883) is False:
            break
        await asyncio.sleep(0.1)
    else:
        await broker.shutdown()
        raise RuntimeError(
            "amqtt broker didn't bind port 1883 within 10s.  Check the "
            "broker logs for a startup error."
        )

    try:
        yield (1883, broker)
    finally:
        await broker.shutdown()


# ---------------------------------------------------------------------------
# MQTT subscriber helper (used by the MQTT integration test)
# ---------------------------------------------------------------------------


@pytest.fixture
def mqtt_subscriber():
    """Factory: subscribe to a topic and return received messages.

    Uses paho-mqtt's loop_start() in a thread.  Returns a
    ``MqttSubscription`` with ``messages`` (list), ``start(topic)``,
    ``stop()``.
    """
    import paho.mqtt.client as mqtt
    import threading
    import time

    class MqttSubscription:
        def __init__(self):
            self.messages: list[dict[str, Any]] = []
            self._client: mqtt.Client | None = None
            self._thread_loop_started = False
            self._connected = False

        def start(self, port: int, topic: str, qos: int = 1, timeout: float = 15.0):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2
            )

            def on_connect(c, ud, flags, rc, props=None):
                self._connected = (rc == 0)
                if self._connected:
                    c.subscribe(topic, qos)

            def on_message(c, ud, msg):
                self.messages.append({
                    "topic": msg.topic,
                    "payload": msg.payload,
                    "qos": msg.qos,
                })

            client.on_connect = on_connect
            client.on_message = on_message
            client.connect("127.0.0.1", port, 15)  # keepalive=15
            client.loop_start()
            self._client = client
            self._thread_loop_started = True

            # Wait for the subscription to be confirmed.  amqtt's
            # broker takes a few seconds to accept the first
            # connection, so we allow a generous timeout.
            deadline = time.time() + timeout
            while time.time() < deadline and not self._connected:
                time.sleep(0.1)
            if not self._connected:
                raise AssertionError(
                    f"MQTT subscriber failed to connect to 127.0.0.1:{port} "
                    f"within {timeout}s"
                )
            # Give the subscribe a moment to settle
            time.sleep(0.2)

        def stop(self):
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass

        def wait_for_messages(self, count: int = 1, timeout: float = 5.0) -> list:
            deadline = time.time() + timeout
            while time.time() < deadline and len(self.messages) < count:
                time.sleep(0.05)
            return self.messages

    sub = MqttSubscription()
    yield sub
    try:
        sub.stop()
    except Exception:
        pass
