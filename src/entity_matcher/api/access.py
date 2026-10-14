"""Loopback request metadata and actual-body guards, without starting a server."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Iterable

from ..io.catalog import MAX_FILE_BYTES

TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
MAX_HEADERS = 64
MAX_HEADER_BYTES = 16 * 1024


class AccessDenied(ValueError):
    """Only a reason code is exposed; header/token/body contents never enter errors."""


def _port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Invalid local port")
    return value


@dataclass(frozen=True)
class LocalAccessPolicy:
    api_port: int
    write_token: str = field(repr=False)
    frontend_ports: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        _port(self.api_port)
        if type(self.write_token) is not str or TOKEN.fullmatch(self.write_token) is None:
            raise ValueError("Invalid launch write token")
        if type(self.frontend_ports) is not tuple or len(self.frontend_ports) > 2:
            raise ValueError("Invalid frontend port configuration")
        for port in self.frontend_ports:
            _port(port)
        if len(set(self.frontend_ports)) != len(self.frontend_ports):
            raise ValueError("Duplicate frontend port configuration")

    @property
    def hosts(self) -> frozenset[str]:
        return frozenset(f"{host}:{self.api_port}" for host in ("127.0.0.1", "localhost"))

    @property
    def origins(self) -> frozenset[str]:
        return frozenset(
            f"http://{host}:{port}"
            for port in (self.api_port, *self.frontend_ports)
            for host in ("127.0.0.1", "localhost")
        )


@dataclass(frozen=True)
class RequestAccess:
    write: bool
    declared_body_length: int | None


def _headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    count = total = 0
    security_headers = {"host", "origin", "x-review-token", "content-length"}
    for pair in headers:
        count += 1
        if count > MAX_HEADERS or type(pair) is not tuple or len(pair) != 2:
            raise AccessDenied("invalid_header_shape_or_count")
        name, value = pair
        if type(name) is not str or type(value) is not str or HEADER_NAME.fullmatch(name) is None:
            raise AccessDenied("invalid_header_shape_or_name")
        if any(char in value for char in ("\r", "\n", "\x00")):
            raise AccessDenied("invalid_header_control_character")
        try:
            total += len(name.encode("ascii")) + len(value.encode("utf-8"))
        except UnicodeError as error:
            raise AccessDenied("invalid_header_encoding") from error
        if total > MAX_HEADER_BYTES:
            raise AccessDenied("header_bytes_exceeded")
        key = name.casefold()
        if key in result and key in security_headers:
            raise AccessDenied("duplicate_security_header")
        if key == "forwarded" or key.startswith("x-forwarded-") or key == "transfer-encoding":
            raise AccessDenied("proxy_or_streaming_header_unsupported")
        result[key] = value
    return result


def authorize_request(
    policy: LocalAccessPolicy,
    headers: Iterable[tuple[str, str]],
    *,
    method: str,
) -> RequestAccess:
    """Internal metadata gate; a future adapter must then read/check bounded actual bytes.

    No wildcard CORS, proxy trust, cookie authorization or null/file-origin writes.
    Actual listener registration/binding and HTTP/browser tests are separate execution gates.
    """
    if type(method) is not str or method not in {"GET", "HEAD", "POST"}:
        raise AccessDenied("unsupported_method")
    values = _headers(headers)
    if values.get("host", "").casefold() not in policy.hosts:
        raise AccessDenied("invalid_loopback_host_or_port")
    origin = values.get("origin")
    if origin is not None and origin.casefold() not in policy.origins:
        raise AccessDenied("invalid_loopback_origin")
    if method in {"GET", "HEAD"}:
        return RequestAccess(False, None)
    if origin is None:
        raise AccessDenied("write_origin_required")
    token = values.get("x-review-token", "")
    if TOKEN.fullmatch(token) is None or not secrets.compare_digest(token, policy.write_token):
        raise AccessDenied("write_token_required_or_invalid")
    length = values.get("content-length", "")
    if not length.isascii() or not length.isdecimal() or len(length) > 10:
        raise AccessDenied("bounded_content_length_required")
    declared = int(length)
    if declared > MAX_FILE_BYTES:
        raise AccessDenied("request_body_too_large")
    return RequestAccess(True, declared)


def check_actual_body(body: bytes, access: RequestAccess) -> bytes:
    """Never trust declared length alone; this helper validates already bounded bytes.

    Future HTTP adapter must bound its streaming read BEFORE allocating an unbounded body.
    This module supplies no framework/middleware/route or durable effect implementation.
    """
    if type(body) is not bytes:
        raise AccessDenied("exact_body_bytes_required")
    if not access.write or access.declared_body_length is None:
        raise AccessDenied("write_metadata_required")
    if len(body) > MAX_FILE_BYTES:
        raise AccessDenied("actual_body_too_large")
    if len(body) != access.declared_body_length:
        raise AccessDenied("actual_body_length_disagrees")
    return body
