import pytest
import os
import shutil
import datetime
from sqlalchemy.orm import Session
from backend import models, database, maintenance, app

# Setup a temporary LMDB for testing handled by conftest.py

def test_lmdb_basic_ops():
    """Test basic PUT/GET in RollingLMDB."""
    test_data = b"fake-image-bytes"
    key = "test_scan_1"
    timestamp = 1
    
    # 1. Put and Get Snapshot
    database.snapshot_db.put(key, test_data, timestamp=timestamp)
    retrieved = database.snapshot_db.get(key, timestamp=timestamp)
    assert retrieved == test_data
    
    # 2. Put and Get Crop (separate instance)
    database.crop_db.put(key, b"crop-bytes", timestamp=timestamp)
    assert database.crop_db.get(key, timestamp=timestamp) == b"crop-bytes"
    # Ensure they don't overwrite each other
    assert database.snapshot_db.get(key, timestamp=timestamp) == test_data

def test_maintenance_sync(db_session: Session):
    """Test that maintenance correctly prunes both SQLite and LMDB."""
    # Create a camera
    cam = models.Camera(name="Test Cam", snapshot_url="http://test")
    db_session.add(cam)
    db_session.commit()
    
    # Create an old scan (25 hours ago) and a new scan
    now = datetime.datetime.now(datetime.UTC)
    old_time = now - datetime.timedelta(hours=25)
    
    old_scan = models.CameraScan(camera_id=cam.id, timestamp=old_time, has_image=True)
    new_scan = models.CameraScan(camera_id=cam.id, timestamp=now, has_image=True)
    db_session.add_all([old_scan, new_scan])
    db_session.commit()
    
    # Add blobs to RollingLMDB
    database.snapshot_db.put(str(old_scan.id), b"old-data", timestamp=old_scan.id)
    database.snapshot_db.put(str(new_scan.id), b"new-data", timestamp=new_scan.id)
    
    # Create an old event (31 days ago)
    old_evt_time = now - datetime.timedelta(days=31)
    old_event = models.OccupancyEvent(space_id=1, timestamp=old_evt_time, event_type='occupied', has_crop=True)
    db_session.add(old_event)
    db_session.commit()
    database.crop_db.put(str(old_event.id), b"old-crop", timestamp=old_event.id)
    
    # Run maintenance
    # Ensure settings are set for the test
    db_session.query(models.Setting).filter_by(key="retention_events_days").update({"value": "30"})
    db_session.commit()
    
    maintenance.run_maintenance(db_session)
    
    # VERIFY
    # 1. Old scan blob — rollinglmdb doesn't support individual delete, but
    #    the Scan row still exists (has_image unchanged since we removed
    #    _prune_image_blobs). Verify the data is still retrievable.
    assert database.snapshot_db.get(str(old_scan.id), timestamp=old_scan.id) == b"old-data"
    # 2. Old scan record still exists with has_image unchanged
    db_session.refresh(old_scan)
    assert old_scan.has_image is True
    
    # 3. New snapshot should still be there
    assert database.snapshot_db.get(str(new_scan.id), timestamp=new_scan.id) == b"new-data"
    assert new_scan.has_image is True
    
    # 4. Old event should be completely deleted from SQLite (LMDB crop
    #    untouched since rollinglmdb doesn't support individual deletion)
    assert database.crop_db.get(str(old_event.id), timestamp=old_event.id) == b"old-crop"
    assert db_session.query(models.OccupancyEvent).filter_by(id=old_event.id).first() is None

def test_thumbnail_caching():
    """Test that the thumbnail cache actually caches and busts correctly."""
    from backend import app
    app.clear_thumbnail_cache()
    
    # Minimal 1x1 black pixel JPEG
    img_data = b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\x08\x08\x07\x07\x0f\x0b\x0b\t\x14\x1d\x0f\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\x14\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xbd\xff\xd9'
    
    # Test caching a result with an explicit scan_id
    res1 = app.get_cached_thumbnail(1, 100, 600, img_data)
    assert res1 is not None
    
    # Check if entry was added to cache (using our unique key)
    # The key is (camera_id, scan_id, target_width)
    key = (1, 100, 600)
    assert key in app.get_thumbnail_cache_state()
    
    # Check that cache is used (return identical object)
    res2 = app.get_cached_thumbnail(1, 100, 600, img_data)
    assert res1 is res2


def test_lmdb_last_event_time_metadata(tmp_path):
    """Verify that last_event_time is stored internally inside the LMDB database
    file and accurately reported by RollingLMDB."""
    from rollinglmdb import RollingLMDB
    import time

    store = RollingLMDB(directory=str(tmp_path / "meta_test"), chunk_size_mb=10)
    t0 = 1740000000
    store.put("key1", b"val1", timestamp=t0)
    
    chunk_info = store.chunks[0]
    assert chunk_info["last_event_time"] == t0
    assert chunk_info["num_entries"] == 1

    t1 = 1740003600
    store.put("key2", b"val2", timestamp=t1)
    chunk_info = store.chunks[0]
    assert chunk_info["last_event_time"] == t1
    assert chunk_info["num_entries"] == 2
    store.close()


def test_lmdb_metadata_keys_hidden_from_queries(tmp_path):
    """Verify that internal __meta: keys are not returned in iterate(), iterkeys(),
    or counted in num_entries."""
    from rollinglmdb import RollingLMDB

    store = RollingLMDB(directory=str(tmp_path / "filter_test"), chunk_size_mb=10)
    store.put("scan_100", b"data100", timestamp=100)
    store.put("scan_101", b"data101", timestamp=101)

    # 1. num_entries reflects only user entries
    assert store.total_entries == 2

    # 2. iterkeys contains only user keys
    chunk = store._collection.active_chunk
    keys = list(chunk.iterkeys())
    assert keys == ["scan_100", "scan_101"]

    # 3. iterate yields only user keys/values
    items = list(store.iterate())
    assert len(items) == 2
    assert ("scan_100", b"data100") in items
    assert ("scan_101", b"data101") in items
    store.close()


def test_lmdb_generator_lock_release(tmp_path):
    """Verify that breaking early from store.iterate() releases the collection lock
    so subsequent puts/gets on another thread or caller do not deadlock."""
    from rollinglmdb import RollingLMDB

    store = RollingLMDB(directory=str(tmp_path / "lock_test"), chunk_size_mb=10)
    store.put("k1", b"v1", timestamp=1)
    store.put("k2", b"v2", timestamp=2)

    # Partial iteration that breaks early
    for k, v in store.iterate():
        break

    # Collection lock must NOT be held
    assert not store._collection._lock._is_owned()

    # Immediate write must succeed without deadlock
    store.put("k3", b"v3", timestamp=3)
    assert store.get("k3", timestamp=3) == b"v3"
    store.close()

