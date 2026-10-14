"""Pure request guards; this is not executed HTTP/browser/service security evidence."""

import pytest

from entity_matcher.api.access import (
    AccessDenied,
    LocalAccessPolicy,
    RequestAccess,
    authorize_request,
    check_actual_body,
)

TEST_TOKEN = "unit_control_token_not_a_secret_123456"


def policy() -> LocalAccessPolicy:
    return LocalAccessPolicy(api_port=20001, frontend_ports=(20002,), write_token=TEST_TOKEN)


def headers(**changes: str) -> tuple[tuple[str, str], ...]:
    values = {
        "Host": "127.0.0.1:20001",
        "Origin": "http://localhost:20002",
        "X-Review-Token": TEST_TOKEN,
        "Content-Length": "2",
    }
    return tuple((values | changes).items())


def test_known_loopback_write_metadata_then_exact_body() -> None:
    """Catches denied valid metadata and declared lengths trusted without actual-byte checks."""
    access = authorize_request(policy(), headers(), method="POST")
    assert access.write and access.declared_body_length == 2
    assert check_actual_body(b"{}", access) == b"{}"
    assert TEST_TOKEN not in repr(policy())


def test_read_without_origin_has_no_write_access() -> None:
    """Catches read metadata becoming a write credential and implicit null/file origins."""
    access = authorize_request(policy(), (("Host", "localhost:20001"),), method="GET")
    assert not access.write
    with pytest.raises(AccessDenied, match="write_metadata_required"):
        check_actual_body(b"", access)


@pytest.mark.parametrize(
    "changes",
    [
        {"Host": "evil.example:20001"},
        {"Host": "127.0.0.1:20002"},
        {"Host": "evil@localhost:20001"},
        {"Origin": "https://localhost:20002"},
        {"Origin": "http://evil.example:20002"},
        {"Origin": "null"},
        {"Origin": "http://localhost:20002/path"},
        {"X-Review-Token": ""},
        {"X-Review-Token": "a" * 40},
        {"X-Review-Token": "é" * 40},
        {"Content-Length": "-1"},
        {"Content-Length": "1e3"},
        {"Content-Length": ""},
        {"Content-Length": "20971521"},
        {"Host": "localhost:20001\r\nInjected: true"},
    ],
)
def test_untrusted_host_origin_token_or_length_rejects(changes: dict) -> None:
    """Catches rebinding/cross-origin/missing-token/header injection and request-bound bypasses."""
    with pytest.raises(AccessDenied) as error:
        authorize_request(policy(), headers(**changes), method="POST")
    assert TEST_TOKEN not in str(error.value)


def test_missing_origin_and_duplicate_case_insensitive_security_headers_reject() -> None:
    """Catches ambiguous duplicate headers or native-style missing Origin bypassing write policy."""
    missing = tuple(pair for pair in headers() if pair[0] != "Origin")
    with pytest.raises(AccessDenied, match="write_origin_required"):
        authorize_request(policy(), missing, method="POST")
    for name, value in (
        ("host", "localhost:20001"),
        ("origin", "http://localhost:20002"),
        ("x-review-token", TEST_TOKEN),
        ("content-length", "2"),
    ):
        with pytest.raises(AccessDenied, match="duplicate_security_header"):
            authorize_request(policy(), (*headers(), (name, value)), method="POST")


@pytest.mark.parametrize(
    "name", ["Forwarded", "X-Forwarded-Host", "X-Forwarded-Proto", "Transfer-Encoding"]
)
def test_local_policy_never_trusts_proxy_or_streaming_headers(name: str) -> None:
    """Catches a future framework silently introducing proxy/chunked semantics into local guards."""
    with pytest.raises(AccessDenied, match="proxy_or_streaming_header_unsupported"):
        authorize_request(policy(), (*headers(), (name, "value")), method="POST")


def test_actual_bytes_length_type_and_bound_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches advertised Content-Length allowing larger or different data afterward."""
    access = authorize_request(policy(), headers(), method="POST")
    with pytest.raises(AccessDenied, match="actual_body_length_disagrees"):
        check_actual_body(b"xxx", access)
    with pytest.raises(AccessDenied, match="exact_body_bytes_required"):
        check_actual_body("{}", access)
    from entity_matcher.api import access as guards

    assert guards.MAX_FILE_BYTES == 20971520
    monkeypatch.setattr(guards, "MAX_FILE_BYTES", 2)
    with pytest.raises(AccessDenied, match="actual_body_too_large"):
        check_actual_body(b"xxx", RequestAccess(True, 3))


@pytest.mark.parametrize(
    "options",
    [
        {"api_port": True},
        {"api_port": 0},
        {"api_port": 65536},
        {"write_token": "short"},
        {"frontend_ports": [20002]},
        {"frontend_ports": (20002, 20002)},
    ],
)
def test_policy_configuration_bounds_and_token_shape_reject(options: dict) -> None:
    """Catches bool ports, mutable port lists, duplicate origins and inadequate launch tokens."""
    with pytest.raises(ValueError):
        LocalAccessPolicy(**({"api_port": 20001, "write_token": TEST_TOKEN} | options))


def test_header_bounds_shape_and_unsupported_methods_reject() -> None:
    """Catches unbounded header accumulation or methods reaching an unintended write route."""
    for bad in (
        (("Host", "localhost:20001"),) * 65,
        (("Host", "localhost:20001"), ("X-Big", "x" * 16385)),
        (("Bad Name", "value"),),
    ):
        with pytest.raises(AccessDenied):
            authorize_request(policy(), bad, method="GET")
    with pytest.raises(AccessDenied, match="unsupported_method"):
        authorize_request(policy(), headers(), method="DELETE")
