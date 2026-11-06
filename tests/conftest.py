import os

# IMPORTANT: This MUST be set before any ``backend.*`` import so that
# ``backend.config._load_or_create_secret_key()`` writes the JWT
# signing key to a test-scoped directory instead of /var/lib/lotvulture.
os.environ.setdefault("VULTURE_LV_HOME", os.path.join(os.path.dirname(__file__), ".test_lv_home"))

# C2 audit speedup: Configure passlib's bcrypt to use 4 rounds (minimum) 
# instead of 12 during test runs to speed up authentication and password checks.
from passlib.hash import bcrypt as _bcrypt
_fast_bcrypt = _bcrypt.using(rounds=4)
_bcrypt.hash = _fast_bcrypt.hash
_bcrypt.verify = _fast_bcrypt.verify

# Stub vulturevision if the compiled C++ wheel is not present in the test environment
import sys
import types
if "vulturevision" not in sys.modules:
    try:
        import vulturevision
    except ImportError:
        _vv_mod = types.ModuleType("vulturevision")
        _vv_c_mod = types.ModuleType("vulturevision._vulturevision")
        
        def _stub_gpu_provider():
            return {"provider": "", "error": "CUDAExecutionProvider could not be loaded: libcudnn.so.9 missing"}
            
        _vv_c_mod.get_available_gpu_provider = _stub_gpu_provider
        _vv_mod._vulturevision = _vv_c_mod
        _vv_mod.get_gpu_provider = _stub_gpu_provider
        
        class _StubVultureVision:
            def __init__(self, *args, **kwargs):
                pass
            def predict(self, *args, **kwargs):
                return []
            def detect(self, *args, **kwargs):
                return []
                
        _vv_mod.VultureVision = _StubVultureVision
        sys.modules["vulturevision"] = _vv_mod
        sys.modules["vulturevision._vulturevision"] = _vv_c_mod

# Stub gi / GStreamer on Windows when running standalone unit tests without bundled MinGW GStreamer
if "gi" not in sys.modules:
    try:
        import gi
    except ImportError:
        from unittest.mock import MagicMock
        _gi_mock = MagicMock()
        sys.modules["gi"] = _gi_mock
        sys.modules["gi.repository"] = _gi_mock
        sys.modules["gi.repository.Gst"] = _gi_mock.Gst
        sys.modules["gi.repository.GstApp"] = _gi_mock.GstApp
        sys.modules["gi.repository.GstVideo"] = _gi_mock.GstVideo
        sys.modules["gi.repository.GLib"] = _gi_mock.GLib

# Stub opentelemetry instrumentors if not present in the environment
try:
    import opentelemetry.instrumentation.fastapi
except ImportError:
    from unittest.mock import MagicMock
    _otel_mock = MagicMock()
    sys.modules["opentelemetry.instrumentation"] = _otel_mock
    sys.modules["opentelemetry.instrumentation.fastapi"] = _otel_mock
    sys.modules["opentelemetry.instrumentation.sqlalchemy"] = _otel_mock

# Stub paho mqtt if not present in the environment
try:
    import paho.mqtt.client
except ImportError:
    from unittest.mock import MagicMock
    _paho_mock = MagicMock()
    sys.modules["paho"] = _paho_mock
    sys.modules["paho.mqtt"] = _paho_mock
    sys.modules["paho.mqtt.client"] = _paho_mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.app import app
from backend import models
from rollinglmdb import RollingLMDB

# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

@pytest.fixture(scope="function", autouse=True)
def force_test_lmdb():
    import shutil
    import tempfile
    from backend import database
    from backend.config import config

    test_snapshots = tempfile.mkdtemp(prefix="test_snapshots_")
    test_crops = tempfile.mkdtemp(prefix="test_crops_")

    orig_snapshot_path = config.SNAPSHOT_DB_PATH
    orig_crop_path = config.CROP_DB_PATH
    orig_snapshot_db = database.snapshot_db
    orig_crop_db = database.crop_db

    config.SNAPSHOT_DB_PATH = test_snapshots
    config.CROP_DB_PATH = test_crops
    database.snapshot_db = RollingLMDB(test_snapshots, max_size_gb=0.1)
    database.crop_db = RollingLMDB(test_crops, max_size_gb=0.1)

    yield

    database.snapshot_db.close()
    database.crop_db.close()
    shutil.rmtree(test_snapshots, ignore_errors=True)
    shutil.rmtree(test_crops, ignore_errors=True)
    config.SNAPSHOT_DB_PATH = orig_snapshot_path
    config.CROP_DB_PATH = orig_crop_path
    database.snapshot_db = orig_snapshot_db
    database.crop_db = orig_crop_db

@pytest.fixture(scope="function")
def db_session(monkeypatch):
    # Create the database and the tables
    Base.metadata.create_all(bind=engine)
    
    # Patch database.SessionLocal so even background tasks use our test DB
    monkeypatch.setattr("backend.database.SessionLocal", TestingSessionLocal)
    
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        # Drop the database tables
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    # C3 audit fix: the production init_db no longer creates a default
    # admin/admin user.  Tests that exercise HTTP endpoints still need
    # *some* admin to log in as, so we ensure one exists for the
    # lifetime of this test.  Tests that want a clean DB (no admin)
    # should call /api/setup/admin explicitly to verify the flow.
    import json as _json
    from backend import models as _models
    from backend.auth import hash_password as _hash_password
    existing_admin = (
        db_session.query(_models.User)
        .filter(_models.User.is_admin == True)
        .first()
    )
    if existing_admin is None:
        _models.User(
            username="testadmin",
            password_hash=_hash_password("testpassword"),
            is_admin=True,
            permissions=_json.dumps([
                "manage_cameras", "view_history", "view_metrics",
                "edit_settings", "manage_users", "manage_api_keys",
                "manage_billing", "manage_alerts",
            ]),
        )
        # The admin_user fixture (if used) will overwrite the username
        # to testadmin and set the same permissions; we just need the
        # row to exist so /api/login passes the C3 "any admin" gate.
        # Use a separate username to avoid the duplicate-key error.
        db_session.add(
            _models.User(
                username="_client_default_admin",
                password_hash=_hash_password("testpassword"),
                is_admin=True,
                permissions=_json.dumps([
                    "manage_cameras", "view_history", "view_metrics",
                    "edit_settings", "manage_users", "manage_api_keys",
                    "manage_billing", "manage_alerts",
                ]),
            )
        )
        db_session.commit()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    # Register the override for BOTH ``get_db`` references the app
    # uses.  The app defines a local ``get_db`` wrapper at
    # module scope (``app.py:184``) that shadows the import from
    # ``backend.database``, so the override must be registered on
    # the app's reference AND on ``backend.database.get_db``
    # directly.  Without the second registration, helper functions
    # like ``auth.get_auth_user`` (which take
    # ``Depends(database.get_db)``) get a fresh session that
    # doesn't share identity-map state with the route's ``db``
    # parameter — which caused the C2 bcrypt migration test to
    # silently fail (the change committed in one session but the
    # test's session didn't see it because the route and the auth
    # helper were on different sessions).
    import backend.app as _app
    app.dependency_overrides[_app.get_db] = override_get_db
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def admin_user(db_session):
    import json
    # C3 audit fix: the login endpoint now requires at least one user
    # with is_admin=True.  The previous test fixture created a user
    # with all permissions but is_admin=False, which was valid when
    # the default admin/admin was always present.  With the default
    # removed, this fixture must explicitly mark the test user as
    # an admin so /api/login accepts it.
    from backend.auth import hash_password
    user = models.User(
        username="testadmin",
        password_hash=hash_password("testpassword"),
        is_admin=True,
        permissions=json.dumps(["manage_cameras", "view_history", "view_metrics", "edit_settings", "manage_users", "manage_api_keys", "manage_billing", "manage_alerts"])
    )
    db_session.add(user)
    db_session.commit()
    return user

@pytest.fixture
def admin_token(client, admin_user):
    response = client.post("/api/login", json={"username": "testadmin", "password": "testpassword"})
    return response.json()["access_token"]

@pytest.fixture
def auth_header(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}

@pytest.fixture
def test_camera(db_session):
    cam = models.Camera(name="Test Camera", snapshot_url="http://test.jpg")
    db_session.add(cam)
    db_session.commit()
    return cam
