"""Unit tests for the feedback uploader's pure helpers.

The HTTP / asyncio pieces are tested via end-to-end tests in
``test_billing_routes.py`` and ``test_feedback_uploader.py``. Here we
exercise the small, dependency-free helpers — pending count,
quarantine logic, attempt bumping, file movement — so they're cheap
to run and obvious to debug when something breaks.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest


def _setup_dirs(monkeypatch, tmp_path):
    """Redirect the uploader's three directories under tmp_path.

    The uploader reads from module-level constants, so we have to
    monkeypatch those for the duration of the test. Each test gets
    a fresh subdirectory tree so they don't see each other's files.
    """
    from backend.billing import feedback_uploader as fu

    annotations = tmp_path / "annotations"
    images = tmp_path / "images"
    failed = tmp_path / "failed"
    for d in (annotations, images, failed):
        d.mkdir()

    monkeypatch.setattr(fu, "ANNOTATIONS_DIR", str(annotations))
    monkeypatch.setattr(fu, "IMAGES_DIR", str(images))
    monkeypatch.setattr(fu, "FAILED_DIR", str(failed))
    return fu, str(annotations), str(images), str(failed)


def test_pending_count_zero_when_dir_missing(monkeypatch, tmp_path):
    from backend.billing import feedback_uploader as fu

    nonexistent = tmp_path / "does-not-exist"
    monkeypatch.setattr(fu, "ANNOTATIONS_DIR", str(nonexistent))

    assert fu.pending_count() == 0


def test_pending_count_counts_only_json(monkeypatch, tmp_path):
    fu, annotations, _, _ = _setup_dirs(monkeypatch, tmp_path)

    # Mix of JSON + non-JSON files — only JSON counts.
    (tmp_path / "annotations" / "a.json").write_text("{}")
    (tmp_path / "annotations" / "b.json").write_text("{}")
    (tmp_path / "annotations" / "ignore.txt").write_text("nope")
    (tmp_path / "annotations" / "README").write_text("nope")

    assert fu.pending_count() == 2


def test_pending_count_handles_oserror(monkeypatch, tmp_path, caplog):
    """If the directory listing blows up (permission denied, race), we
    log a warning and return 0 — never crash the scheduler."""
    from backend.billing import feedback_uploader as fu

    annotations = tmp_path / "annotations"
    annotations.mkdir()

    def boom(_):
        raise OSError("simulated permission error")

    monkeypatch.setattr(fu, "ANNOTATIONS_DIR", str(annotations))
    monkeypatch.setattr("os.listdir", boom)

    with caplog.at_level("WARNING"):
        assert fu.pending_count() == 0


def test_quarantine_when_attempts_exceeded(monkeypatch, tmp_path):
    """After ``MAX_ATTEMPTS`` failed uploads, the file moves to
    ``training_feedback/failed/`` with both image + annotation."""
    fu, annotations, images, failed = _setup_dirs(monkeypatch, tmp_path)

    json_name = "cam1_scan1_20260101.json"
    img_name = "cam1_scan1_20260101.jpg"

    (tmp_path / "annotations" / json_name).write_text(
        json.dumps(
            {
                "camera_id": 1,
                "scan_id": 1,
                "upload_attempts": fu.MAX_ATTEMPTS,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    (tmp_path / "images" / img_name).write_bytes(b"fake-jpeg")

    metadata = {
        "upload_attempts": fu.MAX_ATTEMPTS,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    reason = fu._should_quarantine(metadata)
    assert reason is not None
    assert "attempts" in reason

    fu._move_to_failed(json_name, reason)

    assert not (tmp_path / "annotations" / json_name).exists()
    assert not (tmp_path / "images" / img_name).exists()
    assert (tmp_path / "failed" / json_name).exists()
    assert (tmp_path / "failed" / img_name).exists()


def test_quarantine_when_too_old(monkeypatch, tmp_path):
    """Old submissions (>= MAX_AGE_DAYS) are quarantined even if
    attempts are low — the data is too stale to be useful for
    training and we need to keep the queue draining."""
    fu, _, _, _ = _setup_dirs(monkeypatch, tmp_path)

    ancient = (datetime.now(timezone.utc) - timedelta(days=fu.MAX_AGE_DAYS + 5)).isoformat()
    metadata = {"upload_attempts": 0, "submitted_at": ancient}
    reason = fu._should_quarantine(metadata)
    assert reason is not None
    assert "older than" in reason


def test_no_quarantine_for_fresh_recent_submission(monkeypatch, tmp_path):
    """Normal submission (low attempts, recent) does NOT quarantine."""
    fu, _, _, _ = _setup_dirs(monkeypatch, tmp_path)
    metadata = {
        "upload_attempts": 2,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    assert fu._should_quarantine(metadata) is None


def test_no_quarantine_when_submitted_at_missing(monkeypatch, tmp_path):
    """Defensive: a malformed metadata file without submitted_at is
    treated as 'unknown age' and quarantined only on attempts."""
    fu, _, _, _ = _setup_dirs(monkeypatch, tmp_path)
    assert fu._should_quarantine({"upload_attempts": 0}) is None
    assert fu._should_quarantine({"upload_attempts": fu.MAX_ATTEMPTS}) is not None


def test_bump_attempts_increments_counter(monkeypatch, tmp_path):
    fu, annotations, _, _ = _setup_dirs(monkeypatch, tmp_path)

    json_name = "cam1_scan1_bump.json"
    json_path = tmp_path / "annotations" / json_name
    json_path.write_text(
        json.dumps(
            {
                "camera_id": 1,
                "scan_id": 1,
                "upload_attempts": 3,
            }
        )
    )

    fu._bump_attempts(str(json_path))

    data = json.loads(json_path.read_text())
    assert data["upload_attempts"] == 4
    assert data["last_attempt_at"] is not None


def test_bump_attempts_handles_corrupt_json(monkeypatch, tmp_path, caplog):
    """A corrupt JSON file shouldn't crash the scheduler."""
    fu, annotations, _, _ = _setup_dirs(monkeypatch, tmp_path)

    json_path = tmp_path / "annotations" / "corrupt.json"
    json_path.write_text("not json {{{{")

    # Should not raise
    fu._bump_attempts(str(json_path))


def test_bump_attempts_handles_missing_file(monkeypatch, tmp_path):
    """If the file disappeared between listing and bumping (concurrent
    delete), don't crash."""
    fu, annotations, _, _ = _setup_dirs(monkeypatch, tmp_path)

    ghost = tmp_path / "annotations" / "ghost.json"
    # Don't create it — just call _bump_attempts on a nonexistent path
    fu._bump_attempts(str(ghost))


def test_ensure_dirs_is_idempotent(monkeypatch, tmp_path):
    """``ensure_dirs`` should be safe to call repeatedly without raising."""
    fu, _, _, _ = _setup_dirs(monkeypatch, tmp_path)

    # Directories already exist from _setup_dirs; calling again must
    # not raise (mkdir with exist_ok=True semantics).
    fu.ensure_dirs()
    fu.ensure_dirs()


def test_move_to_failed_handles_missing_image(monkeypatch, tmp_path):
    """If only the JSON exists (image was never saved or got deleted),
    move just the JSON to failed/ — don't crash."""
    fu, annotations, images, failed = _setup_dirs(monkeypatch, tmp_path)

    json_name = "orphan.json"
    (tmp_path / "annotations" / json_name).write_text("{}")
    # No image file.

    fu._move_to_failed(json_name, "image missing")

    assert not (tmp_path / "annotations" / json_name).exists()
    assert (tmp_path / "failed" / json_name).exists()


@pytest.mark.asyncio
async def test_run_upload_tick_no_op_when_no_cloud_url(monkeypatch, tmp_path):
    """If the portal URL is empty, the tick is a no-op — no network
    calls, no errors. (The user's portal may not be configured.)"""
    from backend.billing import feedback_uploader as fu

    _setup_dirs(monkeypatch, tmp_path)

    outcomes = await fu.run_upload_tick("")
    assert outcomes == []


@pytest.mark.asyncio
async def test_run_upload_tick_no_op_when_no_files(monkeypatch, tmp_path):
    """Empty queue is a no-op."""
    from backend.billing import feedback_uploader as fu

    _setup_dirs(monkeypatch, tmp_path)

    outcomes = await fu.run_upload_tick("https://billing.example.com")
    assert outcomes == []


@pytest.mark.asyncio
async def test_run_upload_tick_successful_upload_cleans_up(monkeypatch, tmp_path):
    """Happy path: presign returns URLs, both PUTs succeed, files are
    deleted locally."""
    from backend.billing import feedback_uploader as fu

    _, annotations, images, failed = _setup_dirs(monkeypatch, tmp_path)

    # Write one submission
    json_name = "cam1_scan1_ok.json"
    (tmp_path / "annotations" / json_name).write_text(json.dumps({
        "camera_id": 1,
        "scan_id": 1,
        "hwid": "a" * 64,
        "email": "user@example.com",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "upload_attempts": 0,
    }))
    (tmp_path / "images" / "cam1_scan1_ok.jpg").write_bytes(b"jpeg-bytes")

    # Mock httpx: presign returns 200 + URLs, PUTs return 200
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.post_called = False
            self.put_calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            self.post_called = True
            class R:
                status_code = 200
                def json(self):
                    return {
                        "image_upload_url": "https://s3.example.com/img",
                        "metadata_upload_url": "https://s3.example.com/meta",
                    }
                text = "{}"
            return R()

        async def put(self, url, content=None, headers=None, timeout=None):
            self.put_calls.append((url, content, headers))
            class R:
                status_code = 200
            return R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    outcomes = await fu.run_upload_tick("https://billing.example.com")

    assert len(outcomes) == 1
    assert outcomes[0].success
    # Files are gone from the active dirs
    assert not (tmp_path / "annotations" / json_name).exists()
    assert not (tmp_path / "images" / "cam1_scan1_ok.jpg").exists()
    # ...and nothing leaked into failed/
    assert list((tmp_path / "failed").iterdir()) == []


@pytest.mark.asyncio
async def test_run_upload_tick_presign_failure_leaves_files(monkeypatch, tmp_path):
    """If presign fails (5xx), files stay in the queue with bumped
    attempts so the next tick will retry / quarantine."""
    from backend.billing import feedback_uploader as fu

    _, annotations, images, _ = _setup_dirs(monkeypatch, tmp_path)

    json_name = "cam1_scan1_5xx.json"
    (tmp_path / "annotations" / json_name).write_text(json.dumps({
        "camera_id": 1,
        "scan_id": 1,
        "hwid": "a" * 64,
        "email": "user@example.com",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "upload_attempts": 0,
    }))
    (tmp_path / "images" / "cam1_scan1_5xx.jpg").write_bytes(b"x")

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, **kw):
            class R:
                status_code = 502
                text = "Bad Gateway"
            return R()

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeClient())

    outcomes = await fu.run_upload_tick("https://billing.example.com")

    assert len(outcomes) == 1
    assert not outcomes[0].success

    # Files still there (will retry next tick)
    assert (tmp_path / "annotations" / json_name).exists()


@pytest.mark.asyncio
async def test_run_upload_tick_presign_404_quarantines(monkeypatch, tmp_path):
    """If the cloud returns 404 (account not found), the submission
    can never upload — quarantine immediately rather than wasting 30
    ticks."""
    from backend.billing import feedback_uploader as fu

    _, annotations, images, failed = _setup_dirs(monkeypatch, tmp_path)

    json_name = "cam1_scan1_404.json"
    (tmp_path / "annotations" / json_name).write_text(json.dumps({
        "camera_id": 1,
        "scan_id": 1,
        "hwid": "a" * 64,
        "email": "user@example.com",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "upload_attempts": 0,
    }))
    (tmp_path / "images" / "cam1_scan1_404.jpg").write_bytes(b"x")

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, **kw):
            class R:
                status_code = 404
                text = "Account not found"
            return R()

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeClient())

    outcomes = await fu.run_upload_tick("https://billing.example.com")

    assert len(outcomes) == 1
    assert not outcomes[0].success

    # Files quarantined, NOT in active queue
    assert not (tmp_path / "annotations" / json_name).exists()
    assert not (tmp_path / "images" / "cam1_scan1_404.jpg").exists()
    assert (tmp_path / "failed" / json_name).exists()


@pytest.mark.asyncio
async def test_run_upload_tick_image_put_failure_keeps_files(monkeypatch, tmp_path):
    """If the image PUT fails (network error), the file stays queued
    with bumped attempts."""
    from backend.billing import feedback_uploader as fu

    _, annotations, images, _ = _setup_dirs(monkeypatch, tmp_path)

    json_name = "cam1_scan1_imgfail.json"
    (tmp_path / "annotations" / json_name).write_text(json.dumps({
        "camera_id": 1,
        "scan_id": 1,
        "hwid": "a" * 64,
        "email": "user@example.com",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "upload_attempts": 5,
    }))
    (tmp_path / "images" / "cam1_scan1_imgfail.jpg").write_bytes(b"x")

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, **kw):
            class R:
                status_code = 200
                def json(self):
                    return {
                        "image_upload_url": "https://s3/img",
                        "metadata_upload_url": "https://s3/meta",
                    }
                text = "{}"
            return R()
        async def put(self, url, **kw):
            import httpx
            raise httpx.ConnectError("network error")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeClient())

    outcomes = await fu.run_upload_tick("https://billing.example.com")

    assert len(outcomes) == 1
    assert not outcomes[0].success

    # File still in queue with bumped attempts
    assert (tmp_path / "annotations" / json_name).exists()
    data = json.loads((tmp_path / "annotations" / json_name).read_text())
    assert data["upload_attempts"] == 6


@pytest.mark.asyncio
async def test_run_upload_tick_processes_only_batch(monkeypatch, tmp_path):
    """The loop caps work at BATCH_SIZE per tick — anything left rolls
    over to the next tick. With 10 files and BATCH_SIZE=5, only 5
    should be processed."""
    from backend.billing import feedback_uploader as fu

    _, annotations, images, _ = _setup_dirs(monkeypatch, tmp_path)

    for i in range(10):
        json_name = f"cam1_scan{i}_batch.json"
        (tmp_path / "annotations" / json_name).write_text(json.dumps({
            "camera_id": 1,
            "scan_id": i,
            "hwid": "a" * 64,
            "email": "user@example.com",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "upload_attempts": 0,
        }))
        (tmp_path / "images" / f"cam1_scan{i}_batch.jpg").write_bytes(b"x")

    processed = []

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None, **kw):
            class R:
                status_code = 200
                def json(self):
                    return {
                        "image_upload_url": "https://s3/img",
                        "metadata_upload_url": "https://s3/meta",
                    }
                text = "{}"
            return R()
        async def put(self, url, **kw):
            processed.append(url)
            class R:
                status_code = 200
            return R()

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeClient())

    outcomes = await fu.run_upload_tick("https://billing.example.com")

    assert len(outcomes) == fu.BATCH_SIZE
    # 5 files deleted, 5 still in queue
    remaining = list((tmp_path / "annotations").iterdir())
    assert len(remaining) == 10 - fu.BATCH_SIZE