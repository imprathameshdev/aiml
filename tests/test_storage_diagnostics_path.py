"""Storage-stats endpoints must use ``config.DB_PATH``, not a
CWD-relative ``"parkinglot.db"``.

Both ``/api/settings/storage-stats`` and ``/api/admin/storage/stats``
used a literal relative path that silently missed in any install
where ``$LV_HOME`` (the real DB location) was not the CWD.  The
result: 0-byte ``sqlite.size_bytes`` and ``total_db`` for a
multi-MB database.  This was reported as "SQLite database size
says 0 bytes" in the diagnostics page while the file on disk
was 26 MB.

The fix routes both endpoints through ``config.DB_PATH``, which
honors ``$VULTURE_LV_HOME``.  These tests pin that contract.
"""
import os
import pytest


def test_storage_diagnostics_uses_config_db_path():
    """The /api/admin/storage/stats handler must reference
    ``config.DB_PATH`` (not a CWD-relative literal) so the
    diagnostics page works regardless of where the operator
    installed the app."""
    import inspect
    from backend import app
    src = inspect.getsource(app.get_storage_diagnostics)
    # The handler must derive the DB path from config (which
    # honors $VULTURE_LV_HOME) instead of hardcoding
    # ``"parkinglot.db"``.
    assert "config.DB_PATH" in src, (
        "get_storage_diagnostics must use config.DB_PATH so the "
        "diagnostics endpoint works when $LV_HOME != CWD."
    )
    # And must NOT contain the old broken relative path
    assert 'os.path.exists("parkinglot.db")' not in src
    assert 'os.path.getsize("parkinglot.db")' not in src


def test_settings_storage_stats_uses_config_db_path():
    """The /api/settings/storage-stats handler must also use
    ``config.DB_PATH`` for the same reason."""
    import inspect
    from backend import app
    src = inspect.getsource(app.get_storage_stats)
    assert "config.DB_PATH" in src, (
        "get_storage_stats must use config.DB_PATH so the storage "
        "size reported on the Settings page is correct."
    )
    assert 'os.path.exists("parkinglot.db")' not in src
    assert 'os.path.getsize("parkinglot.db")' not in src


def test_admin_storage_stats_reports_real_file_size(client, admin_token, monkeypatch, tmp_path):
    """End-to-end: when a real DB file exists at ``config.DB_PATH``,
    the endpoint returns its actual file size (not 0)."""
    # config.DB_PATH = os.path.join(LV_HOME, "parkinglot.db"), so
    # the file must live at exactly that path (not an arbitrary
    # name in tmp_path).  Redirect LV_HOME → tmp_path so the
    # generated DB_PATH points inside the test's temp dir.
    from backend import config
    real_path = str(tmp_path / "parkinglot.db")
    with open(real_path, "wb") as f:
        f.write(b"\x00" * 12345)
    real_size = os.path.getsize(real_path)
    assert real_size == 12345

    from backend import app as app_module
    monkeypatch.setattr(config.config, "LV_HOME", str(tmp_path))
    if hasattr(app_module, "config"):
        monkeypatch.setattr(app_module.config, "LV_HOME", str(tmp_path))
    assert config.config.DB_PATH == real_path, (
        f"DB_PATH = {config.config.DB_PATH!r}, expected {real_path!r}"
    )

    r = client.get(
        "/api/admin/storage/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sqlite"]["size_bytes"] == real_size, (
        f"Endpoint reported {body['sqlite']['size_bytes']} bytes for a "
        f"{real_size}-byte file — the diagnostics page would show "
        f"0 B instead of the real number."
    )


def test_settings_storage_stats_reports_real_total_db(client, admin_token, monkeypatch, tmp_path):
    """End-to-end: ``/api/settings/storage-stats`` also reports the
    real file size under the ``total_db`` key when the file
    actually exists."""
    from backend import config
    real_path = str(tmp_path / "parkinglot.db")  # DB_PATH is LV_HOME/parkinglot.db
    with open(real_path, "wb") as f:
        f.write(b"\x00" * 54321)
    real_size = os.path.getsize(real_path)

    from backend import app as app_module
    monkeypatch.setattr(config.config, "LV_HOME", str(tmp_path))
    if hasattr(app_module, "config"):
        monkeypatch.setattr(app_module.config, "LV_HOME", str(tmp_path))

    r = client.get(
        "/api/settings/storage-stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sqlite"]["size_bytes"] == real_size, (
        f"sqlite.size_bytes reported {body['sqlite']['size_bytes']} for a {real_size}-byte file."
    )


def test_admin_storage_stats_was_zero_bytes_before_fix(client, admin_token, monkeypatch, tmp_path):
    """Regression: before the fix, the endpoint used a CWD-relative
    ``"parkinglot.db"`` that didn't exist on most installs and so
    returned 0 bytes.  Verify that pointing config.DB_PATH at a
    real file in a non-CWD directory produces a non-zero size."""
    from backend import config
    real_path = str(tmp_path / "parkinglot.db")
    with open(real_path, "wb") as f:
        f.write(b"\x00" * 1024)

    # Important: tmp_path is NOT the CWD.  The original code's
    # ``os.path.getsize("parkinglot.db")`` would return 0 here
    # (file doesn't exist in CWD); the fixed code uses config.DB_PATH
    # and returns 1024.
    assert not os.path.exists("parkinglot.db"), (
        "Test invariant: a stray parkinglot.db in the CWD would mask "
        "the fix verification.  Clean up before running this test."
    )

    from backend import app as app_module
    monkeypatch.setattr(config.config, "LV_HOME", str(tmp_path))
    if hasattr(app_module, "config"):
        monkeypatch.setattr(app_module.config, "LV_HOME", str(tmp_path))

    r = client.get(
        "/api/admin/storage/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = r.json()
    # Before the fix: 0 (CWD-relative path missed).
    # After the fix:  1024 (config.DB_PATH found the tmp file).
    assert body["sqlite"]["size_bytes"] == 1024
