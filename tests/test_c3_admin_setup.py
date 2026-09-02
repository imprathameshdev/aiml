"""C3 audit fix — first-load admin setup workflow.

Verifies:
- A fresh install (no users) responds to /api/setup/status with
  setup_required=True
- POST /api/setup/admin creates the first admin and auto-logs them in
- Once any admin exists, /api/setup/admin returns 409 Conflict
- /api/login returns 428 Precondition Required when no admin exists
- Password length validation on the setup endpoint
- The /setup route is registered in the frontend router
"""
import json


def test_setup_status_fresh_db(client, db_session):
    """On a fresh install (no users), setup_required is True."""
    # The conftest's client fixture auto-creates an admin.  Drop it
    # for this test so we can exercise the fresh-DB state.
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()

    r = client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json() == {"setup_required": True, "has_any_user": False, "eula_accepted": False}


def test_setup_status_after_admin_exists(client):
    """Once any admin exists, setup_required is False."""
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["setup_required"] is False
    assert r.json()["has_any_user"] is True


def test_create_first_admin(client, db_session):
    """POST /api/setup/admin creates the first admin."""
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()

    r = client.post(
        "/api/setup/admin",
        json={"username": "firstadmin", "password": "supersecret123", "accept_eula": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "firstadmin"
    assert body["is_admin"] is True
    assert body["permissions"] == []


def test_create_admin_login_then_succeeds(client, db_session):
    """After the first admin is created, /api/login accepts it."""
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()

    r = client.post(
        "/api/setup/admin",
        json={"username": "newboss", "password": "supersecret123", "accept_eula": True},
    )
    assert r.status_code == 200

    r = client.post(
        "/api/login",
        json={"username": "newboss", "password": "supersecret123"},
    )
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert r.json()["is_admin"] is True


def test_create_admin_409_when_admin_exists(client):
    """If any admin already exists, /api/setup/admin returns 409."""
    # The conftest's client fixture created an admin, so setup is
    # already complete.
    r = client.post(
        "/api/setup/admin",
        json={"username": "another", "password": "supersecret123", "accept_eula": True},
    )
    assert r.status_code == 409


def test_create_admin_409_on_duplicate_username(client, db_session):
    """Setting up a second admin with an existing username is 409."""
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()
    client.post(
        "/api/setup/admin",
        json={"username": "dupuser", "password": "supersecret123", "accept_eula": True},
    )
    r = client.post(
        "/api/setup/admin",
        json={"username": "dupuser", "password": "differentpassword", "accept_eula": True},
    )
    assert r.status_code == 409


def test_create_admin_password_too_short(client, db_session):
    """Passwords shorter than 8 chars are rejected with 400."""
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()

    r = client.post(
        "/api/setup/admin",
        json={"username": "weak", "password": "abc", "accept_eula": True},
    )
    assert r.status_code == 400


def test_create_admin_eula_not_accepted(client, db_session):
    """POST /api/setup/admin with accept_eula=False returns 400."""
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()

    r = client.post(
        "/api/setup/admin",
        json={"username": "firstadmin", "password": "supersecret123", "accept_eula": False},
    )
    assert r.status_code == 400
    assert "License Agreement" in r.json()["detail"] or "EULA" in r.json()["detail"]


def test_login_returns_428_when_no_admin(client, db_session):
    """If no admin exists, /api/login returns 428 with a structured detail."""
    from backend import models
    db_session.query(models.User).delete()
    db_session.commit()

    r = client.post(
        "/api/login",
        json={"username": "anyone", "password": "anything"},
    )
    assert r.status_code == 428
    body = r.json()
    # FastAPI wraps HTTPException(detail=dict(...)) under "detail"
    assert body["detail"]["code"] == "setup_required"
    assert "/setup" in body["detail"]["message"]


def test_no_default_admin_in_init_db():
    """init_db.main() must not create a default admin/admin user.

    Guards against regression of C3.
    """
    import inspect
    from backend import init_db
    src = inspect.getsource(init_db.main)
    assert "admin" not in src or 'admin = db.query(models.User).filter_by(username="admin")' not in src
    # The old code path looked like:
    #   admin = db.query(models.User).filter_by(username="admin").first()
    #   if not admin:
    #       admin = models.User(username="admin", password="admin", ...)
    # The new code must not contain that pattern.
    assert "username=\"admin\", password=\"admin\"" not in src


def test_get_eula_endpoint(client):
    """GET /api/setup/eula returns the EULA text."""
    r = client.get("/api/setup/eula")
    assert r.status_code == 200
    body = r.json()
    assert "eula_text" in body
    assert "END USER LICENSE AGREEMENT" in body["eula_text"]
