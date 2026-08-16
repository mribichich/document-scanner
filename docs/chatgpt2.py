import cv2
import numpy as np
import json
import os


def classify_checkbox(gray, x1, y1, x2, y2):
    """
    Determine whether a checkbox is checked.

    We ignore the checkbox border and inspect only the interior.
    A checked checkbox contains substantially more dark pixels
    and/or diagonal strokes from an X/checkmark.
    """

    w = x2 - x1
    h = y2 - y1

    # Ignore the border itself.
    margin = max(2, int(min(w, h) * 0.20))

    ix1 = x1 + margin
    iy1 = y1 + margin
    ix2 = x2 - margin
    iy2 = y2 - margin

    if ix2 <= ix1 or iy2 <= iy1:
        return False

    roi = gray[iy1:iy2, ix1:ix2]

    if roi.size == 0:
        return False

    # Convert interior to black/white.
    binary = cv2.adaptiveThreshold(
        roi,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        11,
        3
    )

    # Percentage of dark pixels inside the checkbox.
    dark_ratio = np.mean(binary > 0)

    # Look for diagonal strokes, useful for X marks/check marks.
    lines = cv2.HoughLinesP(
        binary,
        rho=1,
        theta=np.pi / 180,
        threshold=max(3, min(roi.shape) // 3),
        minLineLength=max(3, min(roi.shape) // 3),
        maxLineGap=2
    )

    diagonal_count = 0

    if lines is not None:
        for line in lines[:, 0]:

            lx1, ly1, lx2, ly2 = line

            angle = abs(
                np.degrees(
                    np.arctan2(
                        ly2 - ly1,
                        lx2 - lx1
                    )
                )
            )

            angle = min(angle, 180 - angle)

            # Diagonal line.
            if 20 <= angle <= 70:
                diagonal_count += 1

    # Classification rule.
    return (
        dark_ratio >= 0.12
        or diagonal_count >= 2
    )


def detect_checkboxes(image):
    """
    Detect checkbox rectangles in an image.

    Returns:

    [
        {
            "bbox": [x1, y1, x2, y2],
            "is_checked": True
        },
        ...
    ]
    """

    # Convert to grayscale.
    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    # Small blur removes scanner noise.
    blurred = cv2.GaussianBlur(
        gray,
        (3, 3),
        0
    )

    # Adaptive threshold works better than a fixed threshold
    # for scanned documents with different background levels.
    binary = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        7
    )

    # Close tiny gaps in checkbox borders.
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (2, 2)
    )

    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        kernel
    )

    # Find contours.
    contours, _ = cv2.findContours(
        binary,
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE
    )

    candidates = []

    for contour in contours:

        perimeter = cv2.arcLength(
            contour,
            True
        )

        if perimeter == 0:
            continue

        # Approximate contour with polygons.
        approx = cv2.approxPolyDP(
            contour,
            0.04 * perimeter,
            True
        )

        # Checkbox should look like a quadrilateral.
        if len(approx) != 4:
            continue

        if not cv2.isContourConvex(approx):
            continue

        x, y, w, h = cv2.boundingRect(
            approx
        )

        # Checkbox dimensions in this document.
        if not (
            10 <= w <= 45
            and
            10 <= h <= 45
        ):
            continue

        # A checkbox should be approximately square.
        aspect_ratio = w / float(h)

        if not (
            0.75 <= aspect_ratio <= 1.25
        ):
            continue

        # Check how rectangular the contour is.
        area = cv2.contourArea(contour)

        rectangularity = (
            area /
            float(w * h)
        )

        if rectangularity < 0.45:
            continue

        candidates.append(
            (
                x,
                y,
                x + w,
                y + h
            )
        )

    # ---------------------------------------------------------
    # Remove duplicate detections.
    # ---------------------------------------------------------

    candidates.sort(
        key=lambda box: (
            box[1],
            box[0]
        )
    )

    unique = []

    for box in candidates:

        x1, y1, x2, y2 = box

        duplicate = False

        for existing in unique:

            ux1, uy1, ux2, uy2 = existing

            # Compare centers.
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            ucx = (ux1 + ux2) / 2
            ucy = (uy1 + uy2) / 2

            if (
                abs(cx - ucx) <= 3
                and
                abs(cy - ucy) <= 3
            ):
                duplicate = True
                break

        if not duplicate:
            unique.append(box)

    # ---------------------------------------------------------
    # Classify every detected checkbox.
    # ---------------------------------------------------------

    results = []

    for x1, y1, x2, y2 in unique:

        checked = classify_checkbox(
            gray,
            x1,
            y1,
            x2,
            y2
        )

        results.append({
            "bbox": [
                int(x1),
                int(y1),
                int(x2),
                int(y2)
            ],
            "is_checked": bool(checked)
        })

    # Reading order: top -> bottom, left -> right.
    results.sort(
        key=lambda item: (
            item["bbox"][1],
            item["bbox"][0]
        )
    )

    return results


def draw_results(image, boxes):
    """
    Draw detected checkboxes on the document.

    Red   = checked
    Blue  = unchecked
    """

    output = image.copy()

    for i, item in enumerate(boxes, 1):

        x1, y1, x2, y2 = item["bbox"]

        checked = item["is_checked"]

        # OpenCV uses BGR.
        if checked:
            color = (0, 0, 220)       # red
        else:
            color = (220, 90, 0)      # blue

        # Draw checkbox rectangle.
        cv2.rectangle(
            output,
            (x1, y1),
            (x2, y2),
            color,
            2
        )

        # Draw detection number.
        label_y = max(
            0,
            y1 - 18
        )

        cv2.rectangle(
            output,
            (x1, label_y),
            (x1 + 18, label_y + 16),
            color,
            -1
        )

        cv2.putText(
            output,
            str(i),
            (x1 + 3, label_y + 13),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            (255, 255, 255),
            1,
            cv2.LINE_AA
        )

    return output


def main():

    input_image = "page4_original.jpg"

    output_json = "checkboxes.json"
    output_image = "page4_annotated.png"

    # ---------------------------------------------------------
    # Load original image.
    # ---------------------------------------------------------

    image = cv2.imread(
        input_image
    )

    if image is None:
        raise RuntimeError(
            f"Could not load {input_image}"
        )

    # ---------------------------------------------------------
    # Detect checkboxes.
    # ---------------------------------------------------------

    boxes = detect_checkboxes(
        image
    )

    # ---------------------------------------------------------
    # Save JSON.
    # ---------------------------------------------------------

    result = {
        "boxes": boxes
    }

    with open(
        output_json,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            indent=2
        )

    # ---------------------------------------------------------
    # Create visual verification image.
    # ---------------------------------------------------------

    annotated = draw_results(
        image,
        boxes
    )

    cv2.imwrite(
        output_image,
        annotated
    )

    print(
        f"Detected {len(boxes)} checkboxes"
    )

    print(
        f"JSON saved to: {output_json}"
    )

    print(
        f"Annotated image saved to: {output_image}"
    )


if __name__ == "__main__":
    main()