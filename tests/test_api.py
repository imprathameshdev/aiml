import pytest
from backend.auth import hash_password

def test_login(client, admin_user):
    response = client.post("/api/login", json={"username": "testadmin", "password": "testpassword"})
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_get_me(client, auth_header):
    response = client.get("/api/users/me", headers=auth_header)
    assert response.status_code == 200
    assert response.json()["username"] == "testadmin"

def test_list_cameras_unauthenticated(client):
    # Cameras listing is optional_auth
    response = client.get("/api/cameras")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_create_camera_requires_permission(client, admin_token):
    # Admin has permission
    header = {"Authorization": f"Bearer {admin_token}"}
    response = client.post("/api/cameras", 
                           json={"name": "API Cam", "snapshot_url": "http://api.com/s.jpg"},
                           headers=header)
    assert response.status_code == 200
    assert response.json()["name"] == "API Cam"

def test_create_camera_no_auth(client):
    response = client.post("/api/cameras", json={"name": "Fail", "snapshot_url": "http://fail.com"})
    assert response.status_code == 401 # require_auth fails if no token and no api key

def test_api_key_management(client, auth_header):
    # Create key
    response = client.post("/api/api_keys", json={"name": "Test Key"}, headers=auth_header)
    assert response.status_code == 200
    data = response.json()
    assert "full_key" in data
    api_key = data["full_key"]
    key_id = data["id"]
    
    # List keys
    response = client.get("/api/api_keys", headers=auth_header)
    assert any(k["id"] == key_id for k in response.json())
    
    # Use key to access authenticated endpoint
    # First delete Auth header to be sure
    response = client.get("/api/users/me", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    assert response.json()["username"] == "testadmin"
    
    # Delete key
    response = client.delete(f"/api/api_keys/{key_id}", headers=auth_header)
    assert response.status_code == 200
    
    # Key should no longer work
    response = client.get("/api/users/me", headers={"X-API-Key": api_key})
    assert response.status_code == 401

def test_stats_permissions(client, db_session):
    # Create a user without view_history permission
    import json
    from backend import models, auth
    user = models.User(username="limited", password_hash=hash_password("p"), permissions=json.dumps(["manage_cameras"]))
    db_session.add(user)
    db_session.commit()
    
    # Login
    resp = client.post("/api/login", json={"username": "limited", "password": "p"})
    token = resp.json()["access_token"]
    
    # Attempt to fetch stats
    resp = client.get("/api/stats?start=2026-01-01T00:00:00&end=2026-01-01T23:59:59", 
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403

def test_inference_stats_endpoints(client, admin_token, db_session, test_camera):
    header = {"Authorization": f"Bearer {admin_token}"}
    
    # Add a scan with speed
    from backend import models
    import datetime
    scan = models.CameraScan(camera_id=test_camera.id, timestamp=datetime.datetime.now(datetime.UTC), inference_speed=0.45)
    db_session.add(scan)
    db_session.commit()
    
    # Test camera-specific endpoint
    resp = client.get(f"/api/cameras/{test_camera.id}/inference-stats", headers=header)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert resp.json()[0]["speed"] == 0.45
    
    # Test overall endpoint
    resp = client.get("/api/inference-stats", headers=header)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert resp.json()[0]["speed"] == 0.45

def test_ssl_settings_endpoints(client, admin_token):
    header = {"Authorization": f"Bearer {admin_token}"}
    
    # Clean up old cert files to force a new 50-year generation
    from backend.config import config
    import os
    for filename in ("self_signed.crt", "self_signed.key"):
        filepath = os.path.join(config.DATA_DIR, "ssl", filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            
    # 1. Test generate self-signed
    resp = client.post("/api/settings/ssl/generate-self-signed", headers=header)
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    
    # Verify cert and key settings paths were updated
    resp_cert = client.get("/api/settings/ssl_cert_path", headers=header)
    resp_key = client.get("/api/settings/ssl_key_path", headers=header)
    assert resp_cert.status_code == 200
    assert resp_key.status_code == 200
    
    cert_path = resp_cert.json()["value"]
    key_path = resp_key.json()["value"]
    import os
    assert os.path.exists(cert_path)
    assert os.path.exists(key_path)

    # Verify cert has a 50-year validity period
    from cryptography import x509
    with open(cert_path, "rb") as f:
        cert_bytes = f.read()
    cert = x509.load_pem_x509_certificate(cert_bytes)
    not_after = getattr(cert, "not_valid_after_utc", cert.not_valid_after)
    not_before = getattr(cert, "not_valid_before_utc", cert.not_valid_before)
    valid_duration = not_after - not_before
    assert valid_duration.days >= 365 * 50

    # Verify that calling generate again without force does NOT overwrite the files
    with open(cert_path, "w") as f:
        f.write("MODIFIED")
    resp = client.post("/api/settings/ssl/generate-self-signed", headers=header)
    assert resp.status_code == 200
    with open(cert_path, "r") as f:
        assert f.read() == "MODIFIED"

    # Verify that calling generate WITH force=True DOES overwrite/regenerate the files
    resp = client.post("/api/settings/ssl/generate-self-signed?force=true", headers=header)
    assert resp.status_code == 200
    with open(cert_path, "rb") as f:
        cert_bytes_new = f.read()
    assert cert_bytes_new != b"MODIFIED"
    cert_new = x509.load_pem_x509_certificate(cert_bytes_new)
    not_after_new = getattr(cert_new, "not_valid_after_utc", cert_new.not_valid_after)
    not_before_new = getattr(cert_new, "not_valid_before_utc", cert_new.not_valid_before)
    assert (not_after_new - not_before_new).days >= 365 * 50
    
    # 2. Test upload custom certificates
    import io
    cert_file = io.BytesIO(b"MOCK CERTIFICATE CONTENT")
    key_file = io.BytesIO(b"MOCK KEY CONTENT")
    
    files = {
        "cert": ("custom.crt", cert_file, "application/x-x509-ca-cert"),
        "key": ("custom.key", key_file, "application/octet-stream")
    }
    
    resp = client.post("/api/settings/ssl/upload", files=files, headers=header)
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    
    # Check that cert and key paths are updated to the custom files
    resp_cert = client.get("/api/settings/ssl_cert_path", headers=header)
    resp_key = client.get("/api/settings/ssl_key_path", headers=header)
    assert "custom.crt" in resp_cert.json()["value"]
    assert "custom.key" in resp_key.json()["value"]

def test_enable_ssl_without_certs_fails(client, admin_token, db_session):
    header = {"Authorization": f"Bearer {admin_token}"}
    
    # Ensure cert paths are empty in DB
    from backend import crud
    crud.update_setting(db_session, "ssl_cert_path", "")
    crud.update_setting(db_session, "ssl_key_path", "")
    db_session.commit()
    
    # Attempt to enable SSL
    resp = client.put("/api/settings/ssl_enabled", json={"value": "true"}, headers=header)
    assert resp.status_code == 400
    assert "Cannot enable HTTPS" in resp.json()["detail"]


