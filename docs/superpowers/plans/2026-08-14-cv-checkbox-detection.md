# Python CV Checkbox Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AWS Textract with a Python/OpenCV classic computer-vision pipeline as the checkbox detector behind `POST /detect`, while keeping the existing Go/Textract implementation running unchanged at `POST /detect-textract` for comparison.

**Architecture:** A new `lambda/detect-cv/` Python module detects checkbox-shaped contours, filters/deduplicates them geometrically, and classifies each as checked/unchecked using an ink-ratio + containment check that rejects marks belonging to a stroke that extends well beyond the box (fixing a false positive from a stray scratch observed in Textract's output). Developed and tuned entirely locally against `samples/` first (Phase 1), then wrapped in a Lambda handler and deployed as a container-image Lambda behind a new API Gateway route (Phase 2). The existing Go Lambda's code and route path change only in name (`/detect` -> `/detect-textract`).

**Tech Stack:** Python 3.13, OpenCV (`opencv-python-headless`), NumPy, pytest, Docker, Terraform (existing `hashicorp/aws` provider), AWS Lambda (container image, arm64), API Gateway HTTP API (existing).

## Global Constraints

- API contract is fixed: `{"boxes": [{"bbox": [x1, y1, x2, y2], "is_checked": bool}]}`, `bbox` in absolute pixel coordinates, unchanged from the existing Go implementation.
- All new Python code lives under `lambda/detect-cv/`.
- `POST /detect` must end up routed to the new CV Lambda; `POST /detect-textract` must keep working exactly as `POST /detect` does today (same Go Lambda, same Textract logic, only the route path changes). Both routes must be live after the final `terraform apply` — no window where either is unrouted.
- The CV Lambda gets its own minimal IAM execution role (`AWSLambdaBasicExecutionRole` only — no Textract, no other AWS API access needed at runtime).
- Error mapping mirrors the existing Go Lambda: undecodable/malformed image input -> `400`; unexpected internal error -> `500` (generic message in the response body, detail in CloudWatch Logs only); zero boxes found is a valid `200` with `"boxes": []`.
- **Do not run `git commit` at any point in this plan.** Leave all changes staged/unstaged for the user to review and commit themselves. (This overrides the default per-task commit step normally used by this workflow.)
- Tunable detection constants (thresholds, size ranges, ratios) live as module-level constants at the top of `detect_cv.py`, not hardcoded inline, so they're easy to find and retune.
- Reference material: `docs/chatgpt.md` (prior exploratory session, methodology guide only) and `docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md` (the approved design spec this plan implements — consult it for the "why" behind any task if something here seems underspecified).

---

### Task 1: Scaffolding + checkbox candidate detection

**Files:**
- Create: `lambda/detect-cv/requirements.txt`
- Create: `lambda/detect-cv/requirements-dev.txt`
- Create: `lambda/detect-cv/detect_cv.py`
- Create: `lambda/detect-cv/tests/__init__.py`
- Create: `lambda/detect-cv/tests/test_detect_cv.py`

**Interfaces:**
- Produces: `find_checkbox_candidates(gray: np.ndarray) -> list[tuple[int, int, int, int]]` — each tuple is `(x, y, w, h)` in pixel coordinates. Consumed by Task 2 and Task 4.
- Produces module constants: `BINARY_THRESHOLD`, `MIN_BOX_SIZE`, `MAX_BOX_SIZE`, `ASPECT_RATIO_MIN`, `ASPECT_RATIO_MAX`, `CORNER_EPSILON_FACTOR` (all in `detect_cv.py`).

- [ ] **Step 1: Create the package files and dependency lists**

`lambda/detect-cv/requirements.txt`:
```
opencv-python-headless
numpy
```

`lambda/detect-cv/requirements-dev.txt`:
```
-r requirements.txt
pytest
```

`lambda/detect-cv/tests/__init__.py`: empty file.

- [ ] **Step 2: Set up a local virtualenv and install dependencies**

Run:
```bash
cd lambda/detect-cv
python3 -m venv venv
venv/bin/pip install --quiet -r requirements-dev.txt
```

- [ ] **Step 3: Write the failing tests for `find_checkbox_candidates`**

`lambda/detect-cv/tests/test_detect_cv.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'detect_cv'` (file doesn't exist yet).

- [ ] **Step 5: Implement `find_checkbox_candidates`**

`lambda/detect-cv/detect_cv.py`:
```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -v`
Expected: PASS (5 tests).

---

### Task 2: IoU-based deduplication

**Files:**
- Modify: `lambda/detect-cv/detect_cv.py`
- Modify: `lambda/detect-cv/tests/test_detect_cv.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `deduplicate_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]`. Consumed by Task 4.
- Produces module constant: `IOU_DEDUP_THRESHOLD`.

- [ ] **Step 1: Write the failing tests**

Append to `lambda/detect-cv/tests/test_detect_cv.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -k dedup -v`
Expected: FAIL with `ImportError: cannot import name 'deduplicate_boxes'`.

- [ ] **Step 3: Implement `deduplicate_boxes`**

Append to `lambda/detect-cv/detect_cv.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -k dedup -v`
Expected: PASS (2 tests).

---

### Task 3: Checked/unchecked classification with containment check

**Files:**
- Modify: `lambda/detect-cv/detect_cv.py`
- Modify: `lambda/detect-cv/tests/test_detect_cv.py`

**Interfaces:**
- Consumes: nothing from other tasks (uses `BINARY_THRESHOLD` from Task 1).
- Produces: `is_checked(gray: np.ndarray, box: tuple[int, int, int, int]) -> bool`. Consumed by Task 4.
- Produces module constants: `INK_RATIO_THRESHOLD`, `INTERIOR_MARGIN`, `CONTAINMENT_PADDING_FACTOR`, `CONTAINMENT_EXTENT_RATIO`.

This is the fix for the false positive observed in Textract's output: a stray scratch line crossing through a checkbox's interior must not be classified as a check mark, because it belongs to a stroke that extends far beyond the box. The test for this (`test_unchecked_when_external_line_passes_through`) is the most important test in this task — it encodes the actual bug being fixed.

- [ ] **Step 1: Write the failing tests**

Append to `lambda/detect-cv/tests/test_detect_cv.py`:
```python
from detect_cv import is_checked


def test_unchecked_empty_box():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is False


def test_checked_box_with_contained_x_mark():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    cv2.line(gray, (53, 53), (67, 67), color=0, thickness=2)
    cv2.line(gray, (53, 67), (67, 53), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is True


def test_unchecked_when_external_line_passes_through():
    gray = make_blank_gray()
    cv2.rectangle(gray, (50, 50), (70, 70), color=0, thickness=2)
    # A long line spanning far beyond the box on both sides, incidentally
    # crossing straight through its interior — must NOT be read as a check.
    cv2.line(gray, (0, 60), (299, 60), color=0, thickness=2)

    assert is_checked(gray, (50, 50, 20, 20)) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -k checked -v`
Expected: FAIL with `ImportError: cannot import name 'is_checked'`.

- [ ] **Step 3: Implement `is_checked`**

Append to `lambda/detect-cv/detect_cv.py`:
```python
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

    box_x1 = x - region_x1
    box_y1 = y - region_y1
    box_x2 = box_x1 + w
    box_y2 = box_y1 + h

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -k checked -v`
Expected: PASS (3 tests).

---

### Task 4: End-to-end `detect_checkboxes` orchestrator

**Files:**
- Modify: `lambda/detect-cv/detect_cv.py`
- Modify: `lambda/detect-cv/tests/test_detect_cv.py`

**Interfaces:**
- Consumes: `find_checkbox_candidates` (Task 1), `deduplicate_boxes` (Task 2), `is_checked` (Task 3).
- Produces: `decode_image(image_bytes: bytes) -> np.ndarray` (raises `ValueError` if undecodable) and `detect_checkboxes(image_bytes: bytes) -> list[dict]`, where each dict is `{"bbox": [x1, y1, x2, y2], "is_checked": bool}`. Both consumed by Task 5 (`cli.py`) and Task 7 (`handler.py`).

- [ ] **Step 1: Write the failing tests**

Append to `lambda/detect-cv/tests/test_detect_cv.py`:
```python
import pytest

from detect_cv import detect_checkboxes


def test_detect_checkboxes_end_to_end():
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -k end_to_end -v`
Expected: FAIL with `ImportError: cannot import name 'detect_checkboxes'`.

- [ ] **Step 3: Implement `decode_image` and `detect_checkboxes`**

Append to `lambda/detect-cv/detect_cv.py`:
```python
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
```

- [ ] **Step 4: Run the full test suite to verify everything passes**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_detect_cv.py -v`
Expected: PASS (all tests from Tasks 1-4, 10 total).

---

### Task 5: Local CLI tool

**Files:**
- Create: `lambda/detect-cv/cli.py`
- Create: `lambda/detect-cv/tests/test_cli.py`

**Interfaces:**
- Consumes: `detect_checkboxes` (Task 4).
- Produces: `output_paths(image_path: Path, results_dir: Path) -> tuple[Path, Path]` (unit tested) and a `main()` CLI entry point (verified by manual run, not unit tested — it's I/O glue).

- [ ] **Step 1: Write the failing test for `output_paths`**

`lambda/detect-cv/tests/test_cli.py`:
```python
from pathlib import Path

from cli import output_paths


def test_output_paths_uses_cv_suffix():
    json_path, png_path = output_paths(
        Path("samples/appraisal-1.png"), Path("samples/results")
    )

    assert json_path == Path("samples/results/appraisal-1-cv.json")
    assert png_path == Path("samples/results/appraisal-1-cv-annotated.png")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cli'`.

- [ ] **Step 3: Implement `cli.py`**

`lambda/detect-cv/cli.py`:
```python
import json
import sys
from pathlib import Path

import cv2

from detect_cv import detect_checkboxes

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def output_paths(image_path: Path, results_dir: Path) -> tuple[Path, Path]:
    name = image_path.stem
    return (
        results_dir / f"{name}-cv.json",
        results_dir / f"{name}-cv-annotated.png",
    )


def process_image(image_path: Path, results_dir: Path) -> None:
    image_bytes = image_path.read_bytes()
    boxes = detect_checkboxes(image_bytes)

    json_path, png_path = output_paths(image_path, results_dir)
    json_path.write_text(json.dumps({"boxes": boxes}, indent=2))

    image = cv2.imread(str(image_path))
    for box in boxes:
        x1, y1, x2, y2 = box["bbox"]
        color = (0, 200, 0) if box["is_checked"] else (0, 0, 220)  # BGR
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 3)
    cv2.imwrite(str(png_path), image)

    checked = sum(1 for b in boxes if b["is_checked"])
    print(
        f"{image_path.name}: {len(boxes)} boxes "
        f"({checked} checked, {len(boxes) - checked} unchecked) "
        f"-> {json_path}, {png_path}"
    )


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 cli.py <image_or_folder>", file=sys.stderr)
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"Not found: {target}", file=sys.stderr)
        sys.exit(1)

    if target.is_dir():
        images = sorted(p for p in target.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
        results_dir = target / "results"
    else:
        images = [target]
        results_dir = target.parent / "results"

    if not images:
        print(f"No images found in {target}", file=sys.stderr)
        sys.exit(1)

    results_dir.mkdir(parents=True, exist_ok=True)
    for image_path in images:
        process_image(image_path, results_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Smoke-test the CLI against one real sample**

Run:
```bash
cd lambda/detect-cv
venv/bin/python3 cli.py ../../samples/appraisal-1.png
```
Expected: prints a summary line, and `samples/results/appraisal-1-cv.json` +
`samples/results/appraisal-1-cv-annotated.png` are created. Read the
annotated PNG (e.g. with the Read tool) to confirm boxes are drawn in
plausible positions — this is just a smoke test that the pipeline runs
end-to-end on a real image; thorough calibration happens in Task 6.

---

### Task 6: Calibration pass against all sample images

**Files:**
- Modify: `lambda/detect-cv/detect_cv.py` (constants only, if retuning is needed)

**Interfaces:**
- Consumes: `cli.py` (Task 5).
- Produces: nothing new — this is a validation/tuning gate, not new functionality. Must pass before starting Task 7.

- [ ] **Step 1: Run the CLI against all 4 samples**

Run:
```bash
cd lambda/detect-cv
venv/bin/python3 cli.py ../../samples
```
Expected: `samples/results/appraisal-{1,2,3,4}-cv.json` and
`samples/results/appraisal-{1,2,3,4}-cv-annotated.png` are all created.

- [ ] **Step 2: Visually verify the two known failure cases are fixed**

Read `samples/results/appraisal-2-cv-annotated.png` and confirm the "No
Zoning" checkbox (in the Zoning Compliance row) is **not** drawn green —
the diagonal scratch through the neighborhood description paragraph must
not be misread as a check mark on that box.

Read `samples/results/appraisal-4-cv-annotated.png` and confirm the
Utilities grid (Electricity/Gas/Water/Sanitary Sewer, ~6 boxes) **is**
detected, with the X-marked ones drawn green and the empty ones red.

- [ ] **Step 3: Visually verify overall correctness on all 4 samples**

Read all 4 `*-cv-annotated.png` files. For each: boxes should be tightly
fit around actual checkboxes (not table gridlines or text characters), and
green/red should match the visible marks. This is the same bar used for
the original Textract review.

- [ ] **Step 4: If any sample fails the checks above, retune and re-run**

Adjust the relevant constant(s) in `detect_cv.py` — likely candidates:
`MIN_BOX_SIZE`/`MAX_BOX_SIZE` (missed or spurious boxes of the wrong
size), `INK_RATIO_THRESHOLD` (missed/false marks), `CONTAINMENT_EXTENT_RATIO`
(too aggressive or too lenient rejection of external strokes). Re-run
Step 1 and re-check. Do not proceed to Task 7 until Step 2 and Step 3 both
pass — this is the Phase 1 exit gate from the design spec.

---

### Task 7: Lambda handler with multipart parsing and error mapping

**Files:**
- Create: `lambda/detect-cv/handler.py`
- Create: `lambda/detect-cv/tests/test_handler.py`

**Interfaces:**
- Consumes: `detect_checkboxes` (Task 4).
- Produces: `handler(event: dict, context) -> dict` — the Lambda entry point, returning an API Gateway v2 proxy response dict (`statusCode`, `headers`, `body`). Consumed by Task 8 (Dockerfile `CMD`).

Multipart parsing uses Python's stdlib `email` module (not a third-party
package): `multipart/form-data` is MIME multipart, so prefixing a
synthetic `Content-Type` header and handing the bytes to
`email.parser.BytesParser` lets the stdlib walk the parts. This avoids
adding a dependency beyond OpenCV/NumPy, matching the Go Lambda's use of
only its stdlib `mime/multipart`.

- [ ] **Step 1: Write the failing tests**

`lambda/detect-cv/tests/test_handler.py`:
```python
import base64
import json

import cv2
import numpy as np

from handler import _extract_image_bytes, handler


def _build_multipart_body(
    boundary: str, filename: str, content: bytes, field_name: str = "file"
) -> bytes:
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts)


def _tiny_png_bytes() -> bytes:
    gray = np.full((100, 100), 255, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", gray)
    assert success
    return encoded.tobytes()


def test_extracts_file_from_multipart_body():
    boundary = "TESTBOUNDARY"
    raw_body = _build_multipart_body(boundary, "test.png", b"\x89PNG-fake-binary-content")
    event = {
        "headers": {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        "body": base64.b64encode(raw_body).decode("ascii"),
        "isBase64Encoded": True,
    }

    result = _extract_image_bytes(event)

    assert result == b"\x89PNG-fake-binary-content"


def test_raises_on_missing_content_type():
    import pytest

    event = {"headers": {}, "body": "", "isBase64Encoded": False}

    with pytest.raises(ValueError):
        _extract_image_bytes(event)


def test_raw_body_returned_when_not_multipart():
    event = {
        "headers": {"content-type": "image/png"},
        "body": base64.b64encode(b"raw-png-bytes").decode("ascii"),
        "isBase64Encoded": True,
    }

    result = _extract_image_bytes(event)

    assert result == b"raw-png-bytes"


def test_handler_returns_200_for_valid_raw_image():
    event = {
        "headers": {"content-type": "image/png"},
        "body": base64.b64encode(_tiny_png_bytes()).decode("ascii"),
        "isBase64Encoded": True,
    }

    response = handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["boxes"] == []


def test_handler_returns_400_for_undecodable_body():
    event = {
        "headers": {"content-type": "image/png"},
        "body": base64.b64encode(b"not-an-image").decode("ascii"),
        "isBase64Encoded": True,
    }

    response = handler(event, None)

    assert response["statusCode"] == 400


def test_handler_returns_400_for_missing_content_type():
    event = {"headers": {}, "body": "", "isBase64Encoded": False}

    response = handler(event, None)

    assert response["statusCode"] == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'handler'`.

- [ ] **Step 3: Implement `handler.py`**

`lambda/detect-cv/handler.py`:
```python
import base64
import json
from email import policy
from email.parser import BytesParser

from detect_cv import detect_checkboxes


def _extract_image_bytes(event: dict) -> bytes:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    content_type = headers.get("content-type", "")
    if not content_type:
        raise ValueError("missing Content-Type header")

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(body)
    else:
        raw_body = body.encode("utf-8") if isinstance(body, str) else body

    if not content_type.startswith("multipart/"):
        return raw_body

    header_bytes = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(header_bytes + raw_body)

    if not message.is_multipart():
        raise ValueError("multipart request missing boundary")

    for part in message.iter_parts():
        if part.get_filename():
            return part.get_payload(decode=True)

    raise ValueError("no file found in multipart body")


def _json_response(status_code: int, payload: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def handler(event, context):
    try:
        image_bytes = _extract_image_bytes(event)
    except ValueError as e:
        return _json_response(400, {"error": str(e)})

    try:
        boxes = detect_checkboxes(image_bytes)
    except ValueError as e:
        return _json_response(400, {"error": f"invalid image: {e}"})
    except Exception as e:
        return _json_response(500, {"error": str(e)})

    return _json_response(200, {"boxes": boxes})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lambda/detect-cv && venv/bin/pytest tests/test_handler.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full test suite**

Run: `cd lambda/detect-cv && venv/bin/pytest -v`
Expected: PASS (all tests from Tasks 1-7).

---

### Task 8: Dockerfile and local container smoke test

**Files:**
- Create: `lambda/detect-cv/Dockerfile`
- Create: `lambda/detect-cv/.dockerignore`

**Interfaces:**
- Consumes: `handler.py` (Task 7), `detect_cv.py` (Tasks 1-4), `requirements.txt` (Task 1).
- Produces: a locally buildable Docker image tagged `document-scanner-detect-cv-test`. Consumed by Task 9 (Terraform references the real ECR-pushed build of this same Dockerfile).

- [ ] **Step 1: Write the Dockerfile**

`lambda/detect-cv/Dockerfile`:
```dockerfile
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY detect_cv.py handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
```

`lambda/detect-cv/.dockerignore`:
```
venv/
tests/
__pycache__/
*.pyc
```

- [ ] **Step 2: Build the image locally**

Run:
```bash
cd lambda/detect-cv
docker build -t document-scanner-detect-cv-test .
```
Expected: build succeeds (this will take a minute or two — installing
OpenCV's dependencies).

- [ ] **Step 3: Run the image locally using the Lambda Runtime Interface Emulator**

AWS's base image bundles the RIE automatically. Run:
```bash
docker run --rm -d -p 9000:8080 --name detect-cv-smoke-test document-scanner-detect-cv-test
```

- [ ] **Step 4: Invoke it with a real sample image and verify the response**

Run:
```bash
cd lambda/detect-cv
python3 -c "
import base64, json
img = open('../../samples/appraisal-1.png', 'rb').read()
event = {
    'headers': {'content-type': 'image/png'},
    'body': base64.b64encode(img).decode('ascii'),
    'isBase64Encoded': True,
}
print(json.dumps(event))
" > /tmp/detect-cv-test-event.json

curl -s -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d @/tmp/detect-cv-test-event.json | python3 -m json.tool
```
Expected: a JSON object with `"statusCode": 200` and a `"body"` field
that, when parsed, contains a `"boxes"` array with entries matching (in
count and rough positions) what `cli.py` produced locally for the same
image in Task 6.

- [ ] **Step 5: Stop the container**

Run: `docker stop detect-cv-smoke-test`

---

### Task 9: Terraform infrastructure — ECR, Lambda, IAM, routes

**Files:**
- Create: `infra/detect_cv.tf`
- Modify: `infra/main.tf:93-97` (the `aws_apigatewayv2_route "detect"` resource — change its `route_key`)
- Modify: `infra/outputs.tf`
- Modify: `infra/bootstrap-iam-policy.json`

**Interfaces:**
- Consumes: the Docker image built by Task 8 (same Dockerfile, built fresh and pushed by the `local-exec` provisioner in this task).
- Produces: Terraform-managed resources `aws_lambda_function.detect_cv`, `aws_apigatewayv2_route.detect_cv`, output `detect_textract_endpoint`. Consumed by Task 10.

- [ ] **Step 1: Rename the existing Textract route**

In `infra/main.tf`, find the `aws_apigatewayv2_route "detect"` resource
and change its `route_key` from `"POST /detect"` to
`"POST /detect-textract"`:

```hcl
resource "aws_apigatewayv2_route" "detect" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /detect-textract"
  target    = "integrations/${aws_apigatewayv2_integration.detect.id}"
}
```

Nothing else about the Go Lambda or its integration changes.

- [ ] **Step 2: Add the new CV Lambda's infrastructure**

Create `infra/detect_cv.tf`:
```hcl
# --- Detect-CV Lambda (Python, container image) ----------------------------

resource "aws_ecr_repository" "detect_cv" {
  name                 = "${local.name_prefix}-detect-cv"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "null_resource" "build_and_push_detect_cv_image" {
  triggers = {
    source_hash = sha256(join("", [
      filesha256("${path.module}/../lambda/detect-cv/detect_cv.py"),
      filesha256("${path.module}/../lambda/detect-cv/handler.py"),
      filesha256("${path.module}/../lambda/detect-cv/requirements.txt"),
      filesha256("${path.module}/../lambda/detect-cv/Dockerfile"),
    ]))
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../lambda/detect-cv"
    command     = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.detect_cv.repository_url}
      docker build --platform linux/arm64 -t ${aws_ecr_repository.detect_cv.repository_url}:latest .
      docker push ${aws_ecr_repository.detect_cv.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.detect_cv]
}

data "aws_ecr_image" "detect_cv" {
  repository_name = aws_ecr_repository.detect_cv.name
  image_tag       = "latest"

  depends_on = [null_resource.build_and_push_detect_cv_image]
}

resource "aws_iam_role" "detect_cv_exec" {
  name = "${local.name_prefix}-detect-cv-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "detect_cv_basic_execution" {
  role       = aws_iam_role.detect_cv_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "detect_cv_lambda" {
  name              = "/aws/lambda/${local.name_prefix}-detect-cv"
  retention_in_days = 14
}

resource "aws_lambda_function" "detect_cv" {
  function_name = "${local.name_prefix}-detect-cv"
  role          = aws_iam_role.detect_cv_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.detect_cv.repository_url}@${data.aws_ecr_image.detect_cv.image_digest}"
  architectures = ["arm64"]
  timeout       = 30
  memory_size   = 1024

  depends_on = [
    aws_cloudwatch_log_group.detect_cv_lambda,
    aws_iam_role_policy_attachment.detect_cv_basic_execution,
  ]
}

resource "aws_apigatewayv2_integration" "detect_cv" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.detect_cv.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "detect_cv" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /detect"
  target    = "integrations/${aws_apigatewayv2_integration.detect_cv.id}"
}

resource "aws_lambda_permission" "apigw_invoke_detect_cv" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.detect_cv.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}
```

- [ ] **Step 3: Add the new output for the renamed Textract route**

In `infra/outputs.tf`, add:
```hcl
output "detect_textract_endpoint" {
  description = "Full URL of the POST /detect-textract endpoint (Textract-based)"
  value       = "${aws_apigatewayv2_api.http_api.api_endpoint}/detect-textract"
}
```

The existing `detect_endpoint` output (`"${aws_apigatewayv2_api.http_api.api_endpoint}/detect"`)
needs no change — it's a plain string concatenation of the base URL and
`/detect`, so it automatically now refers to the CV Lambda once this plan
is applied, which is correct: `/detect` is meant to be the canonical
endpoint.

- [ ] **Step 4: Extend the bootstrap deploy-user policy with ECR permissions**

In `infra/bootstrap-iam-policy.json`, add two new statements (matching the
existing least-privilege pattern — `ecr:GetAuthorizationToken` doesn't
support resource-level scoping, same situation as the existing
`CloudWatchLogsDescribe` statement, so it gets `Resource: "*"` in its own
statement; everything else is scoped to this project's repo name):
```json
{
  "Sid": "EcrAuth",
  "Effect": "Allow",
  "Action": "ecr:GetAuthorizationToken",
  "Resource": "*"
},
{
  "Sid": "EcrManageDetectCvRepo",
  "Effect": "Allow",
  "Action": [
    "ecr:CreateRepository",
    "ecr:DeleteRepository",
    "ecr:DescribeRepositories",
    "ecr:BatchCheckLayerAvailability",
    "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload",
    "ecr:PutImage",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
    "ecr:DescribeImages",
    "ecr:ListImages",
    "ecr:TagResource",
    "ecr:UntagResource"
  ],
  "Resource": "arn:aws:ecr:*:*:repository/document-scanner-*"
}
```

Note: `lambda:*` and `iam:*` statements in this file already use a
`document-scanner-*` wildcard that covers the new `detect-cv` function and
role names — no changes needed there.

- [ ] **Step 5: Apply the updated policy to the deploy user**

This needs root/admin credentials, per the existing pattern in this
project (the deploy user cannot grant itself new permissions). Run:
```bash
aws iam put-user-policy \
  --user-name document-scanner-deployer \
  --policy-name document-scanner-deploy \
  --policy-document file://infra/bootstrap-iam-policy.json
```

- [ ] **Step 6: Validate the Terraform configuration**

Run:
```bash
cd infra
terraform fmt -recursive
AWS_PROFILE=document-scanner-tf terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Plan (do not apply yet)**

Run: `AWS_PROFILE=document-scanner-tf terraform plan -input=false -no-color`
Expected: a plan showing the route rename (in-place update), the new ECR
repo, new IAM role/policy, new CloudWatch log group, new Lambda function,
new integration, new route, new permission, and the `null_resource` for
the image build/push — all additions except the one route-rename update,
zero destroys of unrelated resources. If this fails with `AccessDenied`
on ECR actions, it's likely the same IAM-propagation delay seen earlier in
this project (can take a minute or two after Step 5) — wait and retry
before assuming something is wrong.

---

### Task 10: Deploy and validate against both live endpoints

**Files:** none (deployment + verification only).

**Interfaces:**
- Consumes: everything from Task 9.
- Produces: a live `POST /detect` (CV) and `POST /detect-textract` (Textract), both reachable.

- [ ] **Step 1: Apply**

Run:
```bash
cd infra
AWS_PROFILE=document-scanner-tf terraform apply -input=false -auto-approve -no-color
```
Expected: apply succeeds; outputs include `detect_endpoint` (now the CV
Lambda) and the new `detect_textract_endpoint`.

- [ ] **Step 2: Run the folder-testing script against the CV endpoint**

Run:
```bash
CV_ENDPOINT=$(terraform -chdir=infra output -raw detect_endpoint)
./scripts/call_detect_api.sh samples "$CV_ENDPOINT"
```
Expected: HTTP 200 for all 4 samples, with box/checked/unchecked counts
in the same ballpark as the local `cli.py` run from Task 6 (should match
closely — it's the same code, containerized).

- [ ] **Step 3: Confirm the Textract endpoint still works unchanged**

Run:
```bash
TEXTRACT_ENDPOINT=$(terraform -chdir=infra output -raw detect_textract_endpoint)
./scripts/call_detect_api.sh samples "$TEXTRACT_ENDPOINT"
```
Expected: HTTP 200 for all 4 samples, with the same counts as before this
plan (98/33/65, 39/17/22, 48/12/36, 71/26/45 — see
`samples/results/appraisal-*.json`, the pre-existing Textract results).

- [ ] **Step 4: Verify error handling on the live CV endpoint**

Run:
```bash
CV_ENDPOINT=$(terraform -chdir=infra output -raw detect_endpoint)

curl -s -X POST "$CV_ENDPOINT" -H "Content-Type: image/png" --data-binary "" -w "\nHTTP %{http_code}\n"
```
Expected: `400` with a JSON error body (mirrors the Go Lambda's behavior
verified earlier in this project).

- [ ] **Step 5: Check CV Lambda logs for the test invocations**

Run:
```bash
aws logs tail /aws/lambda/document-scanner-dev-detect-cv --profile document-scanner --since 10m
```
Expected: log entries for the invocations above, no unexpected errors or
stack traces.

---

### Task 11: README updates

**Files:**
- Modify: `README.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Update the Architecture section**

Add a paragraph describing the new CV pipeline and the two-endpoint split.
Mention: `POST /detect` now runs the Python/OpenCV pipeline
(`lambda/detect-cv/`); `POST /detect-textract` is the original Go/Textract
implementation, kept for comparison. Link to
`docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md` for
the full rationale (the two Textract failure cases that motivated this).

- [ ] **Step 2: Add Docker as a prerequisite**

In the "Prerequisites" section, note that Docker is required to build/push
the CV Lambda's container image, and that it's not asdf-managed (asdf
pins language/CLI tool versions; a container runtime is a different kind
of dependency).

- [ ] **Step 3: Update the Testing section**

Document the local CV development loop (`lambda/detect-cv/cli.py`) and
that `scripts/call_detect_api.sh` now needs an explicit endpoint argument
to target either `/detect` or `/detect-textract` (both share the same
default-empty second argument behavior already built into that script —
just show both usages).

- [ ] **Step 4: Refresh "Next steps"**

Remove completed items now superseded by this plan (the CV pipeline itself
is done). Keep/add: whether to eventually retire `/detect-textract`
(deferred per the design spec), whether the containment check alone
proves sufficient across more document types, handling documents very
different from the sample set.

- [ ] **Step 5: Re-read the full README for internal consistency**

Check every command, path, and endpoint reference against what's actually
in the repo after this plan — the same standard used for the original
README (verified command-by-command against real repo/AWS state).

---

### Task 12: Review against docs/chatgpt.md

**Files:** none — read-only review task.

**Interfaces:** none.

Added after Task 11, at the user's request, since `docs/chatgpt.md` (the
prior exploratory session that served as methodology guidance for this
whole plan) may contain suggested checks/techniques we didn't end up
covering.

- [ ] **Step 1: Dispatch a fresh agent (no prior context) to cross-check the implementation**

Give it `docs/chatgpt.md` in full and the actual implementation
(`lambda/detect-cv/detect_cv.py`, `tests/test_detect_cv.py`). Ask it to
walk every suggested step/technique in that doc (grayscale, threshold —
including the adaptive-threshold fallback for "real-world scans" the doc
mentions as an alternative to global thresholding, which this
implementation does NOT use — findContours/RETR_TREE, boundingRect,
aspect-ratio + size filtering, `approxPolyDP` 4-corner check, nested-
contour deduplication via IoU, interior-crop ink-density classification,
the doc's suggested Canny+HoughLinesP diagonal-stroke robustness
improvement, reading-order sort, visual validation) and check off which
are implemented, which are deliberately not (and whether that deviation
is justified — e.g. the connected-component containment check this
implementation uses instead of Hough-line diagonal detection was a
deliberate, reviewed design choice, not an oversight), and which are
missing without a documented reason.

- [ ] **Step 2: Triage findings**

Any real, undocumented gap gets added to the README's "Next steps" /
known-limitations section (or fixed, if small and clearly correct) —
controller's judgment call, same as every other review finding in this
plan.

---

### Task 13: Final whole-plan review

**Files:** none — read-only review task.

**Interfaces:** none.

The final whole-branch review this workflow's process calls for (see
`superpowers:subagent-driven-development`'s "Final Review" section) —
not yet performed at the time Task 11 completed. Added explicitly at the
user's request.

- [ ] **Step 1: Dispatch on the most capable available model**

Use `superpowers:requesting-code-review`'s `code-reviewer.md` template.
Base = the commit before this plan's work started (`125c5f1`, "Initial
document-scanner app"). Head = current `HEAD`. Give it the full plan
(this file) and the design spec
(`docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md`) as
the requirements it's reviewing against.

- [ ] **Step 2: Handle findings per the skill's final-review process**

One fix dispatch for all findings together (not one per finding), one
scoped re-review, adjudicate any residuals — per
`subagent-driven-development`'s "Final Review" section.

---

## After this plan

All changes are left uncommitted per the Global Constraints above — review
the diff and commit when ready. Nothing in this plan deletes or modifies
the existing Go/Textract implementation beyond its route path.
