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


def test_rejects_skewed_quadrilateral_hand_drawn_shape():
    # Regression test: a hand-drawn shape (e.g. a freehand checkmark/flag
    # drawn without a real box around it) can have an outer silhouette
    # that's a 4-cornered, checkbox-sized, reasonably-square-aspect,
    # sufficiently-filled quadrilateral — passing every other filter — while
    # visibly not being an actual rectangle. Found on a real sample
    # (appraisal-2.png, near "Other (describe)"): a drawn shape measured
    # side-ratio 0.767, corner-angle deviation 11.5 degrees, and was
    # detected as a checkbox candidate (then classified checked) before
    # this fix. This synthetic shape reproduces the same failure mode: a
    # visibly skewed (non-rectangular) quadrilateral outline, all sides
    # unequal enough that opposite-side length ratio and corner angles both
    # fall outside a real rectangle's tolerance, while size/aspect/corner-
    # count/extent all still pass.
    gray = make_blank_gray()
    skewed = np.array([[50, 51], [51, 72], [78, 69], [76, 53]], dtype=np.int32)
    cv2.polylines(gray, [skewed], isClosed=True, color=0, thickness=3)

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


from detect_cv import is_checked, _adaptive_binary


def test_unchecked_empty_box():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)

    assert is_checked(_adaptive_binary(gray), (50, 50, 20, 20)) is False


def test_checked_box_with_contained_x_mark():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    cv2.line(gray, (53, 53), (67, 67), color=0, thickness=2)
    cv2.line(gray, (53, 67), (67, 53), color=0, thickness=2)

    assert is_checked(_adaptive_binary(gray), (50, 50, 20, 20)) is True


def test_unchecked_when_external_line_passes_through():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    # A long line spanning far beyond the box on both sides, incidentally
    # crossing straight through its interior — must NOT be read as a check.
    # Thickness 4 (not 2) deliberately clears INK_DENSITY_THRESHOLD with
    # real margin (measured ~2.97 vs the 1.5 threshold), so this line is
    # rejected by _mark_is_contained's containment logic — the thing this
    # test exists to exercise — not by the ink-density gate short-circuiting
    # before containment is ever reached.
    cv2.line(gray, (0, 60), (299, 60), color=0, thickness=4)

    assert is_checked(_adaptive_binary(gray), (50, 50, 20, 20)) is False


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

    assert is_checked(_adaptive_binary(gray), (50, 50, 20, 20)) is True


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

    assert is_checked(_adaptive_binary(gray), (49, 49, 27, 27)) is True


def test_checked_when_box_sits_on_shaded_background():
    # Regression test for issues #2/#6 (docs/algorithm-known-issues.md): a
    # checkbox drawn on a shaded/non-white cell background. The old global
    # BINARY_THRESHOLD=200 read the whole shaded fill as "ink" (background
    # gray 184 < 200 in the real case this reproduces), fusing the border
    # into an oversized blob during candidate detection and inflating
    # ink_ratio to ~1.0 during classification. Adaptive thresholding must
    # both still find this box as a candidate and classify it correctly.
    gray = make_blank_gray()
    gray[40:85, 40:85] = 184  # shaded cell background, darker than white
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    cv2.line(gray, (53, 53), (67, 67), color=0, thickness=2)
    cv2.line(gray, (53, 67), (67, 53), color=0, thickness=2)

    candidates = find_checkbox_candidates(gray)
    assert any(abs(x - 50) <= 2 and abs(y - 50) <= 2 for (x, y, w, h) in candidates)
    assert is_checked(_adaptive_binary(gray), (50, 50, 20, 20)) is True


def test_unchecked_when_box_sits_on_shaded_background():
    gray = make_blank_gray()
    gray[40:85, 40:85] = 184  # shaded cell background, darker than white
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)

    candidates = find_checkbox_candidates(gray)
    assert any(abs(x - 50) <= 2 and abs(y - 50) <= 2 for (x, y, w, h) in candidates)
    assert is_checked(_adaptive_binary(gray), (50, 50, 20, 20)) is False


import pytest

from detect_cv import detect_checkboxes


def test_detect_checkboxes_end_to_end(monkeypatch):
    # detect_checkboxes also calls out to Textract for gap-recovery hints
    # (see textract_hints.py) - stubbed here to keep this test offline and
    # deterministic, and because this test's synthetic image has nothing
    # for Textract's FORMS analysis to find labels in anyway.
    monkeypatch.setattr("detect_cv._get_textract_hints", lambda *a, **k: [])

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


def test_probe_dimensions_reads_png_header():
    png_header = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (800).to_bytes(4, "big")
        + (600).to_bytes(4, "big")
    )
    assert detect_cv._probe_dimensions(png_header) == (800, 600)


def test_probe_dimensions_reads_jpeg_sof0_header():
    # Minimal JPEG: SOI, then an SOF0 segment (marker 0xC0) with a 17-byte
    # payload: length(2) + precision(1) + height(2) + width(2) + ... —
    # _probe_dimensions only needs to reach the height/width fields.
    height, width = 480, 640
    sof0_payload = (
        (17).to_bytes(2, "big")
        + bytes([8])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + bytes(10)
    )
    jpeg_header = b"\xff\xd8" + b"\xff\xc0" + sof0_payload
    assert detect_cv._probe_dimensions(jpeg_header) == (width, height)


def test_probe_dimensions_skips_jpeg_fill_bytes_before_marker():
    # JPEG spec (T.81 SS B.1.1.5) legally permits any number of 0xFF fill
    # bytes immediately before a marker code, and real decoders are
    # required to skip them. A single inserted fill byte here must not
    # cause the SOF marker to be missed — that would silently disable the
    # dimension guard for a real decoder while still parsing normally.
    height, width = 480, 640
    sof0_payload = (
        (17).to_bytes(2, "big")
        + bytes([8])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + bytes(10)
    )
    # Extra 0xFF padding between the marker prefix and the SOF0 code.
    jpeg_header = b"\xff\xd8" + b"\xff\xff\xff\xc0" + sof0_payload
    assert detect_cv._probe_dimensions(jpeg_header) == (width, height)


def test_probe_dimensions_returns_none_for_unrecognized_format():
    assert detect_cv._probe_dimensions(b"not a recognized image header") is None


def test_decode_image_rejects_oversized_raw_bytes():
    # A cheap, tight cap on the raw upload itself, applied before any
    # decode is attempted at all.
    oversized = b"x" * (detect_cv.MAX_INPUT_BYTES + 1)

    with pytest.raises(ValueError):
        detect_cv.decode_image(oversized)


def test_decode_image_rejects_oversized_implied_dimensions(monkeypatch):
    # A small-on-disk, highly-compressible file (e.g. a huge flat-color
    # PNG) can still decode to a bitmap large enough to OOM-kill the
    # Lambda. decode_image guards against this by parsing width/height
    # directly from the PNG header — no pixel data is ever decoded, so
    # this can't itself OOM (an earlier version used a cv2-based
    # "downscaled" probe, which turned out not to be cheap for PNG: OpenCV
    # fully decodes PNGs internally before downscaling, so that probe
    # OOM'd live on a large-enough input instead of guarding against it).
    def fail_if_decoded(arr, flag):
        raise AssertionError("no decode should be attempted for an oversized image")

    monkeypatch.setattr(detect_cv.cv2, "imdecode", fail_if_decoded)

    # A minimal PNG header declaring 20000x20000px (400,000,000 pixels,
    # well over MAX_IMAGE_PIXELS) — no real compressed pixel data needed,
    # since the guard only ever reads the header.
    png_header = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (20000).to_bytes(4, "big")
        + (20000).to_bytes(4, "big")
    )

    with pytest.raises(ValueError):
        detect_cv.decode_image(png_header)


def test_decode_image_falls_back_to_byte_cap_for_unrecognized_format():
    # _probe_dimensions returns None for a format it can't parse (not a
    # PNG/JPEG header) — decode_image must still work normally in that
    # case (fall through to the byte-size cap + real decode), not treat an
    # unparseable header as itself an error.
    with pytest.raises(ValueError):
        detect_cv.decode_image(b"not an image and not a recognized header")


# --- Textract hint-driven gap recovery (issue #7) --------------------------
#
# These exercise find_missing_boxes() directly against synthetic images and
# hand-built Hint objects, independent of any live Textract call — the
# accept/reject/skip logic is what's under test here, not Textract's own
# analysis. See textract_hints.py for the Hint parsing tests, and the
# 2026-08-17 session notes for how this exact logic was validated against
# live Textract output on all 4 real samples before being written.

from textract_hints import Hint

from detect_cv import find_missing_boxes, _hint_bbox_plausible


def test_recovers_a_box_the_global_pipeline_misses_via_a_hint():
    # A long diagonal stroke fuses with the box border into one oversized,
    # non-4-corner contour — same failure mode as the real "No Zoning"
    # scratch line. find_checkbox_candidates() misses it entirely (the
    # fused contour fails the size/corner-count filters), but a hint
    # pointing at the box's own location recovers it.
    gray = make_blank_gray(150, 150)
    cv2.rectangle(gray, (50, 50), (74, 74), color=0, thickness=2)
    cv2.line(gray, (10, 65), (130, 45), color=0, thickness=2)

    existing = find_checkbox_candidates(gray)
    assert existing == []  # confirms the global pipeline genuinely misses this

    binary_full = detect_cv._adaptive_binary(gray)
    hints = [Hint(label="Test Label", bbox=(50, 50, 74, 74))]

    recovered = find_missing_boxes(binary_full, existing, hints)

    assert len(recovered) == 1
    x, y, w, h = recovered[0]
    assert abs(x - 50) <= 8 and abs(y - 50) <= 8
    assert 15 <= w <= 28 and 15 <= h <= 28


def test_rejects_hint_pointing_at_a_hand_drawn_non_rectangular_shape():
    # Same fake-shape signature as issue #8 (skewed quadrilateral, not a
    # real printed checkbox). A hint must never blindly trust Textract's
    # own bounding box — it has to fail the same rectangularity check a
    # normal candidate would, exactly what actually happened with the real
    # "Public" mislabel Textract produced for this same fake shape on
    # appraisal-2.
    gray = make_blank_gray(150, 150)
    pts = np.array([[50, 51], [51, 72], [78, 69], [76, 53]])
    cv2.polylines(gray, [pts], isClosed=True, color=0, thickness=3)

    binary_full = detect_cv._adaptive_binary(gray)
    hints = [Hint(label="Other (describe)", bbox=(50, 51, 78, 72))]

    assert find_missing_boxes(binary_full, [], hints) == []


def test_skips_hint_already_covered_by_an_existing_candidate():
    gray = make_blank_gray(150, 150)
    cv2.rectangle(gray, (50, 50), (74, 74), color=0, thickness=2)

    binary_full = detect_cv._adaptive_binary(gray)
    existing = [(48, 48, 28, 28)]
    hints = [Hint(label="Already Found", bbox=(50, 50, 74, 74))]

    assert find_missing_boxes(binary_full, existing, hints) == []


def test_accepts_a_hint_over_blank_space_as_a_last_resort():
    # No contour at all forms (e.g. a border too faint to register, like
    # the real "Neighborhood Boundaries" case) — nothing explicitly
    # rejects this location, and a real label pointed here, so Textract's
    # own geometry is trusted as a last resort. Documents this trade-off
    # deliberately rather than leaving it as an accidental gap.
    gray = make_blank_gray(150, 150)
    binary_full = detect_cv._adaptive_binary(gray)
    hints = [Hint(label="Blank", bbox=(50, 50, 73, 72))]

    recovered = find_missing_boxes(binary_full, [], hints)

    assert recovered == [(50, 50, 23, 22)]


def test_hint_bbox_plausible_rejects_implausible_aspect_ratio():
    # The real fake hand-drawn shape's Textract-reported box (27x17,
    # aspect 1.59) — should be rejected by this cheap pre-filter before
    # any pixel analysis runs at all.
    assert _hint_bbox_plausible((808, 614, 835, 631)) is False


def test_hint_bbox_plausible_accepts_normal_checkbox_size():
    assert _hint_bbox_plausible((713, 492, 736, 514)) is True


def test_local_shape_verdict_rejects_a_valid_but_non_square_rectangle():
    # find_checkbox_candidates enforces ASPECT_RATIO_MIN/MAX everywhere
    # else in the pipeline; _local_shape_verdict must too, or a genuine
    # non-checkbox rectangular structure near a hint (a table-cell border
    # fragment, a text-field underline+box) could get promoted to a
    # candidate purely for being correctly-sized and rectangular.
    gray = make_blank_gray(150, 150)
    cv2.rectangle(gray, (50, 50), (74, 100), color=0, thickness=2)  # 24x48, aspect 0.5

    binary_full = detect_cv._adaptive_binary(gray)
    verdict, bbox = detect_cv._local_shape_verdict(binary_full[45:105, 45:80])

    assert verdict != "accept"


# --- _recover_hint_candidate control flow (monkeypatched _local_shape_verdict) --
#
# The exact geometry that makes local diagonal-stroke erasure damage a real
# border (turning a "no_shape" raw verdict into an erasure-caused "reject")
# is fiddly to reproduce reliably through cv2 drawing calls — the real bug
# this guards against (see algorithm-known-issues.md issue #9's "live bug
# caught during validation") was found on real Textract output, not a
# synthetic case. Controlling _local_shape_verdict's two return values
# directly tests the intended control flow precisely and deterministically,
# independent of cv2 contour geometry, and documents the exact behavior
# being locked in.


def test_erasure_caused_rejection_does_not_block_the_fallback_accept(monkeypatch):
    # raw crop verdict "no_shape" (a border fused into a many-cornered blob
    # — neither evidence for nor against), erasure-repair verdict "reject"
    # (the repair itself damaged the border). The post-erasure reject must
    # NOT block the final trust-Textract fallback — only a reject from the
    # untouched crop is real evidence, exactly the bug fixed after the
    # first live validation run silently dropped the real "No Zoning" box.
    verdicts = iter([("no_shape", None), ("reject", None)])
    monkeypatch.setattr(detect_cv, "_local_shape_verdict", lambda crop: next(verdicts))

    gray = make_blank_gray(150, 150)
    binary_full = detect_cv._adaptive_binary(gray)

    result = detect_cv._recover_hint_candidate(binary_full, (50, 50, 73, 72))

    assert result == (50, 50, 23, 22)  # falls back to trusting the hint's own bbox


def test_erasure_producing_a_valid_shape_is_accepted(monkeypatch):
    verdicts = iter([("no_shape", None), ("accept", (5, 5, 23, 22))])
    monkeypatch.setattr(detect_cv, "_local_shape_verdict", lambda crop: next(verdicts))

    gray = make_blank_gray(150, 150)
    binary_full = detect_cv._adaptive_binary(gray)

    result = detect_cv._recover_hint_candidate(binary_full, (50, 50, 73, 72))

    padding = detect_cv.HINT_SEARCH_PADDING
    rx1, ry1 = 50 - padding, 50 - padding
    assert result == (rx1 + 5, ry1 + 5, 23, 22)


def test_reject_on_the_untouched_crop_is_a_hard_stop_no_erasure_attempted(monkeypatch):
    calls = []

    def fake_verdict(crop):
        calls.append(1)
        return "reject", None

    monkeypatch.setattr(detect_cv, "_local_shape_verdict", fake_verdict)

    gray = make_blank_gray(150, 150)
    binary_full = detect_cv._adaptive_binary(gray)

    result = detect_cv._recover_hint_candidate(binary_full, (50, 50, 73, 72))

    assert result is None
    assert len(calls) == 1  # erasure repair never attempted after a raw reject
