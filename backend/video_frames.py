"""Copy packed BGR frames while respecting GStreamer's row alignment."""

import numpy as np


def copy_bgr_frame(data, width, height, stride, offset=0):
    """Return owned pixels, excluding padding, before the buffer is unmapped."""
    if width <= 0 or height <= 0 or stride < width * 3 or offset < 0:
        raise ValueError("Invalid BGR frame layout")
    return np.ndarray(
        shape=(height, width, 3), dtype=np.uint8, buffer=data,
        offset=offset, strides=(stride, 3, 1),
    ).copy()
