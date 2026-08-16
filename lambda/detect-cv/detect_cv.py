import cv2
import numpy as np

BINARY_THRESHOLD = 200
MIN_BOX_SIZE = 22
MAX_BOX_SIZE = 60
ASPECT_RATIO_MIN = 0.7
ASPECT_RATIO_MAX = 1.3
CORNER_EPSILON_FACTOR = 0.04
MIN_EXTENT_RATIO = 0.6

# Defensive limits for decode_image, guarding against a decompression-bomb
# style upload on the public /detect endpoint: a small, highly-compressible
# file (e.g. a huge flat-color PNG) can still decode to a bitmap large
# enough to OOM-kill the Lambda. MAX_INPUT_BYTES is a cheap, tight cap on
# the raw upload itself, comfortably under API Gateway's 10 MB payload
# limit. MAX_IMAGE_PIXELS caps the implied full-resolution pixel count,
# checked via a cheap 8x-downscaled decode before ever attempting a full
# one (see decode_image).
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
REDUCED_DECODE_SCALE = 8


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
# Minimum fraction of a touching component's pixels that must fall inside
# the box's own outer bounds (excluding the border ring itself, which is
# ambiguous territory) for that component to still count as "the mark".
# See _mark_is_contained for why this replaced a raw bounding-box-extent
# check.
CONTAINMENT_INTERIOR_RATIO = 0.2


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

    _, labels, _, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

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

    # A component that reaches the interior isn't automatically "the mark":
    # if a hand-drawn mark overshoots into the box's own border, and that
    # border is *also* touching an adjacent table gridline, the mark +
    # border + gridline become one connected component whose raw bounding
    # box can be enormous (it now includes the far end of the gridline).
    # Judging containment by that raw extent (the previous approach)
    # rejects this case as "external", even though the mark itself is
    # genuinely inside the box.
    #
    # Instead, judge each touching component by where its pixels actually
    # are: pixels inside the box's own outer bounds vs. pixels genuinely
    # outside them. The box's drawn border ring itself (outer bounds minus
    # the interior inset) is excluded from both counts as ambiguous — it's
    # shared territory between "the box" and whatever it happens to touch.
    # A mark that's mostly inside the box keeps a high interior fraction
    # even when a border-touching sliver drags in an external gridline; a
    # true pass-through stroke (the original appraisal-2 bug this logic
    # was built for) has most of its pixels genuinely outside the box, so
    # its fraction stays low regardless of how much of it happens to
    # overlap the interior sample area.
    outer_x1, outer_y1 = x - region_x1, y - region_y1
    outer_x2, outer_y2 = outer_x1 + w, outer_y1 + h

    for label in box_labels:
        comp_mask = labels == label
        interior_count = int(np.count_nonzero(comp_mask[box_y1:box_y2, box_x1:box_x2]))

        outer_mask = np.zeros_like(comp_mask)
        oy1 = max(0, outer_y1)
        oy2 = min(comp_mask.shape[0], outer_y2)
        ox1 = max(0, outer_x1)
        ox2 = min(comp_mask.shape[1], outer_x2)
        outer_mask[oy1:oy2, ox1:ox2] = True
        exterior_count = int(np.count_nonzero(comp_mask & ~outer_mask))

        total = interior_count + exterior_count
        if total == 0:
            continue
        if interior_count / total < CONTAINMENT_INTERIOR_RATIO:
            return False

    return True


def is_checked(gray: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    if _ink_ratio(gray, box) < INK_RATIO_THRESHOLD:
        return False
    return _mark_is_contained(gray, box)


def decode_image(image_bytes: bytes) -> np.ndarray:
    if len(image_bytes) > MAX_INPUT_BYTES:
        raise ValueError("image too large")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)

    # Peek dimensions cheaply before committing to a full-resolution decode.
    # cv2.IMREAD_REDUCED_COLOR_8 decodes at 1/8 scale, which is far cheaper
    # than a full decode, so this catches a decompression-bomb-style input
    # (small on disk, huge once decoded) without ever allocating the full
    # bitmap. A None/error result here is not itself an error — it just
    # means the full decode below will fail the same way and report it.
    try:
        probe = cv2.imdecode(arr, cv2.IMREAD_REDUCED_COLOR_8)
    except cv2.error:
        probe = None
    if probe is not None:
        estimated_h = probe.shape[0] * REDUCED_DECODE_SCALE
        estimated_w = probe.shape[1] * REDUCED_DECODE_SCALE
        if estimated_h * estimated_w > MAX_IMAGE_PIXELS:
            raise ValueError("image too large")

    try:
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except cv2.error:
        # Some malformed inputs (notably empty bytes) make cv2.imdecode
        # raise a C++ assertion error instead of returning None. Normalize
        # that to a ValueError like the None case below, without leaking
        # OpenCV's internal exception detail (source file paths, assertion
        # text) into the response the caller sees.
        raise ValueError("could not decode image")
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
