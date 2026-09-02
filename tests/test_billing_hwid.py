import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend.billing.hwid import HWIDGenerator


def test_generate_combines_uuid_and_mac():
    with tempfile.TemporaryDirectory() as tmp:
        gen = HWIDGenerator(Path(tmp) / "hwid")
        with patch.object(gen, "_read_sys_file", return_value="abc-uuid"):
            with patch.object(gen, "_get_primary_mac", return_value="00:11:22:33:44:55"):
                hwid = gen.generate()
        assert hwid == __import__("hashlib").sha256(b"abc-uuid:00:11:22:33:44:55").hexdigest()


def test_generate_falls_back_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        gen = HWIDGenerator(Path(tmp) / "hwid")
        with patch.object(gen, "_read_sys_file", return_value=None):
            with patch.object(gen, "_get_primary_mac", return_value=None):
                hwid = gen.generate()
        assert len(hwid) == 64
        assert hwid == __import__("hashlib").sha256(b"dev-no-uuid:dev-no-mac").hexdigest()


def test_get_returns_cached_value():
    with tempfile.TemporaryDirectory() as tmp:
        gen = HWIDGenerator(Path(tmp) / "hwid")
        with patch.object(gen, "_read_sys_file", return_value="cached-uuid"):
            with patch.object(gen, "_get_primary_mac", return_value="ca:fe:ba:be:00:00"):
                first = gen.get()
        # second call without regenerate returns the same value, no IO
        with patch.object(gen, "_read_sys_file", return_value="different-uuid"):
            with patch.object(gen, "_get_primary_mac", return_value="00:00:00:00:00:00"):
                second = gen.get()
        assert first == second
        # and is the hash of the first generation's inputs
        import hashlib
        assert first == hashlib.sha256(b"cached-uuid:ca:fe:ba:be:00:00").hexdigest()


def test_get_persists_to_disk():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hwid"
        gen = HWIDGenerator(path)
        with patch.object(gen, "_read_sys_file", return_value="uuid-x"):
            with patch.object(gen, "_get_primary_mac", return_value="mac-x"):
                hwid = gen.get()
        assert path.exists()
        assert path.read_text().strip() == hwid


def test_force_regenerate_overrides_cache():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hwid"
        gen = HWIDGenerator(path)
        with patch.object(gen, "_read_sys_file", return_value="uuid-1"):
            with patch.object(gen, "_get_primary_mac", return_value="mac-1"):
                first = gen.get()
        with patch.object(gen, "_read_sys_file", return_value="uuid-2"):
            with patch.object(gen, "_get_primary_mac", return_value="mac-2"):
                second = gen.get(force_regenerate=True)
        assert first != second


def test_get_loads_from_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hwid"
        path.write_text("a" * 64)
        gen = HWIDGenerator(path)
        # Should NOT call generate() because the file exists
        with patch.object(gen, "generate") as mock_gen:
            result = gen.get()
        mock_gen.assert_not_called()
        assert result == "a" * 64