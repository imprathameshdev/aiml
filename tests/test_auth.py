import pytest
from backend import auth

def test_generate_api_key():
    key = auth.generate_api_key()
    assert len(key) >= 32
    assert isinstance(key, str)

def test_hash_key():
    key = "test_key_123"
    h1 = auth.hash_key(key)
    h2 = auth.hash_key(key)
    assert h1 == h2
    assert h1 != key
    assert len(h1) == 64 # SHA-256 hex digest length

def test_hash_key_different():
    assert auth.hash_key("key1") != auth.hash_key("key2")
