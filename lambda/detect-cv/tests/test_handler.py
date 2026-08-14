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
