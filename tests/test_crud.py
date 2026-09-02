import json
import datetime
from backend import crud, schemas, models

def test_create_camera(db_session):
    cam_in = schemas.CameraCreate(name="Test Camera", snapshot_url="http://example.com/test.jpg")
    cam = crud.create_camera(cam_in, db_session)
    assert cam.name == "Test Camera"
    assert cam.snapshot_url == "http://example.com/test.jpg"
    assert cam.id is not None

def test_get_cameras(db_session):
    cam_in = schemas.CameraCreate(name="Cam 1", snapshot_url="http://example.com/1.jpg")
    crud.create_camera(cam_in, db_session)
    cams = crud.get_cameras(db_session)
    assert len(cams) == 1
    assert cams[0].name == "Cam 1"

def test_create_space(db_session):
    cam_in = schemas.CameraCreate(name="Cam 1", snapshot_url="http://example.com/1.jpg")
    cam = crud.create_camera(cam_in, db_session)
    
    space_in = schemas.SpaceCreate(camera_id=cam.id, name="Space A1", points=[0,0, 10,0, 10,10, 0,10])
    space = crud.create_space(space_in, db_session)
    assert space.name == "Space A1"
    assert space.camera_id == cam.id
    assert json.loads(space.points) == [0,0, 10,0, 10,10, 0,10]

def test_update_spaces(db_session):
    cam_in = schemas.CameraCreate(name="Cam 1", snapshot_url="http://example.com/1.jpg")
    cam = crud.create_camera(cam_in, db_session)
    
    # Initial spaces
    s1 = schemas.SpaceUpdate(camera_id=cam.id, name="S1", points=[1,1, 2,2, 3,3, 4,4])
    crud.update_spaces([s1], db_session)
    
    spaces = crud.get_spaces(db_session, cam.id)
    assert len(spaces) == 1
    s1_id = spaces[0].id
    
    # Update S1 and add S2
    s1_upd = schemas.SpaceUpdate(id=s1_id, camera_id=cam.id, name="S1 Updated", points=[0,0, 0,0, 0,0, 0,0])
    s2_new = schemas.SpaceUpdate(camera_id=cam.id, name="S2", points=[1,1, 1,1, 1,1, 1,1])
    
    crud.update_spaces([s1_upd, s2_new], db_session)
    
    updated_spaces = crud.get_spaces(db_session, cam.id)
    assert len(updated_spaces) == 2
    assert any(s.name == "S1 Updated" for s in updated_spaces)
    assert any(s.name == "S2" for s in updated_spaces)

def test_user_management(db_session):
    user_in = schemas.UserCreate(username="newuser", password="newpassword", permissions=["manage_cameras"])
    user = crud.create_user(user_in, db_session)
    assert user["username"] == "newuser"
    assert "manage_cameras" in user["permissions"]
    
    # List users
    users = crud.get_users(db_session)
    assert any(u["username"] == "newuser" for u in users)
    
    # Update user
    crud.update_user(db_session, user["id"], schemas.UserUpdate(permissions=["view_history"]))
    # ``crud.get_user_by_id`` was removed as dead code (no production
    # callers); query the model directly to assert the update.
    updated = db_session.query(models.User).filter_by(id=user["id"]).first()
    assert "view_history" in json.loads(updated.permissions)
    assert "manage_cameras" not in json.loads(updated.permissions)

    # Delete user
    crud.delete_user(db_session, user["id"])
    assert db_session.query(models.User).filter_by(id=user["id"]).first() is None

def test_get_stats_aggregated(db_session):
    cam = crud.create_camera(schemas.CameraCreate(name="StatCam"), db_session)
    space = crud.create_space(schemas.SpaceCreate(camera_id=cam.id, name="S1", points=[0,0,1,1,1,1,0,0]), db_session)
    
    # Create some scans
    ts = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    scan = models.CameraScan(camera_id=cam.id, timestamp=ts, inference_speed=0.5)
    db_session.add(scan)
    db_session.flush()
    
    occ = models.Occupancy(space_id=space.id, scan_id=scan.id, occupied=True)
    db_session.add(occ)
    db_session.commit()
    
    start = (ts - datetime.timedelta(minutes=1)).isoformat()
    end = (ts + datetime.timedelta(minutes=1)).isoformat()
    
    stats = crud.get_stats_aggregated(db_session, [cam.id], start, end)
    assert len(stats) > 0
    assert stats[0]["occupied"] == 1.0
    assert stats[0]["total"] == 1.0
    assert stats[0]["speed"] == 0.5

def test_inference_stats(db_session):
    cam = crud.create_camera(schemas.CameraCreate(name="SpeedCam"), db_session)
    ts = datetime.datetime.now(datetime.UTC)
    scan = models.CameraScan(camera_id=cam.id, timestamp=ts, inference_speed=1.23)
    db_session.add(scan)
    db_session.commit()
    
    stats = crud.get_inference_stats(db_session, cam.id)
    assert len(stats) == 1
    assert stats[0]["speed"] == 1.23
    
    overall = crud.get_overall_inference_stats(db_session, limit=1)
    assert len(overall) == 1
    assert overall[0]["speed"] == 1.23
