"""H13 audit fix — two routes named ``legacy_ws_closer``.

The previous code declared both a WebSocket and a GET handler at
``/api/live/{camera_id}`` with the same function name
``legacy_ws_closer``.  The GET variant declared a ``websocket:
WebSocket`` parameter that FastAPI cannot inject into an HTTP
route.  The duplicate name also shadowed the websocket function
in Python's namespace, making the GET the only callable — the
WebSocket route was never actually registered.

The fix removes the broken GET variant entirely.  The canonical
live endpoint is the single global socket at ``/api/live/ws``;
the WebSocket route at ``/api/live/{camera_id}`` is preserved as
a legacy closer (accept + immediately close with code 1001) so
old clients that try to open per-camera sockets don't spin.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_legacy_ws_closer_get_route_is_removed():
    """No broken legacy_ws_closer GET handler should exist at /api/live/{camera_id}."""
    from backend.app import app
    broken_get_routes = [
        r for r in app.routes
        if getattr(r, "path", "") == "/api/live/{camera_id}"
        and "GET" in getattr(r, "methods", set())
        and getattr(r, "name", "") == "legacy_ws_closer"
    ]
    assert broken_get_routes == [], (
        f"H13 regression: broken legacy_ws_closer GET route should be removed, "
        f"found {len(broken_get_routes)} route(s): {broken_get_routes}"
    )


def test_camera_live_occupancy_get_route_is_registered():
    """Ensure the valid REST endpoint get_camera_live_occupancy is registered."""
    from backend.app import app
    get_routes = [
        r for r in app.routes
        if getattr(r, "path", "") == "/api/live/{camera_id}"
        and "GET" in getattr(r, "methods", set())
        and getattr(r, "name", "") == "get_camera_live_occupancy"
    ]
    assert len(get_routes) == 1, "get_camera_live_occupancy GET route is missing"



def test_legacy_ws_closer_ws_route_still_exists():
    """The WebSocket route must still be registered (legacy clients
    expect the accept+close behavior)."""
    from backend.app import app
    ws_routes = [
        r for r in app.routes
        if getattr(r, "path", "") == "/api/live/{camera_id}"
        and getattr(r, "path", "").endswith("{camera_id}")
    ]
    # The WebSocket route is registered with the same path
    assert any(
        getattr(r, "__class__", type(r)).__name__ == "APIWebSocketRoute"
        for r in app.routes
        if getattr(r, "path", "") == "/api/live/{camera_id}"
    ), "WebSocket route at /api/live/{camera_id} is missing"


def test_no_duplicate_legacy_ws_closer_function_name():
    """The fix should leave exactly one function named
    ``legacy_ws_closer`` in ``backend.app`` (the WebSocket handler).
    The duplicate-name shadow was the original H13 bug."""
    import inspect
    from backend import app
    src = inspect.getsource(app)
    # Count occurrences of "def legacy_ws_closer"
    count = src.count("def legacy_ws_closer")
    assert count == 1, (
        f"H13 regression: expected exactly 1 'def legacy_ws_closer' in app.py, "
        f"found {count}."
    )
