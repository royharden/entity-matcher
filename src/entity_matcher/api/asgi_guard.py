"""Bounded single-process loopback ASGI gate, independent of an HTTP framework.

The gate bounds its own buffers, not an HTTP server's earlier transport buffers.
One HTTP operation is admitted at a time; no socket, task worker or listener is
started here. Tests through receive/send do not establish browser/HTTP behavior.
"""

import asyncio
import json
import math

from ..io.catalog import MAX_FILE_BYTES
from .access import (
    MAX_HEADER_BYTES,
    MAX_HEADERS,
    AccessDenied,
    LocalAccessPolicy,
    authorize_request,
    check_actual_body,
)

MAX_BODY_CHUNKS = 4096


def _decode_headers(scope):
    raw = scope.get("headers", ())
    if type(raw) not in (tuple, list) or len(raw) > MAX_HEADERS:
        raise AccessDenied("invalid_header_shape_or_count")
    total, result, seen = 0, [], set()
    for pair in raw:
        if type(pair) is not tuple or len(pair) != 2 or any(type(v) is not bytes for v in pair):
            raise AccessDenied("invalid_header_shape_or_count")
        name, value = pair
        total += len(name) + len(value)
        if total > MAX_HEADER_BYTES:
            raise AccessDenied("header_bytes_exceeded")
        try:
            key, text = name.decode("ascii").lower(), value.decode("latin-1")
        except UnicodeError as error:
            raise AccessDenied("invalid_header_encoding") from error
        if key in {
            "content-type",
            "access-control-request-method",
            "access-control-request-headers",
        }:
            if key in seen:
                raise AccessDenied("duplicate_transport_header")
            seen.add(key)
        result.append((key, text))
    return result


def _cors(headers, origin):
    result = [(k, v) for k, v in headers if not k.lower().startswith(b"access-control-")]
    if origin is not None:
        result.append((b"access-control-allow-origin", origin.encode("ascii")))
        result.append((b"vary", b"Origin"))
    return result


async def _error(send, status, code, origin=None):
    body = json.dumps({"code": code}, separators=(",", ":")).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": _cors(
                [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
                origin,
            ),
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive, access):
    data = bytearray()
    for _ in range(MAX_BODY_CHUNKS):
        message = await receive()
        if type(message) is not dict or message.get("type") == "http.disconnect":
            raise AccessDenied("request_disconnected")
        if message.get("type") != "http.request":
            raise AccessDenied("invalid_body_message")
        body, more = message.get("body", b""), message.get("more_body", False)
        if type(body) is not bytes or type(more) is not bool:
            raise AccessDenied("invalid_body_message")
        # Check BEFORE append, including a single server-supplied oversized chunk.
        if len(body) > MAX_FILE_BYTES - len(data):
            raise AccessDenied("actual_body_too_large")
        if len(body) > access.declared_body_length - len(data):
            raise AccessDenied("actual_body_length_disagrees")
        data.extend(body)
        if not more:
            return check_actual_body(bytes(data), access)
    raise AccessDenied("body_chunk_count_exceeded")


class LocalASGIGuard:
    def __init__(self, app, policy: LocalAccessPolicy, *, body_timeout_s: float = 10.0):
        if type(policy) is not LocalAccessPolicy or (
            type(body_timeout_s) not in (int, float)
            or not math.isfinite(body_timeout_s)
            or not 0 < body_timeout_s <= 30
        ):
            raise ValueError("explicit_local_policy_and_bounded_deadline_required")
        self.app, self.policy, self.body_timeout_s = app, policy, body_timeout_s
        self._active = False

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            return await self.app(scope, receive, send)
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope.get("type") != "http":
            raise AccessDenied("unsupported_asgi_scope")
        origin = None
        try:
            headers = _decode_headers(scope)
            values, method = dict(headers), scope.get("method")
            if method == "OPTIONS":
                authorize_request(self.policy, headers, method="GET")
                origin = values.get("origin")
                requested = values.get("access-control-request-method")
                requested_headers = values.get("access-control-request-headers", "")
                names = {
                    part.strip().lower() for part in requested_headers.split(",") if part.strip()
                }
                if (
                    origin is None
                    or requested not in {"GET", "POST"}
                    or not names.issubset(
                        {
                            "content-type",
                            "x-review-token",
                        }
                    )
                ):
                    raise AccessDenied("preflight_not_allowed")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 204,
                        "headers": _cors(
                            [
                                (b"content-length", b"0"),
                            ],
                            origin,
                        )
                        + [
                            (b"access-control-allow-methods", requested.encode("ascii")),
                            (b"access-control-allow-headers", b"content-type, x-review-token"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
            access = authorize_request(self.policy, headers, method=method)
            origin = values.get("origin")
            if method == "POST":
                content_type = values.get("content-type", "").lower().split(";")
                if (
                    content_type[0].strip() != "application/json"
                    or len(content_type) > 2
                    or (
                        len(content_type) == 2
                        and content_type[1].strip()
                        not in {
                            "charset=utf-8",
                            'charset="utf-8"',
                        }
                    )
                ):
                    raise AccessDenied("json_content_type_required")
            elif values.get("content-length", "0") != "0":
                raise AccessDenied("read_body_unsupported")
        except AccessDenied as error:
            await _error(send, 403, str(error), origin)
            return
        if self._active:
            await _error(send, 503, "api_busy", origin)
            return
        self._active = True
        try:
            try:
                data = (
                    await asyncio.wait_for(_read_body(receive, access), self.body_timeout_s)
                    if access.write
                    else b""
                )
            except TimeoutError:
                await _error(send, 408, "request_body_timeout", origin)
                return
            except AccessDenied as error:
                await _error(send, 400, str(error), origin)
                return
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": data, "more_body": False}

            async def safe_send(message):
                if message.get("type") == "http.response.start":
                    message = {**message, "headers": _cors(message.get("headers", ()), origin)}
                await send(message)

            await self.app(scope, bounded_receive, safe_send)
        finally:
            self._active = False
