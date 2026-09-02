"""Bcrypt password authentication tests.

Verifies:
- Newly-created users have ``password_hash`` (bcrypt) set.
- Newly-set passwords via /api/change-password are bcrypt-hashed.
- Login works with bcrypt-hashed users.
- Login rejects wrong passwords.
- Old passwords are invalidated after a password change.

The legacy plaintext migration support (the "C2 audit fix") was
removed along with the underlying ``User.password`` column and
``must_change_password`` field — the app was never deployed with
legacy plaintext passwords, so we don't need to support them.
"""
import json


def _make_user(db_session, *, username, password, is_admin=True):
    from backend import models
    from backend.auth import hash_password
    u = models.User(
        username=username,
        is_admin=is_admin,
        permissions=json.dumps([
            "manage_cameras", "view_history", "view_metrics",
            "edit_settings", "manage_users", "manage_api_keys",
            "manage_billing", "manage_alerts",
        ]),
        password_hash=hash_password(password),
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


def test_create_user_via_api_uses_bcrypt(client, admin_token, db_session):
    """POST /api/users must write a bcrypt hash."""
    from backend.auth import verify_password
    r = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "newuser",
            "password": "freshpass123",
            "is_admin": False,
            "permissions": ["view_history"],
        },
    )
    assert r.status_code == 200

    from backend import models
    u = db_session.query(models.User).filter_by(username="newuser").first()
    assert u is not None
    assert u.password_hash is not None
    # The hash is a valid bcrypt hash that verifies
    assert verify_password("freshpass123", u.password_hash)
    assert not verify_password("wrong", u.password_hash)


def test_login_rejects_wrong_password_for_bcrypt_user(client, db_session):
    """Bcrypt-hashed users still reject wrong passwords."""
    _make_user(db_session, username="bcuser", password="correctpw")
    r = client.post("/api/login", json={"username": "bcuser", "password": "wrongpw"})
    # auth.login_user returns None on failure; FastAPI returns 422
    # for None return values (return-type validation), or the body
    # is None.
    if r.status_code == 200:
        assert r.json() is None or r.json().get("access_token") is None
    else:
        assert r.status_code in (400, 401, 422)


def test_login_response_no_longer_includes_must_reset(client, db_session):
    """The ``must_reset`` field was part of the C2 audit fix and was
    removed when the fix was deleted. Pin that the login response
    no longer carries the field so a future refactor that adds it
    back is intentional (and can re-introduce the migration code
    in the same commit)."""
    _make_user(db_session, username="noretry", password="anypw")
    r = client.post("/api/login", json={"username": "noretry", "password": "anypw"})
    assert r.status_code == 200
    assert "must_reset" not in r.json()
    # Same for the JWT payload — decode and check.
    import base64
    import json as _json
    token = r.json()["access_token"]
    payload_b64 = token.split(".")[1]
    # Pad base64 to a multiple of 4
    payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
    payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
    assert "must_reset" not in payload


def test_change_password_uses_bcrypt(client, db_session):
    """POST /api/change-password re-hashes with bcrypt."""
    from backend.auth import verify_password
    _make_user(db_session, username="changer", password="oldpw12345")

    # Login first to get a token
    r = client.post("/api/login", json={"username": "changer", "password": "oldpw12345"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    # Change password
    r = client.post(
        "/api/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "oldpw12345", "new_password": "brand_new_pw"},
    )
    assert r.status_code == 200

    u = db_session.query(__import__("backend").models.User).filter_by(username="changer").first()
    assert u.password_hash is not None
    assert verify_password("brand_new_pw", u.password_hash)

    # Old password no longer works — login returns None, which
    # FastAPI wraps as 422 (return-type validation failure).
    r = client.post("/api/login", json={"username": "changer", "password": "oldpw12345"})
    if r.status_code == 200:
        assert r.json() is None or r.json().get("access_token") is None
    else:
        assert r.status_code in (400, 401, 422)


def test_change_password_without_old_endpoint_removed():
    """The ``/api/change-password-without-old`` endpoint was part of
    the C2 audit fix (forced password reset for legacy users). With
    that fix removed, the endpoint no longer exists. Pin that the
    route isn't registered so a future refactor that adds it back
    is intentional."""
    from backend import app as _app
    paths = {r.path for r in _app.app.routes}
    assert "/api/change-password-without-old" not in paths
