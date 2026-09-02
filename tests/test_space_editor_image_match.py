"""Regression tests for the spot-detector "image displayed ≠ image used for
inference" bug.

The bug had two symptoms:

  1. The editor showed a stale frame at quality_snapshots=85; clicking
     "Test" re-grabbed a fresh stream frame and re-encoded it at default
     JPEG quality ~95. Different bytes, different inference.

  2. The frontend normalized ROIs by ``img.width`` (the CSS-rendered
     width), not ``img.naturalWidth`` (the JPEG's pixel width). A
     1920x1080 image drawn into a ~900px-wide wrapper reported
     width=900 and naturalWidth=1920; dividing by 900 placed the
     inference ROIs at ~46% of where the user drew them.

Both are now fixed:

  * ``_acquire_camera_image_bytes`` is shared between
    ``GET /api/cameras/{id}/snapshot`` and
    ``POST /api/cameras/{id}/test-occupancy`` so the *displayed* image
    and the *inference* image are guaranteed to come from the same code
    path. With a specific ``v`` the LMDB blob is returned. Without
    ``v`` the helper produces the same bytes the snapshot endpoint
    would return.

  * The frontend now reads ``imageConfig.naturalWidth/naturalHeight``
    for ROI normalization, with the existing ``imageConfig.width/height``
    retained for the Konva stage layout.

These tests assert the contract on the backend side: test-occupancy and
snapshot use the *same* image bytes for a given (camera_id, v) pair.
"""
import asyncio
import io
import pytest
from PIL import Image
from unittest.mock import patch, MagicMock

from backend import models, database, app as app_module


def _jpeg_bytes(width: int = 320, height: int = 240, color: str = "red") -> bytes:
    """Create a small JPEG with the requested pixel dimensions for tests."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _run(coro):
    """Run an async coroutine in a fresh, explicitly-closed event loop.

    Using ``asyncio.get_event_loop()`` would leak state from
    ``pytest-asyncio``'s loop fixture (and trigger a deprecation
    warning on Python 3.12+). A fresh loop is the safe, isolated choice
    for these unit-level helper tests that don't need the asyncio
    test framework's loop management.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_acquire_helper_returns_archived_blob_when_v_set(client, db_session, test_camera):
    """When v is provided, the helper returns the LMDB blob (the bytes
    the scheduler wrote), bypassing any live stream fetch."""
    payload = _jpeg_bytes()
    database.snapshot_db.put("42", payload, timestamp=42)

    cam = db_session.query(models.Camera).filter_by(id=test_camera.id).first()
    out = _run(app_module._acquire_camera_image_bytes(cam, "42", db_session))
    assert out == payload


def test_acquire_helper_404s_when_archived_blob_missing(client, db_session, test_camera):
    """A bad v returns 404 (not 500) so the UI can show a friendly error."""
    cam = db_session.query(models.Camera).filter_by(id=test_camera.id).first()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run(app_module._acquire_camera_image_bytes(cam, "999999", db_session))
    assert exc.value.status_code == 404


def test_acquire_helper_reads_local_file_for_test_camera(db_session):
    """A test/static camera with a local_path returns the file bytes."""
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(_jpeg_bytes(color="blue"))
        path = f.name
    try:
        cam = models.Camera(
            name="File Cam",
            source_type="test",
            local_path=path,
            is_test=True,
        )
        db_session.add(cam)
        db_session.commit()
        cam = db_session.query(models.Camera).filter_by(id=cam.id).first()
        out = _run(app_module._acquire_camera_image_bytes(cam, None, db_session))
        assert out[:4] == b"\xff\xd8\xff\xe0" or out[:3] == b"\xff\xd8\xff"
        assert len(out) > 0
    finally:
        os.unlink(path)


def test_test_occupancy_uses_archived_blob_not_live_stream(client, db_session, test_camera, auth_header):
    """Regression: POST /test-occupancy?v=X must use the LMDB blob, not
    re-grab a fresh frame. We mock stream_manager.get_latest_frame so
    that, if the test endpoint were to incorrectly call the live path,
    the test would fail with a 'Stream frame not available yet' 503 or
    the assert below would catch the wrong bytes."""
    blob = _jpeg_bytes(color="green")
    database.snapshot_db.put("7", blob, timestamp=7)

    # Force the live path to fail if it were taken
    with patch("backend.stream_manager.stream_manager.get_latest_frame", return_value=None):
        with patch("backend.app.inference.run_inference") as mock_run:
            mock_run.return_value = ({0: 0.91}, 0.0, (320, 240))
            response = client.post(
                f"/api/cameras/{test_camera.id}/test-occupancy",
                json=[{
                    "camera_id": test_camera.id,
                    "name": "s0",
                    "points": [0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]
                }],
                params={"v": "7"},
                headers=auth_header,
            )

    assert response.status_code == 200, response.text
    assert response.json() == [True]
    # Verify the inference call received the archived blob, not fresh bytes.
    # run_inference signature: (camera_id, image_bytes, spaces, max_res)
    args, kwargs = mock_run.call_args
    assert args[1] == blob, "inference should run on the LMDB blob bytes"


def test_test_occupancy_404s_on_missing_archived_blob(client, test_camera, auth_header):
    """A bad v must surface 404, not 500 (was 500 before the refactor —
    the bare ``except Exception as e: raise HTTPException(500, ...)``
    was swallowing the 404 HTTPException raised inside the helper)."""
    response = client.post(
        f"/api/cameras/{test_camera.id}/test-occupancy",
        json=[{
            "camera_id": test_camera.id,
            "name": "s0",
            "points": [0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]
        }],
        params={"v": "999999"},
        headers=auth_header,
    )
    assert response.status_code == 404, response.text


def test_test_occupancy_uses_stream_frame_for_live(client, db_session, auth_header):
    """When no v is provided, the test endpoint should use the live stream
    frame (not 404). This is the path used when the user clicks Test while
    the editor is on LIVE mode — it must agree with the displayed image."""
    import numpy as np

    cam = models.Camera(
        name="Stream Cam",
        source_type="rtsp",
        stream_url="rtsp://example/test",
        is_enabled=True,
    )
    db_session.add(cam)
    db_session.commit()
    cam_id = cam.id

    # Clear the cache so this test exercises the fresh-fetch path
    # (the live_frame_cache is module-global and might be warm
    # from earlier tests in the same session).
    if hasattr(app_module.app, "live_frame_cache"):
        app_module.app.live_frame_cache.clear()

    # Build a 320x240 BGR frame and have get_latest_frame return it
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # blue in BGR

    with patch("backend.stream_manager.stream_manager.get_latest_frame", return_value=frame):
        with patch("backend.stream_manager.stream_manager.start_stream", return_value=None):
            with patch("backend.app.inference.run_inference") as mock_run:
                mock_run.return_value = ({0: 0.42}, 0.0, (320, 240))
                response = client.post(
                    f"/api/cameras/{cam_id}/test-occupancy",
                    json=[{
                        "camera_id": cam_id,
                        "name": "s0",
                        "points": [0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]
                    }],
                    headers=auth_header,
                )
                call_args = mock_run.call_args

    assert response.status_code == 200, response.text
    # Verify the inference call received a non-empty JPEG (the helper
    # encoded the frame).
    # run_inference signature: (camera_id, image_bytes, spaces, max_res)
    args, kwargs = call_args
    assert args[1][:3] == b"\xff\xd8\xff", "inference should receive JPEG-encoded bytes"


def test_snapshot_uses_archived_blob(client, db_session, test_camera):
    """Sanity check: GET /snapshot?v=X returns the LMDB blob. Combined
    with the test-occupancy test above, this proves the two endpoints
    share an image source for a given (camera_id, v)."""
    blob = _jpeg_bytes(color="yellow")
    database.snapshot_db.put("99", blob, timestamp=99)

    response = client.get(f"/api/cameras/{test_camera.id}/snapshot", params={"v": "99"})
    assert response.status_code == 200
    assert response.content == blob


# ---------------------------------------------------------------------------
# LIVE-mode determinism: the editor's "Test" button must run inference on
# the EXACT bytes the editor displayed, not on whatever the stream
# produced in the milliseconds between display and click.
#
# Without the live frame cache, every Test call to
# stream_manager.get_latest_frame() returned a different frame on a
# changing stream — the displayed image stayed the same (browser cache)
# but the inference result flipped between clicks. These tests pin that
# behavior down.
# ---------------------------------------------------------------------------


def test_live_snapshot_test_occupancy_use_same_bytes(client, db_session, auth_header):
    """The two endpoints must see the same JPEG bytes for a given
    (camera, no-v) request — so clicking Test in the editor on a
    LIVE camera runs inference on the displayed image, not a new
    frame.

    We exercise the contract by patching
    stream_manager.get_latest_frame to return a known frame, calling
    the snapshot endpoint first, then calling test-occupancy, and
    asserting both received the same JPEG bytes from the
    shared cache.
    """
    import numpy as np

    cam = models.Camera(
        name="Live Cam",
        source_type="rtsp",
        stream_url="rtsp://example/test",
        is_enabled=True,
    )
    db_session.add(cam)
    db_session.commit()
    cam_id = cam.id

    # Start with a clean cache so previous tests don't pollute
    if hasattr(app_module.app, "live_frame_cache"):
        app_module.app.live_frame_cache.clear()

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # blue in BGR

    with patch("backend.stream_manager.stream_manager.get_latest_frame", return_value=frame):
        with patch("backend.stream_manager.stream_manager.start_stream", return_value=None):
            with patch("backend.app.inference.run_inference") as mock_run:
                mock_run.return_value = ({0: 0.55}, 0.0, (320, 240))

                # 1. Snapshot first (editor's image load)
                snap_resp = client.get(f"/api/cameras/{cam_id}/snapshot")
                assert snap_resp.status_code == 200, snap_resp.text
                snap_bytes = snap_resp.content

                # 2. Then test-occupancy (user clicks Test on the
                #    just-displayed image). With the live cache, this
                #    MUST receive the same bytes the snapshot
                #    returned, even though the underlying stream
                #    might have produced a new frame in between.
                test_resp = client.post(
                    f"/api/cameras/{cam_id}/test-occupancy",
                    json=[{
                        "camera_id": cam_id,
                        "name": "s0",
                        "points": [0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]
                    }],
                    headers=auth_header,
                )
                assert test_resp.status_code == 200, test_resp.text

                inference_args = mock_run.call_args[0]
                inference_bytes = inference_args[1]

    # The two endpoints MUST serve the same bytes.
    assert snap_bytes == inference_bytes, (
        "snapshot and test-occupancy should serve the same cached bytes "
        "for a LIVE camera within the cache TTL; otherwise the Test "
        "button runs inference on a different image than the user sees"
    )


def test_repeated_test_clicks_on_live_return_same_result(client, db_session, auth_header):
    """The exact regression the user reported: clicking Test three
    times on a LIVE camera (without changing the displayed image)
    produced three different occupancy results. With the live
    cache, all three should hit the same bytes and produce the
    same result.

    We simulate the *underlying* stream advancing between calls
    (a different frame is available) and verify the test endpoint
    ignores it because the cache is warm.
    """
    import numpy as np

    cam = models.Camera(
        name="Live Cam Repeated",
        source_type="rtsp",
        stream_url="rtsp://example/test",
        is_enabled=True,
    )
    db_session.add(cam)
    db_session.commit()
    cam_id = cam.id

    if hasattr(app_module.app, "live_frame_cache"):
        app_module.app.live_frame_cache.clear()

    # The displayed image (frame_a) is what the user is looking at
    frame_a = np.zeros((240, 320, 3), dtype=np.uint8)
    frame_a[:, :, 2] = 255  # red in BGR
    # What the stream produces next (a different image) — we
    # only check that it's NOT what test-occupancy sees
    frame_b = np.zeros((240, 320, 3), dtype=np.uint8)
    frame_b[:, :, 0] = 255  # blue in BGR

    call_count = {"n": 0}

    def get_latest_frame(cam_id, since=None):
        # First call returns the displayed frame; later calls
        # return a different one. If the cache works, the test
        # endpoint never even calls this.
        call_count["n"] += 1
        return frame_a if call_count["n"] == 1 else frame_b

    def make_request():
        return client.post(
            f"/api/cameras/{cam_id}/test-occupancy",
            json=[{
                "camera_id": cam_id,
                "name": "s0",
                "points": [0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]
            }],
            headers=auth_header,
        )

    with patch("backend.stream_manager.stream_manager.start_stream", return_value=None):
        with patch("backend.stream_manager.stream_manager.get_latest_frame", side_effect=get_latest_frame):
            with patch("backend.app.inference.run_inference") as mock_run:
                # First request populates the cache with frame_a
                mock_run.return_value = ({0: 0.5}, 0.0, (320, 240))
                r1 = make_request()
                # Subsequent requests within the TTL must NOT
                # re-grab a frame. Inference runs with the
                # cached bytes.
                mock_run.return_value = ({0: 0.99}, 0.0, (320, 240))
                r2 = make_request()
                mock_run.return_value = ({0: 0.01}, 0.0, (320, 240))
                r3 = make_request()

                # Collect the bytes the inference actually saw
                bytes1 = mock_run.call_args_list[0][0][1]
                bytes2 = mock_run.call_args_list[1][0][1]
                bytes3 = mock_run.call_args_list[2][0][1]

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200
    # The cache means bytes 1, 2, 3 are all the SAME JPEG
    # (corresponding to frame_a, the displayed image).
    assert bytes1 == bytes2 == bytes3, (
        "Three Test clicks on a LIVE camera without changing the "
        "displayed image should run inference on the same bytes. "
        "Bytes differed: "
        f"{len(bytes1)} vs {len(bytes2)} vs {len(bytes3)}"
    )


def test_force_query_param_busts_live_cache(client, db_session, auth_header):
    """The Refresh button must show a new image. To do that, the
    snapshot endpoint accepts ``force=true`` to invalidate the
    cache, after which the next request gets a fresh frame.
    """
    import numpy as np

    cam = models.Camera(
        name="Live Cam Force",
        source_type="rtsp",
        stream_url="rtsp://example/test",
        is_enabled=True,
    )
    db_session.add(cam)
    db_session.commit()
    cam_id = cam.id

    if hasattr(app_module.app, "live_frame_cache"):
        app_module.app.live_frame_cache.clear()

    frame_a = np.zeros((240, 320, 3), dtype=np.uint8)
    frame_a[:, :, 2] = 255  # red
    frame_b = np.zeros((240, 320, 3), dtype=np.uint8)
    frame_b[:, :, 0] = 255  # blue

    state = {"current": frame_a}

    def get_latest_frame(cam_id, since=None):
        return state["current"]

    with patch("backend.stream_manager.stream_manager.start_stream", return_value=None):
        with patch("backend.stream_manager.stream_manager.get_latest_frame", side_effect=get_latest_frame):
            # First request populates the cache with frame_a
            r1 = client.get(f"/api/cameras/{cam_id}/snapshot")
            assert r1.status_code == 200
            bytes_a = r1.content

            # Switch the underlying stream to produce a different
            # frame. Without force, the cache keeps returning
            # frame_a.
            state["current"] = frame_b
            r2 = client.get(f"/api/cameras/{cam_id}/snapshot")
            assert r2.status_code == 200
            assert r2.content == bytes_a, "without force=true, the cache should keep returning the same bytes"

            # With force=true, the cache is invalidated and a
            # fresh frame is encoded.
            r3 = client.get(f"/api/cameras/{cam_id}/snapshot", params={"force": "true"})
            assert r3.status_code == 200
            assert r3.content != bytes_a, "with force=true, the snapshot must serve a fresh frame"
