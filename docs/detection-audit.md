# Detection audit: known-bad boxes

A concrete, per-box ledger of every checkbox on the 4 real samples
(`samples/appraisal-{1,2,3,4}.png`) confirmed — by pixel data or visual
crop, never by assertion — to be either **missed entirely** (not detected
as a candidate at all) or **misclassified** (detected, but `is_checked`
wrong). This is the instance-level companion to
`docs/algorithm-known-issues.md`, which covers root causes and code-level
fixes; this file tracks the concrete boxes those fixes are (or aren't yet)
accountable for. Every entry links back to the issue # that explains it.

**Coverage — read before trusting a blank row as "no problem":** every
entry below was found via a specific comparison, not an exhaustive manual
review of every checkbox on every page:

- All 4 samples were cross-checked against Textract's `/detect-textract`
  output (Task 17), which surfaces disagreements but is not ground truth
  itself — Textract has its own known false positive (the original
  scratch-mark bug, still live, see "Reference-data caveat" in
  `algorithm-known-issues.md`).
- `appraisal-2.png` has additionally been cross-checked against a second,
  independent source (`docs/chatgpt2.json`, a ChatGPT vision detection)
  and had a **direct manual review by the project owner** against the
  rendered source image (2026-08-17, 4 findings, 3 real + 1 confirmed
  false positive — see its table below).
- `appraisal-1.png` has had one full manual visual audit of the rendered
  `-annotated.png` output (2026-08-17, by the assistant, not the project
  owner — a lower bar than a source-image review) confirming every visible
  checkbox has a drawn box and no spurious boxes appear; see the closed #5
  row below. `docs/chatgpt.json` (123 boxes) was also checked as a
  possible second cross-check source and ruled out — its coordinates
  don't align to any of our samples at any tested scale factor.
- All 4 samples are now also cross-checked, every `/detect` call, against
  live Textract FORMS analysis (issue #7's gap-recovery pass) — this
  surfaces any label Textract associates with a checkbox that our own
  pixel pass missed, and folds the recoverable ones straight into the
  result rather than just reporting them here. appraisal-1/3/4 show 0
  such gaps as of 2026-08-17 (consistent with, but not the same
  guarantee as, a manual review — it only catches misses Textract's own
  form model also happens to resolve a checkbox for).
  `appraisal-3/4` have not had a manual source-image review yet — that
  could still surface more misses, the way it did for appraisal-2.
- No sample has had a full manual box-by-box review **against the source
  image** other than appraisal-2. **Absence from this list means "not yet
  found," not "confirmed correct."**

## appraisal-1.png (2550x4200, target ~125 real checkboxes)

| bbox | Problem | Status | Issue |
|---|---|---|---|
| `[303,1405,359,1452]` | False negative (ink density 0.075, just under old 0.08 threshold) | **Fixed** 2026-08-16 | [#3](algorithm-known-issues.md#3-thin-diagonal-stroke-x-marks-fail-the-ink-ratio-threshold-by-a-hair) |
| `[303,1354,359,1400]` | False negative (0.077) | **Fixed** 2026-08-16 | #3 |
| `[346,2207,401,2252]` | False negative (0.075) | **Fixed** 2026-08-16 | #3 |
| `[853,3108,909,3153]` | False negative (0.075) | **Fixed** 2026-08-16 | #3 |
| n/a | Suspected candidate detection miss (118 found vs ~125 estimated) | **Closed** 2026-08-17 — full manual visual audit of the entire page (all 6 sections) found 0 missing/spurious boxes; the "~125" figure has no verifiable source (checked and ruled out `docs/chatgpt.json` as its origin) and isn't ground truth | [#5](algorithm-known-issues.md#5-appraisal-1-118-detected-boxes-vs-an-estimated-125) |

## appraisal-2.png (1586x846)

| bbox | Problem | Status | Issue |
|---|---|---|---|
| `[281,187,307,210]` ("Neighborhood Boundaries") | Candidate detection miss — border ~gray 223, faint on all 4 sides | **Fixed** 2026-08-17 — Textract-hint gap recovery (#7); recovered at `[282,185,305,207]`, correctly unchecked, IoU 0.88 vs this estimate | [#1](algorithm-known-issues.md#1-global-binary-threshold-misses-faintlight-gray-checkbox-borders) |
| `[1098,491,1122,515]` | Candidate detection miss, same cause | **Open** — checked live 2026-08-17: Textract's own FORMS model has no resolved checkbox anywhere near here either, so #7 can't recover it; still needs a different technique | #1 |
| `~[715,490,738,514]` ("No Zoning") | Candidate detection miss — the project's original motivating case (scratch line through the box). Failure mode changed under adaptive thresholding: border fragments into 3-6-sided undersized shards (confirmed via raw binary mask), not the old single-oversized-blob fusion the README still describes | **Fixed** 2026-08-17 — Textract-hint gap recovery (#7), after 4 rejected pixel-only attempts; recovered at `[712,491,735,514]`, correctly unchecked despite the scratch, IoU 0.88 vs this estimate | [#9](algorithm-known-issues.md#9-no-zoning-scratch-line-box-still-not-detected-failure-mode-changed) |
| `~[196,613,220,637]` ("Electricity" / "Public") | Candidate detection miss — border intact but an unusual radiating "burst" mark pattern touches it on all 4 sides, merging into one 8-corner, extent-0.214 blob | **Fixed** 2026-08-17 — Textract-hint gap recovery (#7); recovered at `[195,612,218,634]`, correctly unchecked, IoU 0.84 vs this estimate | [#10](algorithm-known-issues.md#10-checkbox-border-obscured-on-all-sides-by-an-unusual-burst-mark-pattern) |
| `[809,613,838,636]` (near "Other (describe)") | False positive — a hand-drawn shape (not a real printed checkbox) whose outer silhouette happened to pass every geometric filter; previously misdescribed in this project's own Task 17 notes as "a real, unambiguous hand-drawn check mark" — it is not a real form checkbox at all, confirmed by the project owner | **Fixed** 2026-08-17 — rectangularity check (side-length ratio + corner-angle deviation) | [#8](algorithm-known-issues.md#8-hand-drawn-shapes-can-mimic-a-checkboxs-4-corner-correct-extent-silhouette) |

## appraisal-3.png (2550x4200)

| bbox | Problem | Status | Issue |
|---|---|---|---|
| 18 boxes across 2 shaded tables (`y:880-1270` and `y:2780-2900`, `x:1740-2340`) — see `algorithm-known-issues.md` #2 for the full coordinate list | Candidate detection miss — shaded row background (gray 184) fused with border under the old global threshold | **Fixed** 2026-08-16, all 18 individually visually verified checked/unchecked correct | [#2](algorithm-known-issues.md#2-shadednon-white-table-row-backgrounds-erase-checkbox-borders-entirely) / [#6](algorithm-known-issues.md#6-classification-checkedunchecked-still-breaks-on-shaded-backgrounds) |

## appraisal-4.png (2550x3301)

| bbox | Problem | Status | Issue |
|---|---|---|---|
| `[1126,2083,1158,2114]` | Candidate detection miss — fused with adjacent "D" character | **Fixed** 2026-08-16 | #2 |
| `[464,1100,494,1131]` | Candidate detection miss, same cause | **Fixed** 2026-08-16 | #2 |
| `[2045,944,2079,977]` | False positive — genuinely empty box, old area-based ink ratio (0.085) read noise as a mark | **Fixed** 2026-08-16 (found as a side effect of #3's fix, not separately searched for) | #3 |

## No confirmed instance yet

- **Ink-blob shape gate** ([#4](algorithm-known-issues.md#4-isolated-ink-blobs-pass-the-containment-check-with-no-shape-requirement)): a real, principled gap (isolated dust speck / print artifact fully contained in a box would classify `checked`), but no real box on any of the 4 samples has been found exhibiting it. Needs a synthetic reproduction, or a real example if one turns up.

## How to add a new finding

When a new miss or misclassification is found (new sample, new comparison
source, or manual review): add a row to the relevant sample's table with
the bbox, a one-line description of the problem, `Open` status, and either
a link to an existing issue in `algorithm-known-issues.md` or a note that
it's new (write up the root cause there before fixing, following this
project's established pattern — reproduce concretely, don't assert). When
fixed: update the row's status to `**Fixed** <date>`, keep the row rather
than deleting it, and update the relevant issue's status table in
`algorithm-known-issues.md` to match.
