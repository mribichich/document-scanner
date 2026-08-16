import base64
import json
import logging
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
    except Exception:
        # Never leak internal exception detail (stack internals, library
        # error text) into the response body. The full exception still
        # reaches CloudWatch Logs via logging.exception, which Lambda
        # ships there automatically — just not to the caller.
        logging.exception("checkbox detection failed")
        return _json_response(500, {"error": "internal error"})

    return _json_response(200, {"boxes": boxes})
