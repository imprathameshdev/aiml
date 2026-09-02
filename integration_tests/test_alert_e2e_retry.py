"""End-to-end integration test for the failure → retry → terminal path.

The dispatcher's backoff schedule is ``(60, 300, 1800, 7200)``
seconds with ``MAX_ATTEMPTS=4``.  Each failed delivery sets
``AlertEvent.next_retry_at`` to ``now + BACKOFF[attempts-1]``.  After
4 failed attempts the event is terminal — ``next_retry_at`` is
``None`` and the dispatcher stops claiming it.

This test points the webhook at a server that **always returns
500** and walks the full retry path:

  1. First dispatch: server returns 500 → event.status='failed',
     attempts=1, next_retry_at ≈ now + 60s
  2. Confirm retry suppression: even after waiting past the
     dispatcher's poll interval, attempts is still 1
  3. Fast-forward time by manually clearing next_retry_at → second
     attempt fails → attempts=2, next_retry_at ≈ now + 300s
  4. Repeat for attempts=3 (backoff 1800s) and attempts=4 (backoff
     7200s)
  5. After attempts=4, next_retry_at is None and the event is
     terminal — a subsequent dispatch tick does not retry

We never wait the real backoff (60s+ between attempts) — we
clear ``next_retry_at`` to simulate time passing.  The production
behavior is verified by the unit tests in
``tests/test_alert_retries.py``; the integration test verifies
the dispatcher actually walks the path through the real poll loop.
"""
import asyncio
import datetime

import pytest

from backend import models
from conftest import wait_for_status


@pytest.mark.asyncio
async def test_retry_path_terminal_after_max_attempts(
    app_client, session, seed_alert, trigger_alert, webhook_capture_server
):
    port, captured, server = webhook_capture_server
    # Always return 500.  The handler still records the request, so
    # we can verify the dispatcher actually attempted delivery.
    server.set_response(500, b"simulated server failure")

    ids = seed_alert(
        channel_type="webhook",
        config={"url": f"http://127.0.0.1:{port}/hook"},
    )

    # ----- Attempt 1 -----
    event_id = trigger_alert(ids["rule_id"], ids["space_id"], occupied=True)
    ev = await wait_for_status(session, event_id, {"failed"}, timeout=5.0)
    session.expire_all()
    ev = session.query(models.AlertEvent).filter_by(id=event_id).first()
    assert ev.status == "failed"
    assert ev.attempts == 1
    assert ev.last_error is not None and "500" in ev.last_error
    # next_retry_at is in the future (60s away — the first backoff bucket)
    assert ev.next_retry_at is not None

    # ----- Retry suppression: a dispatcher tick right now must NOT
    # pick up the event because next_retry_at > now.  Wait one
    # dispatch poll cycle (POLL_INTERVAL_SECONDS = 2) and re-check.
    await asyncio.sleep(3.0)
    session.expire_all()
    ev = session.query(models.AlertEvent).filter_by(id=event_id).first()
    assert ev.attempts == 1, (
        f"Dispatcher retried before next_retry_at; "
        f"attempts={ev.attempts}, next_retry_at={ev.next_retry_at}"
    )

    # ----- Helper: clear next_retry_at to simulate the backoff
    # timer expiring, then wait for the next attempt to fail.  We
    # track ``previous_attempts`` so we can detect a fresh attempt
    # by ``e.attempts > previous_attempts``.
    previous_attempts = 1

    async def _simulate_retry():
        nonlocal previous_attempts
        session.expire_all()
        ev = session.query(models.AlertEvent).filter_by(id=event_id).first()
        # Clear next_retry_at so the dispatcher picks up the event
        # on its next 2s poll.  Use a UTC-naive datetime (the
        # column is stored as naive UTC in SQLite).
        ev.next_retry_at = (
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            - datetime.timedelta(seconds=1)
        )
        session.commit()
        # Wait for the next dispatcher tick (poll interval is 2s)
        # and for the channel handler to run.  Watch for attempts to
        # increase — that's the unambiguous signal of a fresh retry.
        deadline = asyncio.get_event_loop().time() + 6.0
        while asyncio.get_event_loop().time() < deadline:
            session.expire_all()
            e = session.query(models.AlertEvent).filter_by(id=event_id).first()
            if e.attempts > previous_attempts:
                previous_attempts = e.attempts
                return e
            await asyncio.sleep(0.1)
        raise AssertionError(
            f"Dispatcher did not retry within 6s; previous_attempts={previous_attempts}"
        )

    # ----- Attempts 2, 3, 4 -----
    expected_backoff_buckets = [300, 1800, 7200]  # in seconds
    for expected_attempt, expected_backoff in zip([2, 3, 4], expected_backoff_buckets):
        ev = await _simulate_retry()
        assert ev.status == "failed", f"attempt {expected_attempt}: status={ev.status}"
        assert ev.attempts == expected_attempt, (
            f"expected attempts={expected_attempt}, got {ev.attempts}"
        )
        assert ev.last_error is not None and "500" in ev.last_error
        # next_retry_at is set to the expected backoff bucket
        # (or None for the terminal case at attempts=4)
        if expected_attempt < 4:
            assert ev.next_retry_at is not None, (
                f"attempt {expected_attempt}: next_retry_at should be set, not None"
            )
            # The backoff bucket should be within ±10s of the expected
            # value (we just cleared next_retry_at to "now - 1s",
            # so the dispatcher set it to "now + backoff" which is
            # approximately the expected value).
            now_naive = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            seconds_until = (ev.next_retry_at - now_naive).total_seconds()
            assert abs(seconds_until - expected_backoff) < 10, (
                f"attempt {expected_attempt}: backoff {seconds_until}s "
                f"not within ±10s of {expected_backoff}s"
            )
        else:
            # attempts=4 is the terminal state — next_retry_at is None.
            assert ev.next_retry_at is None, (
                f"attempt 4 should be terminal; next_retry_at={ev.next_retry_at}"
            )

    # ----- Final state: terminal, no more retries even after
    # waiting past the next poll cycle.
    # The dispatcher should NOT claim this event again.
    assert len(captured) == 4, (
        f"expected 4 delivery attempts (1 per retry), got {len(captured)}"
    )
    # Wait past the poll interval and verify attempts is still 4.
    await asyncio.sleep(3.0)
    session.expire_all()
    ev = session.query(models.AlertEvent).filter_by(id=event_id).first()
    assert ev.attempts == 4, (
        f"terminal event was retried; attempts={ev.attempts}"
    )
    assert ev.next_retry_at is None
    assert ev.status == "failed"
