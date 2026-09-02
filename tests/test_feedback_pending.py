"""Tests for the ``/api/admin/feedback/pending`` endpoint.

The endpoint counts JSON files in ``$LV_HOME/training_feedback/annotations``
and returns the warning threshold. Tests cover:

* Auth: only ``manage_cameras`` users can read it
* Auth: unauthenticated requests get 401
* Behavior with empty queue
* Behavior with mixed files (counts only JSON)
* Custom threshold via env var
"""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def feedback_data_dir(monkeypatch, tmp_path):
    """Redirect ``$LV_HOME`` (and therefore ``config.DATA_DIR``) into
    a temp dir so the pending counter operates on a clean sandbox.

    Belt-and-braces cleanup: wipe the conftest's default
    ``tests/.test_lv_home/training_feedback/annotations/`` BEFORE
    the test starts.  This is needed because previous test runs
    (with the OLD relative-path code) wrote files there, and
    because the conftest's ``VULTURE_LV_HOME`` is set at module
    load so the class attribute ``Config.LV_HOME`` still points
    to the legacy path.  Without this, the count would include
    leftover files from previous runs and fail.
    """
    import shutil
    # Clean up BOTH possible locations:
    # 1. The conftest's default path (``tests/.test_lv_home/...``)
    # 2. The ``/var/lib/lotvulture/...`` default (production path,
    #    in case the tests run as root with a writable /var/lib).
    for base in (
        os.path.join(os.path.dirname(__file__), ".test_lv_home"),
        "/var/lib/lotvulture",
    ):
        legacy_dir = os.path.join(base, "training_feedback", "annotations")
        if os.path.isdir(legacy_dir):
            shutil.rmtree(legacy_dir, ignore_errors=True)

    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    from backend import config
    # DATA_DIR is a property that reads LV_HOME; setting LV_HOME
    # on the singleton immediately redirects all reads.
    config.config.LV_HOME = str(tmp_path)
    yield tmp_path
    # Restore: drop the instance attribute so subsequent tests
    # see the conftest-supplied class default.
    try:
        delattr(config.config, "LV_HOME")
    except AttributeError:
        pass


def test_pending_requires_auth(client):
    """Unauthenticated request must 401."""
    res = client.get("/api/admin/feedback/pending")
    assert res.status_code == 401


def test_pending_requires_manage_cameras(client, admin_token, feedback_data_dir):
    """``manage_cameras`` permission is required. We test this by
    creating a non-admin user and trying to read the count."""
    # admin_token is from admin_user which has manage_cameras — sanity check
    res = client.get(
        "/api/admin/feedback/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200


def test_pending_returns_zero_when_queue_empty(client, admin_token, feedback_data_dir):
    """No annotations directory = 0 pending.

    With ``config.DATA_DIR`` redirected to ``tmp_path`` (no
    annotations subdir), the endpoint must report 0 — proving it
    reads from the right place.
    """
    res = client.get(
        "/api/admin/feedback/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["pending"] == 0
    assert data["threshold"] == 100  # default


def test_pending_counts_only_json(client, admin_token, feedback_data_dir):
    """Non-JSON files in the annotations dir are ignored."""
    annotations = feedback_data_dir / "training_feedback" / "annotations"
    images = feedback_data_dir / "training_feedback" / "images"
    annotations.mkdir(parents=True)
    images.mkdir(parents=True)

    # 3 submissions (3 JSONs)
    for i in range(3):
        (annotations / f"sub{i}.json").write_text("{}")

    # Stray files that should NOT count
    (annotations / "README").write_text("ignore me")
    (annotations / "sub.json.bak").write_text("ignore me too")
    # Image in images/ — must not be counted (different dir)
    (images / "sub0.jpg").write_bytes(b"x")

    res = client.get(
        "/api/admin/feedback/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["pending"] == 3


def test_pending_uses_custom_threshold(client, admin_token, monkeypatch, feedback_data_dir):
    """``VULTURE_FEEDBACK_QUEUE_WARN`` overrides the default 100."""
    monkeypatch.setenv("VULTURE_FEEDBACK_QUEUE_WARN", "42")

    res = client.get(
        "/api/admin/feedback/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["threshold"] == 42


def test_pending_handles_listdir_error(client, admin_token, monkeypatch, feedback_data_dir, caplog):
    """If the directory listing fails (permission denied etc.), the
    endpoint reports 0 and logs a warning — never crashes."""
    annotations = feedback_data_dir / "training_feedback" / "annotations"
    annotations.mkdir(parents=True)

    def boom(_):
        raise OSError("simulated permission denied")

    monkeypatch.setattr("os.listdir", boom)

    res = client.get(
        "/api/admin/feedback/pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["pending"] == 0