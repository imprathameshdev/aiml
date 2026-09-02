"""Tests for the GPU provider probe and the /api/settings/gpu-provider
endpoint.

Two regressions are pinned here:

  1. The C++ probe used to silently return an empty string on
     failure, hiding the actual reason. Now it returns a dict
     with both ``provider`` and ``error`` so the Settings page
     can show the user *why* GPU is unavailable (typically:
     cuDNN 9.x missing, no NVIDIA driver, no DX12 GPU, etc.).

  2. The Settings page used to hide the "Inference Device"
     section entirely when GPU was unavailable. Now it always
     shows the section, and surfaces the error in a warning
     alert so the user can act on it.
"""
import pytest
from unittest.mock import patch


def test_get_gpu_provider_returns_dict_with_provider_and_error():
    """``backend.inference.get_gpu_provider`` returns a dict
    shaped ``{provider, error}`` — never a bare string. Older
    builds returned a string; the new shape is what the
    Settings page and the C++ probe both expect."""
    from backend import inference
    result = inference.get_gpu_provider()
    assert isinstance(result, dict), (
        f"get_gpu_provider must return a dict, got {type(result).__name__}: {result!r}"
    )
    assert "provider" in result
    assert "error" in result
    assert isinstance(result["provider"], str)
    assert isinstance(result["error"], str)
    # On any host (with or without GPU), the probe must complete
    # without raising.
    assert result["provider"] in ("", "CUDAExecutionProvider", "DmlExecutionProvider")


def test_get_gpu_provider_surfaces_cudnn_error():
    """When the C++ probe fails because cuDNN 9 is missing, the
    error string must contain a useful diagnostic (e.g. 'cudnn',
    'onnxruntime', or a path to a missing .so). On hosts where GPU
    works, this test is a no-op (the provider name will be
    non-empty and error empty).

    The test bypasses the test-time shim by patching the
    ``vulturevision`` module to return a realistic cuDNN error.
    """
    import sys
    from backend import inference

    fake_module = type(sys)("vulturevision")
    fake_module._vulturevision = type(sys)("_vulturevision")

    def _probe():
        return {
            "provider": "",
            "error": "ONNX Runtime error: Failed to load library libonnxruntime_providers_cuda.so with error: libcudnn.so.9: cannot open shared object file: No such file or directory",
        }

    fake_module._vulturevision.get_available_gpu_provider = _probe
    with patch.dict(sys.modules, {"vulturevision": fake_module}):
        result = inference.get_gpu_provider()
        assert result["provider"] == ""
        assert "cudnn" in result["error"].lower()
        # The error must be informative — not just empty, not
        # just "no GPU" or "false".
        assert len(result["error"]) > 20


def test_get_gpu_provider_handles_empty_provider():
    """When the C++ probe returns provider="" with no error, the
    Python wrapper must surface a non-empty error so the Settings
    page shows the warning banner instead of the silent 'No GPU
    detected on this host' text."""
    import sys
    from backend import inference

    fake_module = type(sys)("vulturevision")
    fake_module._vulturevision = type(sys)("_vulturevision")

    class _FakeEngine:
        @staticmethod
        def get_available_gpu_provider():
            return {"provider": "", "error": ""}

    fake_module._vulturevision.get_available_gpu_provider = (
        _FakeEngine.get_available_gpu_provider
    )
    with patch.dict(sys.modules, {"vulturevision": fake_module}):
        result = inference.get_gpu_provider()
        assert isinstance(result, dict)
        assert result["provider"] == ""
        assert result["error"] != "", (
            "Empty provider with no error must surface a reason"
        )


def test_get_gpu_provider_handles_exceptions():
    """If the C++ probe raises (e.g. the .so is missing), the
    Python wrapper must catch it and return an error dict
    instead of propagating the exception to the Settings page."""
    import sys
    from backend import inference

    fake_module = type(sys)("vulturevision")
    fake_module._vulturevision = type(sys)("_vulturevision")

    def _explode():
        raise OSError("libcudnn.so.9: cannot open shared object")

    fake_module._vulturevision.get_available_gpu_provider = _explode
    with patch.dict(sys.modules, {"vulturevision": fake_module}):
        result = inference.get_gpu_provider()
        assert isinstance(result, dict)
        assert result["provider"] == ""
        # The error must mention what went wrong so the user
        # can act on it.
        assert "libcudnn" in result["error"] or "GPU probe failed" in result["error"]


def test_is_cuda_available_returns_bool():
    from backend import inference
    # Returns a bool, not a string or dict.
    result = inference.is_cuda_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# /api/settings/gpu-provider endpoint
# ---------------------------------------------------------------------------


def test_gpu_provider_endpoint_returns_error_field(client, auth_header):
    """The endpoint must include ``error`` in its response so the
    Settings page can show the user *why* GPU is missing."""
    from backend import inference
    with patch.object(inference, "_probe_gpu_provider_safely", return_value={
        "provider": "", "error": "some reason",
    }):
        response = client.get("/api/settings/gpu-provider", headers=auth_header)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "available" in data
        assert "provider" in data
        assert "error" in data
        assert isinstance(data["error"], str)


def test_gpu_provider_endpoint_when_unavailable(client, auth_header):
    """When GPU is unavailable, available=False and error is a
    non-empty string explaining why (not just empty)."""
    from backend import inference

    # Force the safe probe to fail with a known error
    with patch.object(inference, "_probe_gpu_provider_safely", return_value={
        "provider": "",
        "error": "ONNX Runtime error: libcudnn.so.9: cannot open shared object file",
    }):
        response = client.get("/api/settings/gpu-provider", headers=auth_header)
        data = response.json()
        assert data["available"] is False
        assert data["provider"] == ""
        assert "cudnn" in data["error"].lower()


def test_gpu_provider_endpoint_when_available(client, auth_header):
    """When GPU is available, available=True, provider is the
    canonical name, and error is empty."""
    from backend import inference

    with patch.object(inference, "_probe_gpu_provider_safely", return_value={
        "provider": "CUDAExecutionProvider",
        "error": "",
    }):
        response = client.get("/api/settings/gpu-provider", headers=auth_header)
        data = response.json()
        assert data["available"] is True
        assert data["provider"] == "CUDAExecutionProvider"
        assert data["error"] == ""
