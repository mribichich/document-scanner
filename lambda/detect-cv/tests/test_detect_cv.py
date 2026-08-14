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
