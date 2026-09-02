import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

from backend import inference
from backend.billing.session import LocalSession
from backend.billing import enforcer


def test_inference_passes_empty_license_in_community_mode():
    """When no license is cached, get_vulturevision should be called
    with license_key='' and instance_id='' so the engine uses the
    bundled community model.

    Also verifies the safe-default behavior: when the
    ``inference_device`` setting row is missing (mocked as None here),
    the backend should default to ``use_gpu=False`` so hosts without a
    GPU don't log "GPU provider load failed" every 60s on the config
    poll.
    """
    # Reset cached state
    inference._vv_instance = None
    inference._vv_config = {"license": None, "instance_id": None, "use_gpu": None, "max_res": None, "last_check": 0}

    with patch.object(inference, "resolve_license_from_default_session", return_value=("", "")):
        with patch("backend.inference.VultureVision") as mock_vv:
            with patch("backend.database.SessionLocal") as mock_db:
                mock_db.return_value.query.return_value.filter_by.return_value.first.return_value = None
                vv = inference.get_vulturevision()

    args, kwargs = mock_vv.call_args
    assert kwargs["license_key"] == ""
    assert kwargs["instance_id"] == ""
    assert kwargs["use_gpu"] is False


def test_inference_passes_real_license_and_hwid_in_commercial_mode():
    """When a license is cached and active, get_vulturevision should
    be called with the license_token as license_key and the HWID as
    instance_id."""
    inference._vv_instance = None
    inference._vv_config = {"license": None, "instance_id": None, "use_gpu": None, "max_res": None, "last_check": 0}

    test_session = LocalSession()
    test_session.license_token = "real-token"
    test_session.license_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    test_session.trial_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    test_session.mode = "trial_commercial"
    test_session.quota = 5

    # Compute what the real call would produce
    lk, inst = enforcer.resolve_license(test_session)

    with patch.object(inference, "resolve_license_from_default_session", return_value=(lk, inst)):
        with patch("backend.inference.VultureVision") as mock_vv:
            with patch("backend.database.SessionLocal") as mock_db:
                mock_db.return_value.query.return_value.filter_by.return_value.first.return_value = None
                vv = inference.get_vulturevision()

    args, kwargs = mock_vv.call_args
    assert kwargs["license_key"] == "real-token"
    assert kwargs["instance_id"] == inst
    assert len(kwargs["instance_id"]) == 64


def test_invalidate_config_cache_resets_last_check():
    inference._vv_config["last_check"] = 9999
    inference.invalidate_config_cache()
    assert inference._vv_config["last_check"] == 0


def test_inference_reinit_when_license_changes():
    """If license_token or instance_id changes between polls,
    VultureVision must be re-instantiated."""
    inference._vv_instance = MagicMock()
    inference._vv_config = {"license": "old", "instance_id": "old-hwid", "use_gpu": True, "max_res": 1440, "last_check": 0}

    with patch.object(inference, "resolve_license_from_default_session", return_value=("new-license", "new-hwid")):
        with patch("backend.inference.VultureVision") as mock_vv:
            with patch("backend.database.SessionLocal") as mock_db:
                mock_db.return_value.query.return_value.filter_by.return_value.first.return_value = None
                inference.get_vulturevision()

    args, kwargs = mock_vv.call_args
    assert kwargs["license_key"] == "new-license"
    assert kwargs["instance_id"] == "new-hwid"


def test_gpu_commercial_only_demotion_in_community_mode():
    """Verify that if settings configure GPU ('cuda') but we are in
    community mode, get_vulturevision demotes use_gpu to False."""
    inference._vv_instance = None
    inference._vv_config = {"license": None, "instance_id": None, "use_gpu": None, "max_res": None, "last_check": 0}

    def mock_query_filter(key):
        mock_setting = MagicMock()
        if key == "inference_device":
            mock_setting.value = "cuda"
        elif key == "max_inference_resolution":
            mock_setting.value = "1024"
        else:
            mock_setting.value = None
        q = MagicMock()
        q.first.return_value = mock_setting
        return q

    test_session = LocalSession()
    test_session.mode = "community"

    with patch("backend.billing.session.default_session", return_value=test_session):
        with patch.object(inference, "resolve_license_from_default_session", return_value=("", "")):
            with patch("backend.inference.VultureVision") as mock_vv:
                with patch("backend.database.SessionLocal") as mock_db:
                    mock_db.return_value.query.return_value.filter_by.side_effect = mock_query_filter
                    with patch("backend.inference.logger.warning") as mock_warn:
                        inference.get_vulturevision()
                        mock_warn.assert_called_once_with(
                            "GPU processing is a commercial-only feature. Downgrading to CPU mode for community license."
                        )

    args, kwargs = mock_vv.call_args
    assert kwargs["use_gpu"] is False