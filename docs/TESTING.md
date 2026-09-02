# Testing Documentation

This project uses a comprehensive test suite covering both the FastAPI backend and the Vue 3 frontend.

## Backend Testing (Python / Pytest)

The backend tests use `pytest` and `httpx` to verify CRUD operations, authentication, billing integration, and background processes. All tests run against an isolated, in-memory SQLite database.

### Prerequisites
Ensure the virtual environment is active and dependencies are installed:
```bash
# Install testing dependencies if not present
pip install pytest pytest-asyncio httpx
```

### Running Tests
To run the full backend suite:
```bash
pytest
```

To run only the billing tests:
```bash
pytest tests/ -k billing
```

### Coverage (130+ tests across these files)
- **`tests/test_crud.py`** — Database operations for cameras, spaces, user management, and pre-aggregated statistics.
- **`tests/test_api.py`** — REST endpoints, JWT authentication, X-API-Key validation, and performance metric endpoints.
- **`tests/test_auth.py`** — API key generation and hashing security.
- **`tests/test_scheduler.py`** — Background inference loop, maintenance logic, and inference speed recording.
- **`tests/test_inference.py`** — Model-agnostic inference logic, automated architecture detection, and hardware (GPU/CPU) selection.
- **`tests/test_storage.py`** — LMDB blob store operations and retention.
- **`tests/test_hierarchy.py`** — Multi-camera hierarchy / parent-child camera relationships.
- **`tests/test_billing_hwid.py`** — HWID generation, caching, fallback when `/sys/class/dmi/id/product_uuid` is missing.
- **`tests/test_billing_session.py`** — LocalSession save/load/expiry/clear.
- **`tests/test_billing_enforcer.py`** — `compute_mode` matrix across all license/trial/quota states.
- **`tests/test_billing_client.py`** — `BillingClient` with mocked `httpx`, error mapping (`BillingAuthError`, `BillingServerError`, `BillingError`).
- **`tests/test_billing_routes.py`** — `/api/billing/*` endpoints with mocked `BillingClient`.
- **`tests/test_billing_inference.py`** — VultureVision re-instantiated with correct license/HWID per mode.
- **`tests/test_billing_heartbeat_loop.py`** — Background heartbeat loop wiring, `_camera_count_for_heartbeat` helper, `_hydrate_cache`, `_purge_stale_cache_entries`.
- **`tests/test_billing_feedback_uploader.py`** — S3 presigned-PUT loop, retry, quarantine after 30 attempts or 30 days.
- **`tests/test_feedback_submit.py`** — On-prem `/api/admin/feedback/submit` writes corrections + `original_occupancy` to metadata.
- **`tests/test_feedback_pending.py`** — On-prem `/api/admin/feedback/pending` returns `{pending, threshold}` for the warning chip.

---

## Frontend Testing (Vue / Vitest)

The frontend uses `vitest` and `@vue/test-utils` with a `jsdom` environment to verify component rendering and utility logic.

### Prerequisites
Dependencies are managed via npm:
```bash
cd frontend
npm install
```

### Running Tests
To run the frontend suite in non-interactive mode:
```bash
cd frontend
npm test -- --run
```

To run in watch mode (interactive):
```bash
cd frontend
npm test
```

### Coverage (190+ tests across 13 test suites)
- **`frontend/src/tests/format.spec.js`** — Storage size calculation and formatting utility.
- **`frontend/src/tests/Login.spec.js`** — Authentication form, validation, and JWT storage.
- **`frontend/src/tests/BillingPage.spec.js`** — License activation, quota view, and status updates.
- **`frontend/src/tests/LicenseBadge.spec.js`** — License badge rendering, color coding, and state changes.
- **`frontend/src/tests/LiveDashboardBillingBanner.spec.js`** — Dashboard billing warnings and grace-period alerts.
- **`frontend/src/tests/Alerts.spec.js`** — Main alerts page tab transitions and permission gating.
- **`frontend/src/tests/AlertChannelForm.spec.js`** — Webhook, email, and MQTT channel configuration.
- **`frontend/src/tests/AlertRuleForm.spec.js`** — Rule creation, condition builders, and hysteresis cross-validation.
- **`frontend/src/tests/AlertHistory.spec.js`** — Alert delivery event audit log, filtering, and manual retry.
- **`frontend/src/tests/CameraGroupForm.spec.js`** — Camera group creation and member selection.
- **`frontend/src/tests/CameraListGroups.spec.js`** — Camera management and group associations.
- **`frontend/src/tests/SettingsAlerting.spec.js`** — Alerting master switch and SMTP configuration.
- **`frontend/src/tests/SpaceEditor.spec.js`** — Polygon space editor tool and canvas events.

## Key Testing Infrastructure
- **Isolated DB**: Backend tests patch `database.SessionLocal` to use `sqlite://` (in-memory) and point `VULTURE_LV_HOME` at `/tmp/lotvulture_test`, ensuring your production `parkinglot.db` and `/var/lib/lotvulture/` are never touched during tests.
- **Mocking**: External dependencies like `httpx` (cloud billing API), `vulturevision` (the C++ inference engine), and `VueKonva` are mocked to keep tests fast and deterministic.
- **Permission Mapping**: Tests verify that restricted users cannot access admin-only endpoints (camera CRUD, user management, settings, billing).
- **Continuous Integration**: Every Pull Request and push to `master` automatically triggers the complete test suite via GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
