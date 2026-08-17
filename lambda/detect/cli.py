import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2

from detect_cv import detect_checkboxes

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def make_run_timestamp() -> str:
    """Filesystem-safe, sortable timestamp shared by every image in one run."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def output_paths(image_path: Path, results_dir: Path) -> tuple[Path, Path]:
    name = image_path.stem
    return (
        results_dir / f"{name}.json",
        results_dir / f"{name}-annotated.png",
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
        results_root = target / "results"
    else:
        images = [target]
        results_root = target.parent / "results"

    if not images:
        print(f"No images found in {target}", file=sys.stderr)
        sys.exit(1)

    results_dir = results_root / make_run_timestamp()
    results_dir.mkdir(parents=True, exist_ok=True)
    for image_path in images:
        process_image(image_path, results_dir)
    print(f"\nResults written to {results_dir}")


if __name__ == "__main__":
    main()
