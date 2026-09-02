"""H2 audit fix — YouTube ``fdsrc`` reads the correct file descriptor.

The previous code hardcoded ``fdsrc fd=0`` in the GStreamer
pipeline string, but the yt-dlp subprocess's stdout lives on a
different file descriptor (subprocess creates a new pipe).  The
GStreamer pipeline was reading from Python's stdin, not from
yt-dlp's output, so yt-dlp blocked when its stdout buffer
filled and no frames ever reached the appsink.

The fix passes ``proc.stdout.fileno()`` to ``fdsrc fd={fd}``
and additionally sets the ``fd`` property on the fdsrc element
after the pipeline is built.

This test mocks subprocess.Popen + GStreamer so we can verify the
pipeline string contains the right fd and that the fdsrc element
is configured with the same fd.
"""
from unittest.mock import patch, MagicMock


def test_youtube_pipeline_uses_proc_stdout_fd():
    """The pipeline string must contain ``fdsrc fd=<proc.stdout fd>``,
    not ``fdsrc fd=0`` (Python's stdin)."""
    from backend import stream_manager
    from backend.stream_manager import StreamManager

    sm = StreamManager.__new__(StreamManager)  # bypass __init__
    sm.streams = {}

    fake_proc = MagicMock()
    fake_proc.stdout.fileno.return_value = 42
    fake_pipeline = MagicMock()
    fake_fdsrc = MagicMock()
    fake_pipeline.get_by_name.return_value = fake_fdsrc

    captured_pipeline_str = []

    def fake_parse_launch(s):
        captured_pipeline_str.append(s)
        return fake_pipeline

    with patch("subprocess.Popen", return_value=fake_proc) as mock_popen, \
         patch.object(stream_manager.Gst, "parse_launch", side_effect=fake_parse_launch):
        pipeline, proc = sm._get_youtube_pipeline(
            camera_id=7, url="https://youtube.com/watch?v=abc",
            preferred_res="720", fps=5,
        )

    # The Popen call should have stdout=PIPE
    popen_kwargs = mock_popen.call_args.kwargs
    assert popen_kwargs.get("stdout") == -1 or "stdout" in popen_kwargs  # subprocess.PIPE
    # The pipeline string should reference fd 42 (proc.stdout.fileno()),
    # NOT fd 0 (Python's stdin).
    assert len(captured_pipeline_str) == 1
    pipeline_str = captured_pipeline_str[0]
    assert "fdsrc fd=42" in pipeline_str, f"Expected 'fdsrc fd=42' in pipeline, got: {pipeline_str}"
    assert "fdsrc fd=0" not in pipeline_str, "H2 regression: pipeline reads from fd 0 (Python's stdin)"

    # The fdsrc element's fd property must also be set explicitly
    fake_fdsrc.set_property.assert_called_with("fd", 42)
    assert pipeline is fake_pipeline
    assert proc is fake_proc


def test_youtube_pipeline_returns_none_on_popen_failure():
    """If Popen fails, return (None, None) so the caller can handle it."""
    from backend import stream_manager
    from backend.stream_manager import StreamManager

    sm = StreamManager.__new__(StreamManager)
    sm.streams = {}

    with patch("subprocess.Popen", side_effect=OSError("yt-dlp not found")):
        pipeline, proc = sm._get_youtube_pipeline(
            camera_id=1, url="https://youtube.com/watch?v=xyz",
        )

    assert pipeline is None
    assert proc is None


def test_youtube_pipeline_handles_missing_fdsrc_element():
    """If Gst.parse_launch doesn't produce an fdsrc0 element (some
    GStreamer versions name it differently), the explicit
    set_property call is silently skipped — the pipeline string
    still has the correct fd, so the stream still works."""
    from backend import stream_manager
    from backend.stream_manager import StreamManager

    sm = StreamManager.__new__(StreamManager)
    sm.streams = {}

    fake_proc = MagicMock()
    fake_proc.stdout.fileno.return_value = 99
    fake_pipeline = MagicMock()
    fake_pipeline.get_by_name.return_value = None  # no fdsrc0

    captured = []

    def fake_parse_launch(s):
        captured.append(s)
        return fake_pipeline

    with patch("subprocess.Popen", return_value=fake_proc), \
         patch.object(stream_manager.Gst, "parse_launch", side_effect=fake_parse_launch):
        pipeline, proc = sm._get_youtube_pipeline(camera_id=1, url="x")

    assert pipeline is fake_pipeline
    assert proc is fake_proc
    # The pipeline string still has the correct fd even when fdsrc
    # lookup fails — the explicit set_property would have been
    # the belt-and-suspenders; the parse-time fd is the primary
    # mechanism.
    assert len(captured) == 1
    assert "fdsrc fd=99" in captured[0]
