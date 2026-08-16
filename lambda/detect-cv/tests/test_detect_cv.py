import cv2
import numpy as np

from detect_cv import find_checkbox_candidates


def make_blank_gray(width=300, height=300):
    return np.full((height, width), 255, dtype=np.uint8)


def test_finds_checkbox_sized_square():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)

    candidates = find_checkbox_candidates(gray)

    # A hollow rectangle's border legitimately yields both an outer and an
    # inner contour (cv2.RETR_TREE sees both); deduplicating them is Task 2's
    # job (IoU-based dedup), not this function's. Here we only check that a
    # checkbox-sized candidate is present among whatever this step returns.
    assert len(candidates) >= 1
    assert any(18 <= w <= 24 and 18 <= h <= 24 for (x, y, w, h) in candidates)


def test_rejects_too_small_square():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (55, 55), color=0, thickness=1)

    candidates = find_checkbox_candidates(gray)

    assert len(candidates) == 0


def test_rejects_too_large_square():
    gray = make_blank_gray(400, 400)
    cv2.rectangle(gray, (50, 50), (250, 250), color=0, thickness=2)

    candidates = find_checkbox_candidates(gray)

    assert len(candidates) == 0


def test_rejects_elongated_rectangle():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (110, 60), color=0, thickness=2)

    candidates = find_checkbox_candidates(gray)

    assert len(candidates) == 0


def test_rejects_circle():
    gray = make_blank_gray()
    cv2.circle(gray, (60, 60), radius=10, color=0, thickness=2)

    candidates = find_checkbox_candidates(gray)

    assert len(candidates) == 0


def test_rejects_low_rectangularity_filled_shape():
    # Regression test for fa8a27c (MIN_EXTENT_RATIO): a filled, non-square
    # shape can still land a checkbox-sized, roughly-square bounding box and
    # a 4-point approxPolyDP approximation (the way a text glyph like "v" or
    # "o" sometimes does at certain sizes) while covering far less of that
    # bounding box than an actual drawn checkbox border would. A filled
    # diamond is a convenient stand-in: its bounding box is a 41x41 square
    # (aspect ratio 1.0, well within MIN/MAX_BOX_SIZE) and it has exactly 4
    # vertices, so it clears every other filter — but its contour area is
    # only ~48% of the bounding-box area (a rectangle's is ~100%), below
    # MIN_EXTENT_RATIO (0.6), so it must still be rejected as a candidate.
    gray = make_blank_gray()
    diamond = np.array([[60, 40], [80, 60], [60, 80], [40, 60]], dtype=np.int32)
    cv2.fillPoly(gray, [diamond], color=0)

    candidates = find_checkbox_candidates(gray)

    assert len(candidates) == 0


from detect_cv import deduplicate_boxes


def test_drops_nested_duplicate_keeping_outer():
    outer = (50, 50, 20, 20)
    inner = (52, 52, 16, 16)

    result = deduplicate_boxes([outer, inner])

    assert result == [outer]


def test_keeps_non_overlapping_boxes():
    box_a = (10, 10, 20, 20)
    box_b = (100, 100, 20, 20)

    result = deduplicate_boxes([box_a, box_b])

    assert sorted(result) == sorted([box_a, box_b])


from detect_cv import is_checked


def test_unchecked_empty_box():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is False


def test_checked_box_with_contained_x_mark():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    cv2.line(gray, (53, 53), (67, 67), color=0, thickness=2)
    cv2.line(gray, (53, 67), (67, 53), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is True


def test_unchecked_when_external_line_passes_through():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    # A long line spanning far beyond the box on both sides, incidentally
    # crossing straight through its interior — must NOT be read as a check.
    cv2.line(gray, (0, 60), (299, 60), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is False


def test_checked_when_border_fused_with_gridline_and_mark_is_separate():
    # Regression test for 4176cf8: a checkbox's own drawn border can be
    # 8-connected to an adjacent ruled table gridline (flush against one
    # edge) without that gridline having anything to do with the mark
    # itself. Here the X mark sits well inside the interior and never
    # touches the border, so it's already its own separate connected
    # component — this is the scenario 4176cf8 was written to fix, pinned
    # here with an actual test (previously only eyeballed on sample PNGs).
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    cv2.line(gray, (0, 70), (299, 70), color=0, thickness=2)  # gridline flush with bottom border
    cv2.line(gray, (55, 55), (65, 65), color=0, thickness=2)  # X well inside, not touching border
    cv2.line(gray, (55, 65), (65, 55), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is True


def test_checked_when_mark_touches_border_that_is_also_fused_with_gridline():
    # Regression test for the finding-2 fix: a hand-drawn mark routinely
    # overshoots into the box's own drawn border (unlike the tidy,
    # margin-clear X mark above). When that border is *also* touching an
    # adjacent table gridline, mark + border + gridline become one large
    # connected component whose raw bounding-box extent is huge (it now
    # includes the far end of the gridline) — a prior version of
    # _mark_is_contained used that raw extent directly and incorrectly
    # rejected this as "external", even though the mark is genuinely inside
    # the box. Must still classify as checked.
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (74, 74), color=0, thickness=2)
    # X mark drawn corner-to-corner so its strokes touch the border itself.
    cv2.line(gray, (50, 50), (74, 74), color=0, thickness=2)
    cv2.line(gray, (50, 74), (74, 50), color=0, thickness=2)
    cv2.line(gray, (0, 74), (299, 74), color=0, thickness=2)  # gridline flush with the border

    assert is_checked(gray, (49, 49, 27, 27)) is True


import pytest

from detect_cv import detect_checkboxes


def test_detect_checkboxes_end_to_end():
    gray = make_blank_gray(300, 300)
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)  # unchecked, near top
    cv2.rectangle(gray, (50, 150), (70, 170), color=0, thickness=2)  # checked, below it
    cv2.line(gray, (53, 153), (67, 167), color=0, thickness=2)
    cv2.line(gray, (53, 167), (67, 153), color=0, thickness=2)

    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    success, encoded = cv2.imencode(".png", bgr)
    assert success
    image_bytes = encoded.tobytes()

    boxes = detect_checkboxes(image_bytes)

    assert len(boxes) == 2
    assert boxes[0]["bbox"][1] < boxes[1]["bbox"][1]
    assert boxes[0]["is_checked"] is False
    assert boxes[1]["is_checked"] is True


def test_detect_checkboxes_raises_on_undecodable_bytes():
    with pytest.raises(ValueError):
        detect_checkboxes(b"not an image")


def test_detect_checkboxes_raises_valueerror_on_empty_bytes():
    # Empty bytes take a different OpenCV code path than garbage-but-nonempty
    # bytes: cv2.imdecode raises a cv2.error (C++ assertion failure) here
    # rather than returning None. decode_image must normalize that to a
    # ValueError too, so the Lambda handler maps it to a clean HTTP 400
    # instead of leaking OpenCV internals through a 500.
    with pytest.raises(ValueError):
        detect_checkboxes(b"")


import detect_cv


def test_decode_image_rejects_oversized_raw_bytes():
    # A cheap, tight cap on the raw upload itself, applied before any
    # decode is attempted at all.
    oversized = b"x" * (detect_cv.MAX_INPUT_BYTES + 1)

    with pytest.raises(ValueError):
        detect_cv.decode_image(oversized)


def test_decode_image_rejects_oversized_implied_dimensions(monkeypatch):
    # A small-on-disk, highly-compressible file (e.g. a huge flat-color
    # PNG) can still decode to a bitmap large enough to OOM-kill the
    # Lambda. decode_image guards against this by peeking dimensions via a
    # cheap 8x-downscaled decode first. Exercise that guard without
    # actually allocating a giant bitmap in the test: fake the reduced
    # decode's result to imply a huge full-resolution image, and assert the
    # full-resolution decode is never even attempted.
    def fake_imdecode(arr, flag):
        if flag == cv2.IMREAD_REDUCED_COLOR_8:
            # Implies a 20000x8000px image once scaled back up by 8x —
            # 160,000,000 pixels, well over MAX_IMAGE_PIXELS — without
            # allocating anything close to that here.
            return np.zeros((1000, 2500, 3), dtype=np.uint8)
        raise AssertionError("full-resolution decode should never be attempted")

    monkeypatch.setattr(detect_cv.cv2, "imdecode", fake_imdecode)

    with pytest.raises(ValueError):
        detect_cv.decode_image(b"tiny-bytes-claiming-to-be-huge")
