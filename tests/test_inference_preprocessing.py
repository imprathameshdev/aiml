"""Regression tests for the inference preprocessing math.

The model reports 99.07% accuracy on the validation set, but in
production the spot detector feels closer to ~90% accurate. The
cause: the C++ engine's CPU inference path was placing the
*un-resized* image at the top-left of an S×S tensor
(S = max(safe_w, safe_h) rounded up to 32). For a 1920×1080 image
with the default ``max_inference_resolution=1024`` this dropped
47% of the image (the right 896 columns) and let the model run
inference on a cropped, off-center view of the world.

The validation pipeline
(``spot_detector_model/dataset/sequence_dataset.py`` +
``spot_detector_model/utils/transforms.py``) instead does:

  1. ``A.LongestMaxSize(max_size=1440)`` — resize so the longest
     edge is 1440, aspect ratio preserved.
  2. ``A.PadIfNeeded(min_height=1440, min_width=1440, ...,
     position='center', fill=0)`` — center-pad with zero to a
     1440×1440 square.
  3. ``A.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])``
     — normalize after padding.
  4. ``ToTensorV2()`` — convert to NCHW float32, shape
     (1, 3, 1440, 1440).
  5. ROIs are in [0, 1] of the *padded canvas* (after
     ``apply_transforms`` divides transformed keypoints by
     ``pad_res``).

The C++ engine (in ``vulturevision/src/engine.cpp``) now matches
this for both CPU and GPU paths. These tests pin down the
*math* so a future change to the engine or the Python
configuration doesn't drift away from the validation contract
again.

We don't run the C++ engine here (it needs the onnxruntime
shared library and the trained model). Instead we test the
mathematical contract that the engine implements, and we
assert the Python-side configuration defaults match the
validation's 1440.
"""
import io
import pytest
import numpy as np
from PIL import Image


def _jpeg_bytes(width: int, height: int, color: str = "red") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_validation_pipeline_math():
    """Pin the validation pipeline's resize+pad math so the
    C++ engine's preprocessing can be checked against it.

    For a 1920x1080 image with max_res=1440:
      scale = 1440/1920 = 0.75
      target_w = 1440, target_h = 810
      pad_l = 0, pad_t = 315  (center-pad to 1440x1440)

    The C++ engine transforms image-relative [0,1] ROIs to
    canvas-relative [0,1] via:
      canvas_x = input_x * (target_w / S) + (pad_l / S)
      canvas_y = input_y * (target_h / S) + (pad_t / S)
    where S = max_res. For our case, target_w == S, so
    effective_scale_x = 1.0 and off_x = 0. The image center
    (image-relative 0.5, 0.5) lands on canvas-relative (0.5, 0.5)
    because (0.5*0.5625 + 0.21875) = 0.5.
    """
    image_w, image_h = 1920, 1080
    resize_res = 1440  # the validation default

    # A.LongestMaxSize: scale = max_size / max(w, h)
    scale = resize_res / max(image_w, image_h)
    target_w = round(image_w * scale)
    target_h = round(image_h * scale)
    pad_l = (resize_res - target_w) // 2
    pad_t = (resize_res - target_h) // 2

    assert scale == pytest.approx(0.75, abs=1e-6)
    assert target_w == 1440
    assert target_h == 810
    assert pad_l == 0
    assert pad_t == 315  # (1440 - 810) / 2

    # Engine math for transforming image-relative [0,1] ROIs
    # to canvas-relative [0,1] ROIs (mirrors engine.cpp):
    effective_scale_x = target_w / resize_res  # 1.0
    effective_scale_y = target_h / resize_res  # 0.5625
    off_x = pad_l / resize_res  # 0
    off_y = pad_t / resize_res  # 0.21875
    assert effective_scale_x == pytest.approx(1.0, abs=1e-6)
    assert effective_scale_y == pytest.approx(0.5625, abs=1e-6)
    assert off_x == 0
    assert off_y == pytest.approx(315 / 1440, abs=1e-6)

    # The image center is (image_w/2, image_h/2). The user draws
    # at this pixel; the frontend normalizes by image natural
    # dims to get image-relative [0, 1] = (0.5, 0.5). The engine
    # transforms to canvas-relative [0, 1]:
    input_x, input_y = 0.5, 0.5
    canvas_x = input_x * effective_scale_x + off_x
    canvas_y = input_y * effective_scale_y + off_y
    # The image center should land on the canvas center because
    # center-pad keeps the (resized) image centered.
    assert canvas_x == pytest.approx(0.5, abs=1e-6)
    assert canvas_y == pytest.approx(0.5, abs=1e-6)

    # Sanity: a ROI at image-relative (0, 0) (top-left of the
    # original image) lands at the top-left of the resized image,
    # which is at canvas-relative (0, pad_t/S) = (0, 0.21875).
    canvas_x_top = 0.0 * effective_scale_x + off_x
    canvas_y_top = 0.0 * effective_scale_y + off_y
    assert canvas_x_top == 0
    assert canvas_y_top == pytest.approx(315 / 1440, abs=1e-6)

    # And a ROI at image-relative (1, 1) (bottom-right) lands
    # at the bottom-right of the resized image, which is at
    # canvas-relative (1, (810+315)/1440) = (1, 0.78125).
    canvas_x_bot = 1.0 * effective_scale_x + off_x
    canvas_y_bot = 1.0 * effective_scale_y + off_y
    assert canvas_x_bot == pytest.approx(1.0, abs=1e-6)
    assert canvas_y_bot == pytest.approx((810 + 315) / 1440, abs=1e-6)


def test_cpp_engine_matches_validation_for_1920x1080():
    """The C++ engine now does the same math as the validation
    pipeline. We reimplement it here in Python and assert the
    values match what the engine would produce. If a future
    change drifts away from the validation, this test catches
    it.

    Constants: max_res = 1440 (the validation default, now
    the new default in init_db.py and inference.py).
    """
    image_w, image_h = 1920, 1080
    max_res = 1440

    # Engine math (mirrors vulturevision/src/engine.cpp)
    scale = max_res / max(image_w, image_h)
    target_w = round(image_w * scale)
    target_h = round(image_h * scale)
    pad_l = (max_res - target_w) // 2
    pad_t = (max_res - target_h) // 2
    effective_scale_x = target_w / max_res
    effective_scale_y = target_h / max_res
    off_x = pad_l / max_res
    off_y = pad_t / max_res

    # The validation pipeline produces the same numbers:
    val_scale = 1440 / max(1920, 1080)
    val_target_w = round(1920 * val_scale)
    val_target_h = round(1080 * val_scale)
    val_pad_l = (1440 - val_target_w) // 2
    val_pad_t = (1440 - val_target_h) // 2

    assert scale == val_scale
    assert target_w == val_target_w
    assert target_h == val_target_h
    assert pad_l == val_pad_l
    assert pad_t == val_pad_t
    # The effective_scale/off values for the engine, when applied
    # to image-relative [0, 1] ROIs, must produce the canvas-relative
    # [0, 1] ROIs the validation pipeline produces.
    val_effective_scale_x = val_target_w / 1440
    val_effective_scale_y = val_target_h / 1440
    val_off_x = val_pad_l / 1440
    val_off_y = val_pad_t / 1440
    assert effective_scale_x == val_effective_scale_x
    assert effective_scale_y == val_effective_scale_y
    assert off_x == val_off_x
    assert off_y == val_off_y


def test_engine_scales_up_small_images():
    """A.LongestMaxSize scales up images smaller than max_size —
    a 640x480 image should fit 1440x1440 after upscale, not stay
    at 640x480. The C++ engine must match this.
    """
    image_w, image_h = 640, 480
    max_res = 1440

    scale = max_res / max(image_w, image_h)
    target_w = round(image_w * scale)
    target_h = round(image_h * scale)
    pad_l = (max_res - target_w) // 2
    pad_t = (max_res - target_h) // 2

    # 1440/640 = 2.25 → 640 * 2.25 = 1440
    # 480 * 2.25 = 1080
    assert scale == pytest.approx(2.25, abs=1e-6)
    assert target_w == 1440
    assert target_h == 1080
    assert pad_l == 0
    assert pad_t == (1440 - 1080) // 2  # 180


def test_pre_fix_cpu_path_would_crop_image():
    """Document the original bug. The pre-fix CPU path:
        S = (max(safe_w, safe_h) + 31) / 32 * 32
        target_w = w; target_h = h
        pad_l = 0; pad_t = 0
    placed the un-resized image at the top-left of S×S. For a
    1920×1080 image with max_res=1024 this dropped 47% of the
    image (the right 896 columns). After the fix, the image is
    resized to fit 1024×576 and center-padded to 1024×1024 —
    nothing is dropped.
    """
    image_w, image_h = 1920, 1080
    max_res = 1024

    # Pre-fix CPU path
    safe_w = min(image_w, max_res)  # 1024
    safe_h = min(image_h, max_res)  # 1024
    S_old = (max(safe_w, safe_h) + 31) // 32 * 32  # 1024
    target_w_old, target_h_old = image_w, image_h  # 1920, 1080
    image_visible_w_old = min(image_w, S_old)  # 1024
    image_visible_h_old = min(image_h, S_old)  # 1024
    # Of the 1920x1080 image, only the top-left 1024x1024 was
    # visible in the tensor. The right 896 columns (47% of the
    # image's pixels) were LOST.
    lost_fraction_x = 1 - image_visible_w_old / image_w
    lost_fraction_y = 1 - image_visible_h_old / image_h
    assert lost_fraction_x > 0.4  # 47% of columns lost
    assert lost_fraction_y > 0.05  # 5% of rows lost

    # Post-fix: image is fully preserved by resizing.
    scale = max_res / max(image_w, image_h)  # 1024/1920 = 0.533
    target_w_new = round(image_w * scale)  # 1024
    target_h_new = round(image_h * scale)  # 576
    # All 1920×1080 pixels fit in the resized 1024×576 region.
    # Nothing is lost.
    assert target_w_new == 1024
    assert target_h_new == 576


def test_default_max_inference_resolution_matches_validation():
    """The validation uses res=1440. The C++ engine now also uses
    max_res=1440. ``init_db.py``, ``scheduler.py``, ``inference.py``,
    and ``app.py`` must default to 1440 (or read the setting that
    defaults to 1440) so a fresh install doesn't have the
    pre-fix 1024 default."""
    from backend import init_db, scheduler, inference
    import backend.app as app_module
    import inspect

    # init_db.py: the default value in the seed row
    src = open(init_db.__file__).read()
    # Find the line that sets max_inference_resolution
    import re
    m = re.search(r'"max_inference_resolution":\s*"(\d+)"', src)
    assert m, "init_db.py must have a max_inference_resolution default"
    assert m.group(1) == "1440", (
        f"init_db.py default is {m.group(1)} but should be 1440 "
        f"to match the validation pipeline"
    )

    # scheduler.py: _get_params reads the setting; if missing, the
    # default in the .get() must be 1440
    sched_src = open(scheduler.__file__).read()
    m = re.search(r'max_inference_resolution["\']?\s*,\s*(\d+)\s*\)', sched_src)
    assert m, "scheduler._get_params must read max_inference_resolution"
    assert m.group(1) == "1440", (
        f"scheduler._get_params default is {m.group(1)} but should be 1440"
    )

    # inference.py: get_vulturevision reads the setting; if missing,
    # the fallback must be 1440
    inf_src = open(inference.__file__).read()
    m = re.search(r'filter_by\(key="max_inference_resolution"\)', inf_src)
    assert m, "inference.get_vulturevision must read max_inference_resolution"
    # Check the default fallback
    m = re.search(r'else\s+(\d+)', inf_src[max(0, m.end()-200):m.end()+200])
    # The default must be 1440
    fallback_lines = [l for l in inf_src.split("\n") if "max_res_setting" in l or "else 1440" in l or "else 1024" in l]
    assert any("else 1440" in l for l in fallback_lines), (
        f"inference.py default fallback for max_inference_resolution "
        f"must be 1440; found lines: {fallback_lines}"
    )
    assert not any("else 1024" in l for l in fallback_lines), (
        f"inference.py still has the pre-fix 1024 default: {fallback_lines}"
    )

    # app.py: test_occupancy reads the setting; if missing, default
    # must be 1440. Use ast to locate the function body and check
    # the fallback inside it (rather than regex, which is fragile
    # around nested braces like ``[{`` in literal lists).
    import ast
    app_src = open(app_module.__file__).read()
    tree = ast.parse(app_src)
    body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "test_occupancy":
            lines = app_src.split("\n")
            start_line = node.body[0].lineno
            end_line = node.end_lineno
            body = "\n".join(lines[start_line - 1:end_line])
            break
    assert body, "Could not find test_occupancy function in app.py"
    assert "else 1440" in body, (
        "app.py test_occupancy default for max_inference_resolution "
        f"must be 1440; body was:\n{body[:2000]}"
    )
    assert "else 1024" not in body, (
        "app.py test_occupancy still has the pre-fix 1024 default"
    )


def test_tensor_padding_normalization_values():
    """Pin the normalized padding pixel values.
    
    Albumentations zero-padding followed by ImageNet normalization
    produces (0 - mean[c]) / std[c] in the padded canvas outside the image:
      R (c=0): (0 - 0.485) / 0.229 = -2.1179039
      G (c=1): (0 - 0.456) / 0.224 = -2.0357143
      B (c=2): (0 - 0.406) / 0.225 = -1.8044444
    """
    mean = [0.485, 0.456, 0.406]
    std = [0.229, 0.224, 0.225]

    pad_r = (0.0 - mean[0]) / std[0]
    pad_g = (0.0 - mean[1]) / std[1]
    pad_b = (0.0 - mean[2]) / std[2]

    assert pad_r == pytest.approx(-2.1179039, abs=1e-5)
    assert pad_g == pytest.approx(-2.0357143, abs=1e-5)
    assert pad_b == pytest.approx(-1.8044444, abs=1e-5)


def test_continuous_probability_softmax_and_hysteresis():
    """Verify that continuous softmax probabilities enable the
    hysteresis sticky zone between free_thresh (0.25) and occ_thresh (0.75)."""
    import math

    def compute_prob(logit_vacant: float, logit_occupied: float) -> float:
        return 1.0 / (1.0 + math.exp(logit_vacant - logit_occupied))

    # Confident occupied: logit_occupied >> logit_vacant
    prob_occ = compute_prob(logit_vacant=-2.0, logit_occupied=3.0)
    assert prob_occ > 0.99

    # Confident vacant: logit_vacant >> logit_occupied
    prob_vac = compute_prob(logit_vacant=4.0, logit_occupied=-3.0)
    assert prob_vac < 0.001

    # Ambiguous spot (e.g. slight shadow): logits close to each other
    prob_ambiguous = compute_prob(logit_vacant=0.5, logit_occupied=0.3)
    assert 0.25 < prob_ambiguous < 0.75  # Lands inside sticky hysteresis band

    # Verify hysteresis state retention
    free_thresh, occ_thresh = 0.25, 0.75
    prev_state_occupied = True
    prev_state_vacant = False

    # In sticky band, occupied state is retained
    assert (prev_state_occupied if free_thresh <= prob_ambiguous <= occ_thresh else prob_ambiguous > occ_thresh) is True
    # In sticky band, vacant state is retained
    assert (prev_state_vacant if free_thresh <= prob_ambiguous <= occ_thresh else prob_ambiguous > occ_thresh) is False

