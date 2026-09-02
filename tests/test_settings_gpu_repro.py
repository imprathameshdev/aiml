"""Reproduction tests for the two Settings-page bugs:

1. CPU-mode freeze: opening the Settings page when the app boots in
   CPU mode freezes the whole UI.  Root cause: the embedded
   ``libonnxruntime_providers_cuda.so`` (OR/T 1.19.2 CUDA provider)
   has a ``.init`` section constructor that SEGFAULTS when cuDNN 9.x
   is missing (common on hosts with cuDNN 8.x or no cuDNN at all).
   The C++ probe ``dlopen``'s the library, which triggers the crash,
   killing the entire backend process.  The Settings page's first
   serial ``await`` (Settings.vue:686) hits the GPU-probe endpoint,
   so the whole page appears frozen/dead.

2. "No GPU detected on this host" on a GPU-capable instance: same
   root cause.  The probe crashes, the frontend's ``catch`` fires,
   ``gpuError`` stays at its default ``ref('')``, and the silent
   fallback branch renders: "No GPU detected on this host. The
   model will run on CPU." — even though the host HAS a GPU and
   the actual reason is a missing library.

Fix: ``_probe_gpu_provider_safely()`` (backend/inference.py) checks
for ``libcuda`` and ``libcudnn.so.9`` via ``ctypes.util.find_library``
and ``ldconfig -p`` (which consult the linker cache WITHOUT
dlopen'ing, avoiding the crashy .init constructors) BEFORE calling
the C++ probe.  If either prerequisite is missing, it returns a
descriptive error without ever touching the C++ code.
"""
import asyncio
import os
import sys
import time
import threading
import types
import pytest
import httpx
from unittest.mock import patch, MagicMock

from backend import inference, app as app_module


def _reset_inference_state():
    inference._vv_instance = None
    inference._vv_config = {
        "license": None, "instance_id": None,
        "use_gpu": None, "max_res": None, "last_check": 0,
    }


# ---------------------------------------------------------------------------
# Bug 1: CPU-mode freeze — the GPU probe must NOT crash the backend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gpu_probe_endpoint_does_not_crash_the_backend():
    """The GPU-probe endpoint must return a 200 response (even when
    GPU is unavailable) and must NOT crash the backend process.

    Before the fix: the C++ probe ``dlopen``'d
    ``libonnxruntime_providers_cuda.so`` whose ``.init`` constructor
    SEGFAULTS when cuDNN 9.x is missing, killing the process.
    The Settings page's first ``await`` hit this endpoint, so the
    whole page — and every subsequent request — froze.
    """
    _reset_inference_state()

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # Fire the probe multiple times — previously the second
        # call connected to a dead server (segfault on the first).
        for i in range(3):
            resp = await asyncio.wait_for(
                client.get("/api/settings/gpu-provider"),
                timeout=5.0,
            )
            assert resp.status_code == 200, (
                f"probe call {i+1} returned {resp.status_code}; "
                f"the C++ probe likely crashed the worker"
            )
            data = resp.json()
            assert "available" in data
            assert "provider" in data
            assert "error" in data
            assert isinstance(data["error"], str)


@pytest.mark.asyncio
async def test_gpu_probe_returns_quickly_when_prerequisites_missing():
    """When cuDNN 9 is missing, the endpoint must return in
    milliseconds (not seconds).  The Python guard short-circuits
    the call to the C++ probe, so the dlopen never happens and
    the response is instant.
    """
    _reset_inference_state()

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        loop = asyncio.get_running_loop()
        start = loop.time()
        resp = await client.get("/api/settings/gpu-provider")
        elapsed = loop.time() - start
        assert resp.status_code == 200
        assert elapsed < 1.0, (
            f"GPU probe took {elapsed:.2f}s — the Python guard "
            f"should short-circuit when cuDNN 9 is missing"
        )


# ---------------------------------------------------------------------------
# Bug 2: "No GPU detected on this host" — error must be non-empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gpu_probe_surfaces_error_when_cudnn_missing():
    """When the C++ probe fails because cuDNN 9.x is missing, the
    endpoint must return ``available=False`` with a NON-EMPTY
    ``error`` string.  The Settings page's warning banner
    (Settings.vue:127-139) only renders when ``gpuError`` is
    non-empty; if ``error`` is empty, the silent fallback text
    "No GPU detected on this host" renders instead.
    """
    _reset_inference_state()

    result = inference._probe_gpu_provider_safely()
    assert isinstance(result, dict)
    # If GPU is available on this host, the probe succeeds — skip.
    if result["provider"]:
        pytest.skip("GPU is available — can't test failure path")

    assert result["provider"] == ""
    assert result["error"] != "", (
        "Probe failed but returned empty error — the Settings page "
        "will show 'No GPU detected on this host' instead of the "
        "actual reason."
    )
    # Error should mention cudnn, onnxruntime, or libcuda
    err_lower = result["error"].lower()
    assert any(k in err_lower for k in ("cudnn", "onnxruntime", "libcuda", "cuda")), (
        f"Error should mention cuDNN/CUDA/onnxruntime, got: {result['error']}"
    )

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.get("/api/settings/gpu-provider")
        data = resp.json()
        assert data["available"] is False
        assert data["error"] != "", (
            "Endpoint returned empty error — the Settings page falls "
            "through to the silent 'No GPU detected' branch."
        )
        err_lower = data["error"].lower()
        assert any(k in err_lower for k in ("cudnn", "onnxruntime", "libcuda", "cuda"))


# ---------------------------------------------------------------------------
# invalidate_config_cache must not block the event loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_config_cache_does_not_block_on_model_build(db_session):
    """The billing heartbeat coroutine (scheduler._send_one_heartbeat)
    calls ``invalidate_config_cache()`` on the event loop.  This must
    NOT acquire any lock held by ``get_vulturevision()`` during the
    slow C++ build, or the whole event loop freezes.
    """
    _reset_inference_state()

    build_started = threading.Event()

    def slow_vv(**kwargs):
        build_started.set()
        time.sleep(2.0)
        return MagicMock()

    with patch.object(inference, "VultureVision", slow_vv), \
         patch.object(inference, "resolve_license_from_default_session",
                      return_value=("", "")), \
         patch("backend.database.SessionLocal", return_value=db_session):
        build_task = asyncio.create_task(
            asyncio.to_thread(inference.get_vulturevision)
        )
        await asyncio.to_thread(build_started.wait, 5.0)
        assert build_started.is_set()

        loop = asyncio.get_running_loop()
        start = loop.time()
        inference.invalidate_config_cache()
        elapsed = loop.time() - start
        assert elapsed < 0.1, (
            f"invalidate_config_cache took {elapsed:.2f}s on the "
            f"event loop — it must not block on the model build"
        )

        await asyncio.wait_for(build_task, timeout=5.0)