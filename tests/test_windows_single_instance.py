import sys
import pytest
from unittest.mock import MagicMock, patch
from backend._windows_env import enforce_single_instance


def test_enforce_single_instance_non_windows():
    """On non-Windows platforms, enforce_single_instance returns None and does not exit."""
    with patch("sys.platform", "linux"):
        result = enforce_single_instance()
        assert result is None


def test_enforce_single_instance_windows_first_instance():
    """When no other instance exists, creates the mutex and returns the handle."""
    mock_kernel32 = MagicMock()
    mock_kernel32.OpenMutexW.return_value = 0
    mock_kernel32.CreateMutexW.return_value = 12345  # valid handle
    mock_kernel32.GetLastError.return_value = 0

    mock_ctypes = MagicMock()
    mock_ctypes.windll.kernel32 = mock_kernel32

    with patch("sys.platform", "win32"), \
         patch.dict("sys.modules", {"ctypes": mock_ctypes, "ctypes.wintypes": MagicMock()}):
        result = enforce_single_instance(interactive=False)
        assert result == 12345


def test_enforce_single_instance_windows_duplicate_instance_exits():
    """When an instance already exists, notifies user and calls sys.exit(0)."""
    mock_kernel32 = MagicMock()
    mock_kernel32.OpenMutexW.return_value = 99999  # Existing mutex found!

    mock_ctypes = MagicMock()
    mock_ctypes.windll.kernel32 = mock_kernel32

    with patch("sys.platform", "win32"), \
         patch.dict("sys.modules", {"ctypes": mock_ctypes, "ctypes.wintypes": MagicMock()}), \
         pytest.raises(SystemExit) as exc_info:
        enforce_single_instance(interactive=False)

    assert exc_info.value.code == 0
    mock_kernel32.CloseHandle.assert_called_once_with(99999)
