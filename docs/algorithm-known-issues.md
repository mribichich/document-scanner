# CV algorithm: known issues and misses

Working backlog for `lambda/detect/detect_cv.py`. Every item was reproduced
against the real files in `samples/` and pixel-traced to a specific line of
code — nothing here is a guess. Fixed issues are kept as a one-line closure
note (with their original heading, so existing links elsewhere still
resolve) rather than the full investigation — the detail behind a fixed
issue lives in git history and commit messages, not here.

## Open issues, ranked by concrete evidence found so far

### 1. Global binary threshold misses faint/light-gray checkbox borders

**Where:** `find_checkbox_candidates` (`_adaptive_binary`).

**Status: partially fixed.** Two known instances on `appraisal-2.png`, both
with border pixels lighter than any brightness threshold reliably
distinguishes from paper white:

- `[281,187,307,210]` ("Neighborhood Boundaries") — **recovered** via issue
  #7's Textract-hint gap recovery (Textract's own FORMS analysis resolves a
  checkbox for this label even though pixel thresholding can't find the box
  itself).
- `[1098,491,1122,515]` — **still open.** Textract's own FORMS model doesn't
  resolve a checkbox here either, so #7 can't help. Tested a full grid of
  adaptive-threshold blockSize (25/31/41/51/61) × C (5/8/10/12) — no
  combination reliably finds it. Needs a fundamentally different technique
  (e.g. edge/gradient-based detection, or morphological gap-bridging), not
  further threshold tuning. Lowest priority: only 1 known instance.

### 2. Shaded/non-white table-row backgrounds erase checkbox borders entirely

**Fixed 2026-08-16.** Replaced the global `BINARY_THRESHOLD`/`cv2.threshold`
with adaptive thresholding (`_adaptive_binary`, `cv2.adaptiveThreshold`).
Recovered all 18 previously-missed boxes on `appraisal-3.png`'s shaded
table rows, 0 regressions across all 4 samples.

### 3. Thin diagonal-stroke X-marks fail the ink-ratio threshold by a hair

**Fixed 2026-08-16.** Replaced the area-based `_ink_ratio` (not
scale-invariant) with diagonal-normalized `_ink_density`
(`INK_DENSITY_THRESHOLD = 1.5`). Recovered 4 true positives on
`appraisal-1.png` plus 1 bonus true-negative fix, 0 regressions.

### 4. Isolated ink blobs pass the containment check with no shape requirement

**Where:** `is_checked` / `_mark_is_contained`.

**Status: open, no real instance found yet.** Classification requires ink
coverage and that the touching component falls mostly inside the box, but
has no shape/structure requirement — an isolated dust speck, hole-punch
shadow, or print artifact, fully contained and dense enough, would
currently classify `checked`. No concrete instance has turned up in the 4
real samples (unlike every other issue in this doc, which are all
reproduced against real data). Needs either a synthetic test case proving
the current behavior, or a real example if one turns up.

### 5. appraisal-1: 118 detected boxes vs. an estimated ~125

**Closed 2026-08-17 — not a real gap.** A full manual visual audit of the
entire rendered page (all sections) found every checkbox correctly boxed
and no spurious boxes. The "~125" estimate had no verifiable source. 118 is
the correct count for this page; reopen only if a specific missing bbox is
ever identified.

### 6. Classification (checked/unchecked) still breaks on shaded backgrounds

**Fixed 2026-08-16,** landed together with #2: classification now reuses
the same pre-thresholded adaptive-binary array as candidate detection
instead of a separate per-region global threshold. Verified zero
classification differences across all 266 previously-found boxes, plus all
18 newly-recovered shaded-row boxes individually confirmed correct.

### 7. No context/label awareness — detection is purely geometric today

**Where:** the whole pipeline.

**Status: fixed (level 2, additive only), one sub-item still open.**
`textract_hints.py` calls Textract's FORMS analysis purely for
label→checkbox location hints — **never** for Textract's own
checked/unchecked call, which has the false-positive failure mode (a stray
mark crossing a box's border reads as "selected") that motivated this
project's CV pivot in the first place. `find_missing_boxes()` diffs those
hints against the CV pipeline's own candidates and recovers anything
missing via a small, scoped local search. This pass can only add a
candidate the pixel pipeline missed, never remove or re-classify one it
already found.

Live-validated against all 4 samples: recovered issue #1's
`[281,187,307,210]`, issue #9's "No Zoning" scratch-line box (the project's
original motivating case), and issue #10's "Electricity" burst-mark box —
0 regressions. Level 1 (flagging a detected box with no nearby label as a
likely false positive) was explicitly scoped out: Textract's own checkbox
recognition misses too many of the CV pipeline's own correctly-detected
boxes (20/118 on `appraisal-1.png` alone) to safely use as a removal
signal.

**Still open:** an LLM fallback for a label Textract's own form model never
resolves any checkbox for at all (`Hint(bbox=None)` — e.g. "Other
(describe)" on `appraisal-2.png` hits exactly this case in live data).
`find_missing_boxes()` currently just skips these. Would need an LLM call
given the label text + a nearby crop, asked only "is there likely a
checkbox near here, and roughly where" — its answer would feed into the
same accept/reject local-repair logic `_recover_hint_candidate` already
has, not be trusted directly.

### 8. Hand-drawn shapes can mimic a checkbox's 4-corner, correct-extent silhouette

**Fixed 2026-08-17.** Added `_is_rectangular()`: rejects a candidate
quadrilateral if its opposite-side length ratio or corner-angle deviation
falls outside a printed checkbox's real range
(`MIN_RECTANGLE_SIDE_RATIO = 0.8`, `MAX_RECTANGLE_ANGLE_DEVIATION_DEGREES =
10.0`). 0 regressions across all 4 samples after a corrected first
threshold attempt (an initial tighter pair wrongly rejected a real,
slightly imperfect checkbox on `appraisal-4.png`).

### 9. "No Zoning" scratch-line box: still not detected, failure mode changed

**Fixed 2026-08-17,** via issue #7's Textract-hint gap recovery — after 4
rejected pixel-only attempts (morphological closing; global Hough-line
rectangle reconstruction; diagonal-stroke removal in 3 filter variants;
fragment-clustering local scoping — all tried and rejected on concrete
evidence). This is the box behind the original Textract false positive
that motivated this entire CV pivot; it's now recovered with the correct
(unchecked) classification despite the scratch line running through it.

### 10. Checkbox border obscured on all sides by an unusual "burst" mark pattern

**Fixed 2026-08-17,** same mechanism as #9 (issue #7's Textract-hint gap
recovery). 0 regressions across all 4 samples.

## Status

| # | Issue | Status |
|---|---|---|
| 1 | Faint/light-gray borders (2 cases on appraisal-2) | **Partially fixed** — 1/2 recovered via #7; the other has no Textract-resolved label nearby either, still genuinely open |
| 2 | Shaded table-row backgrounds (appraisal-3, 18 boxes) | **Fixed** |
| 3 | Thin X-mark ink-ratio near-misses (appraisal-1, 4 boxes) | **Fixed** |
| 4 | Ink-blob shape gate | Open, no real instance yet |
| 5 | appraisal-1 118 vs ~125 | **Closed** — not a real gap |
| 6 | Classification on shaded backgrounds | **Fixed**, same change as #2 |
| 7 | Context/label awareness (Textract-hint gap recovery) | **Fixed (level 2, additive)** — LLM fallback for Textract-unresolved labels not yet built |
| 8 | Hand-drawn shape mimicking a checkbox silhouette | **Fixed** |
| 9 | "No Zoning" scratch-line box still undetected | **Fixed**, via #7 |
| 10 | "Electricity" burst-pattern mark obscures border on all sides | **Fixed**, via #7 |

Current counts: appraisal-1 118/37 checked, appraisal-2 43/16 checked,
appraisal-3 48/12 checked, appraisal-4 79/28 checked (see README's "Test
against the sample appraisal documents" for the full table). Full pytest
suite passing.

## Remaining working order

1. **#4 (ink-blob shape gate)** — needs a synthetic reproduction case
   first, since no real instance exists yet in the sample set.
2. **#7's LLM fallback** — for a Textract `KEY` with no resolved
   `SELECTION_ELEMENT` at all (e.g. "Other (describe)" on appraisal-2).
3. **#1's remaining instance** (`[1098,491,1122,515]`) — needs the
   fundamentally different technique (edge/gradient detection, or
   morphological gap-bridging) this issue always needed. Lowest priority,
   only 1 instance left.

Every fix here should follow the project's established pattern: reproduce
concretely (pixel data or a synthetic case, not assertion), fix, add a
regression test, re-run the full local sample calibration (not just the
unit suite) to confirm no new false positive/negative was introduced on
the other 3 samples, then redeploy and verify live before considering it
done.
