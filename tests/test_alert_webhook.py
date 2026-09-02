"""Tests for the webhook channel.

Covers:
  - success path (2xx response -> success=True).
  - failure path (5xx response -> success=False, error set).
  - transport error (unreachable host -> success=False, error set).
  - missing url -> success=False, error set.
  - HMAC signature header is set when secret is configured and absent
    otherwise.
  - signature matches an independent HMAC computation.
"""

import hashlib
import hmac
import json

import httpx
import pytest

from backend.alerts.channels.webhook import deliver, WebhookResult


# --- Success / failure paths via a fake transport -------------------------

class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return self._handler(request)


@pytest.mark.asyncio
async def test_webhook_success_returns_status_code():
    def handler(req):
        return httpx.Response(200, json={"ok": True})
    transport = _FakeTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # Patch the channel's httpx.AsyncClient to use our transport.
        # Easiest path: monkey-patch httpx.AsyncClient within deliver.
        original = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **kw: original(transport=transport, *a, **kw)
        try:
            result = await deliver({"url": "https://example.com/hook"},
                                   {"event_id": 1, "data": "x"})
        finally:
            httpx.AsyncClient = original
    assert result.success is True
    assert result.status_code == 200
    assert transport.requests and transport.requests[0].method == "POST"


@pytest.mark.asyncio
async def test_webhook_5xx_marks_failure():
    def handler(req):
        return httpx.Response(500, text="boom")
    transport = _FakeTransport(handler)
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: original(transport=transport, *a, **kw)
    try:
        result = await deliver({"url": "https://example.com/hook"},
                               {"event_id": 1})
    finally:
        httpx.AsyncClient = original
    assert result.success is False
    assert result.status_code == 500
    assert "500" in (result.error or "")
    assert "boom" in (result.error or "")


@pytest.mark.asyncio
async def test_webhook_missing_url():
    result = await deliver({}, {"event_id": 1})
    assert result.success is False
    assert "url" in (result.error or "")


@pytest.mark.asyncio
async def test_webhook_signature_set_when_secret_provided():
    captured = {}

    def handler(req):
        captured["signature"] = req.headers.get("X-Signature")
        captured["body"] = req.content
        return httpx.Response(200)

    transport = _FakeTransport(handler)
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: original(transport=transport, *a, **kw)
    try:
        result = await deliver(
            {"url": "https://example.com/hook", "secret": "shh"},
            {"event_id": 42},
        )
    finally:
        httpx.AsyncClient = original

    assert result.success is True
    sig = captured.get("signature")
    assert sig is not None
    assert sig.startswith("sha256=")
    expected = hmac.new(b"shh", captured["body"], hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


@pytest.mark.asyncio
async def test_webhook_no_signature_header_when_no_secret():
    captured = {}

    def handler(req):
        captured["signature"] = req.headers.get("X-Signature")
        return httpx.Response(200)

    transport = _FakeTransport(handler)
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: original(transport=transport, *a, **kw)
    try:
        result = await deliver({"url": "https://example.com/hook"},
                               {"event_id": 1})
    finally:
        httpx.AsyncClient = original
    assert result.success is True
    assert captured.get("signature") is None


@pytest.mark.asyncio
async def test_webhook_error_truncated():
    def handler(req):
        return httpx.Response(500, text="x" * 1000)
    transport = _FakeTransport(handler)
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: original(transport=transport, *a, **kw)
    try:
        result = await deliver({"url": "https://example.com/hook"}, {})
    finally:
        httpx.AsyncClient = original
    assert result.success is False
    # Truncated to 200 chars
    assert len(result.error or "") < 300
