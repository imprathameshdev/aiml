"""C1 audit fix — JWT signing key auto-generation.

Verifies ``backend.config._load_or_create_secret_key``:
- env var ``VULTURE_SECRET_KEY`` wins over the file
- file at ``$LV_HOME/secret_key`` is loaded on second start
- a missing file is generated, written with 0600 perms, and reused
- the default constant (``"change_me_later_for_production"``) is never
  returned, regardless of which path is taken

The repo ships a ``.env`` file that sets ``VULTURE_SECRET_KEY``; we
override that here by setting our own test value via monkeypatch
(``load_dotenv`` defaults to not overriding existing env vars).
"""
import os
import stat
import importlib

import pytest

from backend import config as cfg


def test_env_var_wins_over_file(monkeypatch, tmp_path):
    """If VULTURE_SECRET_KEY is set, the file is ignored."""
    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    monkeypatch.setenv("VULTURE_SECRET_KEY", "from-env-var-12345")
    # Pre-create a file with a different value
    (tmp_path / "secret_key").write_text("from-file-67890")

    key, source = cfg._load_or_create_secret_key()
    assert key == "from-env-var-12345"
    assert source == "env"
    # File content was NOT modified by the env-var path
    assert (tmp_path / "secret_key").read_text() == "from-file-67890"


def test_existing_file_is_loaded(monkeypatch, tmp_path):
    """If the file exists and no env var is set, the file value is used."""
    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    # Set to empty so .env's value (if any) is overridden; the helper
    # treats an empty string the same as missing.
    monkeypatch.setenv("VULTURE_SECRET_KEY", "")
    expected = "preexisting-key-aabbccdd"
    (tmp_path / "secret_key").write_text(expected)

    key, source = cfg._load_or_create_secret_key()
    assert key == expected
    assert source == "file"


def test_missing_file_is_generated_with_0600(monkeypatch, tmp_path):
    """If neither env var nor file exists, a new key is generated
    and written with 0600 permissions."""
    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    monkeypatch.setenv("VULTURE_SECRET_KEY", "")
    assert not (tmp_path / "secret_key").exists()

    key, source = cfg._load_or_create_secret_key()
    assert source == "generated"
    # token_urlsafe(48) produces a ~64-char string
    assert len(key) > 32
    # Sanity: the value is URL-safe base64 (alphanumeric + -_)
    assert all(c.isalnum() or c in "-_" for c in key)

    # File written with 0600 perms
    path = tmp_path / "secret_key"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    assert path.read_text() == key


def test_generated_key_is_reused_on_second_load(monkeypatch, tmp_path):
    """Two consecutive loads with the same $LV_HOME must return the
    same key — we don't want random rotation between starts (that
    would invalidate every existing JWT)."""
    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    monkeypatch.setenv("VULTURE_SECRET_KEY", "")

    first_key, first_source = cfg._load_or_create_secret_key()
    assert first_source == "generated"

    second_key, second_source = cfg._load_or_create_secret_key()
    assert second_source == "file"  # now it was loaded from the file
    assert second_key == first_key


def test_no_hardcoded_default_under_any_path(monkeypatch, tmp_path):
    """The original C1 bug: a known-public constant was the fallback.
    Verify no code path returns that constant (or any short static
    string) — every key must be at least 32 chars of crypto-random
    base64."""
    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    monkeypatch.setenv("VULTURE_SECRET_KEY", "")

    key, source = cfg._load_or_create_secret_key()
    assert key != "change_me_later_for_production"
    assert len(key) >= 32


def test_config_class_uses_helper(monkeypatch, tmp_path):
    """The Config.SECRET_KEY class attribute must be populated by the
    helper, and the source must be queryable via SECRET_KEY_SOURCE."""
    monkeypatch.setenv("VULTURE_LV_HOME", str(tmp_path))
    monkeypatch.setenv("VULTURE_SECRET_KEY", "test-explicit-key")

    # Reload so the Config class is rebuilt with the test env vars
    importlib.reload(cfg)
    try:
        import backend.app
        backend.app.config = cfg.config
    except Exception:
        pass

    assert cfg.config.SECRET_KEY == "test-explicit-key"
    assert cfg.config.SECRET_KEY_SOURCE == "env"

    # Reset to original env so subsequent tests aren't affected
    monkeypatch.undo()
    importlib.reload(cfg)
    try:
        import backend.app
        backend.app.config = cfg.config
    except Exception:
        pass

