"""End-to-end integration test for the email (SMTP) channel.

Same flow as the webhook test, but routes the alert through SMTP to
a local aiosmtpd server.  Verifies:

  - the SMTP socket is opened to the test server
  - the email envelope (from, to, subject, body) is correct
  - the body contains the JSON envelope
  - the per-channel ``subject_prefix`` and ``from_addr`` overrides
    take effect

The test does NOT exercise STARTTLS — that's covered by the unit
tests in ``tests/test_alert_channels_extra.py``.  The integration
test uses plain SMTP (``use_tls=false``) so aiosmtpd doesn't need a
cert chain.  See README.md for the TLS coverage rationale.
"""
import json
from email import message_from_bytes

import pytest

from backend import models
from conftest import wait_for_status


@pytest.mark.asyncio
async def test_smtp_happy_path(
    app_client, session, seed_alert, trigger_alert,
    smtp_capture_server, clear_smtp_settings,
):
    port, captured = smtp_capture_server

    # Configure the system-wide SMTP settings to point at the test
    # server.  The email channel reads these from the Setting table
    # on every send, so they have to be set in the test session
    # (which the patched SessionLocal makes visible to the channel).
    #
    # init_db.main() (called from the app's lifespan) already inserts
    # default rows for each alerting_smtp_* key, so we UPDATE in place
    # rather than INSERT (which would violate the UNIQUE constraint).
    smtp_settings = {
        "alerting_enabled": "true",
        "alerting_smtp_host": "127.0.0.1",
        "alerting_smtp_port": str(port),
        "alerting_smtp_user": "",
        "alerting_smtp_pass": "",
        "alerting_smtp_from": "alerts@lotvulture.local",
        "alerting_smtp_use_tls": "false",
    }
    for k, v in smtp_settings.items():
        row = session.query(models.Setting).filter_by(key=k).first()
        if row is None:
            session.add(models.Setting(key=k, value=v))
        else:
            row.value = v
    session.commit()

    ids = seed_alert(
        channel_type="email",
        config={
            "to": ["ops@example.com", "oncall@example.com"],
            "subject_prefix": "LotVulture [test]",
            "from_addr": "lotvulture-alerts@example.com",
        },
    )

    event_id = trigger_alert(ids["rule_id"], ids["space_id"], occupied=True)
    ev = await wait_for_status(session, event_id, {"sent"}, timeout=5.0)

    # --- AlertEvent bookkeeping ---
    assert ev.status == "sent"
    assert ev.attempts == 1
    assert ev.last_error is None
    assert ev.delivered_at is not None
    assert ev.next_retry_at is None

    # --- Captured email ---
    assert len(captured) == 1, f"expected 1 email, got {len(captured)}"
    msg_data = captured[0]
    assert msg_data["from"] == "lotvulture-alerts@example.com"
    # The channel's ``to`` is a JSON list, smtplib joins with ", "
    assert msg_data["to"] == ["ops@example.com", "oncall@example.com"]

    # Parse the raw RFC822 bytes with stdlib's email parser (aiosmtpd's
    # Message class is a subclass and lacks get_content()).
    msg = message_from_bytes(msg_data["data"])
    # Subject includes the per-channel subject_prefix
    assert msg["Subject"].startswith("LotVulture [test]:")
    # Walk the parts and find the text/plain body
    body = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break
    else:
        body = msg.get_payload()
    assert body is not None, f"no text/plain body in message; got: {msg.as_string()[:500]}"
    envelope = json.loads(body)
    assert envelope["event_id"] == event_id
    assert envelope["rule_id"] == ids["rule_id"]
    assert envelope["rule_name"].startswith("R-")
    assert envelope["trigger_type"] == "space_occupied"
    # The engine's enriched payload: edge, space, camera names.
    data = envelope["data"]
    assert data["edge"] == "became_occupied"
    assert data["space_id"] == ids["space_id"]
    assert data["space_name"].startswith("S-")
    assert data["space_state"] == "occupied"
    assert data["camera_id"] == ids["camera_id"]
    assert data["camera_name"].startswith("Cam-")
