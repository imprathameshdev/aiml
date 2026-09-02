# Developer Documentation

Welcome to the technical guide for Lot Vulture. This document covers the architecture, internal logic, and setup procedures required for contributing to the project.

## 🏗️ Architecture Overview

Lot Vulture is built as a decoupled full-stack application:

- **Backend:** FastAPI (Python) handles the REST API, WebSocket management, background task scheduling, and inference orchestration.
- **Inference engine:** `vulturevision` — a separate C++/ONNX Runtime package that does the actual model execution. DirectML is used on Windows; CUDA on Linux; CPU fallback otherwise. The Python backend loads the engine as a native extension via `from vulturevision import VultureVision` and hands it the license, HWID, and ROIs.
- **Frontend:** Vue 3 (Vite) with Vuetify 3 provides a responsive Material Design interface.
- **Database:** SQLite with Write-Ahead Logging (WAL) and comprehensive indexing for high-performance concurrent access. Image blobs go to a separate LMDB store at `storage_blobs.mdb/` so the SQLite file stays small.
- **AI Model:** ONNX-format object detection (community and commercial variants). The community model ships at `model/spot_detector.onnx`; the commercial model is downloaded on license activation.

## 🚀 Advanced Backend Features

### Inference engine integration
`backend/inference.py` wraps the `vulturevision` C++ engine. The backend holds a single `VultureVision` instance (re-instantiated on license / device / resolution change) and feeds it normalized ROIs on each scan. The engine returns per-ROI confidence scores; Python applies hysteresis and persists occupancy.

### License / Billing Integration
Briefly: the on-prem app generates a HWID from `/sys/class/dmi/id/product_uuid` + primary MAC, caches it under `VULTURE_LV_HOME`, and POSTs it to the cloud billing portal's `/v1/heartbeat` every `VULTURE_HEARTBEAT_INTERVAL_SECONDS` (default 600s). The response carries the mode (`community` / `trial_commercial` / `commercial`), license token, trial expiry, and seat count. `backend/billing/enforcer.py::compute_mode` decides which license key gets handed to the `VultureVision` engine on its 60-second config poll.

### Drift-Correcting Scheduler
The background task loop in `backend/scheduler.py` uses a drift-correcting timer. It measures inference time and subtracts it from the requested interval, ensuring captures stay as close to real-time as possible even under heavy load. A second coroutine, `_feedback_upload_loop`, drains the "Improve AI" submission queue to S3 via presigned URLs.

### Thread-Safe Inference
To keep the FastAPI event loop responsive, all CPU/GPU intensive inference and crop generation tasks are offloaded to separate worker threads using `asyncio.to_thread`. The `VultureVision` engine manages its own internal engine pool and GPU serialized access.

### Storage & Concurrency
- **WAL Mode:** SQLite is configured in WAL mode to allow simultaneous reads (from the API) and writes (from the background scheduler).
- **Hysteresis Logic:** Occupancy state is stabilized using configurable "Occupied" and "Free" confidence thresholds, preventing status flickering due to model noise.
- **LMDB for blobs:** Full-resolution snapshots and crops are stored in LMDB (`storage_blobs.mdb/`) so SQLite stays small and queries stay fast. The blob store is purged on a retention schedule (24h for images, configurable).

## 🛠️ Technical Stack

### Backend Dependencies
- **FastAPI:** Async API framework.
- **SQLAlchemy:** Database ORM (2.0 declarative style).
- **vulturevision:** C++ ONNX Runtime inference engine (DirectML / CUDA / CPU).
- **PyJWT:** Authentication and authorization.
- **httpx:** Async HTTP client used by `BillingClient` to talk to the cloud portal.
- **loguru:** Centralized logging (`backend.logging_config`).
- **LMDB:** Key-value blob store for snapshots and crops.

### Frontend Dependencies
- **Vue 3:** Reactive UI framework.
- **Vuetify 3:** UI component library.
- **Vue-Konva:** HTML5 Canvas wrapper for polygon drawing.
- **Axios:** HTTP client for `/api/*` calls.

## 📦 Setup & Installation

### Backend Setup
1. **Prepare Environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Initialize Database:**
   ```bash
   # Creates tables. The first user is created via the registration
   # flow (POST /api/register) — there's no built-in admin/admin default.
   PYTHONPATH=. python backend/init_db.py
   ```
3. **Run Server:**
   ```bash
   PYTHONPATH=. uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
   ```

### Frontend Setup
1. **Navigate & Install:**
   ```bash
   cd frontend
   npm install
   ```
2. **Start Dev Server:**
   ```bash
   # Includes a proxy to the backend on port 8000
   npm run dev
   ```

## 🐳 Docker Deployment

The repo ships a single Linux image at the repo root (`Dockerfile`) built
in two stages: a Node 22 alpine stage that builds the Vue SPA, then a
CUDA 12.6 + cuDNN runtime stage that bundles Python, GStreamer, the
C++ `vulturevision` engine, and the ONNX Runtime GPU build. The image
is the same artifact shipped to every customer regardless of whether
they have a GPU — see the "CPU fallback" section below for why that's
safe.

### Image size
Expect **~2.5–3 GB** compressed / ~5–6 GB uncompressed. The bulk is
the CUDA base image (`nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04`),
the ONNX Runtime GPU build, and the GStreamer runtime. If size
matters more than the convenience of a single image, you can build
two variants: a CUDA base (for GPU customers) and `ubuntu:24.04` (for
CPU-only customers), each with a thinner dependency set.

### CPU fallback — how it works

The CUDA-based image runs on hosts **without** a GPU. The
`vulturevision` C++ engine is built against the ONNX Runtime GPU
build, but that build also includes the CPU execution provider
(`CPUExecutionProvider`). The flow:

1. Fresh install seeds `inference_device="cpu"` (`init_db.py:99`).
   The backend passes `use_gpu=False` to the engine.
2. The C++ engine skips the CUDA provider entirely on the
   `if (use_gpu)` branch (`engine.cpp:53`). Inference runs on CPU.
3. If the operator changes the setting to `"cuda"` on a host with a
   GPU, the engine appends the CUDA provider and runs on GPU.
4. If they set `"cuda"` on a host **without** a GPU, the engine's
   try/catch (`engine.cpp:53-66`) catches the
   `Ort::Exception`, captures the error message
   (`get_available_gpu_provider` now returns
   `{provider: "", error: "<reason>"}`), and falls back to the
   default CPU provider. The Settings page surfaces the error in
   a warning so the operator can see *why* the GPU option isn't
   available — typically "libcudnn.so.9: cannot open shared
   object file" on a host with an NVIDIA GPU but no cuDNN 9.x
   runtime.

The Python `onnxruntime-gpu` package is only used to query
`get_available_providers()` so the Settings page can show/hide the
"Inference Device" dropdown. The actual inference path is in C++.
Both ONNX Runtimes ship with the image; both work on CPU-only hosts.

### "GPU not available" — diagnosing why

The Settings page shows a warning alert with the C++ probe's
error message when the GPU option can't be activated. Common
causes:

- **cuDNN 9.x missing.** ONNX Runtime 1.19.2 GPU requires cuDNN
  9.x. On Debian/Ubuntu hosts with only the cuDNN 8.x packages
  installed (`libcudnn8`), the probe fails with
  `libcudnn.so.9: cannot open shared object file`. Install the
  matching cuDNN 9 packages:

  ```bash
  # Debian/Ubuntu — see NVIDIA's repo for the latest
  sudo apt install libcudnn9-cuda-12 libcudnn9-dev-cuda-12
  ```

  The official Lot Vulture Docker image uses
  `nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04` which ships
  cuDNN 9.x — this is a host-only issue.

- **NVIDIA driver too old.** Driver < 530 doesn't ship the
  CUDA 12.x user-mode libraries the ORT GPU build needs. Upgrade
  the driver (or use `--gpus all` with the bundled CUDA image).

- **No GPU.** Self-explanatory. CPU fallback handles this; no
  action needed.

If the Settings page shows a different error, copy the message
verbatim into a support ticket — the C++ probe captures the
underlying `Ort::Exception` text, which is enough to identify the
exact missing library or version mismatch.

### NVIDIA driver requirements (for GPU usage)

If a customer wants to use the GPU:

- **Host driver:** NVIDIA driver ≥ 530 (provides CUDA 12.x user-space
  libraries). The image's CUDA runtime is 12.6.1.
- **Container toolkit:** `nvidia-container-toolkit` installed on the
  host. On modern Docker installations this is the
  `nvidia-container-runtime` registered as a default runtime.
- **Run command:**
  ```bash
  docker run --gpus all -p 8000:8000 \
    -v lotvulture-data:/var/lib/lotvulture \
    lotvulture:latest
  ```
  Without `--gpus all`, the container starts fine and runs on CPU.

Customers on older NVIDIA drivers (pre-530) may see
`"CUDA error: no kernel image is available for execution on the
device"` if they enable `inference_device=cuda`. Setting it back to
`"cpu"` (or removing the override) restores normal operation.

### Persisted state

All persistent files live under `/var/lib/lotvulture` inside the
container — the SQLite database (`parkinglot.db` plus its WAL/SHM
siblings), the LMDB blob store (`storage_blobs.mdb/`), the logs
directory, and a `.env` file for runtime overrides. Mount the whole
directory as a single named volume in compose or a host path with
`docker run` so state survives container restarts and image
upgrades:

```yaml
# docker-compose.yml (excerpt)
volumes:
  - lotvulture-data:/var/lib/lotvulture
```

```bash
# Equivalent docker run (named volume)
docker run --gpus all -p 8000:8000 \
  -v lotvulture-data:/var/lib/lotvulture \
  lotvulture:latest

# Or bind-mount a host directory
docker run --gpus all -p 8000:8000 \
  -v /srv/lotvulture:/var/lib/lotvulture \
  lotvulture:latest
```

The container's `VOLUME ["/var/lib/lotvulture"]` directive in the
Dockerfile declares this as the persistent-data path, and `compose.yml`
shipped in the repo bind-mounts the `lotvulture-data` named volume
over it. Don't bind-mount individual files inside this directory
(Docker doesn't handle that well and SQLite's WAL mode needs the
`.db`, `.db-wal`, and `.db-shm` files co-located).

### Rebuilding the vulturevision C++ engine

The `vulturevision` package is a separate C++/pybind11 extension
that bundles its own `libonnxruntime.so` and (on Linux) the CUDA /
TensorRT execution provider shared libraries. Any change to its
sources (`vulturevision/src/*.cpp`, `include/*.hpp`) requires a
full rebuild before the change takes effect on a running install:

```bash
# From the repo root, on a build machine that has the trained
# model files at ../spot_detector_training/best_models/ and
# the full toolchain (cmake, g++, pybind11, openssl, OpenMP).
./vulturevision/build.sh

# That script produces vulturevision-1.0.0-cp312-cp312-linux_x86_64.whl
# which the Dockerfile then installs into the runtime image.
```

The Linux wheel is produced from the
`vulturevision-linux-x86_64-gpu-1.19.2` ONNX Runtime distribution
(downloaded by CMake during the build). The Windows wheel is
produced by `vulturevision/build_windows.sh` and uses the DirectML
provider instead. See `windows/INSTALL.md` for the Windows build
flow.

**When the engine source changes, you must also bump the wheel
filename** (`vulturevision-1.0.0-cp312-cp312-linux_x86_64.whl` →
`vulturevision-1.0.1-...whl` or similar) and update the
`COPY` / `pip install` line in the Dockerfile to match. Otherwise
the new source won't be picked up by a rebuild of the Docker image.

## 🚀 CI/CD & Automated Releases

Lot Vulture uses **GitHub Flow** with automated CalVer versioning:

### Versioning Scheme
Releases follow the Calendar Versioning + Git SHA format: `vYYYY.MM.DD-<shortsha>` (e.g. `v2026.08.29-5a03db1`). The version is displayed in the sidebar footer and System Diagnostics with a direct link to the corresponding GitHub commit.

### Automated Release Workflow
To publish a new release:
1. Ensure your `master` branch is clean and up to date.
2. Run the release script from the repo root:
   ```bash
   ./release.sh
   ```
3. The script tags the current commit with `vYYYY.MM.DD-<shortsha>` and pushes the tag to GitHub.
4. GitHub Actions ([`.github/workflows/build-and-release.yml`](.github/workflows/build-and-release.yml)) automatically:
   - Builds and publishes the Linux Docker image to GitHub Container Registry (`ghcr.io/lotvulture/lotvulture:<version>` and `:latest`).
   - Cross-compiles the standalone Windows Inno Setup installer (`LotVulture_Setup_<version>.exe`).
   - Creates a GitHub Release at `lotvulture/lotvulture/releases` with auto-generated release notes and attaches the Windows installer executable.

### Pull Request CI
Every Pull Request opened against `master` triggers [`.github/workflows/ci.yml`](.github/workflows/ci.yml) to execute:
- Backend `pytest` suite across all endpoints and database models.
- Frontend `vitest` suite (199 unit tests) and `npm run build` production bundling.

## 📉 Storage Optimization Logic

To prevent uncontrolled database growth, the system uses a tiered storage approach:
1. **CameraScan:** Full-resolution snapshots are stored once per camera per scan cycle. The image bytes live in LMDB (`storage_blobs.mdb/`); the SQL row carries metadata plus the LMDB key.
2. **OccupancyEvent:** Stores only state transitions (e.g., "vacated" -> "occupied") along with a 128x128 crop. Crops also live in LMDB.
3. **OccupancyHourly:** Background rollups aggregate raw data into hourly averages for long-term trends.
4. **Auto-Purge:** A maintenance loop (defined in `maintenance.py` and scheduled by `scheduler.py`) automatically purges image blobs after 24h and raw metrics after 7d based on settings.

## 🔔 Alerting Subsystem

The alerting subsystem fires notifications when a rule's condition is met.
It has three moving parts: **rules** (when to fire), **channels** (where to
deliver), and **camera groups** (named sets of cameras used by threshold
rules). All three are system-wide and permission-gated.

### Concepts

- **Rule** — a single condition + delivery configuration. Six trigger
  types:
  - `space_occupied` / `space_vacated` / `space_edge` — fire when one or
    more specific spaces transition. Optional `consecutive_count` buffer
    (default 1) requires N consecutive observations before firing, so a
    flicker doesn't trigger an alert.
  - `lot_full_above_pct` / `lot_open_below_pct` — fire when occupancy %
    across a camera group crosses `fire_at_pct`. Re-arm only when
    occupancy crosses back to `resolve_at_pct` (e.g. fire at 90%, resolve
    at 70% prevents "lot full" spam at 90/89/90 oscillations).
  - `camera_offline` — fire when a camera's last `CameraScan` is older
    than `minutes_offline`. `camera_id` optional; absent means "any
    enabled, non-test camera".
- **Channel** — a registered destination (webhook / email / mqtt). Rules
  pick a channel by FK; updating the channel updates every rule that uses
  it.
- **Camera group** — a named set of cameras. Threshold rules scope to a
  group; "any camera" offline rules ignore groups entirely.

### Lifecycle (3 states)

```
fired ─► sent     (delivery succeeded; written to alert_events)
fired ─► failed   (delivery error; rescheduled with exponential backoff)
                  max 4 attempts (1m, 5m, 30m, 2h); then terminal
```

Manual retry endpoint `POST /api/alerts/{id}/retry` resets status to
`fired` but does NOT reset `attempts` — the retry budget is global, so
an operator can't loop manual retries to bypass the cap.

### Permissions

- `manage_alerts` — required to create/edit/delete channels, groups, and
  rules, and to manually retry events. Admins auto-have it via
  `ADMIN_PERMISSIONS`.
- `GET /api/alerts` (history list) is open to any authenticated user
  so operators can see what's firing without needing write access.

### SMTP / email configuration

Email channels read credentials from the **system-wide** `Setting` table
(operators provide their own SMTP — no defaults):

| Setting key             | Purpose                          |
|-------------------------|----------------------------------|
| `alerting_enabled`      | master switch (`"true"`/`"false"`)|
| `alerting_smtp_host`    | SMTP server hostname             |
| `alerting_smtp_port`    | SMTP port (default `587`)        |
| `alerting_smtp_user`    | SMTP username                    |
| `alerting_smtp_pass`    | SMTP password                    |
| `alerting_smtp_from`    | From address                     |
| `alerting_smtp_use_tls` | `"true"` to use STARTTLS         |

### Walkthrough: testing a webhook locally

The fastest way to verify a webhook rule end-to-end is with
[webhook.site](https://webhook.site) or a local `nc` listener.

1. Get a webhook URL:
   - Easiest: open https://webhook.site and copy the unique URL it shows.
   - Local: `nc -lk 9000` in one terminal.
2. Create a channel:
   ```bash
   curl -X POST http://localhost:5174/api/alert-channels \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "Local test", "channel_type": "webhook",
          "config": {"url": "https://webhook.site/<your-id>",
                     "secret": "shh"}}'
   ```
3. Create a rule scoped to a real space:
   ```bash
   curl -X POST http://localhost:5174/api/alert-rules \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "Space 1 occupied",
          "trigger_type": "space_occupied",
          "condition": {"space_ids": [1]},
          "channel_id": 1,
          "consecutive_count": 1,
          "cooldown_seconds": 0}'
   ```
4. Watch the delivery on webhook.site (or `nc`). The body is the
   envelope:
   ```json
   {
     "event_id": 42,
     "rule_id": 7,
     "rule_name": "Space 1 occupied",
     "trigger_type": "space_occupied",
     "fired_at": "2026-07-01T12:00:00+00:00",
     "data": {...}
   }
   ```
5. Inspect history:
   ```bash
   curl http://localhost:5174/api/alerts?status=sent \
     -H "Authorization: Bearer $TOKEN"
   ```
   The `attempts`, `last_error`, and `delivered_at` fields show the
   delivery outcome.

### Walkthrough: the GUI

The full GUI lives under **Alerts** in the left sidebar (right after
**Cameras & Spaces**, before **API Access**). The page has four
tabs, each backed by the API endpoints above.

- **Channels** — registered destinations (webhook / email / mqtt).
  Click **Add Channel**, pick a type, fill in the type-specific
  config. Each channel has a **Test Channel** button that runs a
  probe through the channel's handler and reports success/failure
  + latency. The probe works for both new (unsaved) and existing
  channels — for new ones it posts to `/api/alert-channels/test-config`
  with the in-memory payload; for existing ones it posts to
  `/api/alert-channels/{id}/test`.
- **Camera Groups** — named sets of cameras. Threshold rules
  scope to a group. Click **Add Group**, name it, pick cameras.
- **Rules** — the centerpiece. Six trigger types. Per-space rules
  use a shared space picker (grouped by camera, with a
  "Select all in this camera" quick-pick). Threshold rules show
  `Fire at %` and `Resolve at %` side-by-side with a live
  validation that they sit on the right side of each other.
  Camera-offline rules let you pick a specific camera or "any".
  Each rule has a **Test Fire** button (only on saved rules) that
  creates a real `alert_event` row and runs it through the
  dispatcher so you can see the outcome in the History tab.
- **History** — read-only audit list of every fired event with
  status chips (fired / sent / failed), attempts, and the JSON
  payload (click a row to expand). Filters: status, rule, time
  range (1h / 24h / 7d / all). Auto-refreshes every 30s while the
  tab is active. A **Retry** button appears on failed/sent
  events for users with `manage_alerts`; it's disabled when the
  event has exhausted its 4-attempt retry budget.

Email channels also get two optional per-channel fields beyond
the basic `to` list:
- **Subject prefix** — prepended to the email subject (default
  "LotVulture alert").
- **From address override** — overrides the system-wide SMTP
  from address. Useful for routing two alert channels through the
  same SMTP server with different sender domains.

**Permission gating:** the **History** tab is open to any
authenticated user. The action buttons (Add/Edit/Delete,
enable/disable, retry) on the other tabs require `manage_alerts`.
Admins auto-have it.

**SMTP settings** (used by email channels) live in
**Settings → Alerting** (gated by `edit_settings`). A master switch
disables the entire alerting engine when off, and the SMTP fields
are dimmed. The password field is a standard `type="password"`;
credentials are stored in the `Setting` table, not in any
operator-facing log.

### Walkthrough: testing a threshold rule

Threshold rules are easiest to verify with the API rather than waiting
for real occupancy:

1. Create a camera group with one camera:
   ```bash
   curl -X POST http://localhost:5174/api/camera-groups \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "Test lot", "camera_ids": [1]}'
   ```
2. Create the threshold rule (fire at 90%, resolve at 70%):
   ```bash
   curl -X POST http://localhost:5174/api/alert-rules \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "Lot full",
          "trigger_type": "lot_full_above_pct",
          "condition": {"camera_group_id": 1,
                        "fire_at_pct": 90, "resolve_at_pct": 70},
          "channel_id": 1}'
   ```
3. Mark enough spaces occupied to push the group over 90%. The engine
   evaluates threshold rules on every tick (default 5s) and fires once
   when occupancy crosses 90% from below.
4. Mark some spaces vacant (drop below 70%) to re-arm. Mark them
   occupied again — the rule fires again.

### Hysteresis details

- **Per-space edges** (`space_occupied` / `space_vacated` / `space_edge`):
  the buffer counts "consecutive observations of the firing state". For
  `space_edge`, the firing state is dynamic (whichever state the most
  recent edge transitioned to) and re-arms on the next change.
- **Threshold rules**: stay firing through the dead band between
  `fire_at_pct` and `resolve_at_pct`. This is the textbook thermostat
  pattern — `fire_at_pct=90, resolve_at_pct=70` with oscillations
  around 90% fires exactly once, not on every boundary touch.
- **Camera offline**: stays firing as long as the camera has no scan
  newer than `minutes_offline`. Re-arms the moment a new scan arrives.

### Architecture (in-process)

```
Occupancy rows ─┐
CameraScan     ─┼─► engine._tick (every 5s) ─► alert_events (status='fired')
                │                              │
                │                              ▼
                │                      dispatcher._claim_one_event
                │                              │
                │                              ▼
                │                      channels.webhook / .email / .mqtt
                │                              │
                │                              ▼
                │                      alert_events (status='sent'|'failed')
                │                              │
                │                              ▼
                │                      next_retry_at = now + BACKOFF[attempts-1]
                │                              (until MAX_ATTEMPTS=4)
```

The engine and dispatcher are two separate asyncio tasks started from
`main.py`'s lifespan. They share the same `SessionLocal` so writes from
one are immediately visible to the other.

## 🧪 Testing & Quality

We use `pytest` for the backend and `vitest` for the frontend.
Refer to **[TESTING.md](./TESTING.md)** for full details on running the test suite.

## 📡 API Integration

The system supports unified authentication via JWT or `X-API-Key`.
- **WebSocket:** Live updates for all cameras are pushed on a single socket at `ws://<host>/api/live/ws`. Each message carries a `camera_id` so the frontend can dispatch updates to the right card.
- **HTTP:** Standard GET/POST endpoints for all CRUD and status operations.
- **Docs:** Interactive API documentation is available within the app for users with `manage_api_keys` permission.

## 🔒 Security Considerations

### JWT signing key (C1)

The signing key is loaded in this order on every backend start:

1. `VULTURE_SECRET_KEY` env var — explicit operator override
2. `$LV_HOME/secret_key` file — generated on first start with `0600` perms
3. Generated on the fly and persisted to `$LV_HOME/secret_key` with `0600` perms

A loud `WARNING` is logged at startup if the env var is not set. The
file path matches the volume mount point, so a Docker container restart
or `docker-compose` recreate preserves the same key (and existing
JWTs remain valid). If you run multiple replicas behind a load
balancer, set `VULTURE_SECRET_KEY` to the same value on all of them
— the file is local to each container and would otherwise diverge.

### CORS (C5)

The backend is configured with `allow_origins=["*"]` plus
`allow_credentials=True`. This is rejected by the Fetch spec
(browsers refuse the response) and is unsafe if a browser ever does
honor it. A startup `WARNING` makes this visible. For production,
set `VULTURE_CORS_ALLOW_ORIGINS` to a comma-separated allowlist
(e.g. `https://lotvulture.example.com`) and patch the middleware to
read it.

### JWT in URL query strings (C6)

Two call sites — `LogViewer.vue:93` and `StorageDiagnostics.vue:228`
— place the long-lived JWT in the URL query string. The WebSocket
case is the industry-standard pattern: browsers cannot set custom
headers on the WebSocket handshake, so header-based auth isn't an
option. The risk is token leakage via:

- Reverse-proxy access logs (if the URL is logged)
- `Referer` headers on cross-origin loads
- Browser history
- Shoulder surfing

Mitigations for hardened deployments:

- Configure your reverse proxy to strip query strings from access logs
- Migrate the WebSocket auth to a short-lived single-use ticket
  endpoint (`POST /api/admin/debug/logs/ws-ticket` returns a 60s
  ticket that the WebSocket then uses in the query string)
- For `StorageDiagnostics.vue`, replace `window.open` with
  `fetch() + URL.createObjectURL()` so the token never appears in
  the URL

### Password Hashing (bcrypt)

User passwords are securely hashed using **bcrypt** via `passlib.hash.bcrypt` in `backend/auth.py`. When a user authenticates, constant-time verification is performed. New users created via `/api/setup/admin` or `/api/users` always have their passwords hashed prior to storage in the database.

### First-load admin setup (C3)

There is no default `admin/admin` user. On a fresh install,
hitting any protected route (or even `/api/login`) returns 428
Precondition Required with a structured body the frontend uses to
redirect to `/setup`. The operator creates the first admin there.
The unauthenticated `POST /api/setup/admin` endpoint returns 409
once any admin exists — use `/api/users` (authenticated) for
additional users.

### RTSP credentials in API responses (C4)

`GET /api/cameras` (the public list endpoint) uses the
`CameraOutPublic` schema which omits `stream_password`. RTSP
credentials never leave the server for any authenticated user.
Admin-only routes (PUT, POST) still return the full password so
the editor can pre-fill it.

### LMDB handle-swap race (H1)

The weekly LMDB compact in `scheduler._optimization_loop` closes
and reopens the global `lmdb_env`. A single process-wide
`threading.Lock` (`database.LMDB_ENV_LOCK`) serializes the swap
with concurrent `get_blob` / `put_blob` / `delete_blob` calls,
preventing a half-swapped state (new env, old named-db handles)
from crashing readers. Critical section: `compact + close +
os.replace + reopen`. No long-running transactions should hold
the lock for more than a few milliseconds.

### YouTube pipe file descriptor (H2)

The yt-dlp subprocess writes its output to a pipe on a new file
descriptor (NOT the Python process's stdin). The GStreamer
`fdsrc` element's `fd` property is set to `proc.stdout.fileno()`
both in the pipeline string and via `set_property`, so the video
stream actually flows into the appsink.

### RTSP credentials in logs (H3)

All GStreamer pipeline debug logs redact
`user:password@host` → `***:***@host` via
`stream_manager._redact_url_credentials`. The unredacted URL is
only used at the GStreamer parse-time (which needs the real
credentials); it never reaches the log file.

### Billing enforcement (H4, H5, H6)

- H4: `enforcer.compute_mode` checks `is_active()` (license
  expiry) on every commercial/trial path. An expired commercial
  license is downgraded to community within 60s (via the
  `get_vulturevision` re-check interval) without needing a new
  heartbeat.
- H5: `LocalSession.update_from_heartbeat` uses `"key" in data`
  (key-presence) for every field. The cloud can revoke a token,
  clear an email, or expire a license by sending `None` — the
  old truthy-guard would have ignored the change.
- H6: the token-clear on `mode: community` is idempotent — it
  fires every time, regardless of the previous mode. A token
  injected by `activate_callback` while the session is already
  community is cleared on the next heartbeat.

### Frontend timer leaks (H8, H9)

- H8: `LiveDashboard.vue` saves the 5-minute billing interval
  handle to `billingInterval` and clears it in `onUnmounted`.
  Previously the handle was dropped, so the interval kept firing
  after unmount, mutating `billingStatus.value` on an
  unmounted component.
- H9: WebSocket reconnect `setTimeout` handles (`globalReconnectTimer`
  in `LiveDashboard.vue`, `reconnectTimer` in `LogViewer.vue`) are
  tracked and cleared in `onUnmounted`. `ws.onclose` and
  `ws.onerror` are nulled before calling `ws.close()` to
  prevent the close-handler from re-scheduling itself.

### Threshold validation (H10, H11)

- H10: `Settings.vue` text inputs for `freeThresh` / `occThresh`
  cross-validate (`freeThresh < occThresh`) via `:rules`, with
  numeric input enforced. The save button is disabled when
  either is invalid.
- H11: `AlertRuleForm.vue`'s `thresholdError` now requires
  numeric input (returns an error if `typeof !== 'number'` or
  `isNaN`), and `buildPayload` falls back to safe defaults
  (50%/40%) instead of sending `NaN` to the backend.

### Camera-offline state row (H12)

The `AlertStatePerCamera` row is created only in the fire
path, never in the cooldown path. The previous code set the
row in both, so the very first tick (in cooldown) would set
`currently_flagged = True` and the alert was suppressed for
the entire offline episode.

### Legacy `/api/live/{camera_id}` routes (H13)

The duplicate GET handler (which declared an uninjectable
`websocket: WebSocket` parameter) was removed. Only the
WebSocket handler remains, accepting and immediately closing
with code 1001.
