"""End-to-end integration test for the MQTT channel.

Same flow as the webhook/SMTP tests, but routes the alert through
MQTT to a local amqtt broker.  A paho-mqtt subscriber in a
background thread receives the published message.

The broker is bound to the standard MQTT port 1883 — amqtt's
listener config doesn't honor ``port: 0``, so we use the default.
If port 1883 is in use on the test host, the broker start will
fail with a clear error.  See README.md.

The paho subscriber's ``connect()`` is synchronous and blocks
until the broker sends CONNACK.  The amqtt broker runs on the
test's asyncio event loop, so a blocking ``connect()`` inside the
test body would deadlock (broker can't process CONNACK while the
test thread is blocked).  We run the subscriber's ``start()`` in a
thread to keep the event loop free.
"""
import asyncio
import json
import time

import pytest

from backend import models
from conftest import wait_for_status


@pytest.mark.asyncio
async def test_mqtt_happy_path(
    app_client, session, seed_alert, trigger_alert,
    mqtt_capture_broker, mqtt_subscriber,
):
    port, _broker = mqtt_capture_broker

    # Subscribe first so we don't miss the message.  The subscriber's
    # start() blocks for the CONNACK — run it in a thread so the
    # amqtt broker's event loop is free to process the handshake.
    sub = mqtt_subscriber
    topic = f"lotvulture/test/{int(time.time())}"
    await asyncio.to_thread(sub.start, port, topic, 1)

    ids = seed_alert(
        channel_type="mqtt",
        config={
            "broker": f"tcp://127.0.0.1:{port}",
            "topic": topic,
            "qos": 1,
            "client_id": "lotvulture-test",
        },
    )

    event_id = trigger_alert(ids["rule_id"], ids["space_id"], occupied=True)
    ev = await wait_for_status(session, event_id, {"sent"}, timeout=10.0)

    # --- AlertEvent bookkeeping ---
    assert ev.status == "sent"
    assert ev.attempts == 1
    assert ev.last_error is None
    assert ev.delivered_at is not None
    assert ev.next_retry_at is None

    # --- Subscribed message ---
    messages = await asyncio.to_thread(sub.wait_for_messages, 1, 5.0)
    assert len(messages) == 1, f"expected 1 MQTT message, got {len(messages)}"
    msg = messages[0]
    assert msg["topic"] == topic
    assert msg["qos"] >= 1
    envelope = json.loads(msg["payload"].decode("utf-8"))
    assert envelope["event_id"] == event_id
    assert envelope["rule_id"] == ids["rule_id"]
    assert envelope["rule_name"].startswith("R-")
    assert envelope["trigger_type"] == "space_occupied"
    data = envelope["data"]
    assert data["edge"] == "became_occupied"
    assert data["space_id"] == ids["space_id"]
    assert data["space_name"].startswith("S-")
    assert data["space_state"] == "occupied"
    assert data["camera_id"] == ids["camera_id"]
    assert data["camera_name"].startswith("Cam-")
