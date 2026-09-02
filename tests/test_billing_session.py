import json
import pytest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.billing.session import LocalSession


def _new_session(file_path: Path) -> LocalSession:
    s = LocalSession()
    s._file = file_path
    return s


def test_default_state_is_community():
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        assert s.mode == "community"
        assert s.quota == 0
        assert s.license_token is None
        assert s.email is None
        assert s.is_active() is False


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        s = _new_session(path)
        s.license_token = "tok-abc"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
        s.trial_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        s.mode = "trial_commercial"
        s.quota = 4
        s.email = "user@example.com"
        s.save()

        # Read raw JSON to confirm on-disk shape
        raw = json.loads(path.read_text())
        assert raw["license_token"] == "tok-abc"
        assert raw["mode"] == "trial_commercial"
        assert raw["quota"] == 4
        assert raw["email"] == "user@example.com"

        # Now reload into a fresh session
        s2 = _new_session(path)
        assert s2.load() is True
        assert s2.license_token == "tok-abc"
        assert s2.mode == "trial_commercial"
        assert s2.quota == 4
        assert s2.email == "user@example.com"


def test_load_returns_false_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "missing.json")
        assert s.load() is False
        assert s.mode == "community"


def test_load_handles_garbage_json():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        path.write_text("not-json")
        s = _new_session(path)
        assert s.load() is False


def test_is_active_only_when_license_and_future_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")

        # No token, no expiry -> inactive
        assert s.is_active() is False

        # Future expiry -> active
        s.license_token = "tok"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        assert s.is_active() is True

        # Past expiry -> inactive
        s.license_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        assert s.is_active() is False


def test_clear_resets_all_fields():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        s = _new_session(path)
        s.license_token = "tok"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        s.trial_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        s.mode = "commercial"
        s.quota = 10
        s.email = "x@y.com"
        s.save()
        assert path.exists()

        s.clear()
        assert not path.exists()
        assert s.license_token is None
        assert s.mode == "community"
        assert s.quota == 0


def test_update_from_heartbeat():
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        s.license_token = "old-token"
        s.license_expires_at = datetime.now(timezone.utc)
        s.mode = "trial_commercial"

        s.update_from_heartbeat({
            "license_token": "new-token",
            "license_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
            "trial_expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "mode": "commercial",
            "quota": 5,
            "email": "u@v.com",
            "status_code": "active",
        })

        assert s.license_token == "new-token"
        assert s.mode == "commercial"
        assert s.quota == 5
        assert s.email == "u@v.com"
        assert s.last_heartbeat_status == "active"
        assert s.last_heartbeat_at is not None


def test_update_from_heartbeat_clears_token_when_downgraded_to_community():
    """When the cloud downgrades us to community (suspension,
    payment-failed, trial-expired), any locally cached license token
    must be wiped — otherwise the inference engine would keep using
    it offline if billing becomes unreachable.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        s.license_token = "stale-commercial-token"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
        s.mode = "commercial"
        s.quota = 10

        s.update_from_heartbeat({
            "mode": "community",
            "status_code": "account_suspended",
            # No license_token in the response — cloud didn't issue one
        })

        assert s.mode == "community"
        assert s.license_token is None
        assert s.license_expires_at is None
        assert s.last_heartbeat_status == "account_suspended"


def test_update_from_heartbeat_clears_token_on_community_mode_idempotently():
    """H6 audit fix: a community-mode heartbeat must clear the
    stored token even if the mode is already community.  Previously
    the clear only fired on a *transition* (mode != community →
    community), so a token injected by ``activate_callback`` while
    the session was already community persisted indefinitely.

    This test was previously named
    ``test_update_from_heartbeat_keeps_token_when_mode_stays_commercial``
    and locked in the buggy behavior.  The name was also misleading
    — it was actually testing community→community, not commercial.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        s.license_token = "stale-token"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
        s.mode = "community"

        s.update_from_heartbeat({
            "mode": "community",
            "status_code": "active",
        })

        # H6 fix: the community-mode heartbeat always clears the
        # token, regardless of the previous mode.
        assert s.mode == "community"
        assert s.license_token is None
        assert s.license_expires_at is None


def test_update_from_heartbeat_keeps_token_when_mode_stays_commercial():
    """A same-mode commercial update must NOT wipe the token."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        s.license_token = "real-token"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
        s.mode = "commercial"

        s.update_from_heartbeat({
            "mode": "commercial",
            "status_code": "active",
        })

        # Commercial update: token is preserved.
        assert s.mode == "commercial"
        assert s.license_token == "real-token"


def test_update_from_heartbeat_stores_subscription_expires_at():
    """The cloud sends ``subscription_expires_at`` (== camera_quota's
    grace_period_ends_at) as an explicit field.  The on-prem must
    store it as a separate field from ``license_expires_at`` so the
    grace-period banner can render the right date even when a later
    state (e.g. trial) would win the capped TTL.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        grace_end = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        ttl_end = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()

        s.update_from_heartbeat({
            "status_code": "grace_period",
            "mode": "commercial",
            "license_expires_at": ttl_end,
            "subscription_expires_at": grace_end,
        })

        assert s.license_expires_at.isoformat() == ttl_end
        # The grace-period end is stored separately so the
        # frontend can render it even when the capped TTL is
        # different (e.g. during a trial overlapping the grace
        # period).
        assert s.subscription_expires_at is not None
        assert s.subscription_expires_at.isoformat() == grace_end


def test_update_from_heartbeat_stores_device_quota():
    """The cloud sends ``device_quota`` (this device's seat count)
    as a separate field from ``quota`` (the user's total pool).
    Both must round-trip through save → load and the heartbeat
    update.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"

        # 1. Heartbeat writes device_quota
        s = _new_session(path)
        s.update_from_heartbeat({
            "status_code": "active",
            "mode": "commercial",
            "quota": 10,
            "device_quota": 4,
        })
        assert s.quota == 10
        assert s.device_quota == 4

        # 2. Save/load round-trip preserves the new field
        s.save()
        raw = json.loads(path.read_text())
        assert raw["device_quota"] == 4
        s2 = _new_session(path)
        assert s2.load() is True
        assert s2.device_quota == 4

        # 3. Clear resets it
        s2.clear()
        assert s2.device_quota == 0


def test_save_and_load_roundtrip_includes_new_fields():
    """The new subscription_expires_at and device_quota fields
    must survive the on-disk round-trip with the same ISO format
    as the existing date fields.  Pins the storage shape so a
    refactor that drops them is caught by the test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        s = _new_session(path)
        s.license_token = "tok"
        s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
        s.trial_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        s.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=5)
        s.mode = "commercial"
        s.quota = 8
        s.device_quota = 3
        s.save()

        raw = json.loads(path.read_text())
        assert "subscription_expires_at" in raw
        assert raw["device_quota"] == 3

        s2 = _new_session(path)
        assert s2.load() is True
        assert s2.subscription_expires_at is not None
        assert s2.device_quota == 3


def test_clear_resets_new_fields():
    """The new subscription_expires_at and device_quota fields
    must be reset by ``clear()`` (used on unlink/forget).  Without
    this reset, a re-activation on the same device could inherit
    the previous grace-period window or seat count.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        s.license_token = "tok"
        s.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=5)
        s.device_quota = 4
        s.save()

        s.clear()
        assert s.subscription_expires_at is None
        assert s.device_quota == 0


def test_mark_device_forgotten_wipes_all_licensing_data():
    """``mark_device_forgotten()`` is the deterministic state-reset
    called by the heartbeat 404 paths. Unlike ``clear()``, it keeps
    ``last_heartbeat_at`` set to the current time and flips
    ``last_heartbeat_status`` to ``"not_activated"`` (not
    ``"never"``) so the UI renders the "Activate License" prompt
    rather than a generic "never synced" state.

    Wipes every licensing field — license, trial, quota, email,
    camera counts, device_quota, mode — because the cloud's 404
    with ``code: "device_not_activated"`` is the definitive signal
    that the HWID is not on any account, and any stale state would
    either mislead the UI or let the inference engine keep running
    on a dead commercial license.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = _new_session(Path(tmp) / "session.json")
        future = datetime.now(timezone.utc) + timedelta(days=14)
        s.license_token = "stale-token"
        s.license_expires_at = future
        s.trial_expires_at = future
        s.subscription_expires_at = future
        s.mode = "commercial"
        s.quota = 4
        s.email = "x@y.com"
        s.this_device_cameras = 4
        s.other_devices_cameras = 2
        s.device_count = 2
        s.device_quota = 4
        s.save()

        s.mark_device_forgotten()
        s.save()

        # All licensing fields wiped.
        assert s.license_token is None
        assert s.license_expires_at is None
        assert s.trial_expires_at is None
        assert s.subscription_expires_at is None
        assert s.mode == "community"
        assert s.quota == 0
        assert s.email is None
        assert s.this_device_cameras == 0
        assert s.other_devices_cameras == 0
        assert s.device_count == 0
        assert s.device_quota == 0

        # ``last_heartbeat_at`` is set to the call time (NOT None
        # like clear()) so the UI's "Last Sync" timestamp stays
        # fresh.
        assert s.last_heartbeat_at is not None
        # ``last_heartbeat_status`` is "not_activated" (NOT "never"
        # like clear()) so the UI renders the "Activate License"
        # prompt rather than a generic "never synced" state.
        assert s.last_heartbeat_status == "not_activated"


def test_mark_device_forgotten_round_trips_through_disk():
    """The wipe must persist across the save/load round trip so
    the next scheduler tick (or the manual "Sync Now" click) sees
    the clean state.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        s = _new_session(path)
        s.license_token = "stale-token"
        s.mode = "commercial"
        s.quota = 4
        s.email = "x@y.com"
        s.this_device_cameras = 4
        s.device_count = 1
        s.save()

        s.mark_device_forgotten()
        s.save()

        s2 = _new_session(path)
        assert s2.load() is True
        assert s2.license_token is None
        assert s2.mode == "community"
        assert s2.quota == 0
        assert s2.email is None
        assert s2.this_device_cameras == 0
        assert s2.device_count == 0
        assert s2.last_heartbeat_status == "not_activated"