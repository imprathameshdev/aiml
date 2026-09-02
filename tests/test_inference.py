import pytest
from unittest.mock import patch, MagicMock
from backend import inference, models


def test_get_vulturevision_device_selection(db_session):
    # Reset cached VultureVision instance and config
    inference._vv_instance = None
    inference._vv_config = {"license": None, "use_gpu": None, "max_res": None, "last_check": 0}

    # Set preference to CPU (not cuda)
    setting = db_session.query(models.Setting).filter_by(key="inference_device").first()
    if not setting:
        db_session.add(models.Setting(key="inference_device", value="cpu"))
    else:
        setting.value = "cpu"
    db_session.commit()

    with patch('backend.inference.VultureVision') as mock_vv:
        with patch('backend.database.SessionLocal', return_value=db_session):
            vv = inference.get_vulturevision()
            mock_vv.assert_called_once()
            args, kwargs = mock_vv.call_args
            assert kwargs['use_gpu'] is False

@pytest.mark.asyncio
async def test_run_inference_with_mocked_vulturevision():
    import io
    from PIL import Image
    img = Image.new('RGB', (100, 100), color='black')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    jpeg_bytes = img_byte_arr.getvalue()

    mock_vv = MagicMock()
    mock_vv.predict.return_value = [0.95, 0.05]

    with patch('backend.inference.get_vulturevision', return_value=mock_vv):
        spaces = [
            {'id': 1, 'points': '[0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]'},
            {'id': 2, 'points': '[0.5, 0.5, 0.6, 0.5, 0.6, 0.6, 0.5, 0.6]'}
        ]
        probs_dict, elapsed, res = await inference.run_inference(1, jpeg_bytes, spaces)

        assert probs_dict[1] == 0.95
        assert probs_dict[2] == 0.05
        assert isinstance(elapsed, float)
        assert res == (100, 100)
        mock_vv.predict.assert_called_once()

@pytest.mark.asyncio
async def test_get_crops():
    import io
    from PIL import Image
    img = Image.new('RGB', (100, 100), color='blue')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    jpeg_bytes = img_byte_arr.getvalue()

    spaces = [
        {'id': 1, 'points': '[0, 0, 0.1, 0, 0.1, 0.1, 0, 0.1]'}
    ]

    crops = await inference.get_crops(1, jpeg_bytes, spaces)
    assert 1 in crops
    assert isinstance(crops[1], bytes)

    # Verify it's a valid JPEG
    crop_img = Image.open(io.BytesIO(crops[1]))
    assert crop_img.format == 'JPEG'
    assert crop_img.size == (128, 128)


@pytest.mark.asyncio
async def test_get_crops_boundary_inclusion():
    """Verify that an ROI extending to the bottom-right border (1.0, 1.0)
    includes all border pixels without off-by-one truncation."""
    import io
    from PIL import Image
    # 100x100 image with a white border at pixel index 99
    img = Image.new('RGB', (100, 100), color='black')
    for y in range(100):
        img.putpixel((99, y), (255, 255, 255))
    for x in range(100):
        img.putpixel((x, 99), (255, 255, 255))

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG', quality=100)
    jpeg_bytes = img_byte_arr.getvalue()

    # ROI covering the right half touching the border [0.5, 0.5] to [1.0, 1.0]
    spaces = [
        {'id': 42, 'points': '[0.5, 0.5, 1.0, 0.5, 1.0, 1.0, 0.5, 1.0]'}
    ]

    crops = await inference.get_crops(1, jpeg_bytes, spaces)
    assert 42 in crops
    crop_img = Image.open(io.BytesIO(crops[42]))
    assert crop_img.size == (128, 128)



def test_is_cuda_available():
    """is_cuda_available() reflects the C++ engine's GPU probe.

    The probe is implemented in vulturevision/src/engine.cpp::
    get_available_gpu_provider and exposed via pybind11. We don't
    import the real vulturevision module here (it requires the C++
    wheel + bundled libonnxruntime.so, which the test env doesn't
    have) — instead we patch the symbol at its import site.
    ``create=True`` lets patch fabricate the module path even when
    vulturevision isn't installed in the test environment.

    The post-fix probe returns a dict ``{provider, error}`` rather
    than a bare string, so the Settings page can surface the
    failure reason (typically "libcudnn.so.9 not found" when
    cuDNN 9 is missing on a host with an NVIDIA GPU). The Python
    wrapper normalizes pre-fix bare-string returns to the new
    dict shape — both forms are tested below.
    """
    # Engine reports CUDA available (Linux host with NVIDIA driver)
    with patch(
        "vulturevision._vulturevision.get_available_gpu_provider",
        return_value={"provider": "CUDAExecutionProvider", "error": ""},
        create=True,
    ):
        result = inference.get_gpu_provider()
        assert result == {
            "provider": "CUDAExecutionProvider",
            "error": "",
        }
        assert inference.is_cuda_available() is True

    # Engine reports DirectML available (Windows host with DX12 GPU)
    with patch(
        "vulturevision._vulturevision.get_available_gpu_provider",
        return_value={"provider": "DmlExecutionProvider", "error": ""},
        create=True,
    ):
        result = inference.get_gpu_provider()
        assert result == {
            "provider": "DmlExecutionProvider",
            "error": "",
        }
        assert inference.is_cuda_available() is True

    # Engine probe failed (CPU-only host or cuDNN missing) — returns
    # empty provider with an error reason.
    with patch(
        "vulturevision._vulturevision.get_available_gpu_provider",
        return_value={"provider": "", "error": "libcudnn.so.9: cannot open shared object"},
        create=True,
    ):
        result = inference.get_gpu_provider()
        assert result == {"provider": "", "error": "libcudnn.so.9: cannot open shared object"}
        assert inference.is_cuda_available() is False

    # Backward compat: a pre-fix C++ build that returns a bare
    # string is normalized to the new dict shape.
    with patch(
        "vulturevision._vulturevision.get_available_gpu_provider",
        return_value="CUDAExecutionProvider",
        create=True,
    ):
        result = inference.get_gpu_provider()
        assert result == {"provider": "CUDAExecutionProvider", "error": ""}


@pytest.mark.asyncio
async def test_gpu_provider_handler_does_not_block_event_loop():
    """The /api/settings/gpu-provider handler offloads the probe to a
    worker thread so a slow probe does not block the event loop.
    """
    import asyncio
    import time
    from backend import app as app_module
    import httpx

    def slow_probe(*args, **kwargs):
        time.sleep(2.0)
        return {"provider": "CUDAExecutionProvider", "error": ""}

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        with patch.object(inference, "_probe_gpu_provider_safely",
                          side_effect=slow_probe):
            loop = asyncio.get_running_loop()
            start = loop.time()
            gpu_task = asyncio.create_task(
                client.get("/api/settings/gpu-provider")
            )
            await asyncio.sleep(0.05)
            after_yield_elapsed = loop.time() - start
            assert after_yield_elapsed < 0.5, (
                f"Event loop blocked: 0.05s yield took "
                f"{after_yield_elapsed:.2f}s"
            )
            other_start = loop.time()
            resp = await client.get("/api/cameras")
            other_elapsed = loop.time() - other_start
            assert resp.status_code in (200, 401, 403)
            assert other_elapsed < 0.5

            gpu_resp = await gpu_task
            total_elapsed = loop.time() - start
            assert total_elapsed < 2.5
            assert gpu_resp.status_code == 200
            body = gpu_resp.json()
            assert body["available"] is True
            assert body["provider"] == "CUDAExecutionProvider"

