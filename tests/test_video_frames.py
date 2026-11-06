import numpy as np
import pytest

from backend.video_frames import copy_bgr_frame


@pytest.mark.parametrize("width", [1, 2, 3, 4, 641, 1920])
def test_bgr_frame_with_aligned_rows(width):
    height = 3
    stride = (width * 3 + 3) & ~3
    pixels = np.arange(height * width * 3, dtype=np.uint8).reshape(height, width, 3)
    raw = bytearray([255] * (stride * height))
    for row in range(height):
        raw[row * stride:row * stride + width * 3] = pixels[row].tobytes()
    frame = copy_bgr_frame(raw, width, height, stride)
    np.testing.assert_array_equal(frame, pixels)
    raw[:] = bytes(len(raw))
    np.testing.assert_array_equal(frame, pixels)  # survives Gst buffer unmap


def test_bgr_frame_with_plane_offset():
    frame = copy_bgr_frame(bytes([99, 99, 1, 2, 3, 0, 4, 5, 6, 0]), 1, 2, 4, 2)
    np.testing.assert_array_equal(frame, [[[1, 2, 3]], [[4, 5, 6]]])


def test_truncated_bgr_frame_is_rejected():
    with pytest.raises(ValueError):
        copy_bgr_frame(bytes(5), 2, 1, 8)
