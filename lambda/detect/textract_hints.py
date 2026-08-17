"""Textract FORMS analysis, turned into checkbox location hints.

Textract's KEY_VALUE_SET blocks associate a label ("No Zoning") with a
value; when that value contains a SELECTION_ELEMENT, Textract has already
located a checkbox for that label, precisely. detect_cv.py uses that
location as a place to look closer, never as a trusted final answer on its
own — Textract's own checked/unchecked call (SelectionStatus) is
deliberately never read here. It has a known false positive on a stray
mark crossing a box's border (the original bug that motivated this whole
CV pipeline — see docs/algorithm-known-issues.md's "Reference-data
caveat"), so checked/unchecked always goes through this project's own
is_checked() instead, once a location is confirmed.

A KEY block with no VALUE, or a VALUE with no SELECTION_ELEMENT child,
means Textract's own form model didn't manage to associate any checkbox
with that label at all — these come back with bbox=None, for a future
fallback (e.g. an LLM given the label and its surroundings) to attempt.
"""

from dataclasses import dataclass

import boto3

_client = None


def _textract_client():
    global _client
    if _client is None:
        _client = boto3.client("textract")
    return _client


def analyze_forms(image_bytes: bytes) -> dict:
    return _textract_client().analyze_document(
        Document={"Bytes": image_bytes},
        FeatureTypes=["FORMS"],
    )


@dataclass
class Hint:
    label: str
    bbox: tuple[int, int, int, int] | None  # (x1, y1, x2, y2) in pixels, or
    # None if Textract's own form model didn't resolve a checkbox here


def _block_text(block: dict, blocks_by_id: dict[str, dict]) -> str:
    words = []
    for rel in block.get("Relationships", []):
        if rel["Type"] != "CHILD":
            continue
        for child_id in rel["Ids"]:
            child = blocks_by_id.get(child_id)
            if child and child["BlockType"] == "WORD":
                words.append(child["Text"])
    return " ".join(words)


def _selection_element_bbox(
    value_block: dict, blocks_by_id: dict[str, dict], width: int, height: int
) -> tuple[int, int, int, int] | None:
    for rel in value_block.get("Relationships", []):
        if rel["Type"] != "CHILD":
            continue
        for child_id in rel["Ids"]:
            child = blocks_by_id.get(child_id)
            if child and child["BlockType"] == "SELECTION_ELEMENT":
                bb = child["Geometry"]["BoundingBox"]
                x1 = int(bb["Left"] * width)
                y1 = int(bb["Top"] * height)
                x2 = int((bb["Left"] + bb["Width"]) * width)
                y2 = int((bb["Top"] + bb["Height"]) * height)
                return (x1, y1, x2, y2)
    return None


def extract_checkbox_hints(
    textract_response: dict, width: int, height: int
) -> list[Hint]:
    blocks = textract_response.get("Blocks", [])
    blocks_by_id = {b["Id"]: b for b in blocks}

    hints = []
    for block in blocks:
        if block["BlockType"] != "KEY_VALUE_SET":
            continue
        if "KEY" not in block.get("EntityTypes", []):
            continue

        label = _block_text(block, blocks_by_id)
        if not label:
            continue

        bbox = None
        for rel in block.get("Relationships", []):
            if rel["Type"] != "VALUE":
                continue
            for value_id in rel["Ids"]:
                value_block = blocks_by_id.get(value_id)
                if value_block:
                    bbox = _selection_element_bbox(value_block, blocks_by_id, width, height)
                    if bbox is not None:
                        break
            if bbox is not None:
                break

        hints.append(Hint(label=label, bbox=bbox))

    return hints
