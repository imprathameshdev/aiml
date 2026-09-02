"""Tests for the email and MQTT channels.

Email:
  - success path (SMTP send returns -> success=True).
  - missing recipients -> success=False.
  - missing SMTP host -> success=False with clear error.
  - alerting_enabled=false -> success=False.
  - body contains the JSON payload.

MQTT:
  - success path (publish completes -> success=True).
  - missing broker / topic -> success=False.
  - invalid broker URL -> success=False.
"""

import asyncio
import json
import smtplib
import threading
from email import message_from_bytes

import pytest

import pytest

from backend import models
from backend.alerts.channels import email as email_channel
from backend.alerts.channels import mqtt as mqtt_channel


# --- helpers ----------------------------------------------------------------

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
        s = db.query(models.Setting).filter_by(key=k).first()
        if s is None:
            s = models.Setting(key=k, value=v)
            db.add(s)
        else:
            s.value = v
    db.commit()


class _FakeSMTP:
    """Stand-in for smtplib.SMTP that records what was sent."""

    instances = []

    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = port
        self.sent_messages = []
        self.tls_started = False
        self.logged_in_as = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.tls_started = True

    def login(self, user, password):
        self.logged_in_as = (user, password)

    def send_message(self, msg):
        self.sent_messages.append(msg)


class _FakeSMTPRaising(_FakeSMTP):
    def send_message(self, msg):
        raise smtplib.SMTPRecipientsRefused({"to": (550, b"user unknown")})


# --- email tests ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_smtp_fake():
    _FakeSMTP.instances = []
    yield
    _FakeSMTP.instances = []


def test_email_success(monkeypatch, db_session):
    _set_settings(db_session)
    monkeypatch.setattr(email_channel.smtplib, "SMTP", _FakeSMTP)
    result = asyncio.run(email_channel.deliver(
        {"to": ["ops@example.com"]},
        {"event_id": 1, "rule_name": "Lot full", "data": {"pct": 92}},
    ))
    assert result.success is True
    assert len(_FakeSMTP.instances) == 1
    inst = _FakeSMTP.instances[0]
    assert inst.tls_started is True
    assert inst.logged_in_as == ("user", "pw")
    assert len(inst.sent_messages) == 1
    msg = inst.sent_messages[0]
    assert msg["To"] == "ops@example.com"
    assert msg["From"] == "from@example.com"
    assert "Lot full" in msg["Subject"]


def test_email_no_tls(monkeypatch, db_session):
    _set_settings(db_session, alerting_smtp_use_tls="false", alerting_smtp_port="25")
    monkeypatch.setattr(email_channel.smtplib, "SMTP", _FakeSMTP)
    result = asyncio.run(email_channel.deliver(
        {"to": ["ops@example.com"]},
        {"rule_name": "X"},
    ))
    assert result.success is True
    inst = _FakeSMTP.instances[0]
    assert inst.tls_started is False
    assert inst.port == 25


def test_email_no_recipients(db_session):
    _set_settings(db_session)
    result = asyncio.run(email_channel.deliver({}, {"rule_name": "X"}))
    assert result.success is False
    assert "recipients" in (result.error or "")


def test_email_no_host(db_session):
    _set_settings(db_session, alerting_smtp_host="")
    result = asyncio.run(email_channel.deliver(
        {"to": ["x@y"]}, {"rule_name": "X"},
    ))
    assert result.success is False
    assert "host" in (result.error or "")


def test_email_alerting_disabled(db_session):
    _set_settings(db_session, alerting_enabled="false")
    result = asyncio.run(email_channel.deliver(
        {"to": ["x@y"]}, {"rule_name": "X"},
    ))
    assert result.success is False
    assert "disabled" in (result.error or "")


def test_email_smtp_failure(monkeypatch, db_session):
    _set_settings(db_session)
    monkeypatch.setattr(email_channel.smtplib, "SMTP", _FakeSMTPRaising)
    result = asyncio.run(email_channel.deliver(
        {"to": ["ops@example.com"]},
        {"rule_name": "X"},
    ))
    assert result.success is False
    assert "smtp" in (result.error or "")


def test_email_body_contains_payload(monkeypatch, db_session):
    _set_settings(db_session)
    monkeypatch.setattr(email_channel.smtplib, "SMTP", _FakeSMTP)
    payload = {"event_id": 42, "rule_name": "X", "data": {"a": 1}}
    asyncio.run(email_channel.deliver({"to": ["x@y"]}, payload))
    body = _FakeSMTP.instances[0].sent_messages[0].get_content()
    assert json.loads(body) == payload


# --- MQTT tests -------------------------------------------------------------

def _split_broker(url):
    return mqtt_channel._split_broker(url)


def test_mqtt_split_broker_tcp_default():
    host, port, tls = _split_broker("tcp://broker:1884")
    assert host == "broker"
    assert port == 1884
    assert tls is False


def test_mqtt_split_broker_bare_hostname():
    host, port, tls = _split_broker("broker.example.com")
    assert host == "broker.example.com"
    assert port == 1883
    assert tls is False


def test_mqtt_split_broker_mqtts_default():
    host, port, tls = _split_broker("mqtts://broker")
    assert host == "broker"
    assert port == 8883
    assert tls is True


def test_mqtt_missing_broker():
    result = asyncio.run(mqtt_channel.deliver(
        {"topic": "x"}, {"rule_name": "X"},
    ))
    assert result.success is False
    assert "broker" in (result.error or "")


def test_mqtt_missing_topic():
    result = asyncio.run(mqtt_channel.deliver(
        {"broker": "tcp://broker"}, {"rule_name": "X"},
    ))
    assert result.success is False
    assert "topic" in (result.error or "")


def test_mqtt_invalid_broker_url():
    result = asyncio.run(mqtt_channel.deliver(
        {"broker": "tcp://", "topic": "x"}, {"rule_name": "X"},
    ))
    assert result.success is False


def test_mqtt_publish_calls_paho(monkeypatch):
    """Smoke test that publish delegates to paho.mqtt correctly.

    We replace the sync _publish_sync with a recorder so we can assert
    the call shape without actually opening a network connection.
    """
    captured = {}

    def fake_publish(broker, port, username, password, topic,
                     payload_bytes, qos, client_id, use_tls, timeout):
        captured["broker"] = broker
        captured["port"] = port
        captured["username"] = username
        captured["password"] = password
        captured["topic"] = topic
        captured["payload"] = payload_bytes
        captured["qos"] = qos
        captured["client_id"] = client_id
        captured["use_tls"] = use_tls

    monkeypatch.setattr(mqtt_channel, "_publish_sync", fake_publish)

    payload = {"event_id": 1, "rule_name": "X", "data": {"y": 1}}
    result = asyncio.run(mqtt_channel.deliver(
        {"broker": "tcp://broker:1884",
         "topic": "lotvulture/alerts",
         "username": "u", "password": "p",
         "qos": 2, "client_id": "tester"},
        payload,
    ))
    assert result.success is True
    assert captured["broker"] == "broker"
    assert captured["port"] == 1884
    assert captured["topic"] == "lotvulture/alerts"
    assert captured["qos"] == 2
    assert captured["client_id"] == "tester"
    assert json.loads(captured["payload"].decode("utf-8")) == payload


def test_mqtt_publish_raises_recorded_as_failure(monkeypatch):
    def fake_publish(*a, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(mqtt_channel, "_publish_sync", fake_publish)
    result = asyncio.run(mqtt_channel.deliver(
        {"broker": "tcp://broker", "topic": "x"}, {"rule_name": "X"},
    ))
    assert result.success is False
    assert "mqtt" in (result.error or "")


def test_mqtt_publish_sync_does_not_call_asyncio_get_event_loop(monkeypatch):
    """Regression guard for the "no current event loop in ThreadPoolExecutor" error.

    paho-mqtt's ``loop_start()`` runs its own network thread, so the
    publish function is safe in a thread pool.  Calling
    ``asyncio.get_event_loop()`` from there raises "There is no
    current event loop" in Python 3.12+.  Pin that the source does
    not actually call asyncio at the publish-site.
    """
    import re
    import inspect
    from backend.alerts.channels import mqtt as mqtt_mod
    src = inspect.getsource(mqtt_mod._publish_sync)
    # Strip the docstring so comments mentioning asyncio don't trip us.
    src_no_doc = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
    assert (
        "asyncio" not in src_no_doc
    ), "_publish_sync must not use asyncio — it runs in a thread pool executor"
