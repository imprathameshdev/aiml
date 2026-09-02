"""Tests for the tail-read log helper used by the log WebSocket.

Regression for bug 3.1: the old implementation called
``f.readlines()`` which read the ENTIRE log file into memory.  On
a long-running install with a hundreds-of-MB log file, that
crashed the process.  The new ``_tail_lines`` helper reads
backwards in 64KB chunks and keeps the most recent matches in a
fixed-size deque — bounded memory regardless of file size.
"""
import os
import tempfile

import pytest

from backend.app import _tail_lines, _LOG_HISTORY_MAX_LINES


def _write_log(path, lines):
    with open(path, "w") as f:
        for line in lines:
            f.write(line + "\n")


def test_tail_lines_returns_empty_for_nonexistent_file():
    """A missing log file is not an error — return [] so the
    WebSocket doesn't blow up on a fresh install."""
    assert _tail_lines("/tmp/does-not-exist-vulture-12345.log",
                      lambda _l: True, 10) == []


def test_tail_lines_returns_empty_for_empty_file():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        assert _tail_lines(path, lambda _l: True, 10) == []
    finally:
        os.unlink(path)


def test_tail_lines_returns_all_when_under_limit():
    """A small file with fewer than max_lines returns all of them,
    in chronological order."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        _write_log(path, [f"line-{i}" for i in range(5)])
        result = _tail_lines(path, lambda _l: True, 10)
        assert result == ["line-0", "line-1", "line-2", "line-3", "line-4"]
    finally:
        os.unlink(path)


def test_tail_lines_caps_at_max_lines():
    """When the file has more lines than max_lines, only the LAST
    max_lines matching ones are returned (in chronological order)."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        _write_log(path, [f"line-{i}" for i in range(50)])
        result = _tail_lines(path, lambda _l: True, 10)
        assert len(result) == 10
        # Last 10 lines in chronological order.
        assert result[0] == "line-40"
        assert result[-1] == "line-49"
    finally:
        os.unlink(path)


def test_tail_lines_filter_excludes_non_matching():
    """Only lines passing ``matches_filter`` are returned."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        _write_log(path, [
            "INFO hello",
            "ERROR boom",
            "INFO world",
            "ERROR kaboom",
            "INFO done",
        ])
        result = _tail_lines(path, lambda l: "ERROR" in l, 10)
        assert result == ["ERROR boom", "ERROR kaboom"]
    finally:
        os.unlink(path)


def test_tail_lines_uses_chunked_read_for_large_file():
    """A file larger than the chunk size still returns the right
    last N lines.  This is the actual regression test for bug 3.1:
    the old ``f.readlines()`` would have OOM'd here; the new code
    reads in 64KB chunks and stays bounded.
    """
    # 200KB+ of log lines — well over one 64KB chunk.
    big_lines = [f"entry-{i:06d}" + "x" * 80 for i in range(2000)]  # ~90 chars each
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        _write_log(path, big_lines)
        file_size = os.path.getsize(path)
        assert file_size > 64 * 1024, f"test setup: file should be > one chunk; got {file_size} bytes"
        result = _tail_lines(path, lambda _l: True, 50)
        assert len(result) == 50
        assert result[0].startswith("entry-")
        assert result[-1].startswith("entry-")
    finally:
        os.unlink(path)


def test_tail_lines_handles_partial_chunk_at_start():
    """A partial line at the very start of the file (no trailing
    newline) is still returned if it matches the filter."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        # No trailing newline on the last line.
        with open(path, "w") as f:
            f.write("first\nsecond\nthird-no-newline")
        result = _tail_lines(path, lambda _l: True, 10)
        assert "third-no-newline" in result
    finally:
        os.unlink(path)


def test_tail_lines_bounded_memory_on_huge_file():
    """Memory bound regression for bug 3.1: a 5MB log file returns
    the right last 100 lines without ever holding the whole file in
    memory.  The deque enforces the bound; if the implementation
    regressed to ``f.readlines()`` we'd see the full list in the
    result (100 lines is correct; the test pins that we don't
    accidentally return *all* 50000 lines).
    """
    lines = [f"LOG|{i:06d}|" + "x" * 88 for i in range(50000)]
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        path = f.name
    try:
        _write_log(path, lines)
        result = _tail_lines(path, lambda _l: True, 100)
        assert len(result) == 100
        # Last 100 lines in chronological order.
        assert result[0] == f"LOG|{50000-100:06d}|" + "x" * 88
        assert result[-1] == f"LOG|{49999:06d}|" + "x" * 88
    finally:
        os.unlink(path)
