import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from backend import config as backend_config


def test_status_requires_auth(client):
    """Without auth, /status must 401."""
    res = client.get("/api/billing/status")
    assert res.status_code == 401


def test_status_returns_mode_and_hwid(client, admin_token):
    # Reset the default session so we don't pick up state from earlier tests.
    from backend.billing import session as billing_session
    from backend.billing.session import LocalSession
    fresh = LocalSession()
    fresh._file = billing_session.session_path()
    fresh.clear()
    fresh.save()
    with patch("backend.billing.routes.default_session", return_value=fresh):
        res = client.get(
            "/api/billing/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "community"
    assert data["quota"] == 0
    assert "hwid" in data
    assert len(data["hwid"]) == 64


def test_status_reflects_active_license(client, admin_token, db_session, tmp_path):
    """When a license is cached locally, /status returns its mode."""
    from backend.billing.session import LocalSession

    # Point the default session at a tmp file
    session_file = tmp_path / "session.json"
    test_session = LocalSession()
    test_session._file = session_file
    test_session.license_token = "tok-xyz"
    test_session.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    test_session.trial_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    test_session.mode = "trial_commercial"
    test_session.quota = 5
    test_session.email = "user@example.com"
    test_session.save()

    # Inject into the routes module's default
    with patch("backend.billing.routes.default_session", return_value=test_session):
        res = client.get(
            "/api/billing/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "trial_commercial"
    assert data["email"] == "user@example.com"
    assert data["quota"] == 5


def test_connect_url_requires_billing_portal(client, admin_token):
    """When BILLING_PORTAL_URL is empty, /connect-url must 400.

    Note: depending on whether the test runner has a real billing URL
    configured in its .env, the assertion flips:
      - URL configured → returns 200 with url/hwid/return_to fields
      - URL empty → returns 400
    """
    from backend import config as backend_config

    if not backend_config.config.BILLING_PORTAL_URL:
        res = client.get(
            "/api/billing/connect-url",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 400
    else:
        res = client.get(
            "/api/billing/connect-url",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "url" in data
        assert "hwid" in data
        assert "return_to" in data


def test_connect_url_post_accepts_return_to_override(client, admin_token):
    """The POST variant lets the frontend pass its real browser origin
    so the callback URL doesn't accidentally point at the API server's
    internal address."""
    from backend import config as backend_config

    if not backend_config.config.BILLING_PORTAL_URL:
        return  # Skip — no portal configured in this env

    frontend_origin = "http://localhost:5173"
    res = client.post(
        "/api/billing/connect-url",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"return_to": frontend_origin},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["return_to"].startswith(frontend_origin)
    assert data["return_to"].endswith("/billing/callback")
    assert "hwid=" in data["url"]
    assert "return_to=" in data["url"]


def test_connect_url_post_falls_back_when_return_to_missing(client, admin_token):
    """When ``return_to`` is omitted from the body, the server falls
    back to ``request.base_url`` (still works for prod where the
    frontend and backend share a host:port)."""
    from backend import config as backend_config

    if not backend_config.config.BILLING_PORTAL_URL:
        return  # Skip

    res = client.post(
        "/api/billing/connect-url",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["return_to"].endswith("/billing/callback")


def test_logout_clears_session(client, admin_token):
    # Ensure BILLING_PORTAL_URL is empty so logout skips the forget_device call.
    from backend.billing import session as billing_session
    from backend.billing.session import LocalSession
    fresh = LocalSession()
    fresh._file = billing_session.session_path()
    fresh.save()

    with patch("backend.billing.routes.default_session", return_value=fresh):
        with patch.object(backend_config.config, "BILLING_PORTAL_URL", ""):
            res = client.post(
                "/api/billing/logout",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_activate_callback_stores_license(client, admin_token, tmp_path):
    """After activation, the session must hold a license token AND the
    trial expiration. The immediate heartbeat that follows is the
    canonical source of the token, so it overrides whatever the portal
    passed in the redirect."""
    from backend.billing.session import LocalSession

    session_file = tmp_path / "session.json"
    test_session = LocalSession()
    test_session._file = session_file
    test_session.save()

    with patch("backend.billing.routes.default_session", return_value=test_session):
        with patch("backend.billing.routes._invalidate_inference_cache"):
            with patch("backend.billing.routes.BillingClient") as MockClient:
                instance = MagicMock()
                # Heartbeat returns a fresh, canonical token.
                instance.heartbeat = AsyncMock(return_value={
                    "status_code": "active",
                    "mode": "trial_commercial",
                    "quota": 5,
                    "license_token": "tok-from-heartbeat",
                    "license_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    "email": "user@example.com",
                })
                MockClient.return_value = instance
                res = client.post(
                    "/api/billing/activate-callback",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={
                        "license_token": "tok-from-portal",
                        "trial_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    },
                )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    # Heartbeat's license_token wins (canonical source)
    assert test_session.license_token == "tok-from-heartbeat"
    assert test_session.trial_expires_at is not None
    # The response payload reflects the heartbeat-updated state
    assert data["mode"] == "trial_commercial"
    assert data["email"] == "user@example.com"


def test_activate_callback_keeps_portal_token_when_heartbeat_returns_none(client, admin_token, tmp_path):
    """If the immediate heartbeat returns no license_token (or fails
    entirely), the session keeps the token the portal gave us."""
    from backend.billing.session import LocalSession

    session_file = tmp_path / "session.json"
    test_session = LocalSession()
    test_session._file = session_file
    test_session.save()

    with patch("backend.billing.routes.default_session", return_value=test_session):
        with patch("backend.billing.routes._invalidate_inference_cache"):
            with patch("backend.billing.routes.BillingClient") as MockClient:
                instance = MagicMock()
                # Heartbeat returns empty body (no license_token field).
                instance.heartbeat = AsyncMock(return_value={})
                MockClient.return_value = instance
                res = client.post(
                    "/api/billing/activate-callback",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={
                        "license_token": "tok-from-portal-only",
                        "trial_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    },
                )
    assert res.status_code == 200
    # Without a license_token in the heartbeat response, we keep what
    # the portal gave us.
    assert test_session.license_token == "tok-from-portal-only"


def test_activate_callback_passes_real_camera_count_to_heartbeat(client, admin_token, tmp_path):
    """activate_callback MUST pass the real DB camera count to the
    heartbeat — not 0, not the stale cache size. Otherwise billing
    never sees the device's true seat count."""
    from backend.billing.session import LocalSession
    from backend import database, models as m

    session_file = tmp_path / "session.json"
    test_session = LocalSession()
    test_session._file = session_file
    test_session.save()

    # Seed a real camera so the DB count is non-zero
    db = database.SessionLocal()
    try:
        # Don't add one if there's already one — just count
        db_count_before = db.query(m.Camera).count()
        if db_count_before == 0:
            cam = m.Camera(name="heartbeat-test-cam", is_enabled=True)
            db.add(cam)
            db.commit()
            db.refresh(cam)
    finally:
        db.close()

    # Read the count as it stands now
    db = database.SessionLocal()
    try:
        expected = db.query(m.Camera).count()
    finally:
        db.close()

    captured = {}

    with patch("backend.billing.routes.default_session", return_value=test_session):
        with patch("backend.billing.routes._invalidate_inference_cache"):
            with patch("backend.billing.routes.BillingClient") as MockClient:
                instance = MagicMock()
                async def fake_heartbeat(camera_count):
                    captured["camera_count"] = camera_count
                    return {
                        "mode": "trial_commercial",
                        "license_token": "tok",
                        "license_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                        "trial_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                        "email": "user@example.com",
                        "quota": 5,
                        "status_code": "active",
                    }
                instance.heartbeat = fake_heartbeat
                MockClient.return_value = instance

                res = client.post(
                    "/api/billing/activate-callback",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={
                        "license_token": "tok",
                        "trial_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    },
                )
    assert res.status_code == 200
    # Must be the real DB count, NOT zero (the previous bug)
    assert captured["camera_count"] == expected
    # Cleanup the seeded camera
    if db_count_before == 0:
        db = database.SessionLocal()
        try:
            cam_to_del = db.query(m.Camera).filter_by(name="heartbeat-test-cam").first()
            if cam_to_del:
                db.delete(cam_to_del)
                db.commit()
        finally:
            db.close()


def test_activate_callback_returns_200_even_when_heartbeat_fails(client, admin_token, tmp_path):
    """The license IS stored locally even if the immediate heartbeat
    can't reach billing. The background loop will reconcile."""
    from backend.billing.client import BillingServerError
    from backend.billing.session import LocalSession

    session_file = tmp_path / "session.json"
    test_session = LocalSession()
    test_session._file = session_file
    test_session.save()

    with patch("backend.billing.routes.default_session", return_value=test_session):
        with patch("backend.billing.routes._invalidate_inference_cache"):
            with patch("backend.billing.routes.BillingClient") as MockClient:
                instance = MagicMock()
                instance.heartbeat = AsyncMock(
                    side_effect=BillingServerError("Connection refused")
                )
                MockClient.return_value = instance
                res = client.post(
                    "/api/billing/activate-callback",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={
                        "license_token": "tok-stored-locally",
                        "trial_expires_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    },
                )
    # Local license is preserved; user sees 200 and can retry heartbeat manually
    assert res.status_code == 200
    assert test_session.license_token == "tok-stored-locally"
    assert test_session.last_heartbeat_status == "error"


def test_activate_callback_requires_license_token(client, admin_token):
    """An empty body is rejected by Pydantic validation (422) before
    reaching our handler. An empty license_token string triggers our
    400."""
    # 1. Empty body — Pydantic rejects (422)
    res = client.post(
        "/api/billing/activate-callback",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={},
    )
    assert res.status_code == 422

    # 2. Explicit empty token — our handler rejects (400)
    res = client.post(
        "/api/billing/activate-callback",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"license_token": ""},
    )
    assert res.status_code == 400


def test_status_requires_manage_billing(client, db_session):
    """A logged-in user WITHOUT ``manage_billing`` permission must be 403'd."""
    import json
    from backend import models as m
    from backend.auth import hash_password

    user = m.User(
        username="no_billing_user",
        password_hash=hash_password("pw"),
        permissions=json.dumps(["view_history"]),
    )
    db_session.add(user)
    db_session.commit()

    login = client.post("/api/login", json={"username": "no_billing_user", "password": "pw"})
    token = login.json()["access_token"]

    res = client.get(
        "/api/billing/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert "manage_billing" in res.json()["detail"]


def test_admin_user_can_access_billing_without_permission_string(client, db_session):
    """``is_admin=True`` users bypass granular permission checks."""
    import json
    from backend import models as m
    from backend.auth import hash_password

    user = m.User(
        username="true_admin",
        password_hash=hash_password("pw"),
        is_admin=True,
        permissions=json.dumps([]),  # explicitly empty
    )
    db_session.add(user)
    db_session.commit()

    login = client.post("/api/login", json={"username": "true_admin", "password": "pw"})
    token = login.json()["access_token"]

    res = client.get(
        "/api/billing/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


def test_all_billing_routes_require_manage_billing(client, db_session):
    """Every /api/billing/* endpoint must enforce the permission."""
    import json
    from backend import models as m
    from backend.auth import hash_password as _hash_password

    user = m.User(
        username="no_perm",
        password_hash=_hash_password("pw"),
        permissions=json.dumps(["view_history"]),
    )
    db_session.add(user)
    db_session.commit()

    login = client.post("/api/login", json={"username": "no_perm", "password": "pw"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # GET endpoints
    assert client.get("/api/billing/status", headers=headers).status_code == 403
    assert client.get("/api/billing/connect-url", headers=headers).status_code == 403

    # POST endpoints
    assert client.post("/api/billing/heartbeat/trigger", headers=headers, json={}).status_code == 403
    assert client.post("/api/billing/logout", headers=headers, json={}).status_code == 403
    assert client.post(
        "/api/billing/activate-callback",
        headers=headers,
        json={"license_token": "tok"},
    ).status_code == 403


def test_user_with_manage_billing_can_access_routes(client, db_session):
    """A non-admin user explicitly granted ``manage_billing`` can call the API."""
    import json
    from backend import models as m
    from backend.auth import hash_password as _hash_password

    user = m.User(
        username="billing_user",
        password_hash=_hash_password("pw"),
        permissions=json.dumps(["manage_billing"]),
    )
    db_session.add(user)
    db_session.commit()

    login = client.post("/api/login", json={"username": "billing_user", "password": "pw"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/api/billing/status", headers=headers)
    assert res.status_code == 200


def test_manage_billing_in_admin_permissions_list():
    """The permission must be in ADMIN_PERMISSIONS so newly-created admins
    automatically inherit it."""
    from backend.auth import ADMIN_PERMISSIONS
    assert "manage_billing" in ADMIN_PERMISSIONS


def test_heartbeat_trigger_returns_404_when_device_not_activated(client, admin_token, db_session):
    """When the billing portal returns 'Device not found' the on-prem
    route must surface 404 (not 502) so the UI can show the user an
    actionable 'Activate License' message."""
    from backend.billing.client import BillingDeviceNotFoundError

    with patch("backend.config.config.BILLING_PORTAL_URL", "https://billing.example.com"):
        with patch("backend.billing.routes.BillingClient") as MockClient:
            instance = MagicMock()
            instance.heartbeat = AsyncMock(
                side_effect=BillingDeviceNotFoundError("Device not found. Activate first via /activate.")
            )
            MockClient.return_value = instance
            res = client.post(
                "/api/billing/heartbeat/trigger",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={},
            )
    assert res.status_code == 404
    detail = res.json()["detail"].lower()
    assert "activate" in detail
    assert "billing" not in detail or "activate" in detail


def test_heartbeat_trigger_clears_local_license_on_404(client, admin_token, db_session):
    """When the billing portal returns ``code: "device_not_activated"``
    on a manual 'Sync Now' heartbeat, ALL licensing data on the
    on-prem must be wiped. This is the definitive signal that the
    HWID is not on any account — every byte of stale billing state
    (license, quota, email, camera counts, mode) must be cleared
    so the user is forced to reactivate and the inference engine
    falls back to community.

    Without this fix, the engine keeps running on commercial mode
    forever off the stale cached token, and every subsequent
    heartbeat re-fails with 404 and re-sets the same status.
    """
    from backend.billing.client import BillingDeviceNotFoundError
    from backend.billing.paths import session_path
    import json as _json
    from datetime import datetime, timezone, timedelta

    # Pre-populate a local session with a full commercial state
    # — license, trial, quota, email, camera counts, device_quota
    # — every field that ``mark_device_forgotten()`` is supposed
    # to wipe. If we miss any field in the new helper, this test
    # catches it.
    path = session_path()
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    path.write_text(_json.dumps({
        "license_token": "stale-commercial-token",
        "license_expires_at": future,
        "trial_expires_at": future,
        "subscription_expires_at": future,
        "mode": "commercial",
        "quota": 4,
        "email": "x@y.com",
        "last_heartbeat_at": past,
        "last_heartbeat_status": "active",
        "this_device_cameras": 4,
        "other_devices_cameras": 2,
        "device_count": 2,
        "device_quota": 4,
    }))

    with patch("backend.config.config.BILLING_PORTAL_URL", "https://billing.example.com"):
        with patch("backend.billing.routes.BillingClient") as MockClient:
            instance = MagicMock()
            instance.heartbeat = AsyncMock(
                side_effect=BillingDeviceNotFoundError("Device not found. Activate first via /activate.")
            )
            MockClient.return_value = instance
            res = client.post(
                "/api/billing/heartbeat/trigger",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={},
            )
    assert res.status_code == 404

    # All licensing fields are wiped.
    on_disk = _json.loads(path.read_text())
    assert on_disk["license_token"] is None
    assert on_disk["license_expires_at"] is None
    assert on_disk["trial_expires_at"] is None
    assert on_disk["subscription_expires_at"] is None
    assert on_disk["mode"] == "community"
    assert on_disk["quota"] == 0
    assert on_disk["email"] is None
    assert on_disk["this_device_cameras"] == 0
    assert on_disk["other_devices_cameras"] == 0
    assert on_disk["device_count"] == 0
    assert on_disk["device_quota"] == 0
    # Status flips to 'not_activated' so the UI can render the
    # "Activate License" prompt.
    assert on_disk["last_heartbeat_status"] == "not_activated"
    # ``last_heartbeat_at`` stays fresh so the "Last Sync" timestamp
    # in the UI shows when we last tried the cloud.
    assert on_disk["last_heartbeat_at"] is not None
    assert on_disk["last_heartbeat_at"] != past


def test_heartbeat_trigger_invalidates_inference_cache_on_404(client, admin_token, db_session):
    """On 404 we must invalidate the inference cache so the engine
    re-reads the (now-empty) license config on its next poll rather
    than continuing to use the stale commercial license it cached
    before the user clicked Unlink & Cancel.
    """
    from backend.billing.client import BillingDeviceNotFoundError
    from backend import inference
    from backend.billing.paths import session_path
    import json as _json
    from datetime import datetime, timezone, timedelta

    # Pre-populate a local session with a commercial license.
    path = session_path()
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    path.write_text(_json.dumps({
        "license_token": "stale-commercial-token",
        "license_expires_at": future,
        "trial_expires_at": None,
        "subscription_expires_at": None,
        "mode": "commercial",
        "quota": 4,
        "email": "x@y.com",
        "last_heartbeat_at": None,
        "last_heartbeat_status": "active",
        "this_device_cameras": 4,
        "other_devices_cameras": 0,
        "device_count": 1,
        "device_quota": 4,
    }))

    # Seed a non-zero last_check on the inference module's config
    # cache so we can observe the invalidation.
    inference._vv_config["last_check"] = 999

    with patch("backend.config.config.BILLING_PORTAL_URL", "https://billing.example.com"):
        with patch("backend.billing.routes.BillingClient") as MockClient:
            instance = MagicMock()
            instance.heartbeat = AsyncMock(
                side_effect=BillingDeviceNotFoundError("Device not found. Activate first via /activate.")
            )
            MockClient.return_value = instance
            res = client.post(
                "/api/billing/heartbeat/trigger",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={},
            )
    assert res.status_code == 404
    # The cache was invalidated — last_check is reset to 0 so the
    # next inference poll re-reads the (now-empty) license.
    assert inference._vv_config["last_check"] == 0


def test_heartbeat_trigger_returns_502_for_unreachable_portal(client, admin_token, db_session):
    """When the portal is unreachable we keep 502 — the user needs to
    know it's a network/server problem, not an activation issue."""
    from backend.billing.client import BillingServerError

    with patch("backend.config.config.BILLING_PORTAL_URL", "https://billing.example.com"):
        with patch("backend.billing.routes.BillingClient") as MockClient:
            instance = MagicMock()
            instance.heartbeat = AsyncMock(
                side_effect=BillingServerError("Could not connect to billing portal at https://billing.example.com")
            )
            MockClient.return_value = instance
            res = client.post(
                "/api/billing/heartbeat/trigger",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={},
            )
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "billing.example.com" in detail


def test_logout_calls_forget_device_before_clearing_session(client, admin_token, db_session):
    """When the user clicks 'Unlink & Cancel', the on-prem backend must
    call billing's /device/forget endpoint so the device is removed from
    the billing server (not just cleared locally)."""
    from backend.billing.client import BillingClient as RealBillingClient

    forget_called = {"v": False}
    clear_called = {"v": False}

    with patch("backend.billing.routes.BillingClient") as MockClient:
        instance = MagicMock()
        async def fake_forget():
            forget_called["v"] = True
            return {"status": "ok", "already_gone": False}
        instance.forget_device = fake_forget
        MockClient.return_value = instance

        with patch("backend.billing.routes.default_session") as mock_session:
            session = MagicMock()
            session.clear = MagicMock(side_effect=lambda: clear_called.update({"v": True}))
            session.save = MagicMock()
            mock_session.return_value = session

            with patch("backend.billing.routes._invalidate_inference_cache"):
                with patch("backend.config.config.BILLING_PORTAL_URL", "https://billing.example.com"):
                    res = client.post(
                        "/api/billing/logout",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={},
                    )

    assert res.status_code == 200
    assert forget_called["v"] is True
    assert clear_called["v"] is True


def test_logout_returns_502_when_forget_device_fails(client, admin_token, db_session):
    """If billing rejects the forget_device call (network/server error),
    the local session must NOT be cleared — otherwise the user thinks
    they're disconnected but billing is still charging them."""
    from backend.billing.client import BillingServerError

    with patch("backend.billing.routes.BillingClient") as MockClient:
        instance = MagicMock()
        instance.forget_device = AsyncMock(
            side_effect=BillingServerError("Connection refused")
        )
        MockClient.return_value = instance

        with patch("backend.billing.routes.default_session") as mock_session:
            session = MagicMock()
            session.clear = MagicMock()
            session.save = MagicMock()
            mock_session.return_value = session

            with patch("backend.billing.routes._invalidate_inference_cache"):
                with patch("backend.config.config.BILLING_PORTAL_URL", "https://billing.example.com"):
                    res = client.post(
                        "/api/billing/logout",
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={},
                    )

    assert res.status_code == 502
    # The local session.clear() must NOT have been called
    assert session.clear.call_count == 0