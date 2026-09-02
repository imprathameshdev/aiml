"""Tests for the alerting API (channel / rule / history / retry).

Covers:
  - permission gating (401 unauthenticated, 403 wrong permission).
  - channel CRUD with validation.
  - rule CRUD with condition + channel validation.
  - rule deletion with cascade behavior (force=true / 409 default).
  - channel deletion with reference check (409 if a rule references it).
  - alert list filters and retry endpoint.
"""

import json
import pytest
from backend.auth import hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- Permission gating ------------------------------------------------------

def test_list_channels_requires_auth(client):
    r = client.get("/api/alert-channels")
    assert r.status_code == 401


def test_list_channels_requires_manage_alerts_permission(client, db_session):
    import backend.models as models
    u = models.User(username="plain", password_hash=hash_password("x"), permissions=json.dumps([]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "plain", "password": "x"})
    token = r.json()["access_token"]
    r = client.get("/api/alert-channels", headers=_auth(token))
    assert r.status_code == 403
    assert "manage_alerts" in r.json()["detail"]


def test_list_rules_requires_manage_alerts(client, db_session):
    import backend.models as models
    u = models.User(username="plain2", password_hash=hash_password("x"), permissions=json.dumps(["view_history"]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "plain2", "password": "x"})
    token = r.json()["access_token"]
    r = client.get("/api/alert-rules", headers=_auth(token))
    assert r.status_code == 403


def test_list_alerts_history_open_to_any_authenticated_user(client, db_session):
    import backend.models as models
    u = models.User(username="viewer", password_hash=hash_password("x"), permissions=json.dumps(["view_history"]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "viewer", "password": "x"})
    token = r.json()["access_token"]
    r = client.get("/api/alerts", headers=_auth(token))
    assert r.status_code == 200


def test_alert_event_fired_at_serialized_with_utc_marker(client, auth_header, db_session):
    """Regression: ``fired_at`` must be serialized with a ``Z``
    suffix (or explicit offset) so the frontend's
    ``new Date(iso)`` reliably interprets it as UTC and
    ``toLocaleString()`` converts to the user's local time.

    The bug: SQLite strips timezone info on round-trip, so
    model attributes written as ``datetime.now(UTC)`` come
    back as naive datetimes.  Pydantic's default serialization
    then emits a naive ISO string
    (``2026-07-04T16:26:13.932529``), which the browser
    interprets as local time — so the displayed time was
    wrong by the user's UTC offset (or appeared as UTC if the
    browser happened to be in UTC).

    The fix adds a ``field_serializer`` on ``AlertEventOut``
    (and the other *Out schemas) that pins every datetime
    to UTC and emits the ``Z`` suffix.
    """
    import backend.models as models
    ch = models.AlertChannel(
        name="T", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    rule = models.AlertRule(
        name="T", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [1]}),
        channel_id=ch.id, enabled=True, consecutive_count=1,
    )
    db_session.add(rule)
    db_session.flush()
    # Insert an AlertEvent with a NAIVE datetime — this is
    # what the engine actually writes (datetime.now(UTC) is
    # timezone-aware in memory, but SQLite strips it).
    from datetime import datetime
    ev = models.AlertEvent(
        rule_id=rule.id,
        fired_at=datetime(2026, 7, 4, 16, 26, 13, 932529),  # naive
        status="sent",
        payload=json.dumps({"test": 1}),
        attempts=1,
        last_attempt_at=datetime(2026, 7, 4, 16, 26, 15),
        delivered_at=datetime(2026, 7, 4, 16, 26, 17),
    )
    db_session.add(ev)
    db_session.commit()

    r = client.get("/api/alerts", headers=auth_header)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    # The serialized datetime must end with 'Z' (or have an
    # explicit offset) so the browser reads it as UTC.
    assert rows[0]["fired_at"].endswith("Z"), (
        f"fired_at must have a Z suffix for correct timezone "
        f"interpolation; got {rows[0]['fired_at']!r}"
    )
    assert rows[0]["last_attempt_at"].endswith("Z")
    assert rows[0]["delivered_at"].endswith("Z")


# --- Channel CRUD -----------------------------------------------------------

def test_create_channel_webhook(client, auth_header):
    r = client.post(
        "/api/alert-channels",
        json={"name": "Slack", "channel_type": "webhook",
              "config": {"url": "https://hooks.example.com/x", "secret": "k"}},
        headers=auth_header,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Slack"
    assert body["channel_type"] == "webhook"
    assert body["created_by_username"] == "testadmin"


def test_create_channel_webhook_missing_url(client, auth_header):
    r = client.post(
        "/api/alert-channels",
        json={"name": "Slack", "channel_type": "webhook", "config": {}},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "url" in r.json()["detail"]


def test_create_channel_email_requires_to_list(client, auth_header):
    r = client.post(
        "/api/alert-channels",
        json={"name": "EmailOps", "channel_type": "email",
              "config": {"to": "ops@example.com"}},  # wrong shape: must be list
        headers=auth_header,
    )
    assert r.status_code == 400


def test_create_channel_email_with_recipients(client, auth_header):
    r = client.post(
        "/api/alert-channels",
        json={"name": "EmailOps", "channel_type": "email",
              "config": {"to": ["ops@example.com", "mgr@example.com"]}},
        headers=auth_header,
    )
    assert r.status_code == 201


def test_create_channel_unknown_type(client, auth_header):
    r = client.post(
        "/api/alert-channels",
        json={"name": "X", "channel_type": "telegram", "config": {}},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_create_channel_mqtt_requires_broker_and_topic(client, auth_header):
    """MQTT validation was missing (audit 3.2) — the placeholder
    comment said ``# mqtt shape validated in phase E when its
    handler lands`` and was never replaced when the handler
    landed.  These tests pin the fix.
    """
    # Missing broker
    r = client.post(
        "/api/alert-channels",
        json={"name": "M", "channel_type": "mqtt",
              "config": {"topic": "lotvulture/alerts"}},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "broker" in r.json()["detail"]
    # Missing topic
    r = client.post(
        "/api/alert-channels",
        json={"name": "M", "channel_type": "mqtt",
              "config": {"broker": "tcp://broker"}},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "topic" in r.json()["detail"]
    # Both present — accepted
    r = client.post(
        "/api/alert-channels",
        json={"name": "M", "channel_type": "mqtt",
              "config": {"broker": "tcp://broker", "topic": "lotvulture/alerts"}},
        headers=auth_header,
    )
    assert r.status_code == 201


def test_sqlite_foreign_keys_pragme_is_enabled():
    """Audit 2.2: PRAGMA foreign_keys must be ON on every new
    connection, otherwise the ``ondelete='CASCADE'`` declarations
    on ``CameraGroupMembership`` and friends are silently ignored
    and orphaned rows accumulate when a camera group is deleted.
    """
    from backend import database
    # Use a fresh connection so we see what the pragma-event
    # actually does at connect-time.
    from sqlalchemy import text
    with database.engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert result == 1, (
        f"PRAGMA foreign_keys returned {result}; expected 1 (ON). "
        f"Without it, SQLAlchemy's ondelete='CASCADE' is ignored and "
        f"camera_group_memberships accumulates orphans on group delete."
    )


def test_list_channels_returns_all(client, auth_header):
    client.post("/api/alert-channels",
                json={"name": "A", "channel_type": "webhook",
                      "config": {"url": "https://a"}}, headers=auth_header)
    client.post("/api/alert-channels",
                json={"name": "B", "channel_type": "webhook",
                      "config": {"url": "https://b"}}, headers=auth_header)
    r = client.get("/api/alert-channels", headers=auth_header)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_update_channel(client, auth_header):
    r = client.post("/api/alert-channels",
                    json={"name": "Old", "channel_type": "webhook",
                          "config": {"url": "https://a"}}, headers=auth_header)
    cid = r.json()["id"]
    r = client.put(f"/api/alert-channels/{cid}",
                   json={"name": "New"}, headers=auth_header)
    assert r.status_code == 200
    assert r.json()["name"] == "New"
    assert r.json()["last_edited_by_username"] == "testadmin"


def test_delete_channel_unreferenced(client, auth_header):
    r = client.post("/api/alert-channels",
                    json={"name": "Disposable", "channel_type": "webhook",
                          "config": {"url": "https://a"}}, headers=auth_header)
    cid = r.json()["id"]
    r = client.delete(f"/api/alert-channels/{cid}", headers=auth_header)
    assert r.status_code == 200


def test_delete_channel_referenced_by_rule_returns_409(client, auth_header):
    cr = client.post("/api/alert-channels",
                     json={"name": "Ref", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    client.post("/api/alert-rules",
                json={"name": "R1", "trigger_type": "space_occupied",
                      "condition": {"space_ids": [1]}, "channel_id": cid},
                headers=auth_header)
    r = client.delete(f"/api/alert-channels/{cid}", headers=auth_header)
    assert r.status_code == 409
    assert "rule" in r.json()["detail"].lower()


# --- Rule CRUD --------------------------------------------------------------

def test_create_rule_per_space(client, auth_header, db_session):
    # Need at least one space to validate condition.space_ids (we don't
    # require spaces to exist; we just check shape).
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    r = client.post(
        "/api/alert-rules",
        json={"name": "VIP lot",
              "trigger_type": "space_occupied",
              "condition": {"space_ids": [12, 13]},
              "channel_id": cid,
              "consecutive_count": 3,
              "cooldown_seconds": 300},
        headers=auth_header,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["consecutive_count"] == 3
    assert body["cooldown_seconds"] == 300
    assert body["created_by_username"] == "testadmin"


def test_create_rule_default_cooldown_180(client, auth_header):
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    r = client.post(
        "/api/alert-rules",
        json={"name": "Default cd",
              "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]},
              "channel_id": cid},
        headers=auth_header,
    )
    assert r.status_code == 201
    assert r.json()["cooldown_seconds"] == 180
    assert r.json()["consecutive_count"] == 1


def test_create_rule_invalid_condition(client, auth_header):
    r = client.post(
        "/api/alert-rules",
        json={"name": "Bad", "trigger_type": "space_occupied",
              "condition": {}},  # missing space_ids
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "space_ids" in r.json()["detail"]


def test_create_rule_unknown_trigger(client, auth_header):
    r = client.post(
        "/api/alert-rules",
        json={"name": "X", "trigger_type": "not_a_real_type",
              "condition": {"space_ids": [1]}},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_create_rule_channel_must_exist(client, auth_header):
    r = client.post(
        "/api/alert-rules",
        json={"name": "X", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": 99999},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_update_rule_can_change_consecutive_count(client, auth_header):
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    rr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": cid,
              "consecutive_count": 1},
        headers=auth_header,
    )
    rid = rr.json()["id"]
    r = client.put(f"/api/alert-rules/{rid}",
                   json={"consecutive_count": 5}, headers=auth_header)
    assert r.status_code == 200
    assert r.json()["consecutive_count"] == 5


def test_delete_rule_no_events_succeeds(client, auth_header):
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    rr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": cid},
        headers=auth_header,
    )
    rid = rr.json()["id"]
    r = client.delete(f"/api/alert-rules/{rid}", headers=auth_header)
    assert r.status_code == 200


def test_delete_rule_with_events_returns_409(client, auth_header, db_session):
    import backend.models as models
    import datetime
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    rr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": cid},
        headers=auth_header,
    )
    rid = rr.json()["id"]
    # Create a fired event so the rule has history.
    db_session.add(models.AlertEvent(
        rule_id=rid, fired_at=datetime.datetime.now(datetime.UTC),
        status="fired", payload="{}", attempts=0,
    ))
    db_session.commit()
    r = client.delete(f"/api/alert-rules/{rid}", headers=auth_header)
    assert r.status_code == 409
    # force=true cascades.
    r = client.delete(f"/api/alert-rules/{rid}?force=true", headers=auth_header)
    assert r.status_code == 200


# --- History list + retry ---------------------------------------------------

def test_list_alerts_filters_by_status_and_rule(client, auth_header, db_session):
    import backend.models as models
    import datetime
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    rr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": cid},
        headers=auth_header,
    )
    rid = rr.json()["id"]
    db_session.add(models.AlertEvent(
        rule_id=rid, fired_at=datetime.datetime.now(datetime.UTC),
        status="fired", payload="{}", attempts=0,
    ))
    db_session.add(models.AlertEvent(
        rule_id=rid, fired_at=datetime.datetime.now(datetime.UTC),
        status="sent", payload="{}", attempts=1, delivered_at=datetime.datetime.now(datetime.UTC),
    ))
    db_session.commit()

    r = client.get("/api/alerts", headers=auth_header)
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/api/alerts?status=fired", headers=auth_header)
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "fired"

    r = client.get(f"/api/alerts?rule_id={rid}", headers=auth_header)
    assert len(r.json()) == 2

    r = client.get("/api/alerts?rule_id=99999", headers=auth_header)
    assert r.json() == []


def test_retry_failed_alert_resets_status(client, auth_header, db_session):
    import backend.models as models
    import datetime
    cr = client.post("/api/alert-channels",
                     json={"name": "Hook", "channel_type": "webhook",
                           "config": {"url": "https://a"}}, headers=auth_header)
    cid = cr.json()["id"]
    rr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": cid},
        headers=auth_header,
    )
    rid = rr.json()["id"]
    ev = models.AlertEvent(
        rule_id=rid, fired_at=datetime.datetime.now(datetime.UTC),
        status="failed", payload="{}", attempts=1, last_error="500",
    )
    db_session.add(ev)
    db_session.commit()
    eid = ev.id

    r = client.post(f"/api/alerts/{eid}/retry", headers=auth_header)
    assert r.status_code == 200
    db_session.refresh(ev)
    assert ev.status == "fired"
    assert ev.last_error is None


def test_retry_without_permission_403(client, db_session):
    import backend.models as models
    import json
    u = models.User(username="noperm", password_hash=hash_password("x"), permissions=json.dumps([]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "noperm", "password": "x"})
    token = r.json()["access_token"]
    r = client.post("/api/alerts/1/retry", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
