from textract_hints import Hint, extract_checkbox_hints

WIDTH, HEIGHT = 1000, 1000


def _word(block_id, text):
    return {"Id": block_id, "BlockType": "WORD", "Text": text}


def _selection_element(block_id, bbox_norm, selected):
    return {
        "Id": block_id,
        "BlockType": "SELECTION_ELEMENT",
        "SelectionStatus": "SELECTED" if selected else "NOT_SELECTED",
        "Geometry": {"BoundingBox": bbox_norm},
    }


def _key_block(block_id, word_ids, value_ids=None):
    relationships = [{"Type": "CHILD", "Ids": word_ids}]
    if value_ids:
        relationships.append({"Type": "VALUE", "Ids": value_ids})
    return {
        "Id": block_id,
        "BlockType": "KEY_VALUE_SET",
        "EntityTypes": ["KEY"],
        "Relationships": relationships,
    }


def _value_block(block_id, child_ids):
    return {
        "Id": block_id,
        "BlockType": "KEY_VALUE_SET",
        "EntityTypes": ["VALUE"],
        "Relationships": [{"Type": "CHILD", "Ids": child_ids}] if child_ids else [],
    }


def test_resolved_key_with_selection_element_becomes_a_hint_with_pixel_bbox():
    blocks = [
        _key_block("key1", ["word1"], value_ids=["val1"]),
        _word("word1", "No"),
        _value_block("val1", ["sel1"]),
        _selection_element("sel1", {"Left": 0.1, "Top": 0.2, "Width": 0.05, "Height": 0.04}, selected=True),
    ]

    hints = extract_checkbox_hints({"Blocks": blocks}, WIDTH, HEIGHT)

    assert len(hints) == 1
    assert hints[0] == Hint(label="No", bbox=(100, 200, 150, 240))


def test_selection_status_is_never_surfaced_on_the_hint():
    # Textract's own checked/unchecked call is deliberately ignored - it
    # has a known false positive on stray marks crossing a box's border
    # (see textract_hints.py's module docstring). Hint only carries a
    # label and a location, nothing about checked state.
    blocks = [
        _key_block("key1", ["word1"], value_ids=["val1"]),
        _word("word1", "No Zoning"),
        _value_block("val1", ["sel1"]),
        _selection_element("sel1", {"Left": 0.1, "Top": 0.2, "Width": 0.05, "Height": 0.04}, selected=True),
    ]

    hints = extract_checkbox_hints({"Blocks": blocks}, WIDTH, HEIGHT)

    assert not hasattr(hints[0], "is_checked")
    assert not hasattr(hints[0], "selection_status")


def test_key_with_no_value_relationship_is_unresolved():
    blocks = [
        _key_block("key1", ["word1"]),  # no value_ids at all
        _word("word1", "Other (describe)"),
    ]

    hints = extract_checkbox_hints({"Blocks": blocks}, WIDTH, HEIGHT)

    assert hints == [Hint(label="Other (describe)", bbox=None)]


def test_key_whose_value_has_no_selection_element_is_unresolved():
    # A VALUE block that only contains WORD children (a text answer, not a
    # checkbox) - Textract's own form model never attempted a checkbox
    # association for this label.
    blocks = [
        _key_block("key1", ["word1"], value_ids=["val1"]),
        _word("word1", "Zoning Description"),
        _value_block("val1", ["word2"]),
        _word("word2", "Residential"),
    ]

    hints = extract_checkbox_hints({"Blocks": blocks}, WIDTH, HEIGHT)

    assert hints == [Hint(label="Zoning Description", bbox=None)]


def test_non_key_blocks_are_ignored():
    blocks = [
        _value_block("val1", ["word1"]),
        _word("word1", "stray value block with no matching key"),
    ]

    hints = extract_checkbox_hints({"Blocks": blocks}, WIDTH, HEIGHT)

    assert hints == []


def test_multi_word_label_is_joined_with_spaces():
    blocks = [
        _key_block("key1", ["word1", "word2"], value_ids=["val1"]),
        _word("word1", "Neighborhood"),
        _word("word2", "Boundaries"),
        _value_block("val1", ["sel1"]),
        _selection_element("sel1", {"Left": 0.0, "Top": 0.0, "Width": 0.01, "Height": 0.01}, selected=False),
    ]

    hints = extract_checkbox_hints({"Blocks": blocks}, WIDTH, HEIGHT)

    assert hints[0].label == "Neighborhood Boundaries"
