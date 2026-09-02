# Integration tests — alerting end-to-end

This suite exercises the full alerting pipeline (engine → dispatcher
poll → channel handler → real wire protocol) for the three channel
types: **webhook**, **email (SMTP)**, and **MQTT**. A fourth test
covers the failure → retry → terminal path.

The suite lives **outside** the main app's test directory and has its
own `requirements.txt`. The main app's test env doesn't need
`aiosmtpd` or `amqtt`; this suite does. This keeps the main test
suite fast and the production `requirements.txt` minimal.

## Running

```bash
cd integration_tests
pip install -r requirements.txt
pytest -v
```

The full suite takes ~30s (most of which is the retry test's four
attempt cycles).

## What's covered

| Test | Channel | Coverage |
|---|---|---|
| `test_alert_e2e_webhook.py`           | webhook   | Engine creates per-space event → dispatcher polls → real HTTP POST to local aiohttp server. Verifies envelope shape, HMAC signature, `delivered_at` timestamp, **rich payload** (edge direction, space + camera names/IDs). |
| `test_alert_e2e_smtp.py`              | email     | Same flow → real SMTP send to local aiosmtpd server. Verifies `from`/`to`/subject/body + rich payload. |
| `test_alert_e2e_mqtt.py`              | mqtt      | Same flow → real MQTT publish to local amqtt broker. Verifies topic + payload via paho subscribe + rich payload. |
| `test_alert_e2e_lot_utilization.py`   | webhook   | Threshold (lot utilization) rule — computes current occupancy %, fires when crossing the threshold, verifies `current_pct`/`spaces_occupied`/`spaces_vacant`/`spaces_total`/camera name in payload. Negative case (below threshold) does NOT fire. |
| `test_alert_e2e_camera_offline.py`    | webhook   | Camera-offline rule — fires when last `CameraScan` is older than `minutes_offline`, verifies `camera_name`/`minutes_offline`/`last_scan_at`/`gap_seconds` in payload. |
| `test_alert_e2e_retry.py`             | webhook   | Server returns 500. Verifies `attempts`, `next_retry_at` (60s/300s/1800s/terminal), retry suppression until time passes. |

## What's NOT covered (intentionally)

- **TLS for SMTP/MQTT** — the unit tests in `tests/test_alert_channels_extra.py`
  already cover the channel-level TLS code path. Wrapping aiosmtpd / amqtt
  with self-signed certs adds CI complexity for limited additional coverage.
- **Permission gating** — covered by `tests/test_alert_api.py`.
- **Engine state machine** (per-space hysteresis, threshold rules) —
  covered by `tests/test_alert_hysteresis.py` and
  `tests/test_alert_engine.py`.

## Runtime model

- The app is started **in-process** via `fastapi.testclient.TestClient`.
  This triggers the app's lifespan, which starts the engine and
  dispatcher background tasks on the test's event loop.
- `database.SessionLocal` is patched to return the conftest's
  in-memory SQLite session, so the engine and dispatcher see the
  same rows the test inserts.
- Capture servers (aiohttp, aiosmtpd Controller, amqtt Broker) run
  in the same event loop as async tasks.
- The dispatcher polls every 2s; tests wait up to 5s for delivery
  and use `asyncio.sleep(0.1)` to yield to the event loop.

## CI integration

Add as a separate CI step AFTER the main test suite:

```yaml
- name: Run alerting integration tests
  run: |
    cd integration_tests
    pip install -r requirements.txt
    pytest -v
```

The integration tests are NOT run by the main `pytest tests/`
invocation. They must be run from this directory.
