import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.billing.client import (
    BillingAuthError,
    BillingClient,
    BillingDeviceNotFoundError,
    BillingError,
    BillingServerError,
)


class _MockResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.mark.asyncio
async def test_heartbeat_sends_camera_count_and_hwid():
    client = BillingClient(base_url="https://billing.example.com")
    body = {"camera_count": 4, "hwid": "abc"}

    mock_resp = _MockResp(200, {"mode": "commercial", "license_token": "tok", "license_expires_at": "2026-07-25T00:00:00+00:00"})
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value=body) as mock_hwid:
            with patch.object(client, "_url", return_value="https://billing.example.com/v1/heartbeat") as mock_url:
                result = await client.heartbeat(4)

    assert result["mode"] == "commercial"
    mock_hwid.assert_called_once()
    args, _ = mock_hwid.call_args
    assert args[0] == {"camera_count": 4}


@pytest.mark.asyncio
async def test_401_raises_billing_auth_error():
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(401, text="device not found")
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingAuthError):
                    await client.heartbeat(0)


@pytest.mark.asyncio
async def test_500_raises_billing_server_error():
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(500, text="boom")
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingServerError):
                    await client.heartbeat(0)


@pytest.mark.asyncio
async def test_no_base_url_raises_billing_error():
    """Empty base_url + empty env must raise BillingError before any HTTP attempt."""
    import os
    from backend import config as config_module

    # Force the config to report empty portal URL for this test only.
    saved = os.environ.pop("VULTURE_BILLING_PORTAL_URL", None)
    try:
        with patch.object(config_module.config, "BILLING_PORTAL_URL", ""):
            client = BillingClient(base_url="")
            with pytest.raises(BillingError):
                await client.heartbeat(0)
    finally:
        if saved is not None:
            os.environ["VULTURE_BILLING_PORTAL_URL"] = saved


@pytest.mark.asyncio
async def test_get_session_no_hwid_in_body():
    """The /session endpoint accepts hwid-in-body or no auth at all.
    We still send hwid to be safe."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(200, {"email": "x@y.com", "mode": "commercial"})
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={"hwid": "abc"}) as mock_hwid:
            with patch.object(client, "_url", return_value="https://billing.example.com/v1/session"):
                result = await client.get_session()
    assert result["email"] == "x@y.com"


def test_url_normalization():
    """All paths go through /api/v1/ — that's what nginx proxies to the
    cloud billing FastAPI backend."""
    client = BillingClient(base_url="https://billing.example.com/")
    assert client._url("heartbeat") == "https://billing.example.com/api/v1/heartbeat"
    assert client._url("/heartbeat") == "https://billing.example.com/api/v1/heartbeat"
    assert client._url("/v1/heartbeat") == "https://billing.example.com/api/v1/heartbeat"
    assert client._url("/api/v1/heartbeat") == "https://billing.example.com/api/v1/heartbeat"

    # Trailing slash on base URL is normalized
    client2 = BillingClient(base_url="https://billing.example.com")
    assert client2._url("/heartbeat") == "https://billing.example.com/api/v1/heartbeat"


@pytest.mark.asyncio
async def test_404_with_device_not_found_detail_raises_device_not_found():
    """When the portal returns 404 with a 'device not found' message,
    we MUST raise BillingDeviceNotFoundError — NOT BillingServerError.
    This is what tells the on-prem UI 'not yet activated' vs
    'portal down'."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(
        404,
        json_data={"detail": "Device not found. Activate first via the portal."},
    )
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingDeviceNotFoundError) as exc_info:
                    await client.heartbeat(0)
    assert "activate" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_404_with_other_detail_still_raises_server_error():
    """A 404 with a different detail (not 'Device not found') should be
    treated as a generic server error, not the activation-needed case."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(404, json_data={"detail": "Not Found"})
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingServerError):
                    await client.heartbeat(0)


@pytest.mark.asyncio
async def test_404_with_cloud_error_envelope_raises_device_not_found():
    """The cloud wraps every error in an envelope
    ``{"error": {"code": "not_found", "message": "Device not found...",
    "details": {}}}`` (see lotvulture_billing/app/errors.py) — NOT the
    FastAPI default ``{"detail": "..."}`` shape. The on-prem client must
    pull the message from ``error.message`` and treat any 404 with
    ``error.code == "not_found"`` (or a message containing both "device"
    and "not found") as the pre-activation state, not a server error.
    Otherwise the operator gets a confusing 'Billing portal unreachable'
    banner on a freshly-installed, unactivated system."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(
        404,
        json_data={
            "error": {
                "code": "not_found",
                "message": "Device not found. Activate first via /activate.",
                "details": {},
            }
        },
    )
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingDeviceNotFoundError) as exc_info:
                    await client.heartbeat(0)
    # The human-readable message from the cloud should propagate.
    assert "activate" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_404_with_cloud_envelope_but_not_device_raises_server_error():
    """A 404 with the cloud envelope but a different error code (e.g.
    some other resource missing) should NOT be treated as the
    activation-needed state."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(
        404,
        json_data={
            "error": {
                "code": "not_found",
                "message": "Subscription not found",
                "details": {},
            }
        },
    )
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingServerError):
                    await client.heartbeat(0)


@pytest.mark.asyncio
async def test_404_with_specific_device_not_activated_code():
    """The cloud's /v1/heartbeat returns the specific code
    ``device_not_activated`` for the unactivated case (vs. the
    generic ``not_found`` used for other 404s). Matching on the
    specific code is the primary signal — it survives message-text
    changes and localization."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(
        404,
        json_data={
            "error": {
                "code": "device_not_activated",
                "message": "Device not found. Activate first via /activate.",
                "details": {},
            }
        },
    )
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingDeviceNotFoundError) as exc_info:
                    await client.heartbeat(0)
    assert "activate" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_404_with_generic_code_but_device_message_still_activates():
    """Back-compat: if a future cloud build changes the code back to
    something generic (or a stale cloud returns the old envelope with
    ``not_found``), the message-substring fallback still recognizes
    the activation case. Belt-and-suspenders against a code revert."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(
        404,
        json_data={
            "error": {
                "code": "not_found",  # generic, NOT "device_not_activated"
                "message": "Device not found. Activate first via /activate.",
                "details": {},
            }
        },
    )
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingDeviceNotFoundError):
                    await client.heartbeat(0)


@pytest.mark.asyncio
async def test_connect_error_includes_portal_url():
    """When the billing portal is unreachable, the error message should
    mention the URL so the operator knows where to look."""
    import httpx
    client = BillingClient(base_url="https://billing.example.com")
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="https://billing.example.com/v1/heartbeat"):
                with pytest.raises(BillingServerError) as exc_info:
                    await client.heartbeat(0)
    msg = str(exc_info.value)
    assert "billing.example.com" in msg
    # The three except branches were collapsed (ConnectError and
    # TimeoutException are subclasses of HTTPError, so the prior
    # specific-clause messages like "Could not connect" / "timed out"
    # were dead code). The collapsed message is still informative
    # because httpx's exception text (e.g. "connection refused",
    # "read timeout") flows through ``str(exc)`` in the ``from exc``.
    assert "connection refused" in msg


@pytest.mark.asyncio
async def test_timeout_error_distinguished():
    import httpx
    client = BillingClient(base_url="https://billing.example.com")
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=httpx.TimeoutException("read timeout")
        )
        with patch.object(client, "_hwid_body", return_value={}):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingServerError) as exc_info:
                    await client.heartbeat(0)
    assert "read timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_forget_device_calls_correct_path():
    """forget_device() must POST to /api/v1/device/forget with the HWID."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(200, json_data={"status": "ok", "already_gone": False})
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_post = AsyncMock(return_value=mock_resp)
        mock_async.return_value.__aenter__.return_value.post = mock_post
        with patch("backend.billing.client.get_hwid", return_value="abc-hwid"):
            with patch.object(client, "_url", return_value="https://billing.example.com/api/v1/device/forget") as mock_url:
                result = await client.forget_device()
    assert result == {"status": "ok", "already_gone": False}
    mock_url.assert_called_once_with("/device/forget")
    # Verify POST was made with HWID in body
    call_args = mock_post.call_args
    assert call_args.kwargs["json"]["hwid"] == "abc-hwid"


@pytest.mark.asyncio
async def test_forget_device_idempotent_when_already_gone():
    """If billing returns 'already_gone: true', the client still
    resolves successfully (no exception)."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(200, json_data={"status": "ok", "already_gone": True})
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch("backend.billing.client.get_hwid", return_value="abc-hwid"):
            result = await client.forget_device()
    assert result["already_gone"] is True


@pytest.mark.asyncio
async def test_forget_device_404_raises_device_not_found():
    """A 404 'Device not found' on /device/forget means the device was
    already removed. The on-prem /logout route catches
    BillingDeviceNotFoundError and treats it as success, so this is the
    expected behavior."""
    client = BillingClient(base_url="https://billing.example.com")
    mock_resp = _MockResp(
        404,
        json_data={"detail": "Device not found. Activate first via /activate."},
    )
    with patch("backend.billing.client.httpx.AsyncClient") as mock_async:
        mock_async.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with patch("backend.billing.client.get_hwid", return_value="abc-hwid"):
            with patch.object(client, "_url", return_value="x"):
                with pytest.raises(BillingDeviceNotFoundError):
                    await client.forget_device()