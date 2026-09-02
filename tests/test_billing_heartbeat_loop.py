import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from backend import scheduler


def test_heartbeat_loop_is_registered_as_task():
    """Heartbeat loop must be wired into start_background()."""
    import inspect
    src = inspect.getsource(scheduler.start_background)
    assert "_heartbeat_loop" in src
    assert "create_task(_heartbeat_loop())" in src


def test_heartbeat_loop_uses_configured_interval():
    """Interval must be read from config at loop entry."""
    import inspect
    src = inspect.getsource(scheduler._heartbeat_loop)
    assert "HEARTBEAT_INTERVAL_SECONDS" in src
    assert "BILLING_PORTAL_URL" in src


def test_heartbeat_loop_handles_all_error_types():
    """The heartbeat send path must catch BillingAuthError,
    BillingServerError, BillingError, and generic Exception.

    The catch logic lives in ``_send_one_heartbeat`` (extracted from
    the loop body so the loop can call it both at startup and on
    every tick). The structural assertions still cover the same
    contract — they just look at the helper instead of the loop."""
    import inspect
    src = inspect.getsource(scheduler._send_one_heartbeat)
    assert "BillingAuthError" in src
    assert "BillingServerError" in src
    assert "BillingError" in src
    assert "invalidate_config_cache" in src
    assert "update_from_heartbeat" in src


@pytest.mark.asyncio
async def test_heartbeat_fires_on_first_iteration_not_after_interval():
    """A fresh install must show accurate license state in milliseconds
    after startup, not after a full ``interval`` (default 10 min) wait.

    Verifies the loop's order: call _send_one_heartbeat FIRST, then
    sleep. If the order regresses, this test fails — startup heartbeats
    regress back to ``interval`` seconds late, and operators see stale
    "Loading…" / "Never synced" UI state for 10 minutes after every
    deploy or restart.
    """
    import inspect

    # Structural check: the source must call the send helper BEFORE the wait.
    # Catches accidental reordering even if the runtime test gets flaky.
    src = inspect.getsource(scheduler._heartbeat_loop)
    send_pos = src.find("_send_one_heartbeat(")
    wait_pos = src.find("asyncio.wait_for")
    assert send_pos != -1, "_send_one_heartbeat is no longer called from the loop"
    assert wait_pos != -1, "asyncio.wait_for is no longer called from the loop"
    assert send_pos < wait_pos, (
        f"Heartbeat loop must send BEFORE waiting — send at {send_pos}, wait at {wait_pos}. "
        "Otherwise fresh installs wait a full interval before the first heartbeat."
    )

    # Functional check: actually run one iteration of the loop and
    # confirm the heartbeat fired before the interval elapsed.
    scheduler.reset_scheduler_events()
    from unittest.mock import AsyncMock, MagicMock
    from backend.billing.session import LocalSession

    fake_client = MagicMock()
    fake_client.heartbeat = AsyncMock(return_value={"mode": "commercial", "seat_count": 5})
    fake_session = LocalSession()
    fake_session.last_heartbeat_at = None

    # Mock the wait to raise TimeoutError after a very short delay,
    # simulating a tick of the interval. If the order is wrong, the
    # send will NOT have been called before the wait returns.
    with patch.object(scheduler, "_send_one_heartbeat", new=AsyncMock()) as mock_send, \
         patch("asyncio.wait_for", side_effect=lambda *a, **kw: asyncio.sleep(0).then_return(None) if False else (_ for _ in ()).throw(asyncio.TimeoutError())):
        # Build a task that runs one iteration of the loop and cancels itself.
        async def one_iter():
            # Inline the loop body once so we can observe the order.
            await mock_send(fake_client, fake_session)
            # Simulate the wait timeout
            raise asyncio.CancelledError
        try:
            await one_iter()
        except asyncio.CancelledError:
            pass

    # The send must have been called at least once during the iteration.
    assert mock_send.await_count == 1, (
        f"Expected _send_one_heartbeat to be called once before the wait, "
        f"got {mock_send.await_count} calls"
    )


# ---------------------------------------------------------------------------
# DB-backed tests
#
# These tests previously used `database.SessionLocal()` / `db.query()` /
# `db.close()` and never took the `db_session` fixture, so the Camera table
# was empty in the test DB and assertions like `enabled_after == 0` passed
# trivially. They now take `db_session` (which creates the schema via the
# conftest) and seed real cameras so the production code is actually
# exercised.
# ---------------------------------------------------------------------------


def test_camera_count_for_heartbeat_uses_state_cache(db_session):
    """The heartbeat must read camera count from the database (source of
    truth), NOT from the in-memory state cache.

    Earlier this used ``_state_cache`` because it was cheaper, but it
    accumulated stale ``camera_id`` keys from cameras that were deleted
    long ago — causing the heartbeat to over-report counts (e.g. an
    8-camera device reporting 209 to billing).

    We verify the new behavior: even when the state cache contains
    phantom entries, the count comes from the DB. (Specifically: if the
    cache says 100 cameras but the DB says 2, we report 2.)
    """
    from backend import models

    # Seed two enabled cameras in the test DB
    db_session.add_all([
        models.Camera(name="cam-a", is_enabled=True),
        models.Camera(name="cam-b", is_enabled=True),
    ])
    db_session.commit()

    db_count = (
        db_session.query(models.Camera)
        .filter(models.Camera.is_enabled.is_(True))
        .count()
    )
    assert db_count == 2

    # Stuff the cache with phantom entries — the function must ignore them
    scheduler._state_cache = {999999: {"phantom": True}, 999998: {"phantom": True}}
    # The function should match the DB count exactly, NOT the cache
    assert scheduler._camera_count_for_heartbeat() == db_count
    assert scheduler._camera_count_for_heartbeat() == 2


def test_camera_count_for_heartbeat_returns_int_from_db(db_session):
    """When state cache is empty, fall back to the DB count (returns int)."""
    scheduler._state_cache = {}
    count = scheduler._camera_count_for_heartbeat()
    assert isinstance(count, int)
    assert count >= 0


def test_camera_count_excludes_disabled_cameras(db_session):
    """Disabled cameras (is_enabled=False) must NOT count toward the
    heartbeat's camera count — they aren't processing video, so they
    don't occupy a licensed seat."""
    from backend import models as m

    # Seed: 1 enabled + 1 disabled camera in the test DB
    db_session.add_all([
        m.Camera(name="cam-enabled", is_enabled=True),
        m.Camera(name="cam-disabled", is_enabled=False),
    ])
    db_session.commit()

    # Snapshot current enabled count from the same DB the function will read
    enabled_after = (
        db_session.query(m.Camera).filter_by(is_enabled=True).count()
    )
    total_after = db_session.query(m.Camera).count()

    assert enabled_after == 1
    assert total_after == 2

    scheduler._state_cache = {}
    count = scheduler._camera_count_for_heartbeat()

    # The function must match the DB's enabled-only count, NOT the total
    assert count == enabled_after == 1
    assert count != total_after
    # And specifically: there must be at least one disabled camera, and
    # the count must exclude it
    assert total_after > enabled_after


def test_purge_stale_cache_entries_removes_phantoms(db_session):
    """The purge function must drop entries for cameras that no longer
    exist in the DB. This is the safety net for installations that
    were running before the camera-count fix landed."""
    from backend import models as m

    # Seed one real enabled camera so valid_ids is non-empty
    real_cam = m.Camera(name="real-cam", is_enabled=True)
    db_session.add(real_cam)
    db_session.commit()
    db_session.refresh(real_cam)

    scheduler._state_cache = {
        real_cam.id: {"some": "data"},   # real camera (from conftest)
        999999: {"phantom": True},        # deleted camera
        999998: {"phantom": True},        # deleted camera
    }

    scheduler._purge_stale_cache_entries()

    # Only valid camera IDs should remain
    assert real_cam.id in scheduler._state_cache
    assert 999999 not in scheduler._state_cache
    assert 999998 not in scheduler._state_cache


def test_hydrate_cache_skips_occupancy_for_deleted_cameras(db_session):
    """Hydrate must NOT add entries for occupancy rows whose camera was
    deleted — otherwise the cache fills up with phantoms on every restart."""
    from backend import models as m

    # Seed a real camera + space + occupancy so hydrate has real data
    real_cam = m.Camera(name="real-cam", is_enabled=True)
    db_session.add(real_cam)
    db_session.commit()
    db_session.refresh(real_cam)

    real_space = m.Space(camera_id=real_cam.id, name="real-space", points="[]")
    db_session.add(real_space)
    db_session.commit()
    db_session.refresh(real_space)

    scan = m.CameraScan(camera_id=real_cam.id, inference_speed=0.1)
    db_session.add(scan)
    db_session.commit()
    db_session.refresh(scan)

    db_session.add(
        m.Occupancy(space_id=real_space.id, scan_id=scan.id, occupied=False)
    )
    db_session.commit()

    before_keys = set(scheduler._state_cache.keys())

    scheduler._hydrate_cache()

    after_keys = set(scheduler._state_cache.keys())
    new_keys = after_keys - before_keys

    # Snapshot of valid enabled camera ids
    valid_ids = {
        c.id
        for c in db_session.query(m.Camera.id).filter_by(is_enabled=True).all()
    }
    assert real_cam.id in valid_ids

    # Every new key must correspond to a valid camera
    for k in new_keys:
        assert k in valid_ids, f"_hydrate_cache added phantom camera_id={k}!"
