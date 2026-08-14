import cv2
import numpy as np

BINARY_THRESHOLD = 200
MIN_BOX_SIZE = 10
MAX_BOX_SIZE = 60
ASPECT_RATIO_MIN = 0.7
ASPECT_RATIO_MAX = 1.3
CORNER_EPSILON_FACTOR = 0.04


def find_checkbox_candidates(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    _, binary = cv2.threshold(gray, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (MIN_BOX_SIZE <= w <= MAX_BOX_SIZE and MIN_BOX_SIZE <= h <= MAX_BOX_SIZE):
            continue
        aspect_ratio = w / h
        if not (ASPECT_RATIO_MIN <= aspect_ratio <= ASPECT_RATIO_MAX):
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, CORNER_EPSILON_FACTOR * perimeter, True)
        if len(approx) != 4:
            continue
        candidates.append((x, y, w, h))
    return candidates


IOU_DEDUP_THRESHOLD = 0.5


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, aw, ah = a
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx1, by1, bw, bh = b
    bx2, by2 = bx1 + bw, by1 + bh

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = aw * ah
    area_b = bw * bh
    union_area = area_a + area_b - inter_area

    if union_area == 0:
        return 0.0
    return inter_area / union_area


def deduplicate_boxes(
    boxes: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    boxes_by_area = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []

    for box in boxes_by_area:
        if any(_iou(box, existing) > IOU_DEDUP_THRESHOLD for existing in kept):
            continue
        kept.append(box)

    return kept
