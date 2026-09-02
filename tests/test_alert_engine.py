"""Integration tests for the alert engine.

The engine tails ``Occupancy`` rows and drives the per-space state
machines.  These tests exercise one full tick of the engine against
an in-memory database with synthetic Occupancy + AlertRule rows.

The dispatch path is not exercised here — that's covered by the
webhook channel tests.  This file pins the engine's behavior:
  - it picks up new Occupancy rows and advances the state machine;
  - a fire writes an AlertEvent row with status='fired';
  - consecutive_count=N delays the fire by N observations;
  - cooldown suppresses refire;
  - per-rule condition.space_ids scopes the rule.
"""

import datetime
import json
import pytest

from backend import models
from backend.alerts import engine as alert_engine


def _make_camera_with_space(db_session, space_name="S1"):
    cam = models.Camera(name="C1", snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.flush()
    sp = models.Space(camera_id=cam.id, name=space_name, points="[]")
    db_session.add(sp)
    db_session.commit()
    return cam, sp


def _make_channel(db_session):
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}),
        enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    return ch


def _run_tick(db_session, *, from_id: int = -1):
    """Run one engine tick against the test session, processing all
    Occupancy rows with id > ``from_id``.

    The engine's production hydration sets the high-water mark to
    max(occupancy.id) on first run, which would skip the rows the
    test just inserted.  We bypass hydration by pre-seeding
    ``_last_seen_occupancy_id`` to ``from_id`` (default -1, the
    "never hydrated" sentinel).  The engine then queries rows with
    id > from_id and processes them.
    """
    alert_engine._last_seen_occupancy_id = from_id
    original = alert_engine.database.SessionLocal
    alert_engine.database.SessionLocal = lambda: db_session
    try:
        alert_engine._tick()
    finally:
        alert_engine.database.SessionLocal = original


def test_engine_fires_on_observation_with_count_1(db_session):
    cam, sp = _make_camera_with_space(db_session)
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="R", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ)
    db_session.commit()

    _run_tick(db_session, from_id=occ.id - 1)

    events = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).all()
    assert len(events) == 1
    assert events[0].status == "fired"


def test_engine_does_not_fire_below_consecutive_count(db_session):
    cam, sp = _make_camera_with_space(db_session)
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="R", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=3, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # First occupied observation: count=1, no fire.
    occ1 = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ1)
    db_session.commit()
    _run_tick(db_session, from_id=occ1.id - 1)
    assert db_session.query(models.AlertEvent).count() == 0

    # Second: count=2, no fire.
    occ2 = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ2)
    db_session.commit()
    _run_tick(db_session, from_id=occ1.id)
    assert db_session.query(models.AlertEvent).count() == 0

    # Third: count=3, fire.
    occ3 = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ3)
    db_session.commit()
    _run_tick(db_session, from_id=occ2.id)
    assert db_session.query(models.AlertEvent).count() == 1


def test_engine_respects_rule_scope(db_session):
    cam, sp = _make_camera_with_space(db_session)
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="R", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [999]}),  # different space
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()
    occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ)
    db_session.commit()
    _run_tick(db_session, from_id=occ.id - 1)
    assert db_session.query(models.AlertEvent).count() == 0


def test_engine_disabled_rule_does_not_fire(db_session):
    cam, sp = _make_camera_with_space(db_session)
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="R", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=False,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()
    occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ)
    db_session.commit()
    _run_tick(db_session, from_id=occ.id - 1)
    assert db_session.query(models.AlertEvent).count() == 0


def test_engine_cooldown_suppresses_refire(db_session):
    cam, sp = _make_camera_with_space(db_session)
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="R", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=3600,
    )
    db_session.add(rule)
    db_session.commit()

    # First observation fires.
    occ1 = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ1)
    db_session.commit()
    _run_tick(db_session, from_id=occ1.id - 1)
    assert db_session.query(models.AlertEvent).count() == 1

    # Vacant -> re-arm.
    occ2 = models.Occupancy(space_id=sp.id, scan_id=None, occupied=False)
    db_session.add(occ2)
    db_session.commit()
    _run_tick(db_session, from_id=occ1.id)

    # Occupied again — state machine says fire, but cooldown suppresses.
    occ3 = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ3)
    db_session.commit()
    _run_tick(db_session, from_id=occ2.id)
    # Still just one event.
    assert db_session.query(models.AlertEvent).count() == 1


# --- Enriched payload -------------------------------------------------------
# The engine enriches each fired event's payload with the human-readable
# names of the space, camera, and (for threshold rules) the camera
# group.  Receivers (webhook/email/mqtt) render these directly without
# having to query the database, so operators see what happened at a
# glance in the alert subject/body.

def test_engine_per_space_payload_includes_space_and_camera_names(db_session):
    """A space_occupied fire carries space + camera names and IDs."""
    cam, sp = _make_camera_with_space(db_session, space_name="A-12")
    db_session.query(models.Camera).filter_by(id=cam.id).update(
        {models.Camera.name: "North entrance"}
    )
    db_session.commit()
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="VIP became occupied", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ)
    db_session.commit()
    _run_tick(db_session, from_id=occ.id - 1)

    ev = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).first()
    payload = json.loads(ev.payload)
    assert payload["trigger_type"] == "space_occupied"
    assert payload["edge"] == "became_occupied"
    assert payload["space_state"] == "occupied"
    assert payload["space_id"] == sp.id
    assert payload["space_name"] == "A-12"
    assert payload["camera_id"] == cam.id
    assert payload["camera_name"] == "North entrance"


def test_engine_per_space_payload_edge_reflects_vacated_trigger(db_session):
    """space_vacated fires carry edge=became_vacant and space_state=vacant."""
    cam, sp = _make_camera_with_space(db_session, space_name="B-7")
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="VIP vacated", trigger_type="space_vacated",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # Vacant observation: state machine fires immediately.
    occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=False)
    db_session.add(occ)
    db_session.commit()
    _run_tick(db_session, from_id=occ.id - 1)

    ev = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).first()
    payload = json.loads(ev.payload)
    assert payload["edge"] == "became_vacant"
    assert payload["space_state"] == "vacant"
    assert payload["space_id"] == sp.id
    assert payload["space_name"] == "B-7"


def test_engine_per_space_payload_handles_missing_space_gracefully(db_session):
    """If the space row was deleted between fire and payload-build,
    the payload still gets the IDs but names are None.  No crash."""
    cam, sp = _make_camera_with_space(db_session, space_name="C-1")
    ch = _make_channel(db_session)
    rule = models.AlertRule(
        name="R", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()
    occ = models.Occupancy(space_id=sp.id, scan_id=None, occupied=True)
    db_session.add(occ)
    db_session.commit()

    # Yank the space row before the tick (simulates a deleted space
    # under a still-active rule — unusual but possible).
    db_session.query(models.Space).filter_by(id=sp.id).delete()
    db_session.commit()
    _run_tick(db_session, from_id=occ.id - 1)

    ev = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).first()
    assert ev is not None
    payload = json.loads(ev.payload)
    assert payload["space_id"] == sp.id
    assert payload["space_name"] is None
    assert payload["camera_id"] is None
    assert payload["camera_name"] is None
