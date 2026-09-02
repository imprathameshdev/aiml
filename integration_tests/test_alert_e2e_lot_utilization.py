"""End-to-end integration test for the lot-utilization (threshold) rule.

Drives the full alert pipeline for the ``lot_full_above_pct`` rule
type: a webhook is configured to receive the alert, the engine
computes the current occupancy across the camera's spaces, and when
the threshold is crossed the dispatcher POSTs the alert to the
webhook.  This is the engine's "scope" path — the per-space test
covers the simple "single space, single camera" case; this one
covers the "compute % across many spaces in a camera, fire when
above X%".
"""
import json
from datetime import datetime, timezone

import pytest

from backend import models
from conftest import wait_for_status


@pytest.mark.asyncio
async def test_lot_full_above_pct_happy_path(
    app_client, session, seed_alert, trigger_alert, webhook_capture_server
):
    """A 10-space camera at 90% occupancy fires the lot_full rule and
    the webhook receives a payload with the rich context (camera
    name + ID, current %, occupied/vacant/total counts, thresholds).
    """
    port, captured, _ = webhook_capture_server
    # Build a separate setup: 10 spaces in one camera, occupancy at
    # 90%.  We use the seed_alert factory but pass a custom condition
    # for the threshold rule.  Because the engine's threshold rules
    # resolve a single camera_id (not space_ids), we need a slightly
    # different setup.  Instead of seed_alert, build inline.
    import uuid
    uid = uuid.uuid4().hex[:8]

    camera = models.Camera(
        name=f"LotCam-{uid}", snapshot_url="http://x", is_enabled=True,
    )
    session.add(camera)
    session.flush()

    spaces = []
    for i in range(10):
        sp = models.Space(camera_id=camera.id, name=f"S-{uid}-{i}", points="[]")
        session.add(sp)
        spaces.append(sp)
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
        trigger_type="lot_full_above_pct",
        condition=json.dumps({
            "camera_id": camera.id,
            "fire_at_pct": 90, "resolve_at_pct": 70,
        }),
        channel_id=channel.id,
        enabled=True,
        cooldown_seconds=0,
    )
    session.add(rule)
    session.commit()

    # 9 of 10 spaces occupied = 90% (boundary fires).
    for i, sp in enumerate(spaces):
        occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 9))
        session.add(occ)
    session.commit()

    # The threshold rule fires on every engine tick (not tied to
    # occupancy tail), so we just run the tick.
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
    assert ev is not None, "Engine did not create an AlertEvent for the threshold rule"
    ev = await wait_for_status(session, ev.id, {"sent"}, timeout=5.0)

    # --- AlertEvent bookkeeping ---
    assert ev.status == "sent"
    assert ev.attempts == 1
    assert ev.last_error is None

    # --- Captured webhook request ---
    assert len(captured) == 1, f"expected 1 POST, got {len(captured)}"
    envelope = json.loads(captured[0]["body"])
    assert envelope["trigger_type"] == "lot_full_above_pct"
    assert envelope["rule_id"] == rule.id
    data = envelope["data"]
    # Rich payload: current %, scope, counts
    assert data["current_pct"] == 90.0
    assert data["fire_at_pct"] == 90
    assert data["resolve_at_pct"] == 70
    assert data["spaces_occupied"] == 9
    assert data["spaces_vacant"] == 1
    assert data["spaces_total"] == 10
    assert data["camera_id"] == camera.id
    assert data["camera_name"] == f"LotCam-{uid}"


@pytest.mark.asyncio
async def test_lot_full_above_pct_does_not_fire_below_threshold(
    app_client, session, seed_alert, trigger_alert, webhook_capture_server
):
    """At 80% occupancy, the rule (fire_at_pct=90) does NOT fire.
    Verifies the threshold path is correctly evaluated.
    """
    port, captured, _ = webhook_capture_server
    import uuid
    uid = uuid.uuid4().hex[:8]

    camera = models.Camera(
        name=f"LotCam-{uid}", snapshot_url="http://x", is_enabled=True,
    )
    session.add(camera)
    session.flush()

    spaces = []
    for i in range(10):
        sp = models.Space(camera_id=camera.id, name=f"S-{uid}-{i}", points="[]")
        session.add(sp)
        spaces.append(sp)
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
        trigger_type="lot_full_above_pct",
        condition=json.dumps({
            "camera_id": camera.id,
            "fire_at_pct": 90, "resolve_at_pct": 70,
        }),
        channel_id=channel.id,
        enabled=True,
        cooldown_seconds=0,
    )
    session.add(rule)
    session.commit()

    # 8/10 = 80%, below the 90% threshold.
    for i, sp in enumerate(spaces):
        occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 8))
        session.add(occ)
    session.commit()

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
    # No event should have been created.
    ev_count = (
        session.query(models.AlertEvent)
        .filter_by(rule_id=rule.id)
        .count()
    )
    assert ev_count == 0
    assert len(captured) == 0
