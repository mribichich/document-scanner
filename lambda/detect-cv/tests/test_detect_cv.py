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
