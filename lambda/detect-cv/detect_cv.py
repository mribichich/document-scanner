import cv2
import numpy as np

BINARY_THRESHOLD = 200
MIN_BOX_SIZE = 22
MAX_BOX_SIZE = 60
ASPECT_RATIO_MIN = 0.7
ASPECT_RATIO_MAX = 1.3
CORNER_EPSILON_FACTOR = 0.04
MIN_EXTENT_RATIO = 0.6


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
        # A real checkbox's contour (the outer or inner edge of a drawn
        # square) fills nearly all of its own bounding box. A text glyph
        # (e.g. "v", "o") can coincidentally have a square-ish, 4-corner
        # bounding box at certain sizes, but its actual ink covers far less
        # of that box — reject those low-rectangularity contours here.
        extent = cv2.contourArea(contour) / (w * h) if w * h else 0.0
        if extent < MIN_EXTENT_RATIO:
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


INK_RATIO_THRESHOLD = 0.08
INTERIOR_MARGIN = 3
CONTAINMENT_PADDING_FACTOR = 2.0
CONTAINMENT_EXTENT_RATIO = 1.75


def _ink_ratio(
    gray: np.ndarray, box: tuple[int, int, int, int], margin: int = INTERIOR_MARGIN
) -> float:
    x, y, w, h = box
    x1, y1 = x + margin, y + margin
    x2, y2 = x + w - margin, y + h - margin
    if x2 <= x1 or y2 <= y1:
        return 0.0

    interior = gray[y1:y2, x1:x2]
    _, binary = cv2.threshold(interior, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY_INV)
    dark_pixels = int(np.count_nonzero(binary))
    total_pixels = binary.size
    if total_pixels == 0:
        return 0.0
    return dark_pixels / total_pixels


def _mark_is_contained(gray: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    x, y, w, h = box
    pad_x = int(w * CONTAINMENT_PADDING_FACTOR)
    pad_y = int(h * CONTAINMENT_PADDING_FACTOR)

    region_x1 = max(0, x - pad_x)
    region_y1 = max(0, y - pad_y)
    region_x2 = min(gray.shape[1], x + w + pad_x)
    region_y2 = min(gray.shape[0], y + h + pad_y)

    region = gray[region_y1:region_y2, region_x1:region_x2]
    _, binary = cv2.threshold(region, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

    _, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

    # Sample components from the box's *interior* (inset by the same margin
    # used for ink-ratio measurement), not the full bounding box. The full
    # box includes the checkbox's own drawn border, which can be
    # 8-connected to neighboring structure it happens to touch or overlap
    # (e.g. a ruled table gridline the checkbox sits against) without that
    # structure being part of the mark itself. A component that only
    # reaches the box via its border/edge pixels is not "the mark"; a
    # component that reaches into the true interior is. This still catches
    # a stroke that genuinely passes through the box (it necessarily
    # crosses the interior, not just the edge).
    box_x1 = x - region_x1 + INTERIOR_MARGIN
    box_y1 = y - region_y1 + INTERIOR_MARGIN
    box_x2 = box_x1 + w - 2 * INTERIOR_MARGIN
    box_y2 = box_y1 + h - 2 * INTERIOR_MARGIN

    if box_x2 <= box_x1 or box_y2 <= box_y1:
        return False

    box_labels = set(np.unique(labels[box_y1:box_y2, box_x1:box_x2]))
    box_labels.discard(0)  # background label

    if not box_labels:
        return False

    for label in box_labels:
        comp_w = stats[label, cv2.CC_STAT_WIDTH]
        comp_h = stats[label, cv2.CC_STAT_HEIGHT]
        if comp_w > w * CONTAINMENT_EXTENT_RATIO or comp_h > h * CONTAINMENT_EXTENT_RATIO:
            return False

    return True


def is_checked(gray: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    if _ink_ratio(gray, box) < INK_RATIO_THRESHOLD:
        return False
    return _mark_is_contained(gray, box)


def decode_image(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not decode image")
    return image


def _sort_reading_order(
    boxes: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    return sorted(boxes, key=lambda b: (b[1], b[0]))


def detect_checkboxes(image_bytes: bytes) -> list[dict]:
    image = decode_image(image_bytes)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    candidates = find_checkbox_candidates(gray)
    candidates = deduplicate_boxes(candidates)
    candidates = _sort_reading_order(candidates)

    boxes = []
    for (x, y, w, h) in candidates:
        boxes.append(
            {
                "bbox": [x, y, x + w, y + h],
                "is_checked": is_checked(gray, (x, y, w, h)),
            }
        )
    return boxes
