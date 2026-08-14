# Checkbox Detection: Python CV Pipeline

## Context / motivation

The current `POST /detect` implementation calls AWS Textract's `AnalyzeDocument`
(`FeatureTypes=["FORMS"]`) from a Go Lambda and maps `SELECTION_ELEMENT` blocks
to the API's `bbox`/`is_checked` contract. Manual review of the annotated
detections against the 4 sample appraisal images (see
`samples/results/*-annotated.png`) found two concrete failure classes:

1. **False positive from an unrelated mark.** On `appraisal-2.png`, a stray
   diagonal scratch/strike-through line crosses several lines of paragraph
   text and happens to pass near the "No Zoning" checkbox. Textract
   classified that checkbox as `SELECTED` even though the user never marked
   it — the mark isn't a checkbox selection at all.
2. **False negative under visual noise.** On `appraisal-4.png`, the entire
   "Utilities" checkbox grid (6 boxes: Electricity/Gas/Water/Sanitary Sewer,
   4 of them marked with an X) has zero detections. A diagonal "trial
   version" watermark overlaps that region, and Textract's form model
   appears to have dropped the whole cluster rather than degrading
   gracefully.

Textract's `SELECTION_ELEMENT` detector is a black box: it can't be tuned,
inspected, or corrected when it fails in either direction. The Chromium/CV
literature and a prior exploratory session (recorded in `docs/chatgpt.md`)
both suggest classic computer vision (contour detection + geometric
filtering + interior ink-density classification) is a well-understood,
tunable alternative for exactly this kind of dense, printed-form checkbox
detection.

Reference: `docs/chatgpt.md` is a reconstructed pipeline from a prior
ChatGPT session that reproduced good results on one sample page with this
approach. Treated here as a strong methodology guide, not a spec to follow
verbatim — this design deliberately adds a containment check
(see "Checked/unchecked classification" below) that the reference doc
didn't fully solve, and doesn't target its specific detection count (123
boxes on a differently-sized page) as a goal.

## Goals

- Detect and classify checkboxes on printed forms (initially: the 4 sample
  appraisal documents) at least as well as the Textract implementation,
  and specifically fix the two failure cases above.
- Same API contract: `{"boxes": [{"bbox": [x1,y1,x2,y2], "is_checked": bool}]}`.
- Local-first development: tune the algorithm against sample images with a
  fast local loop before touching AWS at all.
- Keep the existing Textract path deployed and working (moved to
  `POST /detect-textract`) for side-by-side comparison — not deleted.

## Non-goals

- OCR, form-field extraction, or anything beyond checkbox geometry +
  checked state.
- ML training / learned detectors. This is classic CV only.
- Handling arbitrary non-form document layouts well. Tuned for dense
  printed forms like the sample set; other document types are out of
  scope until they're actually encountered.
- Deciding whether to eventually retire `/detect-textract` — deferred
  until there's enough side-by-side data to justify it.

## Architecture

```
Phase 1 (local, no AWS):
  lambda/detect-cv/cli.py <image_or_folder>
    -> lambda/detect-cv/detect_cv.py: detect_checkboxes(image_bytes) -> list[Box]
    -> samples/results/<name>-cv.json
    -> samples/results/<name>-cv-annotated.png   (drawn directly via cv2.rectangle)

Phase 2 (AWS):
  API Gateway --POST /detect--------> Lambda "detect-cv" (Python, container image, ECR)
                                         -> same detect_cv.py, wrapped by handler.py
  API Gateway --POST /detect-textract-> Lambda "detect" (Go, unchanged)
                                         -> Textract AnalyzeDocument
```

Both Lambdas are managed by the same Terraform config, live behind the same
API Gateway HTTP API, on separate routes. The Go/Textract Lambda's code and
behavior do not change — only its route path moves.

## Algorithm design

Core pipeline (`detect_cv.py`):

```
grayscale
  -> threshold (binary; start with global threshold, adaptiveThreshold as
     fallback if the clean-scan assumption doesn't hold on other documents)
  -> findContours(RETR_TREE, CHAIN_APPROX_SIMPLE)
  -> for each contour:
       boundingRect -> x, y, w, h
       aspect_ratio = w / h, filtered to an approximately-square band
       approxPolyDP corner count, filtered to ~4 corners
       size filtered to a checkbox-plausible px range
     -> candidate checkboxes
  -> IoU-based deduplication (checkbox borders often yield nested outer +
     inner contours for the same physical box; drop high-IoU duplicates,
     keeping the outer/best candidate)
  -> for each remaining candidate, classify checked/unchecked:
       1. crop interior with a small margin (excludes the box's own
          border from the ink measurement)
       2. compute ink ratio (fraction of dark pixels) in the interior
       3. if ink ratio is negligible -> unchecked (fast path, no further
          analysis needed)
       4. otherwise, run connectedComponentsWithStats on a padded region
          around the box (~2x the box size in each direction)
       5. take the connected component(s) overlapping the box interior;
          measure that component's own bounding box
       6. if the component's extent is much larger than the checkbox's
          own box (width or height > ~1.5-2x the box size) -> it belongs
          to a stroke that extends well beyond this box (e.g. an
          unrelated long line passing through) -> classify unchecked
       7. otherwise the mark is contained within/near the box -> checked
  -> sort boxes top-to-bottom, left-to-right (stable, human-readable order)
  -> [{"bbox": [x1,y1,x2,y2], "is_checked": bool}, ...]
```

Step 4-7 (the containment check) is the direct fix for the appraisal-2
false positive: a stray line crossing through a box's interior is not, by
itself, evidence of a checkbox selection — what distinguishes a real mark
is that it's a stroke sized to fit inside the box, not a fragment of a much
longer line. This is deliberately a containment/extent test rather than
Hough-line diagonal detection, since it doesn't assume a real mark has to
look like an "X" (a checkmark or filled square should also pass) and it
tests the actual failure mode observed rather than a proxy for it.

**Tunable constants** (top of `detect_cv.py`, calibrated against `samples/`,
not hardcoded blind): binarization threshold, min/max box size in px,
aspect-ratio band, `approxPolyDP` corner-count tolerance, IoU dedup
threshold, ink-ratio cutoff for "has a mark," component-to-box extent
ratio for the containment check.

**Held in reserve, not built now:** Canny + `HoughLinesP` diagonal-stroke
detection as an additional classification signal, if the containment check
alone proves insufficient once tested against more/harder documents than
the 4 samples.

## Local prototyping tool

New directory, structured to become the Lambda source directly in Phase 2:

```
lambda/detect-cv/
  detect_cv.py       # core pipeline: detect_checkboxes(image_bytes) -> list[Box]
  cli.py              # local-only entry point (not deployed)
  requirements.txt    # opencv-python-headless, numpy
```

`cli.py <image_or_folder>`: accepts a single image path or a folder (same
shape as `scripts/call_detect_api.sh`, for consistency). For each image,
runs `detect_checkboxes`, then writes:

- `samples/results/<name>-cv.json` — the raw detection result
- `samples/results/<name>-cv-annotated.png` — boxes drawn directly via
  `cv2.rectangle` (green = checked, red = unchecked)

The `-cv` suffix keeps these alongside the existing Textract-derived
`samples/results/<name>.json` / `<name>-annotated.png` without clobbering
them, so the two detectors' outputs can be diffed/compared directly.

**Tuning loop:** run `cli.py samples`, eyeball the annotated PNGs, adjust
constants in `detect_cv.py`, repeat.

**Exit criteria for Phase 1** (must hold before starting Phase 2):

- All 4 sample images produce visually correct annotated output on manual
  review (same bar as the original Textract review).
- The appraisal-2 scratch no longer produces a false-positive "checked" on
  the "No Zoning" box.
- The appraisal-4 Utilities grid checkboxes are detected (all 6, correctly
  classified).
- No unexplained wild divergence from the Textract counts — not required
  to match, but any large gap should be visually justified, not silently
  accepted.

## AWS deployment (Phase 2)

**Lambda handler** (`lambda/detect-cv/handler.py`, added once Phase 1 exit
criteria are met): parses the API Gateway v2 proxy event (raw or
`multipart/form-data` body, respecting `isBase64Encoded`), calls
`detect_checkboxes()`, returns `200` with the JSON body on success. Error
mapping mirrors the Go Lambda: undecodable/malformed image -> `400`;
unexpected pipeline exception -> `500` with a generic message (detail goes
to CloudWatch Logs only). Multipart parsing uses the `python-multipart`
package rather than a hand-rolled parser — Python's old stdlib option
(`cgi.FieldStorage`) is deprecated/removed as of 3.13, unlike Go's built-in
`mime/multipart`.

**Container image, not zip.** `opencv-python-headless` needs native shared
libraries, so this Lambda must be `package_type = "Image"`: a `Dockerfile`
in `lambda/detect-cv/` based on the AWS public Python Lambda base image,
`pip install -r requirements.txt`, copy the code in.

**Terraform additions** (`infra/`):

- `aws_ecr_repository` for the image.
- Build/push step: a `null_resource` + `local-exec` (docker build, tag,
  `aws ecr get-login-password` + push), triggered on a hash of the Python
  source — mirrors the existing pattern used to build the Go binary, since
  Terraform can't build Docker images natively.
- `aws_lambda_function "detect_cv"`, `image_uri` pointing at the pushed
  image, with its **own minimal IAM execution role**: just
  `AWSLambdaBasicExecutionRole` for CloudWatch Logs. No Textract, no other
  AWS API access needed at runtime — tighter than the Go Lambda's role.
- Route changes, applied together in one `terraform apply` (no window
  where `/detect` is unrouted):
  - existing `POST /detect` -> `POST /detect-textract` (same Go Lambda,
    integration unchanged, only the route path moves)
  - new `POST /detect` -> the new `detect-cv` Lambda
- `infra/bootstrap-iam-policy.json`: add ECR permissions (create/describe
  repo, push/pull image layers) scoped to a `document-scanner-*`-prefixed
  repo name, matching the least-privilege pattern already used for every
  other resource type in that policy.

**New local prerequisite:** Docker. Not asdf-managed (asdf pins
language/CLI tool versions; a container runtime is a different kind of
dependency) — documented as a normal install (e.g. Docker Desktop) in the
README.

Suggested initial Lambda config: `memory_size = 1024` (OpenCV + numpy need
more headroom than the Go Lambda's 512 MB), `timeout = 30` (contour
detection on these images is expected to be well under a second once
warm; the budget is mostly for container cold starts).

## Testing & validation

**Phase 1 (local):** `cli.py` against `samples/`, visual review of the
annotated PNGs against the exit criteria above. This is where algorithm
correctness is established.

**Phase 2 (live):** `scripts/call_detect_api.sh samples <detect-cv-endpoint>`
(already accepts an explicit endpoint override, no script changes needed)
to confirm the deployed container reproduces the same results as the local
run. This step validates deployment plumbing (correct dependency versions
baked into the image, HTTP contract, cold-start latency within timeout) —
it is not re-validating the algorithm, since it's the same code path.
Also re-run the bad-input checks already used against the Go Lambda
(garbage bytes, empty body -> expect `400`, not `500`).

**Error handling:** undecodable/malformed image -> `400`; unexpected
pipeline exception -> `500` (generic message, detail in logs only); zero
checkboxes found is a valid `200` with `"boxes": []`, not an error state.

## Documentation

`README.md` updates once Phase 2 is deployed: both endpoints documented
(`/detect` = CV, `/detect-textract` = Textract, kept for comparison), new
Docker prerequisite, updated testing instructions, refreshed Next steps.

## Open questions / explicit follow-ups (not blocking this design)

- Whether the containment check alone is sufficient, or whether the
  reserved Hough-diagonal-detection signal ends up needed — decide after
  testing against the samples, don't build it speculatively.
- Long-term fate of `/detect-textract` (keep permanently vs. retire) —
  deferred until there's real comparison data.
- Behavior on documents very different from the sample set (handwritten
  forms, non-Latin text, very low-DPI scans) — out of scope until such a
  document is actually encountered.
