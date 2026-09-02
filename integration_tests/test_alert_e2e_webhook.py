"""End-to-end integration test for the webhook channel.

Drives the full alert pipeline:

  1. seed a camera + space + per-space rule + webhook channel
  2. trigger an alert by inserting an Occupancy row and running
     one engine tick directly (skips the engine's 5s poll wait)
  3. the real dispatcher task (started by the app's lifespan) picks
     up the new AlertEvent on its next 2s poll
  4. the channel handler POSTs the JSON envelope to a local aiohttp
     server
  5. we verify the captured request: method, headers (including the
     HMAC X-Signature when a secret is configured), and the parsed
     envelope body

We also assert that the AlertEvent row's bookkeeping is correct:
``status='sent'``, ``delivered_at`` is recent, ``attempts=1``,
``last_error is None``.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from backend import models
from conftest import wait_for_status


@pytest.mark.asyncio
async def test_webhook_happy_path(
    app_client, session, seed_alert, trigger_alert, webhook_capture_server
):
    port, captured, _ = webhook_capture_server
    secret = "s3cret-shared-with-receiver"

    ids = seed_alert(
        channel_type="webhook",
        config={
            "url": f"http://127.0.0.1:{port}/hook",
            "secret": secret,
        },
    )

    fired_at_before = datetime.now(timezone.utc)
    event_id = trigger_alert(ids["rule_id"], ids["space_id"], occupied=True)

    ev = await wait_for_status(session, event_id, {"sent"}, timeout=5.0)
    fired_at_after = datetime.now(timezone.utc)

    # --- AlertEvent bookkeeping ---
    assert ev.status == "sent"
    assert ev.attempts == 1
    assert ev.last_error is None
    assert ev.delivered_at is not None
    # delivered_at should be within the window of the test.  The
    # column is stored as a naive UTC datetime (SQLite), so
    # compare against naive UTC.
    if ev.delivered_at.tzinfo is None:
        delivered_at_naive = ev.delivered_at
    else:
        delivered_at_naive = ev.delivered_at.astimezone(timezone.utc).replace(tzinfo=None)
    before_naive = fired_at_before.astimezone(timezone.utc).replace(tzinfo=None)
    after_naive = fired_at_after.astimezone(timezone.utc).replace(tzinfo=None)
    assert before_naive <= delivered_at_naive <= after_naive
    # next_retry_at is cleared on success
    assert ev.next_retry_at is None

    # --- Captured request ---
    assert len(captured) == 1, f"expected 1 POST, got {len(captured)}"
    req = captured[0]
    assert req["method"] == "POST"
    assert req["headers"]["Content-Type"] == "application/json"

    # HMAC signature header must match an independent computation
    sig = req["headers"].get("X-Signature")
    assert sig is not None, "X-Signature header missing"
    assert sig.startswith("sha256=")
    expected_sig = hmac.new(secret.encode("utf-8"), req["body"], hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected_sig}"

    # Envelope body
    envelope = json.loads(req["body"])
    assert envelope["event_id"] == event_id
    assert envelope["rule_id"] == ids["rule_id"]
    assert envelope["rule_name"].startswith("R-")
    assert envelope["trigger_type"] == "space_occupied"
    assert envelope["fired_at"] is not None
    data = envelope["data"]
    assert isinstance(data, dict)
    # Engine-enriched payload: the receiver should know the space,
    # its new state, and which camera owns it.  No more guessing
    # from raw IDs.
    assert data["trigger_type"] == "space_occupied"
    assert data["space_id"] == ids["space_id"]
    assert data["space_name"].startswith("S-")
    assert data["space_state"] == "occupied"
    assert data["camera_id"] == ids["camera_id"]
    assert data["camera_name"].startswith("Cam-")
    assert data["edge"] == "became_occupied"
