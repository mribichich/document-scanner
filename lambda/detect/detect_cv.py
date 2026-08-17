import logging
import math

import cv2
import numpy as np

import textract_hints

MIN_BOX_SIZE = 22
MAX_BOX_SIZE = 60
ASPECT_RATIO_MIN = 0.7
ASPECT_RATIO_MAX = 1.3
CORNER_EPSILON_FACTOR = 0.04
MIN_EXTENT_RATIO = 0.6

# A single fixed global brightness cutoff (the previous approach: any pixel
# darker than a flat value is "ink," everywhere on the page) breaks in two
# opposite ways real scanned forms exhibit: a lightly-drawn border can be
# lighter than the cutoff (never registers as ink at all), and a shaded
# table-row background can be darker than the cutoff (the whole cell reads
# as ink and fuses with the border into one oversized blob). Adaptive
# thresholding compares each pixel to the mean of its own local
# neighborhood instead of one global value, so it tracks a lighter border
# against white paper and a normal-darkness border against a shaded cell
# equally, without needing a value tuned to either. Verified empirically
# against all 4 real samples: adaptive thresholding alone is a strict
# superset of the old global threshold's candidates (zero candidates the
# global pass found that adaptive misses, across appraisal-1/2/3/4), while
# also recovering 18 checkboxes on shaded rows (appraisal-3) and 2 more
# fusing with adjacent text (appraisal-4) that the global threshold missed
# entirely. See docs/algorithm-known-issues.md issues #1/#2/#6.
ADAPTIVE_BLOCK_SIZE = 51
ADAPTIVE_C = 10


def _adaptive_binary(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        ADAPTIVE_BLOCK_SIZE, ADAPTIVE_C,
    )


# Defensive limits for decode_image, guarding against a decompression-bomb
# style upload on the public /detect endpoint: a small, highly-compressible
# file (e.g. a huge flat-color PNG) can still decode to a bitmap large
# enough to OOM-kill the Lambda. MAX_INPUT_BYTES is a cheap, tight cap on
# the raw upload itself, comfortably under API Gateway's 10 MB payload
# limit. MAX_IMAGE_PIXELS caps the implied full-resolution pixel count,
# checked via a zero-decode header parse (see _probe_dimensions) before
# ever calling into OpenCV.
#
# An earlier version of this guard used cv2.imdecode(..., IMREAD_REDUCED_COLOR_8)
# as a "cheap" downscaled probe. That's cheap for JPEG (the decoder can
# skip DCT coefficients), but NOT for PNG: OpenCV's PNG codec fully decodes
# to full resolution internally and only downscales afterward, so the
# "cheap" probe itself OOM'd on a large-enough PNG (confirmed live:
# Runtime.OutOfMemory, 1022/1024 MB, on a PNG whose implied full resolution
# was ~20000x20000). Parsing width/height directly from the file header
# never decodes any pixel data, so it can't OOM regardless of format.
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000


def _probe_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    """Read (width, height) straight from a PNG/JPEG header, no decode.

    Returns None if the format isn't recognized or the header is
    malformed/truncated — callers should fall back to the byte-size cap
    as the only guard in that case, not attempt a decode-based probe.
    """
    # PNG: 8-byte signature, then an IHDR chunk (length, "IHDR", width,
    # height, ...) — width/height are big-endian uint32 at a fixed offset.
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        if len(image_bytes) < 24 or image_bytes[12:16] != b"IHDR":
            return None
        width = int.from_bytes(image_bytes[16:20], "big")
        height = int.from_bytes(image_bytes[20:24], "big")
        return width, height

    # JPEG: scan markers for a Start-Of-Frame segment (SOF0-SOF15, except
    # the DHT/JPG-extension marker numbers), which stores height then
    # width as big-endian uint16, 5 bytes into the segment payload.
    if image_bytes[:2] == b"\xff\xd8":
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        pos = 2
        n = len(image_bytes)
        while pos < n:
            if image_bytes[pos] != 0xFF:
                pos += 1
                continue
            # A marker's 0xFF prefix may be followed by any number of
            # extra 0xFF fill bytes before the actual marker code — legal
            # per the JPEG spec (T.81 SS B.1.1.5), and real decoders skip
            # them. Not skipping them here lets a single inserted fill
            # byte silently disable this guard while the real decoder
            # proceeds normally, reopening the vulnerability this
            # function exists to close.
            mpos = pos + 1
            while mpos < n and image_bytes[mpos] == 0xFF:
                mpos += 1
            if mpos >= n:
                break
            marker = image_bytes[mpos]
            if marker in (0xD8, 0xD9):  # SOI/EOI, no payload
                pos = mpos + 1
                continue
            if mpos + 3 > n:
                break
            seg_len = int.from_bytes(image_bytes[mpos + 1:mpos + 3], "big")
            if marker in sof_markers:
                if mpos + 8 > n:
                    return None
                height = int.from_bytes(image_bytes[mpos + 4:mpos + 6], "big")
                width = int.from_bytes(image_bytes[mpos + 6:mpos + 8], "big")
                return width, height
            pos = mpos + 1 + seg_len
        return None

    return None


# A 4-corner polygon (the corner-count filter above) admits any
# quadrilateral, including a visibly skewed one — a hand-drawn shape (e.g.
# a thick freehand checkmark/flag whose own outer silhouette happens to
# have 4 dominant corners) can pass corner-count, size, aspect-ratio, and
# extent-ratio filters while being nowhere near an actual rectangle. A
# printed checkbox border is a precise rectangle: opposite sides equal
# length, corners at ~90°. Reject quadrilaterals that visibly aren't.
#
# Thresholds validated against every current candidate across all 4 real
# samples (~285 boxes), ranked by side-ratio, not fit to one example: a
# real hand-drawn test case (a freehand flag/checkmark shape drawn without
# a box, appraisal-2.png near "Other (describe)") measured ratio=0.767,
# angle_dev=11.5° — clearly the worst of every candidate. The next-worst
# is a real, if imperfectly rendered, printed checkbox (appraisal-4.png)
# at ratio=0.846, angle_dev=9.1° — an earlier, tighter pair of thresholds
# (0.85/7°) rejected this real box outright (a confirmed false-negative
# regression, caught by re-running the full sample suite before trusting
# the fix, not assumed safe from the fake shape alone). These values leave
# real margin on both sides of that real box's numbers while still
# rejecting the fake shape on both metrics.
MIN_RECTANGLE_SIDE_RATIO = 0.8
MAX_RECTANGLE_ANGLE_DEVIATION_DEGREES = 10.0


def _is_rectangular(approx: np.ndarray) -> bool:
    pts = approx.reshape(-1, 2).astype(float)
    sides = [float(np.linalg.norm(pts[(i + 1) % 4] - pts[i])) for i in range(4)]
    side_ratio = min(
        min(sides[0], sides[2]) / max(sides[0], sides[2]),
        min(sides[1], sides[3]) / max(sides[1], sides[3]),
    )
    if side_ratio < MIN_RECTANGLE_SIDE_RATIO:
        return False

    for i in range(4):
        prev_pt, cur_pt, next_pt = pts[(i - 1) % 4], pts[i], pts[(i + 1) % 4]
        v1, v2 = prev_pt - cur_pt, next_pt - cur_pt
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        angle_degrees = math.degrees(math.acos(np.clip(cos_angle, -1.0, 1.0)))
        if abs(angle_degrees - 90.0) > MAX_RECTANGLE_ANGLE_DEVIATION_DEGREES:
            return False

    return True


def find_checkbox_candidates(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    binary = _adaptive_binary(gray)
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
        if not _is_rectangular(approx):
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


# A raw dark-pixel-fraction-of-area measure (the previous approach) is not
# scale-invariant for stroke-based marks: a fixed-width diagonal stroke's
# pixel count scales with box *diagonal* (roughly linear in box size), but
# area scales quadratically — so the same confident, unambiguous X mark
# reads as a much lower "ink ratio" in a larger checkbox than an identical
# stroke in a smaller one, purely as an artifact of box size, not mark
# confidence. Confirmed empirically: 4 real X-marks on appraisal-1
# (57x46px boxes) computed ink_ratio 0.075-0.077, just under the old
# INK_RATIO_THRESHOLD=0.08, while every other real checked mark on that
# same sample (identical box size) computed 0.087-0.112 — the mark itself
# wasn't ambiguous, the area-based measure was penalizing it for the box's
# size. Normalizing by box diagonal instead of box area removes that
# size-dependent penalty: measured on all 4 real samples across box sizes
# 24px to 57px, every genuinely empty box (including several with nonzero
# noise from anti-aliasing/adaptive-threshold artifacts, e.g. 3 empty
# appraisal-4 boxes measuring ink_density~1.10) stays well below 1.5, while
# every real checked mark across every sample and box size measures at
# least ~2.0 (appraisal-1's near-misses) up to ~7+ (appraisal-4's bold
# marks in its smaller boxes) — a threshold anywhere in the wide gap
# between those two clusters works; 1.5 sits roughly in the middle. See
# docs/algorithm-known-issues.md issue #3.
INK_DENSITY_THRESHOLD = 1.5
INTERIOR_MARGIN = 3
CONTAINMENT_PADDING_FACTOR = 2.0
# Minimum fraction of a touching component's pixels that must fall inside
# the box's own outer bounds (excluding the border ring itself, which is
# ambiguous territory) for that component to still count as "the mark".
# See _mark_is_contained for why this replaced a raw bounding-box-extent
# check.
CONTAINMENT_INTERIOR_RATIO = 0.2


def _ink_density(
    binary_full: np.ndarray, box: tuple[int, int, int, int], margin: int = INTERIOR_MARGIN
) -> float:
    x, y, w, h = box
    x1, y1 = x + margin, y + margin
    x2, y2 = x + w - margin, y + h - margin
    if x2 <= x1 or y2 <= y1:
        return 0.0

    interior = binary_full[y1:y2, x1:x2]
    dark_pixels = int(np.count_nonzero(interior))
    diagonal = (w**2 + h**2) ** 0.5
    if diagonal == 0:
        return 0.0
    return dark_pixels / diagonal


def _mark_is_contained(binary_full: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    x, y, w, h = box
    pad_x = int(w * CONTAINMENT_PADDING_FACTOR)
    pad_y = int(h * CONTAINMENT_PADDING_FACTOR)

    region_x1 = max(0, x - pad_x)
    region_y1 = max(0, y - pad_y)
    region_x2 = min(binary_full.shape[1], x + w + pad_x)
    region_y2 = min(binary_full.shape[0], y + h + pad_y)

    binary = binary_full[region_y1:region_y2, region_x1:region_x2]

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


def is_checked(binary_full: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    if _ink_density(binary_full, box) < INK_DENSITY_THRESHOLD:
        return False
    return _mark_is_contained(binary_full, box)


# Textract's own FORMS analysis associates labels with checkboxes far more
# precisely than any pixel heuristic could ("No Zoning" -> that exact box),
# but its geometry is still just a hint, not a trusted answer: a hint is
# only ever used to look closer at a location the pixel pass already missed
# entirely, never to override or remove something already found. See
# docs/algorithm-known-issues.md issue #7 and textract_hints.py.
#
# HINT_SEARCH_PADDING sizes the local window searched around a hint - big
# enough to catch a checkbox whose Textract-reported position is off by a
# few px, small enough that this stays a scoped, low-risk local search
# rather than reintroducing the page-wide false-positive problem that
# rejected every earlier attempt at issue #9 this session (see that
# issue's "attempted and rejected" history for why page-wide search on
# this document family doesn't work).
HINT_SEARCH_PADDING = 15
HINT_COVERED_IOU_THRESHOLD = 0.3
# Looser than the main pipeline's own MIN_BOX_SIZE/MAX_BOX_SIZE/aspect
# bounds: Textract's geometry is independently measured, not derived from
# our own contours, so it carries its own small margin of error - but
# still tight enough to reject an implausible shape outright (e.g. the
# known fake hand-drawn shape's Textract-reported box, aspect 1.59,
# already falls outside this band before any pixel analysis runs).
HINT_SIZE_TOLERANCE_FACTOR = 1.3
HINT_ASPECT_TOLERANCE_FACTOR = 1.2
LOCAL_STROKE_ANGLE_TOLERANCE_DEGREES = 15.0
LOCAL_STROKE_MIN_LENGTH = 15
LOCAL_STROKE_ERASE_THICKNESS = 4


def _hint_bbox_plausible(bbox: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return False
    min_size = MIN_BOX_SIZE / HINT_SIZE_TOLERANCE_FACTOR
    max_size = MAX_BOX_SIZE * HINT_SIZE_TOLERANCE_FACTOR
    if not (min_size <= w <= max_size and min_size <= h <= max_size):
        return False
    aspect_ratio = w / h
    min_aspect = ASPECT_RATIO_MIN / HINT_ASPECT_TOLERANCE_FACTOR
    max_aspect = ASPECT_RATIO_MAX * HINT_ASPECT_TOLERANCE_FACTOR
    return min_aspect <= aspect_ratio <= max_aspect


def _local_shape_verdict(
    crop: np.ndarray,
) -> tuple[str, tuple[int, int, int, int] | None]:
    """Look for a checkbox-shaped contour within a small local crop.

    Returns ("accept", bbox) if a valid rectangle is found (crop-local
    coordinates); ("reject", None) if a checkbox-scale, 4-corner contour
    exists but explicitly fails rectangularity (the hand-drawn-shape
    signature from issue #8 - strong evidence this location should NOT
    become a candidate); or ("no_shape", None) if nothing checkbox-scale
    with 4 corners is present at all. "no_shape" is absence of evidence,
    not evidence of absence: the only reason this crop is being examined
    is that a real label pointed here.
    """
    contours, _ = cv2.findContours(crop, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    saw_4corner_candidate = False
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w == 0 or h == 0:
            continue
        min_size = MIN_BOX_SIZE / HINT_SIZE_TOLERANCE_FACTOR
        max_size = MAX_BOX_SIZE * HINT_SIZE_TOLERANCE_FACTOR
        if not (min_size <= w <= max_size and min_size <= h <= max_size):
            continue
        # Mirrors find_checkbox_candidates' own aspect-ratio filter - without
        # this, a genuine but non-square rectangular structure nearby (a
        # table-cell border fragment, a text-field underline+box) could pass
        # every other check here and get promoted to a candidate. Skipped
        # rather than treated as reject-worthy evidence: an elongated shape
        # just isn't the checkbox this crop is looking for, unlike a failed
        # rectangularity check, which specifically signals a hand-drawn fake
        # (issue #8) - a bad-aspect contour shouldn't hard-reject the whole
        # location when another contour in the same crop might still be it.
        aspect_ratio = w / h
        min_aspect = ASPECT_RATIO_MIN / HINT_ASPECT_TOLERANCE_FACTOR
        max_aspect = ASPECT_RATIO_MAX * HINT_ASPECT_TOLERANCE_FACTOR
        if not (min_aspect <= aspect_ratio <= max_aspect):
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, CORNER_EPSILON_FACTOR * perimeter, True)
        if len(approx) != 4:
            continue
        saw_4corner_candidate = True
        if not _is_rectangular(approx):
            continue
        extent = cv2.contourArea(contour) / (w * h) if w * h else 0.0
        if extent < MIN_EXTENT_RATIO:
            continue
        return "accept", (x, y, w, h)
    if saw_4corner_candidate:
        return "reject", None
    return "no_shape", None


def _erase_local_diagonal_strokes(crop: np.ndarray) -> np.ndarray:
    """Erase long diagonal strokes within a small local crop.

    The same idea tried page-wide for issue #9 (real form structure is
    only ever horizontal/vertical, so a diagonal stroke is never part of
    the printed form) - rejected at page scale because dense text and
    evenly-spaced real check marks produce too many coincidental diagonal
    alignments to safely filter (see that issue's "attempted and
    rejected" history). At the scale of a single hint's search window,
    that noise source isn't present, so the same technique is safe here.
    """
    lines = cv2.HoughLinesP(
        crop, 1, np.pi / 360, threshold=10,
        minLineLength=LOCAL_STROKE_MIN_LENGTH, maxLineGap=4,
    )
    if lines is None:
        return crop

    cleaned = crop.copy()
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
        is_diagonal = not (
            angle < LOCAL_STROKE_ANGLE_TOLERANCE_DEGREES
            or angle > 180 - LOCAL_STROKE_ANGLE_TOLERANCE_DEGREES
            or abs(angle - 90) < LOCAL_STROKE_ANGLE_TOLERANCE_DEGREES
        )
        if is_diagonal:
            cv2.line(cleaned, (x1, y1), (x2, y2), 0, LOCAL_STROKE_ERASE_THICKNESS)
    return cleaned


def _recover_hint_candidate(
    binary_full: np.ndarray, hint_bbox: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = hint_bbox
    img_h, img_w = binary_full.shape
    rx1 = max(0, x1 - HINT_SEARCH_PADDING)
    ry1 = max(0, y1 - HINT_SEARCH_PADDING)
    rx2 = min(img_w, x2 + HINT_SEARCH_PADDING)
    ry2 = min(img_h, y2 + HINT_SEARCH_PADDING)
    if rx2 <= rx1 or ry2 <= ry1:
        return None

    crop = binary_full[ry1:ry2, rx1:rx2]

    # A "reject" verdict is only trusted as genuine evidence when it comes
    # from the untouched crop (a real hand-drawn shape actually sitting
    # there, e.g. the fake "Public"/"Other (describe)" blob from issue #8).
    # A "reject" that only appears AFTER our own diagonal-stroke erasure
    # is not independent evidence - erasure is a repair attempt, and can
    # itself damage a genuine (if diagonal-fused) border into something
    # that fails rectangularity, which is exactly what happened on the
    # real "No Zoning" box: the untouched crop shows an intact border
    # fused with the scratch into a many-cornered blob ("no_shape", since
    # it doesn't cleanly parse as 4 corners either way), but erasing the
    # diagonal also ate into the top-left corner, turning it into a
    # broken 4-corner shape that then fails rectangularity. Letting that
    # erasure-caused rejection block acceptance would silently reintroduce
    # this exact miss. So only the raw crop's reject is a hard stop; a
    # post-erasure reject just means the repair didn't help, not that the
    # location is bad.
    verdict, bbox = _local_shape_verdict(crop)
    if verdict == "accept":
        cx, cy, cw, ch = bbox
        return (cx + rx1, cy + ry1, cw, ch)
    if verdict == "reject":
        return None

    cleaned = _erase_local_diagonal_strokes(crop)
    verdict, bbox = _local_shape_verdict(cleaned)
    if verdict == "accept":
        cx, cy, cw, ch = bbox
        return (cx + rx1, cy + ry1, cw, ch)

    # Nothing found even after local repair, and the untouched crop never
    # produced an explicit reject either - a real label pointed here (e.g.
    # a border too faint to form a closed contour at all - "Neighborhood
    # Boundaries", or one fused into a many-cornered blob by a stray mark
    # - "No Zoning"). Trust Textract's own geometry as a last resort.
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    return (x1, y1, w, h)


def find_missing_boxes(
    binary_full: np.ndarray,
    existing_candidates: list[tuple[int, int, int, int]],
    hints: list[textract_hints.Hint],
) -> list[tuple[int, int, int, int]]:
    new_candidates = []
    for hint in hints:
        if hint.bbox is None:
            # Textract's own form model didn't resolve a checkbox for this
            # label at all - no fallback for this case yet (would need an
            # LLM or similar reasoning step; see algorithm-known-issues.md
            # issue #7).
            continue
        if not _hint_bbox_plausible(hint.bbox):
            continue
        hx1, hy1, hx2, hy2 = hint.bbox
        hint_xywh = (hx1, hy1, hx2 - hx1, hy2 - hy1)
        if any(_iou(hint_xywh, e) > HINT_COVERED_IOU_THRESHOLD for e in existing_candidates):
            continue
        recovered = _recover_hint_candidate(binary_full, hint.bbox)
        if recovered is None:
            continue
        # Re-check the box _recover_hint_candidate actually returned, not
        # just the hint's own raw bbox checked above: local search can shift
        # the result enough that it now overlaps an existing pixel-pipeline
        # candidate even when the original hint didn't. Additive-only means
        # never touching a box already found - if this recovery lands on top
        # of one, drop it rather than let it become a near-duplicate that
        # could out-compete the original in deduplicate_boxes' area-based
        # tie-break later.
        if any(_iou(recovered, e) > HINT_COVERED_IOU_THRESHOLD for e in existing_candidates):
            continue
        new_candidates.append(recovered)
    return new_candidates


def _get_textract_hints(
    image_bytes: bytes, width: int, height: int
) -> list[textract_hints.Hint]:
    """Best-effort: Textract is a pure enhancement here, never a
    requirement. This pass can only add candidates the pixel pipeline
    already missed, so any failure (network, throttling, credentials, an
    unparseable response) should fall back to CV-only results rather than
    breaking the endpoint - losing a few recoverable boxes is a much
    smaller failure than a 500.
    """
    try:
        response = textract_hints.analyze_forms(image_bytes)
    except Exception:
        logging.exception("Textract FORMS analysis failed; continuing with CV-only detection")
        return []
    try:
        return textract_hints.extract_checkbox_hints(response, width, height)
    except Exception:
        logging.exception("failed to parse Textract FORMS response; continuing with CV-only detection")
        return []


def decode_image(image_bytes: bytes) -> np.ndarray:
    if len(image_bytes) > MAX_INPUT_BYTES:
        raise ValueError("image too large")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)

    # Reject an implied-too-large image before ever decoding any pixel
    # data. If the header can't be parsed (unrecognized/truncated format),
    # fall through — the byte-size cap above is the only guard for that
    # case, since there's no safe way to peek dimensions without a decode.
    dimensions = _probe_dimensions(image_bytes)
    if dimensions is not None:
        width, height = dimensions
        if width * height > MAX_IMAGE_PIXELS:
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

    # Computed once and reused for every box's classification below, rather
    # than re-running adaptiveThreshold per box (it's a full-image op; with
    # ~100+ candidates per page, recomputing it per box would be wasteful).
    binary_full = _adaptive_binary(gray)

    hints = _get_textract_hints(image_bytes, gray.shape[1], gray.shape[0])
    if hints:
        missing = find_missing_boxes(binary_full, candidates, hints)
        if missing:
            candidates = deduplicate_boxes(candidates + missing)

    candidates = _sort_reading_order(candidates)

    boxes = []
    for (x, y, w, h) in candidates:
        boxes.append(
            {
                "bbox": [x, y, x + w, y + h],
                "is_checked": is_checked(binary_full, (x, y, w, h)),
            }
        )
    return boxes
