# CV algorithm: known issues and misses

Working backlog for `lambda/detect-cv/detect_cv.py`, to work through one at a
time. Every item below was reproduced against the real files in `samples/`
and pixel-traced to a specific line of code — nothing here is a guess.
Baseline counts referenced throughout (before any fix in this doc):
appraisal-1: 118 boxes/33 checked, appraisal-2: 41/17, appraisal-3: 30/8,
appraisal-4: 77/25.

## Open issues, ranked by concrete evidence found so far

### 1. Global binary threshold misses faint/light-gray checkbox borders

**Where:** `find_checkbox_candidates`, `BINARY_THRESHOLD = 200` (global
`cv2.threshold`).

**Found:** 2026-08-16, comparing a user-supplied ChatGPT vision detection
(`docs/chatgpt2.json`) against our own live `/detect` output on
`appraisal-2.png`. 40/43 of ChatGPT's boxes matched ours within 15px with
100% checked/unchecked agreement on every match — but 3 didn't match; one
turned out to be a ChatGPT hallucination (see "Reference-data caveat"
below), and **two are real checkboxes we miss entirely**:
`[281,187,307,210]` and `[1098,491,1122,515]`.

**Root cause, pixel-confirmed:** both boxes' border pixels sample at gray
value ~223–224 (box 1) and even lighter (box 2) — lighter than
`BINARY_THRESHOLD = 200`, so `THRESH_BINARY_INV` never marks them as "ink."
The border produces no contour at all; the box doesn't just get
misclassified, it never becomes a candidate in the first place.

**Severity:** High — silent, total miss, not a borderline classification.
Only found 2 instances via one sample's comparison; there is no reason to
believe these are the only two in the wild.

**FIXED 2026-08-16, partially — see status below.** Landed together with
#2/#6 (all three share the same root cause and fix: adaptive thresholding,
`_adaptive_binary()` in `detect_cv.py`, `ADAPTIVE_BLOCK_SIZE=51`,
`ADAPTIVE_C=10`). **Status: 0/2 of this issue's own two examples recovered**
— tested a full grid of blockSize (25/31/41/51/61) × C (5/8/10/12); no
combination reliably finds both, and most find neither. Cropped
`[281,187,307,210]` at 6x and confirmed visually: this border is faintly
gray on *all four sides*, not just fused with nearby text or broken on one
edge — a genuinely low-contrast rendering in the source scan itself, at or
below the sensitivity floor local-contrast thresholding can distinguish
from paper white. This is a different, harder problem than #2/#6 (which
are cleanly fixed) — not chasing it further with parameter tuning per the
user's explicit concern (2026-08-16) about over-fitting to specific
threshold values on this one sample. Left open; a real fix would need a
different technique entirely (e.g. edge/gradient-based detection instead
of any brightness threshold, or morphological closing to bridge a
discontinuous border before contour extraction).

### 2. Shaded/non-white table-row backgrounds erase checkbox borders entirely

**Where:** `find_checkbox_candidates`, same global threshold.

**Found:** Task 17 (Textract-vs-CV live comparison), pixel-traced today.
18 real checkboxes on `appraisal-3.png`, all sitting in alternating
blue-shaded table rows (crop region `y:880-1270, x:1740-2320`, the
Inventory Analysis / Overall Trend tables), are completely absent from CV's
output. Textract finds all of them.

**Root cause, pixel-confirmed:** the shaded cell background samples at gray
value **184** — already below `BINARY_THRESHOLD = 200`, so the *entire
shaded cell* binarizes as "ink," not just the checkbox border. The border
contour fuses with the whole cell into one large connected region, which
then fails the `MIN_BOX_SIZE <= w <= MAX_BOX_SIZE` size filter and is
dropped before ever reaching classification.

**Severity:** High — 18 boxes is the single largest concentrated miss found
in any sample.

**Relationship to #1:** both #1 and #2 are symptoms of the same
architectural choice — a single fixed global threshold. #1 is a border too
*light* relative to a white background; #2 is a background too *dark*
relative to white, swallowing a normal-darkness border. Adaptive
thresholding (`cv2.adaptiveThreshold`, locally normalizing contrast instead
of a fixed global cutoff) is the one change that plausibly fixes both at
once. This was flagged in the Task 12 audit against `docs/chatgpt.md` as "a
deliberate deviation, assessed as sound" — that assessment predates this
evidence and should be revisited first, before smaller point-fixes, since
it may subsume #1 and #2 (and possibly help #5).

**FIXED 2026-08-16.** Landed in `detect_cv.py` as a full replacement of
`BINARY_THRESHOLD`/`cv2.threshold` with `_adaptive_binary()`
(`cv2.adaptiveThreshold`, `ADAPTIVE_THRESH_GAUSSIAN_C`,
`ADAPTIVE_BLOCK_SIZE=51`, `ADAPTIVE_C=10`) — not a second unioned pass as
first prototyped. Before replacing outright, explicitly verified adaptive
alone is a strict superset of global's candidates on all 4 samples (every
global-threshold candidate has an IoU>0.5 match in adaptive's output, zero
exceptions, checked across appraisal-1's 210 raw contours down to
appraisal-4's 128) — so keeping global as a second pass would have added
complexity for zero additional coverage. Final sample counts:
appraisal-1 118→118 (unchanged), appraisal-2 41→41 (unchanged), appraisal-3
30→48 (+18, exactly the shaded-row misses, two separate shaded tables not
just one), appraisal-4 77→79 (+2, both visually confirmed genuine missed
checked boxes — one fusing with an adjacent "D" character under the old
global threshold, unrelated to shading). Zero new false positives in any
sample. Classification was fixed together with this — see #6, landed in
the same change; do not read #2 as complete without #6.

### 3. Thin diagonal-stroke X-marks fail the ink-ratio threshold by a hair

**Where:** `is_checked`, `INK_RATIO_THRESHOLD = 0.08`.

**Found:** Task 17 flagged 4 "bold, unambiguous" X-marks on
`appraisal-1.png` classified `is_checked: false`:
`[303,1405,359,1452]`, `[303,1354,359,1400]`, `[346,2207,401,2252]`,
`[853,3108,909,3153]`. Root-caused today.

**Root cause, precisely confirmed:** all 4 are correctly detected as
candidates and correctly pass `_mark_is_contained` (the mark genuinely is
inside the box). But `_ink_ratio` computes **0.075, 0.077, 0.075, 0.075** —
each just under the 0.08 cutoff. Visually, the marks are thin diagonal
strokes (not filled/bold as the naked eye perceives them against a small
box) — two thin diagonal lines naturally cover a small fraction of a
46x57px interior even when a human reads them as an obvious, confident X.

**Severity:** High but narrow — precisely understood, single-constant
distance to the fix, but must be re-verified against every checked box in
all 4 samples before changing the constant, to avoid dragging in the kind
of low-density false positive (stray dot, print speck) `INK_RATIO_THRESHOLD`
exists to reject. A margin-only fix (lowering the constant) is fragile the
same way `MIN_EXTENT_RATIO`'s current margin was flagged as fragile in
Task 6 — consider whether a relative/shape-aware measure (e.g. stroke
length rather than raw pixel-count ratio) is more robust than another bare
threshold tweak.

**User-raised concern (2026-08-16), applies to every threshold in this
file, not just this one:** hand-drawn/printed marks vary — a line could be
thinner, bolder, or lighter across different documents/scanners — so any
single fixed cutoff (`INK_RATIO_THRESHOLD`, `BINARY_THRESHOLD`,
`MIN_EXTENT_RATIO`, adaptive threshold's own blockSize/C) is calibrated to
this sample set's specific rendering, not provably general.

**FIXED 2026-08-16.** Took the "relative/shape-aware measure" path flagged
above rather than nudging the constant: replaced `_ink_ratio`
(dark-pixel-count / box **area**) with `_ink_density` (dark-pixel-count /
box **diagonal**), `INK_DENSITY_THRESHOLD = 1.5`. The area-based measure
was never scale-invariant — a fixed-width stroke's pixel count scales
roughly linearly with box size (it's still just a line), but area scales
quadratically, so the identical confident mark reads as a much lower
"ratio" in a bigger box. Diagonal-length normalization removes that
size-dependent penalty. Validated against **every** box in all 4 real
samples before picking the threshold value (not just the 4 known cases):
plotted both measures for all ~285 boxes across box sizes 24-57px, found a
clean, wide gap between the "genuinely empty" cluster (max ~1.10,
including several boxes with nonzero noise from anti-aliasing/adaptive-
threshold artifacts near borders) and the "real mark" cluster (min ~2.0,
up to ~7+ in appraisal-4's smaller/bolder-relative boxes) — 1.5 sits in
the middle of that gap with real margin on both sides, not chosen to
exactly split the 4 known cases. Before trusting a nonzero-but-unchecked
cluster as noise rather than a missed real mark, cropped and visually
confirmed one (an appraisal-4 box with `ink_density≈1.10` — genuinely
empty). Result: recovers all 4 appraisal-1 X-marks *and* an unexpected
bonus — one appraisal-4 box previously misclassified `checked` by the old
area-based measure (`ink_ratio=0.085`, just over the old 0.08 cutoff) is
visually confirmed empty and now correctly classifies `unchecked`. Net:
+4 true positives, +1 true negative, 0 regressions. One test
(`test_unchecked_when_external_line_passes_through`) needed its line
thickness bumped (2px→4px) to keep clearing the new ink gate with margin —
otherwise it was accidentally passing for the wrong reason (rejected by
the ink gate itself, not by the `_mark_is_contained` logic it exists to
exercise). 34/34 tests passing.

### 4. Isolated ink blobs pass the containment check with no shape requirement

**Where:** `is_checked` / `_mark_is_contained`.

**Found:** Task 12 audit against `docs/chatgpt.md`; already documented in
`README.md`'s known-limitations section. Restated here to keep this doc the
single backlog.

**Root cause:** `is_checked` requires ink coverage (`_ink_ratio >= 0.08`)
and that the touching component's pixels fall mostly inside the box
(`_mark_is_contained`) — neither checks *shape*. An isolated dust speck,
hole-punch shadow, or print artifact, fully contained and dense enough,
currently classifies as `checked`. `docs/chatgpt.md`'s Hough-line diagonal
check would have rejected this (no line structure); the containment check
doesn't.

**Severity:** Medium — real, principled gap, but **no concrete instance
found yet in the 4 real samples** (unlike #1-#3, which are all reproduced
against real data). Needs either (a) a synthetic test case built to prove
the current behavior first, matching how the JPEG fill-byte bug and PNG
decompression-bomb bug were each proven with a hand-built reproduction
before fixing, or (b) a real example if one turns up.

### 5. appraisal-1: 118 detected boxes vs. an estimated ~125

**Where:** `find_checkbox_candidates`, general candidate detection.

**Found:** Task 6 calibration; already documented in `README.md`. Restated
here for completeness of the backlog. Issue #3 above (4 of what could be
part of this gap) turned out to be a *classification* miss, not a
*candidate-detection* miss (the boxes were always being found, just
misclassified) — so the 118 count itself was unaffected by fixing #3.

**Re-checked 2026-08-16 after #2's fix landed: unaffected, count is still
118.** #2's adaptive threshold recovered boxes on appraisal-3 and
appraisal-4, but appraisal-1's count didn't move at all — the README's
working theory (gridline fusion) evidently isn't the same mechanism #2
fixed, or isn't present on appraisal-1 in a way adaptive thresholding
reaches.

**Investigated fully, 2026-08-17 — closed, not a real gap.** Two lines of
evidence, both against the "~125" figure rather than the detector:

1. `docs/chatgpt.json` (123 boxes) was the suspected source of the "~125"
   estimate, given the near-identical count. Checked directly this time
   rather than assumed unrelated: its bounding boxes only span y=300-1349
   of this 4200px-tall image (roughly the top third), and even restricted
   to that same region our own detector only has 20 real candidates there
   — not remotely close to 123. Tried matching its coordinates against our
   real candidates at a range of uniform scale factors (2.7x-3.3x, since
   its box sizes are ~1/3 of ours, suggesting a downscaled image was shown
   to it) on both appraisal-1 and appraisal-3 (the only other sample with
   matching pixel dimensions) — best case was 6/123 matched at IoU>0.3.
   Confirms the existing session note: `chatgpt.json`'s coordinates aren't
   pixel-grounded to any of our samples at all, so it cannot be the
   origin of a reliable "~125" ground truth, whatever its actual source
   was (the README only credits "a similar prior manual analysis," now
   unverifiable).
2. Full manual visual audit of the entire rendered page (all 4200px of
   height, reviewed in six 700px sections against the current
   `-annotated.png` output): every checkbox in every row — including the
   dense Attic/Basement/Heating/Cooling/Amenities grid and the
   Utilities/Zoning Compliance rows — has a drawn (red or green) box
   around it. Zero visually-missing checkboxes found anywhere on the page,
   and zero spurious boxes drawn where no real checkbox exists.

**Conclusion:** 118 is very likely the correct count for this page. The
"~125" figure has no verifiable source and shouldn't be treated as ground
truth going forward — this is the same "reference data isn't ground truth"
lesson as the ChatGPT-comparison caveat below, just for a number instead of
a JSON file. Closed as investigated; reopen only if a future
comparison source or manual review turns up a specific missing bbox (the
standard this project holds every other finding to).

### 6. Classification (checked/unchecked) still breaks on shaded backgrounds

**Where:** `_ink_ratio`, `_mark_is_contained` — both use `BINARY_THRESHOLD
= 200` internally, same as candidate detection.

**Found:** while validating #2's adaptive-threshold fix. Adaptive
thresholding fixes *finding* the 18 shaded-row boxes on appraisal-3 as
candidates, but classification is a separate code path that wasn't
touched — it still uses the plain global threshold. All 18 recovered
candidates compute `_ink_ratio = 1.000` (the shaded background itself
reads as "ink"), and currently classify `False` only because
`_mark_is_contained`'s connected-component logic happens to reject a
component that sprawls across the whole shaded row. This needs box-by-box
visual verification against the real checked/unchecked marks in that
region before trusting it — a shaded row that's actually checked could
easily classify wrong for the same underlying reason. Do not close #2 as
"fixed" without also fixing/verifying this.

**Severity:** High — candidate detection and classification are both
gated on the same fragile absolute threshold; fixing one without the other
leaves a real, silent misclassification risk in exactly the region #2 was
supposed to fix.

**FIXED 2026-08-16.** `_ink_ratio`/`_mark_is_contained` now take a
pre-thresholded binary array (`_adaptive_binary(gray)`, computed once per
image in `detect_checkboxes` and reused across every candidate's
classification, not recomputed per box) instead of raw `gray` +
per-region `cv2.threshold`. Before landing, explicitly verified adaptive
threshold produces **zero classification differences** from the old global
threshold across every one of the 266 boxes already being correctly found
across all 4 samples (118+41+30+77) — this is a pure win, not a tradeoff.
For the 18 newly-recovered shaded-row boxes, visually verified all 18
individually against cropped source image regions (both shaded tables) —
every checked/unchecked call now matches the visible mark exactly. Added 2
regression tests (`test_checked_when_box_sits_on_shaded_background`,
`test_unchecked_when_box_sits_on_shaded_background` in
`tests/test_detect_cv.py`) reproducing a shaded background synthetically.
Full suite: 34/34 passing.

### 7. No context/label awareness — detection is purely geometric today (future direction)

**Where:** the whole pipeline. Not a bug in existing code, a capability
gap.

**Raised by the user, 2026-08-16.** Every check in this pipeline today —
size, aspect ratio, corner count, extent ratio, ink ratio, containment — is
purely geometric/pixel-level. None of it asks "is there a label near this
box?" or "does the surrounding text imply a checkbox belongs here at all?"
Two escalating levels of ambition, as the user described them:

- **Level 1 — label proximity.** For each detected candidate, check
  whether there's actually nearby text (OCR on a small region adjacent to
  the box) — a checkbox with no associated label at all is a strong signal
  it's a false positive (stray mark, table-rule artifact, print noise)
  rather than a real form field, independent of its own geometry.
- **Level 2 — form semantics.** Go further: OCR/understand the document's
  text well enough to know where a checkbox is *expected* (e.g. a "Yes/No"
  or multiple-choice line implies boxes should exist nearby) and cross-check
  that against what geometric detection actually found — both to suppress
  false positives lacking a plausible label, and potentially to recover
  false negatives geometric detection missed entirely by knowing where to
  look harder.

**Why this matters relative to #1-#6:** every fix above is still working
within "smarter pixel thresholds." This is a different axis entirely —
using the document's actual textual content as a cross-check, which is
exactly the kind of signal a purely geometric/contour pipeline can never
have access to. It's also the most expensive to build (needs an OCR step —
Tesseract locally, or reusing Textract's OCR output even while keeping CV's
own checked/unchecked classification) and the most architecturally
different from everything else in this document — not a constant to tune,
a new pipeline stage.

**Status:** not investigated yet — no prototype, no evidence beyond the
idea itself. Needs its own scoped investigation (what OCR engine, cost/
latency impact on the Lambda, false-positive-suppression vs. false-
negative-recovery as separate sub-goals) before any implementation.

### 8. Hand-drawn shapes can mimic a checkbox's 4-corner, correct-extent silhouette

**Where:** `find_checkbox_candidates` — the corner-count/aspect-ratio/
extent-ratio filters admit any quadrilateral, including a visibly skewed
one.

**Found:** 2026-08-17, user-supplied test case on `appraisal-2.png` near
"Other (describe)" — a freehand flag/checkmark-like shape drawn without an
actual box around it. Its own outer silhouette (ink boundary, not a
printed rectangle) happened to approximate 4 corners closely enough to
pass every existing filter, and was detected as a candidate and classified
`checked`.

**Root cause, precisely confirmed:** `approxPolyDP` returning 4 points only
confirms corner *count*, not that those 4 points form an actual rectangle.
The shape's 4 sides measured [25.0, 22.0, 25.5, 26.0]px — a 23% length
mismatch between the two "opposite" 22.0/26.0 sides — and one corner
deviated 11.5° from 90°, both far outside what any real printed checkbox
border on any of the 4 samples measures.

**FIXED 2026-08-17.** Added `_is_rectangular()`: rejects a candidate
quadrilateral if its opposite-side length ratio falls below
`MIN_RECTANGLE_SIDE_RATIO` or any corner deviates more than
`MAX_RECTANGLE_ANGLE_DEVIATION_DEGREES` from 90°. Thresholds required two
passes to get right — a first attempt (0.85 / 7°) was validated only
against the fake shape's numbers and looked safe with wide margin, but
re-running the full sample suite (this project's standing practice before
trusting any fix) caught a real regression: a genuine, if slightly
imperfectly rendered, printed checkbox on `appraisal-4.png` measured
0.846/9.1° and was wrongly rejected. Corrected to `MIN_RECTANGLE_SIDE_RATIO
= 0.8`, `MAX_RECTANGLE_ANGLE_DEVIATION_DEGREES = 10.0` — re-verified this
keeps every real box across all 4 samples (the closest real call is that
same 0.846/9.1° box) while still rejecting the fake shape on both metrics.
Net effect: appraisal-2 41→40 boxes (17→16 checked, the fake shape
removed); appraisal-1/3/4 unchanged. Added a regression test
(`test_rejects_skewed_quadrilateral_hand_drawn_shape`) reproducing the
same skewed-quadrilateral shape synthetically. 35/35 tests passing.

### 9. "No Zoning" scratch-line box: still not detected, failure mode changed

**Where:** `find_checkbox_candidates`.

**Found:** this is the box behind the *original* Textract false positive
that motivated this entire CV pivot (README's "appraisal-2.png scratch
line" known limitation, and Task 15's trace of the same coordinates).
Re-confirmed 2026-08-17 by direct user inspection of the source image: the
box (next to "No Zoning") is still not detected by the CV pipeline.

**New finding:** the failure mode is not what the README currently
describes. The README's existing text says the border+scratch fuse into
one oversized (~859x31px) contour that fails the size filter — that was
true under the old global `BINARY_THRESHOLD`. Under the new adaptive
threshold (issue #2's fix), the mechanism is different: visually confirmed
via the raw binary mask that a genuine portion of the box's own border
(the top edge and part of the right edge, near where the scratch crosses)
is **entirely absent** from the binary image, not fused with anything —
the remaining border fragments are 3-6 sided, undersized shards that don't
individually reconstruct a valid rectangle. **README's existing
explanation of this case needs updating** — it no longer accurately
describes the current failure mechanism (task not yet done — carried here
as a known documentation gap, not fixed in this pass).

**Attempted and rejected (1):** morphological closing (kernel sizes 2 and
3), intended to bridge the missing border segment — did not recover this
box at all, and reduced the sample's total candidate count from ~41 to
24-26 (real boxes elsewhere lost too), confirming it's too blunt an
instrument applied image-wide. Not attempted as a scoped/local operation
given the missing segment is large (a significant fraction of two sides),
well beyond what a small-kernel close can bridge.

**Attempted and rejected (2), 2026-08-17:** global Hough-line-based
rectangle reconstruction, tolerant of partial occlusion — the technique
flagged above as the likely fix. Implementation: `cv2.HoughLinesP` run
once over the whole binary image, segments classified horizontal/vertical
by angle (±8°), paired (top+bottom horizontal within `MIN_BOX_SIZE`..
`MAX_BOX_SIZE` apart, x-ranges overlapping ≥50%) and confirmed by a
vertical segment covering ≥50% of the height near either x boundary.
Prototyped and traced by hand first: this box's own fragments genuinely do
reconstruct correctly this way (bottom edge `(713,514)-(736,514)`, right
edge `(734,517)-(737,486)` spanning nearly the full height, left edge
`(713,507)-(713,491)` partial, top edge `(720,493)-(740,492)` partial — all
consistent with one ~24x23 rectangle matching the ledger's estimate). But
run over the full page, the same pairing logic that reconstructs this one
real box also fires on every regularly-spaced pair of ruled table lines
page-wide, since a form full of gridlines has enormous numbers of
horizontal-pair-plus-connecting-vertical coincidences at checkbox scale.
Measured on all 4 samples (candidates not already matched to an existing
contour-based candidate, IoU > 0.3): appraisal-1 +349, appraisal-2 +17,
appraisal-3 +345, appraisal-4 +487 spurious candidates — and on
appraisal-2 specifically, **the target box wasn't even among the 17**
(lost to its own dedup pass against nearby spurious matches). Rejected:
worse than useless as implemented — massive false-positive rate without
reliably solving the one case it was built for. A viable version would
need much stronger scoping (e.g. only trigger in small, already-identified
gap regions rather than globally, plus a way to distinguish an isolated
checkbox-shaped gap from a repeating table-grid pattern) — enough
additional heuristic surface that it risks overfitting to this one
example, the exact failure mode this project's methodology exists to
catch before shipping.

**Attempted and rejected (3), 2026-08-17 — diagonal-stroke removal, 3
filter variants:** a different framing, suggested by the project owner:
rather than reconstructing the box's rectangle directly, detect and erase
long *diagonal* strokes (real form structure is only ever horizontal or
vertical) from the binary mask first, then let the existing, already-
validated contour pipeline run normally on the cleaned result. Confirmed
by rendering the detected line back onto the source image that the real
"No Zoning" scratch is exactly this: one continuous hand-drawn correction
spanning nearly the full diagonal of the page, cutting through several
unrelated rows (Neighborhood Description, Market Conditions, Zoning
Compliance, Utilities). But safely isolating *only* that stroke via
`cv2.HoughLines` proved unreliable on this document:
  - **Variant A** (length + axis-angle filter only): far too permissive —
    218 "strokes" erased on appraisal-2 alone, 17 real checkboxes damaged.
  - **Variant B** (+ require one sustained ≥20px unbroken ink run, to
    reject grid-intersection artifacts that only ever touch briefly):
    reduced to 76-184 erasures depending on length threshold, still 6-18
    real checkboxes damaged. Root cause traced further than variant A:
    rendering all flagged lines revealed a dense radiating *fan* of
    spurious detections converging near the target box, not just isolated
    noise — traced to Hough accumulator peaks generated by dense paragraph
    text (Neighborhood Description/Market Conditions), which has enough
    consistent local structure to rack up long "sustained" runs that look
    identical to genuine strokes by this metric.
  - **Variant C** (+ reject lines whose internal gaps repeat at a
    suspiciously regular interval, to catch coincidental alignment of
    evenly-spaced real checkbox X-marks across grid rows): cut erasures to
    14 and regressions to 8, but also filtered out the *real* target
    stroke and the genuinely-real diagonal watermark on appraisal-4 —
    recovered zero boxes anywhere while still damaging 8 real ones.

  Separately confirmed on appraisal-4: its "diagonal watermark" (visually
  a faint repeated "with a trial version" stamp, not a hand mark) removes
  cleanly with variant A/B and causes no regressions there specifically,
  but recovers **zero** additional boxes — that region is already fully
  detected, consistent with the existing README note. Not useful on its
  own, and variant C loses even this clean case.

  Conclusion: whole-page `cv2.HoughLines`-based diagonal detection cannot
  safely distinguish "one real hand stroke" from "dense printed text and
  evenly-spaced real check marks that coincidentally align," regardless of
  which length/continuity/periodicity statistic is used to filter it —
  the false-positive source is too entangled with genuine page content on
  a form this dense.

**Attempted and rejected (4), 2026-08-17 — scoped local search seeded from
rejected fragments:** to avoid the whole-page noise problem, tried scoping
any future local search to only run near contours that already look like
broken/fragmented checkbox shards (rather than searching the whole page).
Seeding by clustering small-to-medium rejected contours (6-90px,
proximity-merged) produced 561 "suspect regions" on appraisal-1 alone —
useless as a filter, because ordinary printed words and letters are
indistinguishable in size from genuine border shards; the seed can't tell
"part of a broken box" from "part of the word 'Description.'" The one seed
that would plausibly be well-scoped — a gap in the rhythm of an
already-detected checkbox row (3 of 4 boxes in a row found at consistent
spacing implies the 4th should exist too) — is really a narrow slice of
issue #7 (context/label awareness) rather than a standalone fix; see #7.

**Severity:** High (this is the box that started the whole project), but
genuinely hard — four structurally different techniques (morphological
bridging, global Hough-line rectangle reconstruction, diagonal-stroke
removal in 3 filter variants, fragment-clustering local scoping) all tried
and rejected on concrete evidence, not assumption. The clearest remaining
path is building real row/column context-awareness (issue #7) and reusing
its output as the seed for a local repair pass here, rather than any
further page-wide heuristic. Open.

### 10. Checkbox border obscured on all sides by an unusual "burst" mark pattern

**Where:** `find_checkbox_candidates`.

**Found:** 2026-08-17, user-supplied finding on `appraisal-2.png`, the
"Electricity" row's "Public" checkbox. The mark inside is an unusual
pattern — a center X with additional short radiating dash strokes — whose
arms extend out and touch the border on all four sides.

**Root cause, precisely confirmed:** visually confirmed via the raw binary
mask that the border itself is fully intact (a complete rectangular
loop) — unlike issue #9, nothing is missing. But because the mark's arms
touch the border at multiple points, `cv2.findContours` returns one
merged border+mark contour with a complex outer silhouette: 8 corners
(not 4) and extent 0.214 (far below `MIN_EXTENT_RATIO=0.6`). Rejected by
both the corner-count and extent filters before ever reaching
classification.

**Attempted and rejected:** erosion (kernel sizes 2 and 3), intended to
thin and detach the mark's arms from the border while leaving the thicker
border intact — far too destructive image-wide, collapsing the sample's
total candidate count to 4 and then 0. Not viable even as a supplementary
pass given how much it damages unrelated real candidates.

**Severity:** Medium — a genuinely unusual mark pattern, no other instance
of anything like it found across the other 3 samples. Same category of
difficulty as #9 (needs a fundamentally different technique than
contour-extraction-on-a-single-binary-pass), but lower priority given it's
a single, unusual instance rather than the project's original motivating
case. Open.

## Reference-data caveat: ChatGPT's own outputs are not ground truth

While investigating #1, the same `docs/chatgpt2.json` comparison also
surfaced two failures **in ChatGPT's output, not ours**:

- `[330,613,354,637]` on `appraisal-2.png` is marked `is_checked: true` in
  `chatgpt2.json`, but the crop at that exact location shows **no checkbox
  border at all** — a fabricated box, not a borderline call.
- `[809,613,838,636]` on `appraisal-2.png` is a real, unambiguous hand-drawn
  check mark that our pipeline correctly detects and correctly classifies
  `checked` — `chatgpt2.json` has no box there at all, a real miss on its
  side.

Separately, `docs/chatgpt.json` (123 boxes, count close to appraisal-1's
118/~125) could not be reliably matched against any of our 4 samples by
coordinate — its bounding boxes don't align to any consistent pixel scale
of appraisal-1 or appraisal-3, consistent with LLM-vision-estimated
coordinates rather than pixel-grounded ones. Not usable for a precise
per-box comparison the way `chatgpt2.json` was; not investigated further.

**Conclusion:** both `docs/chatgpt.json` and `docs/chatgpt2.json` are useful
as a source of *candidate blind spots to check* (that's how #1 was found),
never as trusted ground truth — every discrepancy must be verified visually
against the actual source image before treating either side as "right,"
the same standard applied to every other finding in this document and
throughout this project.

## Status as of 2026-08-17

| # | Issue | Status |
|---|---|---|
| 1 | Faint/light-gray borders (2 cases on appraisal-2) | **Open** — genuinely faint on all 4 sides, no threshold parameter fix found |
| 2 | Shaded table-row backgrounds (appraisal-3, 18 boxes) | **Fixed** — adaptive threshold, 0 regressions |
| 3 | Thin X-mark ink-ratio near-misses (appraisal-1, 4 boxes) | **Fixed** — diagonal-normalized ink density, +1 bonus fix, 0 regressions |
| 4 | Ink-blob shape gate | Open, no real instance yet |
| 5 | appraisal-1 118 vs ~125 | **Closed** — full visual audit found 0 missing/spurious boxes; "~125" has no verifiable source and isn't ground truth |
| 6 | Classification on shaded backgrounds | **Fixed**, same change as #2 |
| 7 | Context/label awareness (OCR) | Open, not investigated |
| 8 | Hand-drawn shape mimicking a checkbox silhouette | **Fixed** — rectangularity check, 0 regressions (after a caught-and-corrected first attempt) |
| 9 | "No Zoning" scratch-line box still undetected | **Open** — failure mode changed (fragmentation, not fusion), 4 fix attempts rejected (morphological closing; global Hough rectangle reconstruction; diagonal-stroke removal, 3 variants; fragment-clustering local scoping) — path forward is via #7 |
| 10 | "Electricity" burst-pattern mark obscures border on all sides | **Open** — 1 fix attempt rejected as too destructive |

Current counts after #2/#3/#6/#8: appraisal-1 118/**37 checked** (was 33),
appraisal-2 **40/16 checked** (was 41/17 — #8 removed the fake-shape false
positive), appraisal-3 48/12 checked (unchanged since #2/#6), appraisal-4
79/**28 checked** (was 29 — #3's bonus fix corrected one false positive).
Full pytest suite: 35/35 passing.

## Remaining working order

1. **#7 (context/label awareness)** — promoted to active work 2026-08-17.
   Four rejected #9 attempts converged on the same conclusion: further
   page-wide pixel/geometry heuristics are the wrong tool, and the one
   promising scoping signal found (a gap in an already-detected checkbox
   row's rhythm) is naturally a piece of this issue, not a standalone fix.
   Being designed as its own feature (see design doc once written) rather
   than folded into #9's backlog entry.
2. **#4 (ink-blob shape gate)** — needs a synthetic reproduction case
   first, since no real instance exists yet in the sample set.
3. **#9 ("No Zoning")** — highest real-world value (the project's original
   motivating case), but four structurally different techniques are now
   rejected on evidence (see issue body). Next attempt should build on
   whatever #7 produces (row/column context) rather than another
   standalone heuristic. Also still carries the README documentation-gap
   task (its existing explanation is stale).
4. **#10 ("Electricity" burst pattern)** — same technique gap as #9, lower
   priority given it's a single, unusual instance.
5. **#1** — no clear next step; would need a fundamentally different
   technique (edge/gradient detection, or morphological gap-bridging) than
   anything else in this document, not a parameter change. Lowest priority
   unless more instances turn up (only 2 known).

Every fix here should follow the project's established pattern: reproduce
concretely (pixel data or a synthetic case, not assertion), fix, add a
regression test, re-run the full local sample calibration (not just the
unit suite) to confirm no new false positive/negative was introduced on
the other 3 samples, then redeploy and verify live before considering it
done. **#8 is a direct example of why the full-sample re-run step is
non-negotiable** — the first threshold choice looked safe by every check
available at the time (validated against the one known bad example with
what seemed like wide margin) and still turned out to reject a real box;
only the full re-run caught it.
