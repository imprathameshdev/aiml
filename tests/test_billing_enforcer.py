import pytest
from datetime import datetime, timedelta, timezone

from backend.billing.enforcer import (
    compute_mode,
    resolve_license,
    COMMUNITY,
    TRIAL_COMMERCIAL,
    COMMERCIAL,
)
from backend.billing.session import LocalSession


NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)


def _make_session(**kwargs) -> LocalSession:
    s = LocalSession()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_compute_mode_empty_session_is_community():
    s = _make_session()
    assert compute_mode(s, now=NOW) == COMMUNITY


def test_compute_mode_expired_license_is_community():
    s = _make_session(
        license_token="tok",
        license_expires_at=NOW - timedelta(days=1),
        trial_expires_at=NOW + timedelta(days=7),
        quota=10,
    )
    assert compute_mode(s, now=NOW) == COMMUNITY


def test_compute_mode_active_license_in_trial():
    s = _make_session(
        mode=TRIAL_COMMERCIAL,
        license_token="tok",
        license_expires_at=NOW + timedelta(days=14),
        trial_expires_at=NOW + timedelta(days=7),
        quota=10,
    )
    assert compute_mode(s, now=NOW) == TRIAL_COMMERCIAL


def test_compute_mode_active_license_paid_with_quota():
    s = _make_session(
        mode=COMMERCIAL,
        license_token="tok",
        license_expires_at=NOW + timedelta(days=14),
        trial_expires_at=NOW - timedelta(days=1),  # trial expired
        quota=10,
    )
    assert compute_mode(s, now=NOW) == COMMERCIAL


def test_compute_mode_active_license_no_quota_is_community():
    s = _make_session(
        mode=COMMUNITY,
        license_token="tok",
        license_expires_at=NOW + timedelta(days=14),
        trial_expires_at=None,
        quota=0,
    )
    assert compute_mode(s, now=NOW) == COMMUNITY


def test_compute_mode_trusts_clouds_mode_over_legacy_inference():
    """When the cloud sets ``mode='community'`` (e.g. for a suspended
    or payment-failed account), we MUST honour that even when the
    local session still has a valid license_token / license_expires_at
    from before the suspension. Otherwise the C++ inference engine
    would keep running the commercial model on a suspended account.
    """
    s = _make_session(
        mode=COMMUNITY,
        license_token="real-token",
        license_expires_at=NOW + timedelta(days=14),
        trial_expires_at=NOW + timedelta(days=7),
        quota=10,
    )
    assert compute_mode(s, now=NOW) == COMMUNITY


def test_resolve_license_community_returns_empty_strings():
    s = _make_session(mode=COMMUNITY)
    lk, inst = resolve_license(s, now=NOW)
    assert lk == ""
    assert inst == ""


def test_resolve_license_commercial_returns_token_and_hwid():
    s = _make_session(
        mode=COMMERCIAL,
        license_token="real-token-xyz",
        license_expires_at=NOW + timedelta(days=14),
        trial_expires_at=NOW + timedelta(days=7),
        quota=10,
    )
    lk, inst = resolve_license(s, now=NOW)
    assert lk == "real-token-xyz"
    # instance id is the real HWID (length 64)
    assert len(inst) == 64


def test_resolve_license_no_token_returns_empty_key():
    """If there's no token in the session, we fall back to community
    (empty license_key + empty instance_id) regardless of other fields."""
    s = _make_session(
        license_token=None,
        license_expires_at=NOW + timedelta(days=14),
        trial_expires_at=NOW + timedelta(days=7),
        quota=10,
    )
    lk, inst = resolve_license(s, now=NOW)
    assert lk == ""
    assert inst == ""