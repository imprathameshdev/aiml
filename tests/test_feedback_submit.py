"""Tests for the corrections tracking in ``submit_feedback``.

The endpoint accepts per-space ``original_occupied`` (the AI's
prediction) and ``occupied`` (the user's final answer). The metadata
JSON we write to disk must:

* Always emit a parallel ``original_occupancy`` array.
* Populate a ``corrections`` list with one entry per space where the
  user flipped the AI's answer (with a ``kind`` label so the ML
  pipeline can split FP / FN rates).
* Treat ``original_occupied=None`` as "unknown original" — we still
  record the parallel array entry as ``None`` but don't add the space
  to ``corrections``.

These tests don't try to hit S3 — the on-prem handler just writes
files to ``training_feedback/``. We read those files back to inspect
the JSON.
"""

import datetime
import json
import os
from pathlib import Path

import pytest


def _make_scan_with_blob(db, camera, blob_bytes=b"fake-jpeg-bytes"):
    """Create a CameraScan + corresponding image blob so submit_feedback
    can find the image via ``database.snapshot_db.get(str(scan_id), timestamp=scan_id)``."""
    from backend import database, models

    scan = models.CameraScan(
        camera_id=camera.id,
        timestamp=datetime.datetime.now(datetime.UTC),
        has_image=True,
        inference_speed=0.42,
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)
    database.snapshot_db.put(str(scan.id), blob_bytes, timestamp=scan.id)
    return scan


def _read_last_metadata(annotations_dir: Path, images_dir: Path):
    """Read the most recently written metadata JSON from the
    ``training_feedback/annotations/`` directory.  The submit
    handler writes to ``$LV_HOME/training_feedback/...`` (not a
    relative path), so callers pass the resolved directories in.
    Returns ``(json_path, metadata_dict, image_path)``.
    """
    json_files = sorted(annotations_dir.glob("*.json"))
    assert json_files, f"expected submit_feedback to write a metadata file to {annotations_dir}"
    json_path = json_files[-1]
    metadata = json.loads(json_path.read_text())

    img_name = Path(metadata["file_name"]).name
    img_path = images_dir / img_name
    return json_path, metadata, img_path


@pytest.fixture
def feedback_setup(monkeypatch, tmp_path, db_session):
    """Redirect ``$LV_HOME`` (and therefore ``config.DATA_DIR``)
    into a temp dir so the submit-feedback handler (which now
    writes to ``$LV_HOME/training_feedback/...``) and the
    queue-counter helper (which reads from the same path)
    operate on a clean sandbox.

    Belt-and-braces cleanup: see ``test_feedback_pending.py`` for
    the rationale.
    """
    import shutil
    for base in (
        os.path.join(os.path.dirname(__file__), ".test_lv_home"),
        "/var/lib/lotvulture",
    ):
        legacy_dir = os.path.join(base, "training_feedback", "annotations")
        if os.path.isdir(legacy_dir):
            shutil.rmtree(legacy_dir, ignore_errors=True)

    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    from backend import config
    config.config.LV_HOME = str(tmp_path)
    yield tmp_path
    try:
        delattr(config.config, "LV_HOME")
    except AttributeError:
        pass


def test_submit_feedback_records_no_corrections_when_user_confirmed(feedback_setup, client, admin_token, db_session):
    """User clicked Submit without changing anything → corrections is
    empty (confirmed-correct is still a useful signal)."""
    from backend import models

    cam = models.Camera(name="Test Camera")
    db_session.add(cam)
    db_session.commit()
    scan = _make_scan_with_blob(db_session, cam)

    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "scan_id": scan.id,
            "camera_id": cam.id,
            "spaces": [
                {"space_id": 1, "points": [0, 0, 100, 0, 100, 100], "occupied": True,  "original_occupied": True},
                {"space_id": 2, "points": [0, 0, 100, 0, 100, 100], "occupied": False, "original_occupied": False},
            ],
        },
    )
    assert res.status_code == 200

    annotations_dir = Path(feedback_setup) / "training_feedback" / "annotations"
    images_dir = Path(feedback_setup) / "training_feedback" / "images"
    _, metadata, _ = _read_last_metadata(annotations_dir, images_dir)

    assert metadata["original_occupancy"] == [1, 0]
    assert metadata["occupancy"] == [1, 0]
    assert metadata["corrections"] == []


def test_submit_feedback_records_corrections_with_kind_label(feedback_setup, client, admin_token, db_session):
    """User flipped the AI's answer on two spots → two entries in
    corrections with the right ``kind`` (false_positive for AI too
    eager, false_negative for AI too cautious)."""
    from backend import models

    cam = models.Camera(name="Test Camera")
    db_session.add(cam)
    db_session.commit()
    scan = _make_scan_with_blob(db_session, cam)

    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "scan_id": scan.id,
            "camera_id": cam.id,
            "spaces": [
                # AI said occupied → user corrected to free (false positive)
                {"space_id": 1, "points": [0, 0, 100, 100], "occupied": False, "original_occupied": True},
                # AI said free → user corrected to occupied (false negative)
                {"space_id": 2, "points": [0, 0, 100, 100], "occupied": True,  "original_occupied": False},
                # Confirmed correct — NOT in corrections
                {"space_id": 3, "points": [0, 0, 100, 100], "occupied": True,  "original_occupied": True},
            ],
        },
    )
    assert res.status_code == 200

    annotations_dir = Path(feedback_setup) / "training_feedback" / "annotations"
    images_dir = Path(feedback_setup) / "training_feedback" / "images"
    _, metadata, _ = _read_last_metadata(annotations_dir, images_dir)

    assert len(metadata["corrections"]) == 2
    by_space = {c["space_id"]: c for c in metadata["corrections"]}
    assert by_space[1]["kind"] == "false_positive"
    assert by_space[1]["was_occupied"] is True
    assert by_space[1]["now_occupied"] is False
    assert by_space[2]["kind"] == "false_negative"
    assert by_space[2]["was_occupied"] is False
    assert by_space[2]["now_occupied"] is True


def test_submit_feedback_skips_correction_when_original_unknown(feedback_setup, client, admin_token, db_session):
    """When the frontend can't determine the AI's prediction (None),
    we still record the parallel array entry but DON'T add the space
    to corrections — we don't know if it was changed."""
    from backend import models

    cam = models.Camera(name="Test Camera")
    db_session.add(cam)
    db_session.commit()
    scan = _make_scan_with_blob(db_session, cam)

    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "scan_id": scan.id,
            "camera_id": cam.id,
            "spaces": [
                {"space_id": 1, "points": [0, 0, 100, 100], "occupied": True,  "original_occupied": None},
                # Also: omit the field entirely (backward compat with
                # older frontends that don't send it).
                {"space_id": 2, "points": [0, 0, 100, 100], "occupied": False},
            ],
        },
    )
    assert res.status_code == 200

    annotations_dir = Path(feedback_setup) / "training_feedback" / "annotations"
    images_dir = Path(feedback_setup) / "training_feedback" / "images"
    _, metadata, _ = _read_last_metadata(annotations_dir, images_dir)

    assert metadata["original_occupancy"] == [None, None]
    assert metadata["corrections"] == []


def test_submit_feedback_original_occupancy_parallel_to_occupancy(feedback_setup, client, admin_token, db_session):
    """``original_occupancy[i]`` and ``occupancy[i]`` MUST refer to
    the same space — verified by checking they have the same length
    and indexing matches."""
    from backend import models

    cam = models.Camera(name="Test Camera")
    db_session.add(cam)
    db_session.commit()
    scan = _make_scan_with_blob(db_session, cam)

    spaces = [
        {"space_id": i + 1, "points": [0, 0, 100, 100], "occupied": bool(i % 2), "original_occupied": bool((i + 1) % 2)}
        for i in range(7)
    ]
    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"scan_id": scan.id, "camera_id": cam.id, "spaces": spaces},
    )
    assert res.status_code == 200

    annotations_dir = Path(feedback_setup) / "training_feedback" / "annotations"
    images_dir = Path(feedback_setup) / "training_feedback" / "images"
    _, metadata, _ = _read_last_metadata(annotations_dir, images_dir)
    assert len(metadata["original_occupancy"]) == len(metadata["occupancy"]) == 7
    # Corrections: spaces where the two arrays differ
    expected_corrections = sum(
        1 for orig, final in zip(metadata["original_occupancy"], metadata["occupancy"]) if orig != final
    )
    assert len(metadata["corrections"]) == expected_corrections


def test_submit_feedback_requires_manage_cameras(client, db_session):
    """A user without ``manage_cameras`` permission can't submit."""
    from backend import models

    cam = models.Camera(name="Test Camera")
    db_session.add(cam)
    db_session.commit()
    scan = _make_scan_with_blob(db_session, cam)

    # Create a user with NO permissions (plain-text password per spec)
    from backend.auth import hash_password
    regular = models.User(username="viewer", password_hash=hash_password("x"), permissions="[]")
    db_session.add(regular)
    db_session.commit()

    res = client.post(
        "/api/login",
        json={"username": "viewer", "password": "x"},
    )
    token = res.json()["access_token"]

    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={"scan_id": scan.id, "camera_id": cam.id, "spaces": []},
    )
    assert res.status_code == 403


def test_submit_feedback_404_when_image_missing(client, admin_token, db_session):
    """If the image blob has been pruned (e.g. retention), the
    endpoint returns 404 — same behavior as before this feature."""
    from backend import database, models

    cam = models.Camera(name="Test Camera")
    db_session.add(cam)
    db_session.commit()

    # Create scan WITHOUT putting a blob
    scan = models.CameraScan(camera_id=cam.id, has_image=False)
    db_session.add(scan)
    db_session.commit()

    # Belt-and-braces: ensure no blob exists at this scan_id by
    # omitting the put. RollingLMDB doesn't support individual
    # blob deletion, so we just skip putting any data for this
    # scan and the get below will return None.

    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "scan_id": scan.id,
            "camera_id": cam.id,
            "spaces": [{"space_id": 1, "points": [0, 0, 100, 100], "occupied": True}],
        },
    )
    assert res.status_code == 404


def test_submit_feedback_writes_under_data_dir_not_cwd(
    feedback_setup, client, admin_token, db_session, tmp_path, monkeypatch
):
    """The submit endpoint MUST write to ``$LV_HOME/training_feedback/``
    (where the background uploader looks), not to a relative
    ``training_feedback/`` next to the CWD.  On a systemd / Docker
    install the CWD is usually ``/`` and the relative path is
    either unwritable or — worse — silently goes to a different
    location than the operator expects, leaving the queue
    counter reading from a different (empty) directory than the
    submit handler is filling.  Pin the fix.
    """
    from backend import models

    # Move to a CWD that is NOT tmp_path so we can prove the
    # endpoint does NOT write there.
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    cam = models.Camera(name="Datadir Camera")
    db_session.add(cam)
    db_session.commit()
    scan = _make_scan_with_blob(db_session, cam)

    res = client.post(
        "/api/admin/feedback/submit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "scan_id": scan.id,
            "camera_id": cam.id,
            "spaces": [
                {"space_id": 1, "points": [0, 0, 100, 100], "occupied": True, "original_occupied": True},
            ],
        },
    )
    assert res.status_code == 200

    # The handler must have written under the resolved DATA_DIR
    # (which the feedback_setup fixture redirects to ``tmp_path``),
    # not under CWD.
    expected_annotations = Path(feedback_setup) / "training_feedback" / "annotations"
    expected_images = Path(feedback_setup) / "training_feedback" / "images"
    assert expected_annotations.is_dir(), (
        f"submit_feedback did not create {expected_annotations}; "
        f"the handler likely wrote to a relative path under CWD "
        f"({other_cwd}) instead of config.DATA_DIR."
    )
    assert expected_images.is_dir()
    assert list(expected_annotations.glob("*.json")), "no metadata JSON written"
    assert list(expected_images.glob("*.jpg")), "no image file written"

    # Belt-and-braces: nothing leaked into the CWD.
    assert not (other_cwd / "training_feedback").exists(), (
        f"submit_feedback wrote to a relative path under CWD "
        f"({other_cwd}/training_feedback); the uploader at "
        f"{expected_annotations} will never see these files."
    )


def test_pending_count_reads_from_data_dir(feedback_setup, client, admin_token, db_session):
    """``/api/admin/feedback/pending`` reads from
    ``$LV_HOME/training_feedback/annotations`` — the same path
    the submit endpoint writes to — so the count reflects what
    the operator actually submitted, not whatever happens to
    live at ``./training_feedback/annotations`` relative to the
    CWD.  Submit a few items and verify the count matches.
    """
    from backend import models

    cam = models.Camera(name="Pending Camera")
    db_session.add(cam)
    db_session.commit()

    # Submit 3 feedback items.
    for i in range(3):
        scan = _make_scan_with_blob(db_session, cam, blob_bytes=f"bytes-{i}".encode())
        res = client.post(
            "/api/admin/feedback/submit",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "scan_id": scan.id,
                "camera_id": cam.id,
                "spaces": [
                    {"space_id": 1, "points": [0, 0, 100, 100], "occupied": bool(i % 2),
                     "original_occupied": bool((i + 1) % 2)},
                ],
            },
        )
        assert res.status_code == 200

    # Now check the pending count.  The handler must read from
    # ``config.DATA_DIR`` (which the feedback_setup fixture
    # redirects to ``tmp_path``) — not from the CWD.
    res = client.get(
        "/api/admin/feedback/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["pending"] == 3, (
        f"pending count is {body['pending']}, expected 3.  The "
        f"endpoint is likely reading from a relative path that "
        f"doesn't match where submit_feedback wrote to."
    )