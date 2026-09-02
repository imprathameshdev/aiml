import pytest
import datetime
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from backend import scheduler, models, database, maintenance

def test_hydrate_cache(db_session):
    # Add some initial data
    cam = models.Camera(name="C1", snapshot_url="http://c1.jpg")
    db_session.add(cam)
    db_session.flush()
    
    space = models.Space(camera_id=cam.id, name="S1", points="[0,0,0,0,0,0,0,0]")
    db_session.add(space)
    db_session.flush()
    
    scan = models.CameraScan(camera_id=cam.id, timestamp=datetime.datetime.now(datetime.UTC))
    db_session.add(scan)
    db_session.flush()
    
    occ = models.Occupancy(space_id=space.id, scan_id=scan.id, occupied=True)
    db_session.add(occ)
    db_session.commit()
    
    # Reset cache
    scheduler._state_cache = {}
    
    # Hydrate
    with patch('backend.database.SessionLocal', return_value=db_session):
        scheduler._hydrate_cache()
    
    assert cam.id in scheduler._state_cache
    assert space.id in scheduler._state_cache[cam.id]
    assert scheduler._state_cache[cam.id][space.id]["occupied"] is True
    assert scheduler._state_cache[cam.id][space.id]["name"] == "S1"

@pytest.mark.asyncio
async def test_maintenance_loop_cleanup(db_session):
    # Ensure events are fresh and on the current loop
    scheduler.reset_scheduler_events()
    
    # Test that maintenance purges old blobs
    now = datetime.datetime.now(datetime.UTC)
    old_time = now - datetime.timedelta(hours=48)
    
    cam = models.Camera(name="C1")
    db_session.add(cam)
    db_session.flush()
    
    # Create scan with has_image=True
    scan = models.CameraScan(camera_id=cam.id, timestamp=old_time, has_image=True)
    db_session.add(scan)
    db_session.commit()
    
    # Put fake blob in RollingLMDB
    database.snapshot_db.put(str(scan.id), b"fake-data", timestamp=scan.id)
    
    # Run maintenance logic directly (avoiding the infinite loop)
    maintenance.run_maintenance(db_session)
    
    # Verify blob is still present (rollinglmdb doesn't support individual deletion)
    assert database.snapshot_db.get(str(scan.id), timestamp=scan.id) == b"fake-data"
    # has_image remains unchanged since we removed _prune_image_blobs
    db_session.refresh(scan)
    assert scan.has_image is True

@pytest.mark.asyncio
async def test_run_loop_recording(db_session):
    # Ensure events are fresh and on the current loop
    scheduler.reset_scheduler_events()
    
    cam = models.Camera(name="C1", snapshot_url="http://c1.jpg")
    db_session.add(cam)
    db_session.flush()
    db_session.add(models.Space(camera_id=cam.id, name="S1", points="[0,0,0,0,0,0,0,0]"))
    db_session.commit()
    
    # Mock inference to return a successful detection
    # Format: (probs_dict, elapsed, actual_res)
    mock_probs = {1: 0.95} 
    
    # Mock Response object
    mock_resp = MagicMock()
    mock_resp.content = b"fake_img"
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch('httpx.AsyncClient.get', new_callable=AsyncMock, return_value=mock_resp), \
         patch('backend.inference.run_inference', return_value=(mock_probs, 0.75, (1440, 1080))) as mock_inf, \
         patch('asyncio.wait', side_effect=asyncio.CancelledError), \
         patch('backend.database.SessionLocal', return_value=db_session):
        
        # We need to set _stop_event to true after one iteration to stop the loop
        # Or just catch the CancelledError we injected via side_effect
        try:
            await scheduler._run_loop()
        except asyncio.CancelledError:
            pass
            
    # Check that scan was recorded in DB
    scan = db_session.query(models.CameraScan).filter_by(camera_id=cam.id).first()
    assert scan is not None
    assert scan.inference_speed == 0.75
    
    # Check that occupancy was recorded
    occ = db_session.query(models.Occupancy).filter_by(scan_id=scan.id).first()
    assert occ is not None
    assert occ.occupied is True
    
    # Check cache
    assert scheduler._state_cache[cam.id]["metadata"]["inference_speed"] == 0.75

@pytest.mark.asyncio
async def test_scheduler_skips_disabled_cameras(db_session):
    scheduler.reset_scheduler_events()
    
    # 1. Enabled Camera
    c1 = models.Camera(name="Enabled", snapshot_url="http://c1", is_enabled=True)
    # 2. Disabled Camera
    c2 = models.Camera(name="Disabled", snapshot_url="http://c2", is_enabled=False)
    db_session.add_all([c1, c2])
    db_session.commit()
    
    # Add a space for the enabled camera so it is not skipped
    db_session.add(models.Space(camera_id=c1.id, name="S1", points="[0,0,0,0,0,0,0,0]"))
    db_session.commit()
    
    # Mock Response object
    mock_resp = MagicMock()
    mock_resp.content = b"img"
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch('httpx.AsyncClient.get', new_callable=AsyncMock, return_value=mock_resp), \
         patch('backend.inference.run_inference', return_value=({}, 0.1, (800, 600))), \
         patch('asyncio.wait', side_effect=asyncio.CancelledError), \
         patch('backend.database.SessionLocal', return_value=db_session):
        
        try:
            await scheduler._run_loop()
        except asyncio.CancelledError:
            pass

    # Check that only 'Enabled' camera has a scan record
    scans_c1 = db_session.query(models.CameraScan).filter_by(camera_id=c1.id).count()
    scans_c2 = db_session.query(models.CameraScan).filter_by(camera_id=c2.id).count()

    assert scans_c1 == 1
    assert scans_c2 == 0


@pytest.mark.asyncio
async def test_camera_with_zero_spaces_still_records_scan(db_session):
    """A camera with no parking spaces defined must still create a
    CameraScan record + LMDB blob + state-cache scan_id on each
    scheduler tick, so the dashboard thumbnail can refresh via the
    WebSocket scan_id broadcast. Without this, the cached image stays
    frozen on the first load forever (see the empty-spaces bug fix
    in scheduler._run_loop)."""
    scheduler.reset_scheduler_events()
    scheduler._state_cache = {}

    cam = models.Camera(name="NoSpaces", snapshot_url="http://c_no_spaces.jpg", is_enabled=True)
    db_session.add(cam)
    db_session.commit()
    # Note: deliberately no Space rows for this camera.

    mock_resp = MagicMock()
    mock_resp.content = b"fake_no_spaces_img"
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch('httpx.AsyncClient.get', new_callable=AsyncMock, return_value=mock_resp), \
         patch('backend.inference.run_inference') as mock_inf, \
         patch('asyncio.wait', side_effect=asyncio.CancelledError), \
         patch('backend.database.SessionLocal', return_value=db_session):
        try:
            await scheduler._run_loop()
        except asyncio.CancelledError:
            pass

    # Inference must NOT have been called — there's nothing to infer on.
    mock_inf.assert_not_called()

    # But a CameraScan row was still created so the dashboard has a
    # fresh scan_id to cache-bust the thumbnail.
    scan = db_session.query(models.CameraScan).filter_by(camera_id=cam.id).first()
    assert scan is not None
    assert scan.inference_speed == 0.0  # no inference ran

    # No Occupancy rows — there are no spaces.
    occ_count = db_session.query(models.Occupancy).filter_by(scan_id=scan.id).count()
    assert occ_count == 0

    # The state cache got a scan_id and is_processing=False, so the
    # WebSocket broadcast loop will pick this camera up on its next
    # tick and tell the dashboard to refresh the thumbnail.
    assert scheduler._state_cache[cam.id]["metadata"]["scan_id"] == scan.id
    assert scheduler._state_cache[cam.id]["metadata"]["is_processing"] is False


def test_heartbeat_clears_local_license_on_404_when_device_forgotten(db_session):
    """When the cloud returns ``code: "device_not_activated"`` on a
    scheduled heartbeat, ALL licensing data on the on-prem must be
    wiped — not just the license token.  The 404 with this code is
    the definitive signal that the device is not on any account (the
    user previously unlinked from billing, or never activated).

    Tested via the dedicated ``_send_one_heartbeat`` helper rather
    than the full loop so the assertions stay focused on the 404
    branch and the test doesn't have to fake inference / network
    I/O.
    """
    from backend.billing.client import BillingDeviceNotFoundError
    from backend.billing.session import LocalSession
    from backend.billing.paths import session_path
    import json as _json
    from datetime import datetime, timezone, timedelta

    # Seed a full commercial state — license, trial, quota, email,
    # camera counts, device_quota. Every field is what
    # ``mark_device_forgotten()`` must wipe.
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

    session = LocalSession()
    session._file = path
    assert session.load() is True
    assert session.license_token == "stale-commercial-token"

    # Seed the inference cache so we can observe the invalidation.
    from backend import inference
    inference._vv_config["last_check"] = 999

    # Mock the BillingClient so the heartbeat returns 404.
    mock_client = MagicMock()
    mock_client.heartbeat = AsyncMock(
        side_effect=BillingDeviceNotFoundError("Device not found. Activate first via /activate.")
    )

    # Run the heartbeat helper directly.
    asyncio.run(scheduler._send_one_heartbeat(mock_client, session))

    # All licensing fields are wiped on disk.
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
    assert on_disk["last_heartbeat_status"] == "not_activated"
    assert on_disk["last_heartbeat_at"] is not None
    assert on_disk["last_heartbeat_at"] != past
    # The inference cache was invalidated — last_check is reset to 0
    # so the engine re-reads the (now-empty) license on its next
    # poll cycle (60s).
    assert inference._vv_config["last_check"] == 0

    # Reset for other tests.
    inference._vv_config["last_check"] = 0


@pytest.mark.asyncio
async def test_scheduler_adjusts_interval_immediately(db_session):
    """If the inference interval setting changes, the scheduler must adjust
    its sleep time dynamically:
      - If interval is shortened below current elapsed time, trigger immediately.
      - If interval is lengthened, sleep for the new remaining difference.
    """
    scheduler.reset_scheduler_events()
    
    # Prepopulate database with default settings
    db_session.add(models.Setting(key="inference_interval", value="60"))
    db_session.add(models.Setting(key="hysteresis_occupied_threshold", value="0.75"))
    db_session.add(models.Setting(key="hysteresis_free_threshold", value="0.25"))
    db_session.add(models.Setting(key="max_inference_resolution", value="1440"))
    db_session.add(models.Setting(key="quality_snapshots", value="80"))
    db_session.add(models.Setting(key="quality_crops", value="80"))
    db_session.add(models.Setting(key="max_snapshot_resolution", value="1440"))
    db_session.commit()

    current_time = 1000.0
    def mock_perf_counter():
        nonlocal current_time
        return current_time

    wait_calls = []
    
    async def mock_wait(aws, timeout=None, return_when=asyncio.ALL_COMPLETED):
        wait_calls.append(timeout)
        if len(wait_calls) == 1:
            # 30 seconds elapse, then the user edits the interval setting
            nonlocal current_time
            current_time += 30.0
            db_session.query(models.Setting).filter_by(key="inference_interval").update({"value": "10"})
            db_session.commit()
            scheduler.notify_settings_changed()
            return {MagicMock()}, set()
        elif len(wait_calls) == 2:
            # 1 millisecond elapses, then the user edits the interval setting again
            current_time += 0.001
            db_session.query(models.Setting).filter_by(key="inference_interval").update({"value": "120"})
            db_session.commit()
            scheduler.notify_settings_changed()
            return {MagicMock()}, set()
        else:
            # Stop the scheduler loop
            raise asyncio.CancelledError()

    with patch('time.perf_counter', side_effect=mock_perf_counter), \
         patch('asyncio.wait', side_effect=mock_wait), \
         patch('backend.database.SessionLocal', return_value=db_session):
         
         try:
             await scheduler._run_loop()
         except asyncio.CancelledError:
             pass

    assert len(wait_calls) == 3
    # First sleep: interval is 60s, starts at t=1000
    assert abs(wait_calls[0] - 60.0) < 0.01
    # Second sleep: interval shortened to 10s. Since 30s elapsed >= 10s, it runs immediately and sleeps for 10s.
    assert abs(wait_calls[1] - 10.0) < 0.01
    # Third sleep: interval lengthened to 120s. Since only 0.001s elapsed, it sleeps for the remaining 119.999s.
    assert abs(wait_calls[2] - 119.999) < 0.01

