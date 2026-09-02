"""Tests for the channel-test and rule-test-fire endpoints.

Both endpoints let an operator verify that the delivery chain works
without waiting for a real event.  The channel test sends a synthetic
payload through the channel's handler and returns the result.  The
rule test-fire creates a real ``alert_event`` row and runs it through
the same path a normal delivery would take.
"""

import json
import pytest
from backend.auth import hash_password


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _set_settings(db, **kw):
    """Set alerting_smtp_* / alerting_enabled keys in the Setting table."""
    defaults = {
        "alerting_enabled": "true",
        "alerting_smtp_host": "smtp.example.com",
        "alerting_smtp_port": "587",
        "alerting_smtp_user": "user",
        "alerting_smtp_pass": "pw",
        "alerting_smtp_from": "from@example.com",
        "alerting_smtp_use_tls": "true",
    }
    defaults.update(kw)
    for k, v in defaults.items():
        s = db.query(__import__("backend").models.Setting).filter_by(key=k).first()
        if s is None:
            s = __import__("backend").models.Setting(key=k, value=v)
            db.add(s)
        else:
            s.value = v
    db.commit()


def _make_channel(client, auth_header, **overrides):
    body = {
        "name": "Test hook",
        "channel_type": "webhook",
        "config": {"url": "https://example.com/hook"},
    }
    body.update(overrides)
    r = client.post("/api/alert-channels", json=body, headers=auth_header)
    assert r.status_code == 201
    return r.json()


# --- /test endpoint --------------------------------------------------------

def test_test_channel_requires_manage_alerts(client, db_session):
    """No manage_alerts -> 403 on the test endpoint."""
    import backend.models as models
    u = models.User(username="x", password_hash=hash_password("x"), permissions=json.dumps([]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "x", "password": "x"})
    token = r.json()["access_token"]
    r = client.post("/api/alert-channels/1/test", headers=_auth(token))
    assert r.status_code == 403


def test_test_channel_not_found(client, auth_header):
    r = client.post("/api/alert-channels/99999/test", headers=auth_header)
    assert r.status_code == 404


def test_test_channel_disabled_rejected(client, auth_header, db_session):
    """Disabled channels are rejected so the operator doesn't think
    a real fire would work when the channel is off."""
    import backend.models as models
    ch = models.AlertChannel(
        name="Off", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=False,
    )
    db_session.add(ch)
    db_session.commit()
    r = client.post(f"/api/alert-channels/{ch.id}/test", headers=auth_header)
    assert r.status_code == 400
    assert "disabled" in r.json()["detail"]


def test_test_channel_success_path(monkeypatch, client, auth_header):
    """A 2xx response from the destination is reported as success."""
    ch = _make_channel(client, auth_header)

    from backend.alerts.channels.webhook import WebhookResult
    async def fake_ok(config, payload):
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_ok)

    r = client.post(f"/api/alert-channels/{ch['id']}/test", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status_code"] == 200
    assert "latency_ms" in body


def test_test_channel_failure_path(monkeypatch, client, auth_header):
    """A 5xx response from the destination is reported as failure
    with the error detail."""
    ch = _make_channel(client, auth_header)
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_fail(config, payload):
        return WebhookResult(success=False, status_code=500, error="http 500: boom")
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_fail)

    r = client.post(f"/api/alert-channels/{ch['id']}/test", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "boom" in (body.get("error") or "")


def test_test_channel_payload_includes_test_marker(monkeypatch, client, auth_header):
    """The test payload carries ``_test: True`` so receivers can
    distinguish probes from real fires."""
    ch = _make_channel(client, auth_header)
    captured = {}
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_capture(config, payload):
        captured["data"] = payload.get("data", {})
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_capture)

    client.post(f"/api/alert-channels/{ch['id']}/test", headers=auth_header)
    assert captured["data"].get("_test") is True
    assert "channel_name" in captured["data"]


def test_test_channel_unknown_type(client, auth_header, db_session):
    """A channel with an unknown type is rejected (defense in depth —
    the type is validated on create, but the test endpoint is a
    separate code path)."""
    import backend.models as models
    ch = models.AlertChannel(
        name="Bogus", channel_type="telegram",
        config=json.dumps({}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    r = client.post(f"/api/alert-channels/{ch.id}/test", headers=auth_header)
    assert r.status_code == 400


# --- /test-config endpoint (probe an unsaved config) -----------------------

def test_test_config_requires_manage_alerts(client, db_session):
    import backend.models as models
    u = models.User(username="x", password_hash=hash_password("x"), permissions=json.dumps([]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "x", "password": "x"})
    token = r.json()["access_token"]
    r = client.post(
        "/api/alert-channels/test-config",
        json={"channel_type": "webhook", "config": {"url": "https://x"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_test_config_requires_channel_type(client, auth_header):
    r = client.post(
        "/api/alert-channels/test-config",
        json={"config": {}},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "channel_type" in r.json()["detail"]


def test_test_config_unknown_type_rejected(client, auth_header):
    r = client.post(
        "/api/alert-channels/test-config",
        json={"channel_type": "telegram", "config": {}},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_test_config_probes_without_persisting(monkeypatch, client, auth_header, db_session):
    """A probe through /test-config must not create an alert_channel row."""
    initial_count = db_session.query(__import__("backend").models.AlertChannel).count()

    from backend.alerts.channels.webhook import WebhookResult
    async def fake_ok(config, payload):
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_ok)

    r = client.post(
        "/api/alert-channels/test-config",
        json={"name": "Probe", "channel_type": "webhook",
              "config": {"url": "https://example.com/hook"}},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    # No new row was created.
    after_count = db_session.query(__import__("backend").models.AlertChannel).count()
    assert after_count == initial_count


def test_test_config_failure_path(monkeypatch, client, auth_header):
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_fail(config, payload):
        return WebhookResult(success=False, status_code=500, error="boom")
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_fail)

    r = client.post(
        "/api/alert-channels/test-config",
        json={"channel_type": "webhook", "config": {"url": "https://x"}},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "boom" in (body.get("error") or "")


# --- email subject_prefix + from_addr ---------------------------------------

def test_email_channel_honors_subject_prefix(monkeypatch, db_session):
    """A subject_prefix in the channel config is used in the email subject."""
    from backend.alerts.channels import email as email_channel
    _set_settings(db_session, alerting_smtp_from="global@example.com")
    captured = {}

    def fake_send(host, port, user, password, from_addr, to_addrs, subject, body, use_tls):
        captured["subject"] = subject
        captured["from_addr"] = from_addr

    monkeypatch.setattr(email_channel, "_send_sync", fake_send)
    import asyncio
    asyncio.run(email_channel.deliver(
        {"to": ["ops@example.com"], "subject_prefix": "[ALERT-NYC]"},
        {"rule_name": "Lot full"},
    ))
    assert captured["subject"].startswith("[ALERT-NYC]:")


def test_email_channel_from_addr_override(monkeypatch, db_session):
    """from_addr in the channel config overrides the global SMTP from."""
    from backend.alerts.channels import email as email_channel
    _set_settings(db_session, alerting_smtp_from="global@example.com")
    captured = {}

    def fake_send(host, port, user, password, from_addr, to_addrs, subject, body, use_tls):
        captured["from_addr"] = from_addr

    monkeypatch.setattr(email_channel, "_send_sync", fake_send)
    import asyncio
    asyncio.run(email_channel.deliver(
        {"to": ["ops@example.com"], "from_addr": "custom@example.com"},
        {"rule_name": "X"},
    ))
    assert captured["from_addr"] == "custom@example.com"


def test_email_channel_falls_back_to_global_from(monkeypatch, db_session):
    """If no from_addr is set in config, the global SMTP from is used."""
    from backend.alerts.channels import email as email_channel
    _set_settings(db_session, alerting_smtp_from="global@example.com")
    captured = {}

    def fake_send(host, port, user, password, from_addr, to_addrs, subject, body, use_tls):
        captured["from_addr"] = from_addr

    monkeypatch.setattr(email_channel, "_send_sync", fake_send)
    import asyncio
    asyncio.run(email_channel.deliver(
        {"to": ["ops@example.com"]},
        {"rule_name": "X"},
    ))
    assert captured["from_addr"] == "global@example.com"


# --- end-to-end: create + test ---------------------------------------------

def test_create_then_test_email_channel_preserves_recipients(
    monkeypatch, client, auth_header, db_session
):
    """Reproduces the "always fails with no recipients" bug.

    The full path: create an email channel with a ``to`` field via
    the API, then test it.  The test must succeed (or fail with an
    SMTP error, not "no recipients").  If the saved config somehow
    drops ``to``, the test fires the "no recipients" error instead
    of the SMTP success/failure.
    """
    import backend.models as models
    # Enable alerting and set SMTP creds (defaults leave it disabled).
    _set_settings(db_session)
    # Create a channel with one recipient.
    r = client.post(
        "/api/alert-channels",
        json={"name": "Ops", "channel_type": "email",
              "config": {"to": ["ops@example.com"]}},
        headers=auth_header,
    )
    assert r.status_code == 201
    cid = r.json()["id"]

    # Verify the saved config has the recipient.
    ch = db_session.query(models.AlertChannel).filter_by(id=cid).first()
    assert json.loads(ch.config)["to"] == ["ops@example.com"]

    # Mock SMTP and test the channel.
    from backend.alerts.channels import email as email_channel
    captured = {}

    def fake_send(host, port, user, password, from_addr, to_addrs, subject, body, use_tls):
        captured["to"] = to_addrs

    monkeypatch.setattr(email_channel, "_send_sync", fake_send)
    r = client.post(f"/api/alert-channels/{cid}/test", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    # The test must report success (mock returned success) — and
    # crucially, the SMTP layer must have received the recipient.
    assert body["success"] is True, f"test failed: {body}"
    assert captured.get("to") == ["ops@example.com"]


def test_test_config_email_with_recipients_succeeds(
    monkeypatch, client, auth_header, db_session
):
    """Same path for the unsaved test endpoint."""
    from backend.alerts.channels import email as email_channel
    _set_settings(db_session)
    captured = {}

    def fake_send(host, port, user, password, from_addr, to_addrs, subject, body, use_tls):
        captured["to"] = to_addrs

    monkeypatch.setattr(email_channel, "_send_sync", fake_send)
    r = client.post(
        "/api/alert-channels/test-config",
        json={"name": "Probe", "channel_type": "email",
              "config": {"to": ["ops@example.com"]}},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert captured.get("to") == ["ops@example.com"]


def test_test_config_email_without_recipients_reports_clear_error(
    client, auth_header
):
    """If the operator tests an email channel with no recipients,
    the error message must clearly say so — not something cryptic."""
    r = client.post(
        "/api/alert-channels/test-config",
        json={"name": "Empty", "channel_type": "email",
              "config": {"to": []}},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "recipients" in (body.get("error") or "").lower()


# --- /test-fire endpoint ----------------------------------------------------

def test_test_fire_requires_manage_alerts(client, db_session):
    import backend.models as models
    u = models.User(username="x", password_hash=hash_password("x"), permissions=json.dumps([]))
    db_session.add(u)
    db_session.commit()
    r = client.post("/api/login", json={"username": "x", "password": "x"})
    token = r.json()["access_token"]
    r = client.post("/api/alert-rules/1/test-fire", headers=_auth(token))
    assert r.status_code == 403


def test_test_fire_not_found(client, auth_header):
    r = client.post("/api/alert-rules/99999/test-fire", headers=auth_header)
    assert r.status_code == 404


def test_test_fire_creates_event_with_status(monkeypatch, client, auth_header, db_session):
    """A successful test fire writes an alert_event with status='sent'."""
    ch = _make_channel(client, auth_header)
    cr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": ch["id"]},
        headers=auth_header,
    )
    rule_id = cr.json()["id"]

    from backend.alerts.channels.webhook import WebhookResult
    async def fake_ok(config, payload):
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_ok)

    r = client.post(f"/api/alert-rules/{rule_id}/test-fire", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["event_id"] is not None
    # Verify the event row was written.
    ev = db_session.query(__import__("backend").models.AlertEvent).filter_by(
        id=body["event_id"]
    ).first()
    assert ev is not None
    assert ev.status == "sent"
    assert ev.attempts == 1


def test_test_fire_failure_writes_failed_event(monkeypatch, client, auth_header, db_session):
    ch = _make_channel(client, auth_header)
    cr = client.post(
        "/api/alert-rules",
        json={"name": "R", "trigger_type": "space_occupied",
              "condition": {"space_ids": [1]}, "channel_id": ch["id"]},
        headers=auth_header,
    )
    rule_id = cr.json()["id"]
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_fail(config, payload):
        return WebhookResult(success=False, error="connection refused")
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_fail)
    r = client.post(f"/api/alert-rules/{rule_id}/test-fire", headers=auth_header)
    body = r.json()
    assert body["success"] is False
    ev = db_session.query(__import__("backend").models.AlertEvent).filter_by(
        id=body["event_id"]
    ).first()
    assert ev.status == "failed"
    assert "refused" in (ev.last_error or "")


def test_test_fire_rejects_disabled_rule(client, auth_header, db_session):
    ch = _make_channel(client, auth_header)
    import backend.models as models
    rule = models.AlertRule(
        name="Off", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [1]}), channel_id=ch["id"],
        enabled=False, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()
    r = client.post(f"/api/alert-rules/{rule.id}/test-fire", headers=auth_header)
    assert r.status_code == 400


def test_test_fire_rejects_rule_without_channel(client, auth_header, db_session):
    import backend.models as models
    rule = models.AlertRule(
        name="Orphan", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [1]}), channel_id=None,
        enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()
    r = client.post(f"/api/alert-rules/{rule.id}/test-fire", headers=auth_header)
    assert r.status_code == 400
    assert "channel" in r.json()["detail"].lower()


# --- Test-fire payload carries rich context --------------------------------
# The "Test Fire" button on the rule form synthesizes a payload that
# mimics a real engine fire, so the operator can verify the receiver
# handles the rich fields (space name, camera name, etc.) correctly.
# These tests pin the contract.

def test_test_fire_per_space_payload_includes_space_and_camera(
    monkeypatch, client, auth_header, db_session
):
    """For a per-space rule, the synthesized payload includes the
    space and camera's human-readable names so the test fire looks
    like a real one to the receiver."""
    import backend.models as models
    cam = models.Camera(name="East gate", snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.flush()
    sp = models.Space(camera_id=cam.id, name="A-1", points="[]")
    db_session.add(sp)
    db_session.commit()
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="VIP occupied", trigger_type="space_occupied",
        condition=json.dumps({"space_ids": [sp.id]}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    captured = {}
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_capture(config, payload):
        captured["data"] = payload.get("data", {})
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_capture)

    r = client.post(f"/api/alert-rules/{rule.id}/test-fire", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["success"] is True
    data = captured["data"]
    assert data["_test"] is True
    assert data["trigger_type"] == "space_occupied"
    assert data["edge"] == "became_occupied"
    assert data["space_id"] == sp.id
    assert data["space_name"] == "A-1"
    assert data["space_state"] == "occupied"
    assert data["camera_id"] == cam.id
    assert data["camera_name"] == "East gate"


def test_test_fire_threshold_payload_includes_scope(
    monkeypatch, client, auth_header, db_session
):
    """For a lot_utilization rule, the synthesized payload includes
    the scope (camera or group) and the configured thresholds."""
    import backend.models as models
    cam = models.Camera(name="South", snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.commit()
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="South full", trigger_type="lot_full_above_pct",
        condition=json.dumps({
            "camera_id": cam.id, "fire_at_pct": 90, "resolve_at_pct": 70,
        }),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    captured = {}
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_capture(config, payload):
        captured["data"] = payload.get("data", {})
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_capture)

    r = client.post(f"/api/alert-rules/{rule.id}/test-fire", headers=auth_header)
    assert r.status_code == 200
    data = captured["data"]
    assert data["_test"] is True
    assert data["trigger_type"] == "lot_full_above_pct"
    assert data["fire_at_pct"] == 90
    assert data["resolve_at_pct"] == 70
    assert data["camera_id"] == cam.id
    assert data["camera_name"] == "South"


def test_test_fire_camera_offline_payload_includes_camera_and_minutes(
    monkeypatch, client, auth_header, db_session
):
    """For a camera_offline rule, the synthesized payload includes
    the camera name and the configured minutes_offline threshold."""
    import backend.models as models
    cam = models.Camera(name="West", snapshot_url="http://x", is_enabled=True)
    db_session.add(cam)
    db_session.commit()
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="West offline", trigger_type="camera_offline",
        condition=json.dumps({"camera_id": cam.id, "minutes_offline": 5}),
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    captured = {}
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_capture(config, payload):
        captured["data"] = payload.get("data", {})
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_capture)

    r = client.post(f"/api/alert-rules/{rule.id}/test-fire", headers=auth_header)
    assert r.status_code == 200
    data = captured["data"]
    assert data["_test"] is True
    assert data["trigger_type"] == "camera_offline"
    assert data["camera_id"] == cam.id
    assert data["camera_name"] == "West"
    assert data["minutes_offline"] == 5


def test_test_fire_camera_offline_any_camera_marker(
    monkeypatch, client, auth_header, db_session
):
    """An "any camera" offline rule (no camera_id) reports
    camera_name="(any camera)" so the receiver can render the
    non-specific scope."""
    import backend.models as models
    ch = models.AlertChannel(
        name="Hook", channel_type="webhook",
        config=json.dumps({"url": "https://x"}), enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    rule = models.AlertRule(
        name="Any offline", trigger_type="camera_offline",
        condition=json.dumps({"minutes_offline": 5}),  # no camera_id
        channel_id=ch.id, enabled=True,
        consecutive_count=1, cooldown_seconds=0,
    )
    db_session.add(rule)
    db_session.commit()

    captured = {}
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_capture(config, payload):
        captured["data"] = payload.get("data", {})
        return WebhookResult(success=True, status_code=200)
    from backend.alerts import dispatcher as _d
    monkeypatch.setitem(_d.CHANNEL_DISPATCH, "webhook", fake_capture)

    r = client.post(f"/api/alert-rules/{rule.id}/test-fire", headers=auth_header)
    assert r.status_code == 200
    data = captured["data"]
    assert data["trigger_type"] == "camera_offline"
    assert data["camera_id"] is None
    assert data["camera_name"] == "(any camera)"
