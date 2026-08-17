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
reaches. Still open, still unexplained; would need a fresh pixel-level
investigation on appraisal-1 specifically (find where the ~7 missing boxes
would be, same method as #1/#2/#3), not assumed fixed by association.

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

## Status as of 2026-08-16

| # | Issue | Status |
|---|---|---|
| 1 | Faint/light-gray borders (2 cases on appraisal-2) | **Open** — genuinely faint on all 4 sides, no threshold parameter fix found |
| 2 | Shaded table-row backgrounds (appraisal-3, 18 boxes) | **Fixed** — adaptive threshold, 0 regressions |
| 3 | Thin X-mark ink-ratio near-misses (appraisal-1, 4 boxes) | **Fixed** — diagonal-normalized ink density, +1 bonus fix, 0 regressions |
| 4 | Ink-blob shape gate | Open, no real instance yet |
| 5 | appraisal-1 118 vs ~125 | Open — re-checked after #2, unaffected, still unexplained |
| 6 | Classification on shaded backgrounds | **Fixed**, same change as #2 |
| 7 | Context/label awareness (OCR) | Open, not investigated |

Current counts after #2/#3/#6: appraisal-1 118/**37 checked** (was 33),
appraisal-2 41/17 checked (unchanged), appraisal-3 48/12 checked
(unchanged since #2/#6), appraisal-4 79/**28 checked** (was 29 — #3's
bonus fix corrected one false positive). Full pytest suite: 34/34 passing.

## Remaining working order

1. **#4 (ink-blob shape gate)** — needs a synthetic reproduction case
   first, since no real instance exists yet in the sample set.
2. **#5** — needs fresh investigation on appraisal-1 specifically; #2
   didn't move this count, so the working theory (gridline fusion) needs
   re-examination, not just a re-run.
3. **#1** — no clear next step; would need a fundamentally different
   technique (edge/gradient detection, or morphological gap-bridging) than
   anything else in this document, not a parameter change. Lowest priority
   unless more instances turn up (only 2 known).
4. **#7 (context/label awareness)** — separate, larger investigation, not
   blocked on or blocking anything above.

Every fix here should follow the project's established pattern: reproduce
concretely (pixel data or a synthetic case, not assertion), fix, add a
regression test, re-run the full local sample calibration (not just the
unit suite) to confirm no new false positive/negative was introduced on
the other 3 samples, then redeploy and verify live before considering it
done.
