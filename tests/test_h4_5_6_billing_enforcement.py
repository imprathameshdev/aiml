"""H4/H5/H6 audit fix — billing enforcement rewrite.

The three related bugs:

- H4: ``compute_mode`` returned ``mode=commercial`` immediately
  without checking ``license_expires_at``.  An expired license
  kept the engine running on the commercial model until the cloud
  bothered to send an explicit ``mode: community`` heartbeat.

- H5: ``update_from_heartbeat`` used ``data.get("key")`` (truthy
  guard) for several fields.  The cloud could not clear
  ``license_token`` / ``email`` / ``license_expires_at`` by
  sending ``None`` — the truthy guard skipped the assignment.

- H6: token-clear on downgrade only fired on a mode *transition*
  (``new_mode == "community" and self.mode != "community"``).
  If a token was injected by ``activate_callback`` while the
  session was already community, a subsequent ``mode: community``
  heartbeat did NOT clear it.

The fix:

- H4: ``compute_mode`` now checks ``is_active()`` inside the
  commercial/trial paths and returns community on expiry.  A
  new ``enforce_expiry`` helper centralises the downgrade +
  token-clear logic; it's called by the heartbeat loop and by
  ``resolve_license_from_default_session``.

- H5: every field check uses ``"key" in data`` (key-presence).

- H6: the token-clear on ``mode: community`` is now idempotent.
  ``update_from_heartbeat`` returns a bool indicating whether a
  downgrade happened, so the caller can invalidate the inference
  engine's license cache.
"""
import json
from datetime import datetime, timezone, timedelta


def _session_with(**kwargs):
    """Construct a LocalSession with the given fields."""
    from backend.billing.session import LocalSession
    s = LocalSession()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# --- H4: compute_mode honours expiry ---

def test_compute_mode_commercial_with_future_expiry_returns_commercial():
    from backend.billing.enforcer import compute_mode, COMMERCIAL
    s = _session_with(
        mode=COMMERCIAL,
        license_token="abc",
        license_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    assert compute_mode(s) == COMMERCIAL


def test_compute_mode_commercial_with_past_expiry_returns_community():
    """H4: expired commercial license is downgraded by compute_mode."""
    from backend.billing.enforcer import compute_mode, COMMUNITY, COMMERCIAL
    s = _session_with(
        mode=COMMERCIAL,
        license_token="abc",
        license_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert compute_mode(s) == COMMUNITY


def test_compute_mode_trial_with_past_expiry_returns_community():
    from backend.billing.enforcer import compute_mode, COMMUNITY, TRIAL_COMMERCIAL
    s = _session_with(
        mode=TRIAL_COMMERCIAL,
        license_token="abc",
        license_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert compute_mode(s) == COMMUNITY


def test_compute_mode_community_returns_community_regardless_of_expiry():
    """Cloud-downgraded sessions stay community."""
    from backend.billing.enforcer import compute_mode, COMMUNITY
    s = _session_with(
        mode=COMMUNITY,
        license_token=None,
        license_expires_at=None,
    )
    assert compute_mode(s) == COMMUNITY


# --- H4: enforce_expiry mutates the session ---

def test_enforce_expiry_downgrades_and_clears_token():
    """H4: enforce_expiry on an expired commercial session clears
    the token and sets mode=community."""
    from backend.billing.enforcer import enforce_expiry, COMMUNITY
    s = _session_with(
        mode="commercial",
        license_token="abc",
        license_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    downgraded = enforce_expiry(s)
    assert downgraded is True
    assert s.mode == COMMUNITY
    assert s.license_token is None
    assert s.license_expires_at is None


def test_enforce_expiry_is_noop_when_still_valid():
    from backend.billing.enforcer import enforce_expiry, COMMERCIAL
    s = _session_with(
        mode=COMMERCIAL,
        license_token="abc",
        license_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    assert enforce_expiry(s) is False
    assert s.mode == COMMERCIAL
    assert s.license_token == "abc"


def test_enforce_expiry_is_noop_for_community_session():
    from backend.billing.enforcer import enforce_expiry, COMMUNITY
    s = _session_with(mode=COMMUNITY, license_token=None, license_expires_at=None)
    assert enforce_expiry(s) is False
    assert s.mode == COMMUNITY


# --- H5: update_from_heartbeat uses key-presence ---

def test_update_from_heartbeat_can_clear_license_token():
    """H5: sending license_token=null clears the field (key-presence)."""
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.license_token = "existing_token"
    s.mode = "commercial"
    s.update_from_heartbeat({"license_token": None, "mode": "community"})
    assert s.license_token is None


def test_update_from_heartbeat_can_clear_email():
    """H5: sending email=null clears the field."""
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.email = "old@example.com"
    s.update_from_heartbeat({"email": None})
    assert s.email is None


def test_update_from_heartbeat_preserves_token_when_key_absent():
    """H5: if license_token is not in the heartbeat body, the
    existing local value is preserved (key-presence, not truthy)."""
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.license_token = "existing_token"
    s.update_from_heartbeat({"mode": "commercial"})
    assert s.license_token == "existing_token"


def test_update_from_heartbeat_preserves_email_when_key_absent():
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.email = "kept@example.com"
    s.update_from_heartbeat({"quota": 5})
    assert s.email == "kept@example.com"


# --- H6: token-clear is idempotent on community mode ---

def test_update_from_heartbeat_clears_token_when_already_community():
    """H6: idempotent — a second community-mode heartbeat clears
    the token even if mode didn't change (it was already community)."""
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.mode = "community"  # already community
    s.license_token = "stale_token"  # but a token was injected
    s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    s.update_from_heartbeat({"mode": "community"})
    assert s.license_token is None
    assert s.license_expires_at is None


def test_update_from_heartbeat_returns_true_on_downgrade():
    """The return value tells the caller to invalidate the engine cache."""
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.mode = "commercial"
    s.license_token = "abc"
    s.license_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    result = s.update_from_heartbeat({"mode": "community"})
    assert result is True


def test_update_from_heartbeat_returns_false_on_normal_update():
    from backend.billing.session import LocalSession
    s = LocalSession()
    s.mode = "community"
    s.license_token = None
    s.license_expires_at = None
    result = s.update_from_heartbeat({"quota": 5, "email": "x@y.com"})
    assert result is False


# --- Integration: H4+H5+H6 together ---

def test_resolve_license_returns_empty_after_expiry():
    """H4 integration: resolve_license returns "" for an expired
    commercial session after enforce_expiry is called."""
    from backend.billing.enforcer import enforce_expiry
    from backend.billing.session import LocalSession

    # Setup an expired commercial session in the default-session
    # singleton (resolve_license_from_default_session reads it)
    from backend.billing.session import default_session
    sess = default_session()
    sess.mode = "commercial"
    sess.license_token = "abc"
    sess.license_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        from backend.billing.enforcer import resolve_license
        # First call — enforce_expiry clears the token
        key, _ = resolve_license(sess)
        assert key == ""
    finally:
        # Reset the singleton so other tests aren't affected
        sess.mode = "community"
        sess.license_token = None
        sess.license_expires_at = None
