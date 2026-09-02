import pytest

def test_thread_diagnostics_endpoint_requires_auth(client):
    """Calling /api/admin/debug/threads without credentials returns 401."""
    r = client.get("/api/admin/debug/threads")
    assert r.status_code == 401

def test_thread_diagnostics_endpoint_returns_threads(client, admin_token):
    """Calling /api/admin/debug/threads with admin token returns current threads and frames."""
    r = client.get(
        "/api/admin/debug/threads",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    data = r.json()
    assert "total_threads" in data
    assert "threads" in data
    assert data["total_threads"] > 0

    # Verify structure of each thread
    for t in data["threads"]:
        assert "ident" in t
        assert "name" in t
        assert "is_daemon" in t
        assert "is_alive" in t
        assert "stack" in t
        assert "formatted_traceback" in t
        assert isinstance(t["stack"], list)
        if t["stack"]:
            first_frame = t["stack"][0]
            assert "file" in first_frame
            assert "line" in first_frame
            assert "func" in first_frame
            assert "code" in first_frame

    # At least one thread should be marked as current
    current_threads = [t for t in data["threads"] if t.get("is_current")]
    assert len(current_threads) == 1
