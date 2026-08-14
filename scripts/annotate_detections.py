import json
import sys
from PIL import Image, ImageDraw

samples_dir = sys.argv[1]
results_dir = sys.argv[2]

for i in range(1, 5):
    img_path = f"{samples_dir}/appraisal-{i}.png"
    json_path = f"{results_dir}/appraisal-{i}.json"
    out_path = f"{results_dir}/appraisal-{i}-annotated.png"

    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    data = json.load(open(json_path))
    boxes = data["boxes"]

    for b in boxes:
        x1, y1, x2, y2 = b["bbox"]
        color = (0, 200, 0) if b["is_checked"] else (220, 0, 0)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=6)

    img.save(out_path)
    checked = sum(1 for b in boxes if b["is_checked"])
    print(f"appraisal-{i}: {len(boxes)} boxes ({checked} checked, {len(boxes)-checked} unchecked) -> {out_path}")
