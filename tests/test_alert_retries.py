"""Tests for the dispatcher's retry + backoff behavior.

Covers:
  - After a failure, the event is scheduled with a backoff interval.
  - The dispatcher skips events whose next_retry_at is in the future.
  - The dispatcher picks up events once next_retry_at has passed.
  - After MAX_ATTEMPTS failures, the event is terminal (no next_retry_at).
  - Manual retry resets status='fired' but counts toward the budget.
  - Manual retry returns 409 when attempts >= MAX_ATTEMPTS.
  - The claim query is correct (filters out future-retry events).
"""

import asyncio
import asyncio
import datetime
import json
import pytest

from backend import models
from backend.alerts import dispatcher as alerts_dispatcher


def _deliver(db, event):
    """Synchronous wrapper for tests.

    ``_deliver_one`` is async (the old sync version hit
    ``RuntimeError: this event loop is already running`` when called
    from a running loop in production).  Tests run outside a loop,
    so we use ``asyncio.run`` to drive the coroutine.
    """
    return asyncio.run(alerts_dispatcher._deliver_one(db, event))


def _make_event(db, *, rule, status="fired", attempts=0,
                next_retry_at=None, last_error=None):
    ev = models.AlertEvent(
        rule_id=rule.id, fired_at=datetime.datetime.now(datetime.UTC),
        status=status, payload="{}", attempts=attempts,
        last_error=last_error, next_retry_at=next_retry_at,
    )
    db.add(ev)
    db.commit()
    return ev


def _make_rule(db, channel_id, *, trigger_type="space_occupied",
               condition=None):
    if condition is None:
        condition = {"space_ids": [1]}
    r = models.AlertRule(
        name="R", trigger_type=trigger_type,
        condition=json.dumps(condition), channel_id=channel_id,
        enabled=True, consecutive_count=1, cooldown_seconds=0,
    )
    db.add(r)
    db.commit()
    return r


def _make_channel(db, *, name="Hook", channel_type="webhook", url="https://x"):
    ch = models.AlertChannel(
        name=name, channel_type=channel_type,
        config=json.dumps({"url": url}), enabled=True,
    )
    db.add(ch)
    db.commit()
    return ch


# --- backoff schedule -------------------------------------------------------

def test_backoff_schedule_indexing():
    """BACKOFF_SECONDS is indexed by (attempts-1) on failure."""
    assert alerts_dispatcher.BACKOFF_SECONDS[0] == 60     # 1st retry
    assert alerts_dispatcher.BACKOFF_SECONDS[1] == 300    # 2nd retry
    assert alerts_dispatcher.BACKOFF_SECONDS[2] == 1800   # 3rd retry
    # 4th retry is terminal (event moves to 'failed' status) so no
    # backoff entry is needed. Array is sized to MAX_ATTEMPTS - 1.
    assert len(alerts_dispatcher.BACKOFF_SECONDS) == alerts_dispatcher.MAX_ATTEMPTS - 1
    assert alerts_dispatcher.MAX_ATTEMPTS == 4


# --- _claim_one_event filter ------------------------------------------------

def test_claim_skips_events_with_future_retry(db_session):
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    # Future retry -> skip.
    _make_event(
        db_session, rule=rule, status="failed", attempts=1,
        next_retry_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(minutes=5),
    )
    claimed = alerts_dispatcher._claim_one_event(db_session)
    assert claimed is None


def test_claim_picks_up_events_with_past_retry(db_session):
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    _make_event(
        db_session, rule=rule, status="failed", attempts=1,
        next_retry_at=datetime.datetime.now(datetime.UTC)
        - datetime.timedelta(seconds=10),
    )
    claimed = alerts_dispatcher._claim_one_event(db_session)
    assert claimed is not None


def test_claim_picks_up_events_with_null_retry(db_session):
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    _make_event(
        db_session, rule=rule, status="fired", attempts=0,
        next_retry_at=None,
    )
    claimed = alerts_dispatcher._claim_one_event(db_session)
    assert claimed is not None


# --- retry endpoint honors MAX_ATTEMPTS ------------------------------------

def test_manual_retry_when_attempts_below_max_succeeds(client, auth_header, db_session):
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(db_session, rule=rule, status="failed", attempts=2)
    r = client.post(f"/api/alerts/{ev.id}/retry", headers=auth_header)
    assert r.status_code == 200
    db_session.refresh(ev)
    assert ev.status == "fired"
    assert ev.next_retry_at is None


def test_manual_retry_when_attempts_at_max_returns_409(client, auth_header, db_session):
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(
        db_session, rule=rule, status="failed",
        attempts=alerts_dispatcher.MAX_ATTEMPTS,
    )
    r = client.post(f"/api/alerts/{ev.id}/retry", headers=auth_header)
    assert r.status_code == 409
    assert "4 times" in r.json()["detail"]


def test_manual_retry_on_sent_event_returns_400(client, auth_header, db_session):
    """Successfully delivered ('sent') events must NOT be
    retryable.  The old code allowed it, which meant operators
    could re-fire a delivered alert to the same channel — the
    downstream system would get a duplicate.  The fix rejects
    with 400 and points the operator at the test-fire endpoint
    for channel verification.
    """
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(
        db_session, rule=rule, status="sent", attempts=1,
    )
    r = client.post(f"/api/alerts/{ev.id}/retry", headers=auth_header)
    assert r.status_code == 400
    assert "only 'failed' events are retryable" in r.json()["detail"]
    # Status should be unchanged.
    db_session.refresh(ev)
    assert ev.status == "sent"


def test_manual_retry_counts_toward_budget(client, auth_header, db_session):
    """A manual retry doesn't reset attempts; the budget is global.

    The endpoint resets status to 'fired' but leaves attempts alone.
    The dispatcher is what increments attempts on each delivery.
    Here we simulate the dispatcher's increment between two manual
    retries to show that the budget is enforced.
    """
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(db_session, rule=rule, status="failed", attempts=3)
    r = client.post(f"/api/alerts/{ev.id}/retry", headers=auth_header)
    assert r.status_code == 200
    db_session.refresh(ev)
    assert ev.attempts == 3  # not reset
    # Simulate the dispatcher running and failing again: attempts
    # becomes 4 (max).  Status flips back to 'failed'.
    ev.attempts = 4
    ev.status = "failed"
    db_session.commit()
    r = client.post(f"/api/alerts/{ev.id}/retry", headers=auth_header)
    assert r.status_code == 409


# --- delivery function: backoff scheduling ----------------------------------

def test_delivery_failure_schedules_first_retry(monkeypatch, db_session):
    """A failure with attempts=1 schedules the 60s backoff."""
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(db_session, rule=rule, status="fired", attempts=0)
    ev.attempts = 1  # post-attempt increment

    # Patch the channel deliver to always fail.
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_fail(config, payload):
        return WebhookResult(success=False, error="boom")
    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_fail,
    )

    db_session.refresh(ev)
    # Drive _deliver_one: the function increments attempts inside,
    # so we start with attempts=0.
    ev.attempts = 0
    db_session.commit()
    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.status == "failed"
    assert ev.attempts == 1
    assert ev.next_retry_at is not None
    # SQLite strips tzinfo on read; normalize.
    from datetime import timezone
    retry_at = ev.next_retry_at
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    delta = (retry_at - datetime.datetime.now(datetime.UTC)).total_seconds()
    # Should be ~60s (first backoff bucket)
    assert 55 < delta < 65


def test_delivery_terminal_after_max_attempts(monkeypatch, db_session):
    """After MAX_ATTEMPTS failures, next_retry_at stays None."""
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    # Pre-set attempts=MAX-1 so the post-increment lands at MAX.
    ev = _make_event(
        db_session, rule=rule, status="fired",
        attempts=alerts_dispatcher.MAX_ATTEMPTS - 1,
    )
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_fail(config, payload):
        return WebhookResult(success=False, error="boom")
    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_fail,
    )
    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.status == "failed"
    assert ev.attempts == alerts_dispatcher.MAX_ATTEMPTS
    assert ev.next_retry_at is None  # terminal


def test_delivery_success_clears_retry(monkeypatch, db_session):
    """On success, next_retry_at is cleared."""
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(
        db_session, rule=rule, status="fired", attempts=0,
        next_retry_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(minutes=5),
    )
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_ok(config, payload):
        return WebhookResult(success=True, status_code=200)
    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_ok,
    )
    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.status == "sent"
    assert ev.next_retry_at is None
    assert ev.delivered_at is not None


def test_delivery_failure_stamps_last_attempt_at(monkeypatch, db_session):
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(db_session, rule=rule, status="fired", attempts=0)
    from backend.alerts.channels.webhook import WebhookResult
    async def fake_fail(config, payload):
        return WebhookResult(success=False, error="500")
    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_fail,
    )
    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.last_attempt_at is not None


def test_delivery_exception_schedules_retry(monkeypatch, db_session):
    """Regression: an unhandled exception in the channel handler
    must schedule a retry just like a clean ``result.success=False``
    does.  The old code set ``status='failed'`` but left
    ``next_retry_at`` as NULL; SQLite's NULL comparison in the
    dispatcher's claim query (``next_retry_at <= now``) then
    evaluated to False, stranding the event in failed state
    forever.
    """
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(db_session, rule=rule, status="fired", attempts=0)

    async def fake_explode(config, payload):
        raise RuntimeError("channel handler crashed")

    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_explode,
    )

    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.status == "failed"
    assert ev.attempts == 1
    # The fix: next_retry_at must be set (not NULL) so the
    # dispatcher's claim query picks the event up again.
    assert ev.next_retry_at is not None, (
        "exception path must schedule a retry; a NULL next_retry_at "
        "strands the event because the claim query's NULL comparison "
        "is always False in SQLite"
    )
    # And the last_error must mention the exception for the
    # operator to debug.
    assert "channel handler crashed" in (ev.last_error or "")


def test_delivery_exception_at_max_attempts_is_terminal(monkeypatch, db_session):
    """If the exception path is hit at the last allowed attempt,
    the event is terminal and ``next_retry_at`` is cleared.
    """
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(
        db_session, rule=rule, status="fired",
        attempts=alerts_dispatcher.MAX_ATTEMPTS - 1,
    )

    async def fake_explode(config, payload):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_explode,
    )

    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.status == "failed"
    assert ev.attempts == alerts_dispatcher.MAX_ATTEMPTS
    assert ev.next_retry_at is None  # terminal: no more retries


def test_deliver_one_works_under_running_event_loop(monkeypatch, db_session):
    """Regression: ``_deliver_one`` must NOT call ``loop.run_until_complete``
    on a running loop.  The old sync version did exactly that, and
    in production (where the dispatcher loop is itself an async task
    on FastAPI's main loop) every delivery failed with
    ``RuntimeError: this event loop is already running``.

    This test runs ``_deliver_one`` from inside a real running loop
    via ``asyncio.run``.  If the function uses the broken pattern
    (``asyncio.get_running_loop() + run_until_complete``), the
    coroutine itself raises and the event ends up ``status='failed'``
    with ``last_error`` containing ``'event loop is already running'``.
    With the fix (plain ``await handler(...)``), the event lands
    ``status='sent'`` and the test passes.
    """
    import threading
    ch = _make_channel(db_session)
    rule = _make_rule(db_session, ch.id)
    ev = _make_event(db_session, rule=rule, status="fired", attempts=0)

    from backend.alerts.channels.webhook import WebhookResult
    async def fake_ok(config, payload):
        return WebhookResult(success=True, status_code=200)
    monkeypatch.setitem(
        alerts_dispatcher.CHANNEL_DISPATCH, "webhook", fake_ok,
    )

    # Drive the async function from a real running event loop.  In
    # the broken version, the inner ``run_until_complete`` raises
    # ``RuntimeError: this event loop is already running`` and the
    # event is marked failed.
    _deliver(db_session, ev)
    db_session.refresh(ev)
    assert ev.status == "sent", (
        f"_deliver_one failed under a running loop — "
        f"regression of the run_until_complete deadlock. "
        f"last_error={ev.last_error!r}"
    )
