"""Tests for the camera-group API + threshold rule evaluation.

Covers:
  - camera-group CRUD with validation and reference checking.
  - threshold rule end-to-end (create rule, simulate occupancy, fire).
  - hysteresis: lot-full fires once, stays firing through dead band,
    rearms only when crossing below resolve_at_pct.
  - lot-open mirror direction.
  - empty / unknown group behaviour.
"""

import json
import pytest

from backend import models
from backend.alerts import engine as alert_engine


# --- helpers ----------------------------------------------------------------

def _make_cam(db_session, name="C", spaces=4):
    cam = models.Camera(name=name, snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.flush()
    sps = []
    for i in range(spaces):
        sp = models.Space(camera_id=cam.id, name=f"{name}-S{i}", points="[]")
        db_session.add(sp)
        sps.append(sp)
    db_session.commit()
    return cam, sps


def _run_tick(db_session, *, from_id: int = -1):
    alert_engine._last_seen_occupancy_id = from_id
    original = alert_engine.database.SessionLocal
    alert_engine.database.SessionLocal = lambda: db_session
    try:
        alert_engine._tick()
    finally:
        alert_engine.database.SessionLocal = original


# --- camera-group CRUD -----------------------------------------------------

def test_create_camera_group(client, auth_header, db_session):
    cam, _ = _make_cam(db_session)
    r = client.post(
        "/api/camera-groups",
        json={"name": "North lot", "camera_ids": [cam.id]},
        headers=auth_header,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "North lot"
    assert body["camera_ids"] == [cam.id]
    assert body["created_by_username"] == "testadmin"


def test_create_camera_group_with_unknown_camera_400(client, auth_header):
    r = client.post(
        "/api/camera-groups",
        json={"name": "G", "camera_ids": [99999]},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_list_camera_groups_returns_ids(client, auth_header, db_session):
    cam, _ = _make_cam(db_session)
    client.post("/api/camera-groups",
                json={"name": "G1", "camera_ids": [cam.id]}, headers=auth_header)
    r = client.get("/api/camera-groups", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["camera_ids"] == [cam.id]


def test_update_camera_group_replaces_membership(client, auth_header, db_session):
    cam1, _ = _make_cam(db_session, name="C1")
    cam2, _ = _make_cam(db_session, name="C2")
    r = client.post("/api/camera-groups",
                    json={"name": "G", "camera_ids": [cam1.id]}, headers=auth_header)
    gid = r.json()["id"]
    r = client.put(f"/api/camera-groups/{gid}",
                   json={"camera_ids": [cam2.id]}, headers=auth_header)
    assert r.status_code == 200
    assert r.json()["camera_ids"] == [cam2.id]


def test_delete_unreferenced_camera_group(client, auth_header, db_session):
    cam, _ = _make_cam(db_session)
    r = client.post("/api/camera-groups",
                    json={"name": "G", "camera_ids": [cam.id]}, headers=auth_header)
    gid = r.json()["id"]
    r = client.delete(f"/api/camera-groups/{gid}", headers=auth_header)
    assert r.status_code == 200


def test_delete_camera_group_referenced_by_rule_409(client, auth_header, db_session):
    cam, _ = _make_cam(db_session)
    r = client.post("/api/camera-groups",
                    json={"name": "G", "camera_ids": [cam.id]}, headers=auth_header)
    gid = r.json()["id"]
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    client.post(
        "/api/alert-rules",
        json={"name": "Lot full",
              "trigger_type": "lot_full_above_pct",
              "condition": {"camera_group_id": gid, "fire_at_pct": 90, "resolve_at_pct": 70},
              "channel_id": cid},
        headers=auth_header,
    )
    r = client.delete(f"/api/camera-groups/{gid}", headers=auth_header)
    assert r.status_code == 409
    assert "rule" in r.json()["detail"].lower()


# --- threshold rule creation API validation --------------------------------

def test_create_threshold_rule_validates_inverted_thresholds(client, auth_header, db_session):
    cam, _ = _make_cam(db_session)
    r = client.post("/api/camera-groups",
                    json={"name": "G", "camera_ids": [cam.id]}, headers=auth_header)
    gid = r.json()["id"]
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    # resolve >= fire on lot_full_above_pct -> reject
    r = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "lot_full_above_pct",
              "condition": {"camera_group_id": gid,
                            "fire_at_pct": 70, "resolve_at_pct": 80},
              "channel_id": cid},
        headers=auth_header,
    )
    assert r.status_code == 400
    # resolve <= fire on lot_open_below_pct -> reject
    r = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "lot_open_below_pct",
              "condition": {"camera_group_id": gid,
                            "fire_at_pct": 30, "resolve_at_pct": 10},
              "channel_id": cid},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_create_threshold_rule_requires_camera_group(client, auth_header):
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    r = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "lot_full_above_pct",
              "condition": {"fire_at_pct": 90, "resolve_at_pct": 70},  # no group
              "channel_id": cid},
        headers=auth_header,
    )
    assert r.status_code == 400


# --- threshold rule engine end-to-end --------------------------------------

def test_threshold_lot_full_fires_when_at_threshold(db_session):
    cam, sps = _make_cam(db_session, spaces=10)
    grp = models.CameraGroup(name="All")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot full", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 9 of 10 spaces occupied = 90%.  Insert Occupancy rows.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 9)))
    db_session.commit()
    _run_tick(db_session)

    events = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).all()
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["current_pct"] == 90.0
    assert payload["camera_group_name"] == "All"


def test_threshold_lot_full_does_not_fire_below_threshold(db_session):
    cam, sps = _make_cam(db_session, spaces=10)
    grp = models.CameraGroup(name="All")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot full", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 8/10 = 80%.  Below fire_at_pct=90.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 8)))
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 0


def test_threshold_lot_full_only_counts_group_spaces(db_session):
    """Spaces in other cameras (not in the group) don't contribute."""
    cam_in, sps_in = _make_cam(db_session, name="IN", spaces=10)
    cam_out, sps_out = _make_cam(db_session, name="OUT", spaces=10)
    grp = models.CameraGroup(name="IN-only")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam_in.id))
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot full IN", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # IN: 9/10 occupied (90% in-group).  OUT: 10/10 occupied (irrelevant).
    for i, sp in enumerate(sps_in):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 9)))
    for sp in sps_out:
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=True))
    db_session.commit()
    _run_tick(db_session)
    events = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).all()
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["current_pct"] == 90.0


def test_threshold_lot_open_mirror_direction(db_session):
    cam, sps = _make_cam(db_session, spaces=10)
    grp = models.CameraGroup(name="All")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot open", trigger_type="lot_open_below_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 10, "resolve_at_pct": 30}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 1/10 = 10%.  Fire_at_pct=10 (boundary fires per evaluate_threshold_rule).
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i >= 9)))
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 1


def test_threshold_empty_group_does_not_fire(db_session):
    """A group with no cameras short-circuits to no fire."""
    grp = models.CameraGroup(name="Empty")
    db_session.add(grp)
    db_session.flush()
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot full", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 0


def test_threshold_rearms_after_crossing_resolve(db_session):
    """Full hysteresis cycle: fire -> drop below resolve -> re-fire."""
    cam, sps = _make_cam(db_session, spaces=10)
    grp = models.CameraGroup(name="All")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot full", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # Tick 1: 10/10 occupied = 100%.  Fires.
    for sp in sps:
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=True))
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 1

    # Tick 2: 9/10 = 90% (dead band).  No new fire.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 9)))
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 1

    # Tick 3: 5/10 = 50% (below resolve).  Re-arm.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 5)))
    db_session.commit()
    _run_tick(db_session)
    state = db_session.query(models.AlertState).filter_by(rule_id=rule.id).first()
    assert state.state == "armed"

    # Tick 4: 10/10 = 100% again.  Fire again.
    for sp in sps:
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=True))
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 2


def test_threshold_cooldown_does_not_lock_state_in_firing(db_session):
    """Regression: when the first fire of a threshold rule is
    blocked by cooldown, the state must NOT be persisted as
    "firing" — otherwise the state machine (which only fires on
    armed -> firing transitions) would never fire again while
    occupancy stays above the threshold, silently losing the alert.

    With the fix, the state stays "armed" through the blocked
    fire, so the next tick (after cooldown elapses) re-evaluates
    and fires normally.
    """
    cam, sps = _make_cam(db_session, spaces=10)
    grp = models.CameraGroup(name="CooldownLockoutGroup")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    ch = models.AlertChannel(
        name="CooldownLockoutHook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    # Unique rule name so the find-by-id queries below are unambiguous.
    rule = models.AlertRule(
        name="CooldownLockoutRule", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True, consecutive_count=1,
        # 600s cooldown so the first fire is definitely blocked
        # by the back-dated last_fired_at below.
        cooldown_seconds=600,
    )
    db_session.add(rule)
    db_session.commit()

    # Seed an AlertState with last_fired_at = now so the cooldown
    # blocks the next fire.  The AlertState row is created lazily
    # by the engine on the first fire, so we create it here.
    from datetime import datetime, timezone
    state_row = models.AlertState(rule_id=rule.id)
    state_row.last_fired_at = datetime.now(timezone.utc)
    db_session.add(state_row)
    db_session.commit()

    # Tick 1: 10/10 = 100% (above fire threshold).  Cooldown
    # blocks the fire.  The OLD code would set state to "firing"
    # anyway, stranding the rule.  The FIX must keep state at
    # "armed" so a later tick can re-fire.
    for sp in sps:
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=True))
    db_session.commit()
    _run_tick(db_session)
    # Re-query: the engine closes its own session each tick.
    state_row = (
        db_session.query(models.AlertState)
        .filter_by(rule_id=rule.id)
        .first()
    )
    assert state_row.state == "armed", (
        f"state is {state_row.state!r} after cooldown-blocked fire; "
        f"the old code set it to 'firing' here, which strands the "
        f"rule because the state machine only fires on armed -> "
        f"firing transitions."
    )
    # No event was created (cooldown blocked).
    assert db_session.query(models.AlertEvent).count() == 0

    # Tick 2: clear the cooldown by back-dating last_fired_at to
    # long ago, then re-tick.  Now the fire must succeed and
    # transition armed -> firing with a new event.
    state_row.last_fired_at = None
    db_session.commit()
    for sp in sps:
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=True))
    db_session.commit()
    _run_tick(db_session)
    state_row = (
        db_session.query(models.AlertState)
        .filter_by(rule_id=rule.id)
        .first()
    )
    assert state_row.state == "firing"
    assert db_session.query(models.AlertEvent).count() == 1


# --- single-camera scope (camera_id, not camera_group_id) ---------------

def test_create_threshold_rule_requires_scope(client, auth_header):
    """A threshold rule's condition must carry a scope (camera_id
    or camera_group_id).  Missing both → 400.
    """
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    r = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "lot_full_above_pct",
              "condition": {"fire_at_pct": 90, "resolve_at_pct": 70},  # no scope
              "channel_id": cid},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "exactly one" in r.json()["detail"]


def test_create_threshold_rule_accepts_single_camera_id(client, auth_header, db_session):
    """The GUI offers 'pick this single camera' as an alternative to a
    group.  The condition can carry either ``camera_group_id``
    (legacy) or ``camera_id`` (new) — the engine treats both the
    same way.
    """
    import backend.models as models
    cam = models.Camera(name="Solo", snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.commit()
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=__import__("json").dumps({"url": "https://a"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    r = client.post(
        "/api/alert-rules",
        json={"name": "Solo cam", "trigger_type": "lot_full_above_pct",
              "condition": {"camera_id": cam.id,
                            "fire_at_pct": 90, "resolve_at_pct": 70},
              "channel_id": ch.id},
        headers=auth_header,
    )
    assert r.status_code == 201, r.json()


def test_create_threshold_rule_rejects_both_camera_and_group(client, auth_header, db_session):
    """The condition must have exactly one scope field — not both."""
    import backend.models as models
    cam = models.Camera(name="Solo", snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.commit()
    grp = models.CameraGroup(name="G")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    db_session.commit()
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=__import__("json").dumps({"url": "https://a"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    r = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "lot_full_above_pct",
              "condition": {"camera_id": cam.id, "camera_group_id": grp.id,
                            "fire_at_pct": 90, "resolve_at_pct": 70},
              "channel_id": ch.id},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "exactly one" in r.json()["detail"]


def test_engine_fires_threshold_rule_with_single_camera_id(db_session):
    """End-to-end: a threshold rule scoped to a single camera fires
    when occupancy on that camera crosses fire_at_pct.
    """
    import backend.models as models
    cam, sps = _make_cam(db_session, name="Solo", spaces=10)
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=__import__("json").dumps({"url": "https://a"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    import backend.models as models
    rule = models.AlertRule(
        name="Solo cam full", trigger_type="lot_full_above_pct",
        condition=__import__("json").dumps({
            "camera_id": cam.id,
            "fire_at_pct": 90, "resolve_at_pct": 70,
        }),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 9/10 occupied = 90% in this single camera.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 9)))
    db_session.commit()
    _run_tick(db_session)

    events = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).all()
    assert len(events) == 1
    payload = __import__("json").loads(events[0].payload)
    assert payload["camera_id"] == cam.id
    assert payload["camera_name"] == "Solo"
    assert payload["current_pct"] == 90.0
    assert "camera_group_id" not in payload


def test_engine_skips_threshold_rule_with_misconfigured_scope(db_session):
    """A hand-edited condition with both camera_id AND camera_group_id
    is misconfigured — the engine should skip it silently rather
    than crash or fire on the wrong scope.
    """
    import backend.models as models
    cam, sps = _make_cam(db_session, name="X", spaces=4)
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=__import__("json").dumps({"url": "https://a"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="Bad", trigger_type="lot_full_above_pct",
        # Intentionally broken — both fields set.
        condition=__import__("json").dumps({
            "camera_id": cam.id, "camera_group_id": 999,
            "fire_at_pct": 90, "resolve_at_pct": 70,
        }),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    for sp in sps:
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=True))
    db_session.commit()
    _run_tick(db_session)
    assert db_session.query(models.AlertEvent).count() == 0


# --- Enriched payload for threshold rules ----------------------------------
# Each threshold-rule fire must carry the operator's view of "what
# happened": the current %, the occupied/vacant/total counts (so the
# receiver can phrase "9/10 spaces occupied" without a DB lookup), and
# the camera / camera group name + ID.

def test_threshold_payload_includes_occupied_vacant_counts_group(db_session):
    """A lot_full_above_pct fire on a group includes spaces_occupied,
    spaces_vacant, spaces_total, and the group's name + ID."""
    cam, sps = _make_cam(db_session, name="South", spaces=10)
    db_session.query(models.Camera).filter_by(id=cam.id).update(
        {models.Camera.name: "South lot cam"}
    )
    db_session.commit()
    grp = models.CameraGroup(name="All lots")
    db_session.add(grp)
    db_session.flush()
    db_session.add(models.CameraGroupMembership(camera_group_id=grp.id, camera_id=cam.id))
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="Lot full", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_group_id": grp.id,
                              "fire_at_pct": 90, "resolve_at_pct": 70}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 9/10 occupied = 90% → fires.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 9)))
    db_session.commit()
    _run_tick(db_session)

    ev = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).first()
    payload = json.loads(ev.payload)
    assert payload["trigger_type"] == "lot_full_above_pct"
    assert payload["current_pct"] == 90.0
    assert payload["fire_at_pct"] == 90
    assert payload["resolve_at_pct"] == 70
    assert payload["spaces_occupied"] == 9
    assert payload["spaces_vacant"] == 1
    assert payload["spaces_total"] == 10
    assert payload["camera_group_id"] == grp.id
    assert payload["camera_group_name"] == "All lots"


def test_threshold_payload_includes_occupied_vacant_counts_single_camera(db_session):
    """Single-camera scope carries camera_id + camera_name (not group)."""
    cam, sps = _make_cam(db_session, name="Solo", spaces=4)
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="Solo full", trigger_type="lot_full_above_pct",
        condition=json.dumps({"camera_id": cam.id,
                              "fire_at_pct": 75, "resolve_at_pct": 50}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 3/4 = 75% → fires.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i < 3)))
    db_session.commit()
    _run_tick(db_session)

    ev = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).first()
    payload = json.loads(ev.payload)
    assert payload["current_pct"] == 75.0
    assert payload["spaces_occupied"] == 3
    assert payload["spaces_vacant"] == 1
    assert payload["spaces_total"] == 4
    assert payload["camera_id"] == cam.id
    assert payload["camera_name"] == "Solo"
    assert "camera_group_id" not in payload


def test_threshold_lot_open_payload_shape(db_session):
    """lot_open_below_pct fires carry the same stats as lot_full
    (just triggered in the opposite direction)."""
    cam, sps = _make_cam(db_session, name="Q", spaces=10)
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="Lot open", trigger_type="lot_open_below_pct",
        condition=json.dumps({"camera_id": cam.id,
                              "fire_at_pct": 10, "resolve_at_pct": 30}),
        channel_id=ch.id, enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    # 1/10 = 10% → fires.
    for i, sp in enumerate(sps):
        db_session.add(models.Occupancy(space_id=sp.id, scan_id=None, occupied=(i >= 9)))
    db_session.commit()
    _run_tick(db_session)

    ev = db_session.query(models.AlertEvent).filter_by(rule_id=rule.id).first()
    payload = json.loads(ev.payload)
    assert payload["trigger_type"] == "lot_open_below_pct"
    assert payload["current_pct"] == 10.0
    assert payload["fire_at_pct"] == 10
    assert payload["resolve_at_pct"] == 30
    assert payload["spaces_occupied"] == 1
    assert payload["spaces_vacant"] == 9
    assert payload["spaces_total"] == 10
    assert payload["camera_id"] == cam.id
    assert payload["camera_name"] == "Q"
