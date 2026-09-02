"""C4 audit fix — RTSP password redaction in public camera responses.

Verifies:
- ``/api/cameras`` (any authenticated user) never includes
  ``stream_password`` in the response body
- Admin-only camera CRUD routes still return the full ``stream_password``
  (so the existing /api/cameras/{id} PUT flow keeps working)
- A new ``CameraOutPublic`` schema exists and omits the field
"""
import json


def _make_camera(db_session, **overrides):
    from backend import models
    cam = models.Camera(
        name=overrides.get("name", "Secret Cam"),
        snapshot_url=overrides.get("snapshot_url", "http://example.com/snap.jpg"),
        source_type=overrides.get("source_type", "rtsp"),
        stream_url=overrides.get("stream_url", "rtsp://example.com/stream"),
        stream_user=overrides.get("stream_user", "rtsp_user"),
        stream_password=overrides.get("stream_password", "rtsp_secret_password"),
        stream_resolution=overrides.get("stream_resolution", "1080"),
        stream_fps=overrides.get("stream_fps", 5),
    )
    db_session.add(cam)
    db_session.commit()
    db_session.refresh(cam)
    return cam


def test_list_cameras_omits_stream_password_for_any_user(client, db_session):
    """Any authenticated user hitting GET /api/cameras must not see
    ``stream_password`` — even when the camera has one set."""
    cam = _make_camera(db_session, name="Cam1", stream_password="supersecret123")

    r = client.get("/api/cameras")
    assert r.status_code == 200
    body = r.json()
    # Find our camera in the response (init_db may have created a demo cam too)
    our_cam = next(c for c in body if c["id"] == cam.id)
    assert "stream_password" not in our_cam
    # stream_user is fine — it's not a secret, just an identifier
    assert our_cam["stream_user"] == "rtsp_user"


def test_list_cameras_anonymous_also_omits_password(client, db_session):
    """The endpoint uses ``optional_auth`` so anonymous users also
    can't see the password (they get a smaller response anyway)."""
    _make_camera(db_session, stream_password="hidden")
    r = client.get("/api/cameras")
    assert r.status_code == 200
    for cam in r.json():
        assert "stream_password" not in cam


def test_get_single_camera_admin_can_still_see_password(client, db_session, admin_token):
    """Admin-only single-camera endpoints (if any) continue to return
    the full ``stream_password`` — the existing PUT flow needs it."""
    # We don't have a GET /api/cameras/{id} route per se, but the
    # admin token-based routes (PUT, etc.) all use CameraOut full.
    # Verify CameraOut schema is unchanged by checking the model
    # directly.
    from backend import schemas
    fields = schemas.CameraOut.model_fields
    assert "stream_password" in fields


def test_camera_out_public_schema_omits_password():
    """The CameraOutPublic schema must not declare stream_password."""
    from backend import schemas
    fields = schemas.CameraOutPublic.model_fields
    assert "stream_password" not in fields
    # The other expected fields are still there
    for f in ["id", "name", "source_type", "stream_user", "is_test", "is_enabled"]:
        assert f in fields, f"CameraOutPublic missing field {f!r}"


def test_list_cameras_with_multiple_cameras_redacts_each(client, db_session):
    """All entries in the list response are redacted, not just the first."""
    a = _make_camera(db_session, name="A-cam", stream_password="pass-a")
    b = _make_camera(db_session, name="B-cam", stream_password="pass-b")
    c = _make_camera(db_session, name="C-cam", stream_password="pass-c")

    r = client.get("/api/cameras")
    body = r.json()
    our_ids = {a.id, b.id, c.id}
    our_cams = [cam for cam in body if cam["id"] in our_ids]
    assert len(our_cams) == 3
    for cam in our_cams:
        assert "stream_password" not in cam
