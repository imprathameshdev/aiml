"""End-to-end integration test for the camera-offline rule.

Drives the full pipeline for ``camera_offline``: the engine polls
each enabled camera's last ``CameraScan`` recency, fires the rule
when the gap exceeds ``minutes_offline``, and the dispatcher POSTs
the alert to a webhook.  The test verifies the rich payload shape
(camera name + ID, minutes_offline, last_scan_at, gap_seconds).
"""
import datetime
import json
import uuid

import pytest

from backend import models
from conftest import wait_for_status


@pytest.mark.asyncio
async def test_camera_offline_happy_path(
    app_client, session, webhook_capture_server
):
    """A camera whose last scan is older than minutes_offline fires
    the rule, the dispatcher delivers it via webhook, and the
    payload carries the camera name, ID, and the offline metadata.
    """
    port, captured, _ = webhook_capture_server
    uid = uuid.uuid4().hex[:8]

    camera = models.Camera(
        name=f"WestCam-{uid}", snapshot_url="http://x", is_enabled=True,
    )
    session.add(camera)
    session.flush()

    channel = models.AlertChannel(
        name=f"Ch-{uid}", channel_type="webhook",
        config=json.dumps({"url": f"http://127.0.0.1:{port}/hook"}),
        enabled=True,
    )
    session.add(channel)
    session.flush()

    rule = models.AlertRule(
        name=f"R-{uid}",
        trigger_type="camera_offline",
        condition=json.dumps({
            "camera_id": camera.id, "minutes_offline": 5,
        }),
        channel_id=channel.id,
        enabled=True,
        cooldown_seconds=0,
    )
    session.add(rule)
    session.commit()

    # Last scan was 10 minutes ago → offline (gap > 5 min).
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    session.add(models.CameraScan(
        camera_id=camera.id, timestamp=old, has_image=True,
    ))
    session.commit()

    # Run a single engine tick to evaluate the camera-offline rules.
    alerts_engine_module = (
        __import__("backend.alerts.engine", fromlist=["_tick"])
    )
    alerts_engine_module._last_seen_occupancy_id = -1
    original = alerts_engine_module.database.SessionLocal
    alerts_engine_module.database.SessionLocal = lambda: session
    try:
        alerts_engine_module._tick()
    finally:
        alerts_engine_module.database.SessionLocal = original

    session.expire_all()
    ev = (
        session.query(models.AlertEvent)
        .filter_by(rule_id=rule.id)
        .order_by(models.AlertEvent.id.desc())
        .first()
    )
    assert ev is not None, "Engine did not create an AlertEvent for the camera-offline rule"
    ev = await wait_for_status(session, ev.id, {"sent"}, timeout=5.0)

    # --- AlertEvent bookkeeping ---
    assert ev.status == "sent"
    assert ev.attempts == 1
    assert ev.last_error is None

    # --- Captured webhook request ---
    assert len(captured) == 1, f"expected 1 POST, got {len(captured)}"
    envelope = json.loads(captured[0]["body"])
    assert envelope["trigger_type"] == "camera_offline"
    data = envelope["data"]
    assert data["camera_id"] == camera.id
    assert data["camera_name"] == f"WestCam-{uid}"
    assert data["minutes_offline"] == 5
    assert data["last_scan_at"] is not None
    # gap_seconds should be roughly 600 (10 minutes), allow ±5s
    # slack for tick timing.
    assert 595 < data["gap_seconds"] < 605
